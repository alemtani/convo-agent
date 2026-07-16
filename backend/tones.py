"""Deterministic tone-error extraction from Azure PA scores (Phase 3b).

The conversation worker is text-only and never sees audio, so tone errors are
*not* the model's guess — they are computed here from the per-syllable accuracy
Azure's Pronunciation Assessment returns. `expected` is the target tone derived
locally from the recognized hanzi (`pinyin.tone_numbers`); a syllable is flagged
only when its accuracy falls below `threshold`.

`said` (the tone the learner actually produced) is the spec's open question:
Azure's standard PA reports accuracy, not a detected tone (see DESIGN.md Risk 1 /
the Phase 3b plan). **Branch B (current):** we don't fabricate one — `said` is the
sentinel `0` ("off / not clearly produced"). **Branch A:** pass a `detected`
resolver and `said` carries the produced tone. The two branches differ only in
that one argument.
"""
from typing import Callable, List, Optional

from backend.models import PronunciationScore, ToneError
from backend.pinyin import tone_numbers

#: `said` value when no produced-tone signal is available (Branch B).
SAID_UNKNOWN = 0


def tone_errors_from_score(
    score: PronunciationScore,
    *,
    threshold: float,
    detected: Optional[Callable[[str], int]] = None,
) -> List[ToneError]:
    """Flag under-threshold syllables as tone errors with their expected tone.

    Each scored chunk may be a multi-character grapheme; a flagged chunk expands
    to one `ToneError` per character so `expected` is a single tone. `said` comes
    from `detected(hanzi)` when provided (Branch A), else `SAID_UNKNOWN`.
    """
    errors: List[ToneError] = []
    for syllable in score.syllables:
        if syllable.accuracy >= threshold:
            continue
        chars = list(syllable.hanzi)
        expected_tones = tone_numbers(syllable.hanzi)
        # Guard against a grapheme whose char/tone counts disagree (punctuation,
        # non-Han): zip truncates to the safe overlap rather than misaligning.
        for char, expected in zip(chars, expected_tones):
            errors.append(
                ToneError(
                    syllable=char,
                    expected=expected,
                    said=detected(char) if detected else SAID_UNKNOWN,
                )
            )
    return errors
