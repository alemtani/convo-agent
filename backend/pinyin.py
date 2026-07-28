"""Romanize recognized Mandarin into tone-marked pinyin for display.

The learner reads pinyin alongside characters (the partner reply carries it too),
so the user's own transcribed speech is shown with a pinyin line as well. Unlike
the partner reply — whose pinyin is co-authored with the characters — this is
*machine-derived* from arbitrary STT output via pypinyin, so heteronyms may
occasionally slip (好 hǎo/hào). Good enough for an input echo.

pypinyin is run on the whole string so its 不/一 tone sandhi has context.
"""
import re
from typing import List

from pypinyin import Style, pinyin as _pinyin

#: Han characters — CJK Unified Ideographs plus Extension A. Used to tell 汉字
#: from romanization, and to count syllables in a reading (one hanzi = one).
HANZI = re.compile(r"[㐀-䶿一-鿿]")


def to_pinyin(zh: str) -> str:
    """Tone-marked, space-separated pinyin for `zh`; "" for empty/no speech."""
    if not zh:
        return ""
    syllables = (s[0] for s in _pinyin(zh, style=Style.TONE))
    # pypinyin emits punctuation as its own token — keep only romanized syllables.
    return " ".join(s for s in syllables if any(c.isalpha() for c in s))


def tone_numbers(zh: str) -> List[int]:
    """Per-syllable tone numbers for `zh` (1–4; neutral → 5); [] for empty input.

    The source of the *expected* tone in tone-error detection. `Style.TONE3`
    appends the tone digit (`hao3`); an unmarked syllable is the neutral tone,
    which we normalize to 5. Punctuation tokens (no romanized letters) are
    dropped so the result aligns one-to-one with the spoken syllables.
    """
    out: List[int] = []
    for syllable in (s[0] for s in _pinyin(zh, style=Style.TONE3)):
        if not any(c.isalpha() for c in syllable):
            continue
        out.append(int(syllable[-1]) if syllable[-1].isdigit() else 5)
    return out
