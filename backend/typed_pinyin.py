"""Tone errors from *typed* pinyin — text mode's counterpart to `tones.py`.

The spoken path can only report that a syllable was off: Azure PA returns an
accuracy number, not a produced tone, so `ToneError.said` is the sentinel
`tones.SAID_UNKNOWN`. Typing inverts that. A learner who writes `ni2hao3` has
stated their belief outright, so `said` carries the tone they actually intended
and the misconception is nameable — "你 is tone 3, you wrote tone 2" rather than
"that syllable scored 42".

**We never segment typed pinyin blindly.** The conversation worker tells us which
汉字 it read, so the expected syllable sequence is already known and alignment is
a walk down that list rather than an open-vocabulary parse. That is what makes
partial tone marking work: in `peng2you` — 朋友, whose second syllable is neutral
and which a learner would rarely mark — `peng` and `you` are matched positionally,
`peng` yields tone 2, and `you` simply carries no tone signal.
"""
from typing import List, Optional

from backend.models import ToneError
from backend.pinyin import HANZI, tone_numbers, toneless_syllables

_VALID_TONES = {1, 2, 3, 4, 5}

#: The neutral tone. Never flagged in either direction — the expected tone comes
#: from pypinyin, which disagrees with the KB's curated readings exactly here: it
#: calls 谢谢 `xie4xie4` where the KB (and every textbook) teaches `xièxie`,
#: neutral on the second syllable. A learner typing what they were taught would be
#: told they were wrong. Neutral-tone reduction is unstable enough across sources
#: that silence is the only honest answer.
_NEUTRAL = 5


def align_typed_tones(
    typed: str, expected_syllables: List[str]
) -> Optional[List[Optional[int]]]:
    """Walk `typed` against a known syllable sequence, reading off tone digits.

    Returns one entry per expected syllable — the typed tone, or `None` where the
    learner left that syllable unmarked — or `None` for the whole thing when the
    input doesn't line up (a misspelling, a different word, trailing junk). Tone
    digits are optional per syllable, which is the point: `peng2you` is a normal
    way to write 朋友 and still tells us something about `peng`.

    Separators (spaces, apostrophes, punctuation) are skipped between syllables,
    and `ü` is folded to `v` to match the IME convention pypinyin already uses.
    `u` and `v` stay distinct — 鹿 `lu` and 绿 `lv` are different syllables, so
    conflating them would invent errors.
    """
    text = typed.lower().replace("ü", "v")
    pos = 0
    tones: List[Optional[int]] = []

    for syllable in expected_syllables:
        while pos < len(text) and not text[pos].isalnum():
            pos += 1
        if not text.startswith(syllable, pos):
            return None
        pos += len(syllable)

        if pos < len(text) and text[pos].isdigit():
            tone = int(text[pos])
            if tone not in _VALID_TONES:
                return None
            tones.append(tone)
            pos += 1
        else:
            tones.append(None)

    while pos < len(text) and not text[pos].isalnum():
        pos += 1
    if pos != len(text):
        return None   # leftover input — we didn't read the same utterance

    return tones


def tone_errors_from_typed(typed: str, reading_zh: str) -> List[ToneError]:
    """Compare the typed tones against the worker's reading of the same turn.

    `reading_zh` is the 汉字 the conversation worker understood from the learner's
    pinyin; its correct tones and expected spelling both come from `pinyin`. Each
    hanzi is one syllable, so once punctuation is stripped the sequences align
    one-to-one — and where they can't, we report nothing rather than guess.
    """
    chars = HANZI.findall(reading_zh)
    if not chars:
        return []

    zh = "".join(chars)
    expected_syllables = toneless_syllables(zh)
    expected_tones = tone_numbers(zh)
    if not (len(chars) == len(expected_syllables) == len(expected_tones)):
        return []

    said_tones = align_typed_tones(typed, expected_syllables)
    if said_tones is None:
        return []

    return [
        ToneError(syllable=char, expected=expected, said=said, index=i)
        for i, (char, expected, said) in enumerate(
            zip(chars, expected_tones, said_tones)
        )
        if said is not None and said != expected and _NEUTRAL not in (expected, said)
    ]
