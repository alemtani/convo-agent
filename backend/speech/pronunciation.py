"""Azure Pronunciation Assessment boundary (Phase 2).

The second module that imports the Azure Speech SDK (alongside `stt.py`). It is
the *second pass* of a two-pass flow: `stt.transcribe` recognizes the speech,
then this assesses the same audio **against that transcript** as the reference
text, yielding a per-syllable accuracy breakdown (which, for Mandarin, folds in
tone correctness). Running PA against the STT transcript sidesteps PA's
known-reference requirement — see DESIGN.md Risk 1.

Azure assesses zh-CN at the grapheme level: it leaves the romanized `syllable`
field empty and reports the scored hanzi in `grapheme`, so we key scores by
hanzi and derive each chunk's pinyin locally via `pinyin.to_pinyin` for display.
"""
import asyncio
from typing import Optional

import azure.cognitiveservices.speech as speechsdk

from backend import config
from backend.models import PronunciationScore, SyllableScore
from backend.pinyin import to_pinyin
from backend.speech._azure import cancellation_message, recognizer_for


class PaError(RuntimeError):
    """Azure pronunciation assessment failed (canceled or unexpected reason)."""


def _to_score(result) -> PronunciationScore:
    """Map Azure's assessment result onto our `PronunciationScore`.

    Walk words → syllables, keying each scored chunk by its hanzi grapheme. When
    a word carries no syllable breakdown, score the whole word as one chunk so a
    grapheme always surfaces.
    """
    assessment = speechsdk.PronunciationAssessmentResult(result)
    syllables = []
    for word in assessment.words:
        word_syllables = word.syllables or []
        if word_syllables:
            for syl in word_syllables:
                hanzi = syl.grapheme or syl.syllable
                syllables.append(
                    SyllableScore(
                        hanzi=hanzi,
                        pinyin=to_pinyin(hanzi),
                        accuracy=syl.accuracy_score,
                    )
                )
        else:
            syllables.append(
                SyllableScore(
                    hanzi=word.word,
                    pinyin=to_pinyin(word.word),
                    accuracy=word.accuracy_score,
                )
            )
    return PronunciationScore(
        overall=assessment.accuracy_score, syllables=syllables
    )


def _assess_sync(
    audio_wav: bytes, reference_text: str, language: str
) -> Optional[PronunciationScore]:
    """Blocking pronunciation assessment of ``audio_wav`` against a reference.

    Shares the recognizer construction with `stt` via `recognizer_for`; the only
    PA-specific step is applying the assessment config before recognition.
    """
    pa_config = speechsdk.PronunciationAssessmentConfig(
        reference_text=reference_text,
        grading_system=speechsdk.PronunciationAssessmentGradingSystem.HundredMark,
        granularity=speechsdk.PronunciationAssessmentGranularity.Phoneme,
    )

    with recognizer_for(audio_wav, language) as recognizer:
        pa_config.apply_to(recognizer)
        result = recognizer.recognize_once()

    reason = result.reason
    if reason == speechsdk.ResultReason.RecognizedSpeech:
        return _to_score(result)
    if reason == speechsdk.ResultReason.NoMatch:
        # Audio processed but nothing scorable — degrade to no scores.
        return None

    raise PaError(f"Azure PA canceled {cancellation_message(result)}")


async def assess(
    audio_wav: bytes, reference_text: str, language: str = "zh-CN"
) -> Optional[PronunciationScore]:
    """Assess ``audio_wav`` against ``reference_text`` and return tone scores.

    Blocking ``recognize_once`` runs in a worker thread to stay async-correct,
    mirroring `stt.transcribe`.

    Bounded by `PA_TIMEOUT_S`. A wedged PA call is the one most likely to go
    unnoticed: it is the *faster* branch, so nothing else is waiting on it, and
    without a deadline it would hold the streamed response open past a reply the
    learner already has on screen. The timeout raises `PaError`, which
    `_assess_or_degrade` already turns into `pronunciation: null` — the turn
    completes and says "not scored" rather than stalling.

    Same caveat as `stt.transcribe`: this cancels the await, not the thread.
    """
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_assess_sync, audio_wav, reference_text, language),
            timeout=config.PA_TIMEOUT_S,
        )
    except asyncio.TimeoutError as exc:
        raise PaError(
            f"Azure PA timed out after {config.PA_TIMEOUT_S:g}s"
        ) from exc
