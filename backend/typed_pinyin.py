"""Tone errors from *typed* pinyin — text mode's counterpart to `tones.py`.

The spoken path can only report that a syllable was off: Azure PA returns an
accuracy number, not a produced tone, so `ToneError.said` is the sentinel
`tones.SAID_UNKNOWN`. Typing inverts that. A learner who writes `ni2hao3` has
stated their belief outright, so `said` carries the tone they actually intended
and the misconception is nameable — "你 is tone 3, you wrote tone 2" rather than
"that syllable scored 42".

Tone digits are optional (a beginner chatting shouldn't have to mark every
syllable), so this module is deliberately conservative: it produces errors only
when the input is *fully* marked, and reports nothing otherwise. See
`typed_tones` for why partial marking can't be aligned without guessing.
"""
import re
from typing import List, Optional

from backend.models import ToneError
from backend.pinyin import HANZI, tone_numbers

#: A typed syllable: letters (with `v` standing in for ü) then an optional tone
#: digit. Apostrophes, spaces and punctuation fall outside and act as separators.
_SYLLABLE = re.compile(r"[a-z]+[0-9]?")

_VALID_TONES = {1, 2, 3, 4, 5}

#: The neutral tone. Never flagged in either direction — `expected` comes from
#: pypinyin, which disagrees with the KB's curated readings exactly here: it calls
#: 谢谢 `xie4xie4` where the KB (and every textbook) teaches `xièxie`, neutral on
#: the second syllable. A learner typing what they were taught would be told they
#: were wrong. Neutral-tone reduction is unstable enough across sources that
#: silence is the only honest answer.
_NEUTRAL = 5


def split_typed_syllables(text: str) -> List[str]:
    """Split typed pinyin into syllables, using tone digits as terminators.

    This is what makes text mode tractable without a pinyin dictionary: `ni3hao3`
    splits on the digits alone. An unmarked run (`nihao`) has no boundary to find,
    so it comes back as a single token — callers must treat that as unsegmented,
    not as one syllable.
    """
    return _SYLLABLE.findall(text.lower())


def typed_tones(text: str) -> Optional[List[int]]:
    """The tones the learner typed, or `None` when the input isn't fully marked.

    `None` is the "no tone signal" answer, and it covers the partial case on
    purpose. A toneless run can't be segmented without a dictionary — is `haoma`
    one syllable or two? — so a partly-marked string has an unknown syllable count
    and any alignment against the reading would be a guess. Since marking tones is
    optional, staying silent beats inventing an error and teaching a beginner
    something false.
    """
    syllables = split_typed_syllables(text)
    if not syllables:
        return None

    tones: List[int] = []
    for syllable in syllables:
        if not syllable[-1].isdigit():
            return None
        tone = int(syllable[-1])
        if tone not in _VALID_TONES:
            return None
        tones.append(tone)
    return tones


def tone_errors_from_typed(typed: str, reading_zh: str) -> List[ToneError]:
    """Compare the typed tones against the worker's reading of the same turn.

    `reading_zh` is the 汉字 the conversation worker understood from the learner's
    pinyin; its correct tones come from `pinyin.tone_numbers`. Each hanzi is one
    syllable, so once punctuation is stripped the two sequences align one-to-one —
    and if they don't (the worker read a different number of syllables than were
    typed), we report nothing rather than misalign.
    """
    said_tones = typed_tones(typed)
    if said_tones is None:
        return []

    chars = HANZI.findall(reading_zh)
    expected_tones = tone_numbers("".join(chars))
    if not chars or not (len(chars) == len(expected_tones) == len(said_tones)):
        return []

    return [
        ToneError(syllable=char, expected=expected, said=said, index=i)
        for i, (char, expected, said) in enumerate(
            zip(chars, expected_tones, said_tones)
        )
        if expected != said and _NEUTRAL not in (expected, said)
    ]
