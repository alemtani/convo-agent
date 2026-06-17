"""Romanize recognized Mandarin into tone-marked pinyin for display.

The learner reads pinyin alongside characters (the partner reply carries it too),
so the user's own transcribed speech is shown with a pinyin line as well. Unlike
the partner reply — whose pinyin is co-authored with the characters — this is
*machine-derived* from arbitrary STT output via pypinyin, so heteronyms may
occasionally slip (好 hǎo/hào). Good enough for an input echo.

pypinyin is run on the whole string so its 不/一 tone sandhi has context.
"""
from pypinyin import Style, pinyin as _pinyin


def to_pinyin(zh: str) -> str:
    """Tone-marked, space-separated pinyin for `zh`; "" for empty/no speech."""
    if not zh:
        return ""
    syllables = (s[0] for s in _pinyin(zh, style=Style.TONE))
    # pypinyin emits punctuation as its own token — keep only romanized syllables.
    return " ".join(s for s in syllables if any(c.isalpha() for c in s))
