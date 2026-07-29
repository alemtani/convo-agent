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

#: Han characters — used to tell 汉字 from romanization, and to count syllables in
#: a reading (one hanzi = one). Covers the Basic Block and Extension A (where all
#: of HSK lives), plus Extension B and the Compatibility Ideographs so a rare
#: character in a worker reading isn't silently dropped. Python's `re` has no
#: `\p{Han}`, so the ranges are explicit; anything outside them degrades safely —
#: the syllable count stops matching and tone analysis reports nothing, rather
#: than misaligning.
HANZI = re.compile(
    "["
    "一-鿿"          # CJK Unified Ideographs (the Basic Block)
    "㐀-䶿"          # Extension A
    "豈-﫿"          # Compatibility Ideographs
    "\U00020000-\U0002a6df"  # Extension B
    "]"
)


def to_pinyin(zh: str) -> str:
    """Tone-marked, space-separated pinyin for `zh`; "" for empty/no speech."""
    if not zh:
        return ""
    syllables = (s[0] for s in _pinyin(zh, style=Style.TONE))
    # pypinyin emits punctuation as its own token — keep only romanized syllables.
    return " ".join(s for s in syllables if any(c.isalpha() for c in s))


def toneless_syllables(zh: str) -> List[str]:
    """Per-character toneless pinyin for `zh` (`上海` → `["shang", "hai"]`).

    The ASCII form a learner actually types — pypinyin's `NORMAL` style even uses
    `v` for ü (`绿` → `lv`), matching the usual IME convention. Pairing this with
    the hanzi the worker read is what lets typed input be aligned without a pinyin
    dictionary: we always know the syllable sequence to expect.
    """
    return [s[0] for s in _pinyin(zh, style=Style.NORMAL)]


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
