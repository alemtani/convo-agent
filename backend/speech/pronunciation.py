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
from backend.speech._azure import (
    RecognitionTimeout,
    cancellation_message,
    recognize_continuous,
    recognizer_for,
)


class PaError(RuntimeError):
    """Azure pronunciation assessment failed (canceled or unexpected reason)."""


def _syllables_of(assessment) -> list:
    """Walk one assessed segment's words → syllables into `SyllableScore`s.

    Key each scored chunk by its hanzi grapheme. When a word carries no syllable
    breakdown, score the whole word as one chunk so a grapheme always surfaces.

    Every read here goes through `getattr`, because the SDK's result classes
    assign `_words`, `_syllables` and `_accuracy_score` **only** when Azure's
    JSON carried the matching key, and each property returns its private
    attribute bare. An absent key therefore raises `AttributeError` instead of
    reading back as `None` — which took down a live turn after the response had
    already committed to 200.

    A word Azure never scored is dropped rather than surfaced at zero: no
    assessment block means nobody judged it, and a zero would tell the learner
    they mispronounced a word that was never listened to.
    """
    syllables = []
    for word in getattr(assessment, "words", None) or []:
        word_syllables = getattr(word, "syllables", None) or []
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
            accuracy = getattr(word, "accuracy_score", None)
            if accuracy is None:
                continue
            syllables.append(
                SyllableScore(
                    hanzi=word.word,
                    pinyin=to_pinyin(word.word),
                    accuracy=accuracy,
                )
            )
    return syllables


def _to_score(segments) -> Optional[PronunciationScore]:
    """Merge every assessed segment into one score for the whole utterance.

    Azure assesses each segment separately, so a learner who pauses mid-sentence
    gets several partial results. They are one utterance to the learner and the
    frontend aligns these syllables onto the whole transcript, so they merge in
    spoken order.

    ``overall`` is the segments' accuracy weighted by how many syllables each
    covers. A flat mean would let a one-syllable afterthought count for as much
    as the sentence before it.
    """
    per_segment = []
    for result in segments:
        assessment = speechsdk.PronunciationAssessmentResult(result)
        syllables = _syllables_of(assessment)
        accuracy = getattr(assessment, "accuracy_score", None)
        if syllables and accuracy is not None:
            per_segment.append((accuracy, syllables))

    if not per_segment:
        return None

    merged = [syl for _, syllables in per_segment for syl in syllables]
    total = sum(
        accuracy * len(syllables) for accuracy, syllables in per_segment
    )
    return PronunciationScore(overall=total / len(merged), syllables=merged)


def _assess_sync(
    audio_wav: bytes, reference_text: str, language: str
) -> Optional[PronunciationScore]:
    """Blocking pronunciation assessment of ``audio_wav`` against a reference.

    Shares the recognizer construction and the continuous-recognition loop with
    `stt` via `_azure`; the only PA-specific step is applying the assessment
    config before recognition.

    Continuous recognition here is not optional polish — it has to match `stt`.
    `stt`'s transcript is this call's reference text, so if PA still stopped at
    the learner's first pause it would score only the phrase before it while the
    transcript carried the whole sentence, and the frontend would render the rest
    permanently unscored.
    """
    pa_config = speechsdk.PronunciationAssessmentConfig(
        reference_text=reference_text,
        grading_system=speechsdk.PronunciationAssessmentGradingSystem.HundredMark,
        granularity=speechsdk.PronunciationAssessmentGranularity.Phoneme,
    )

    with recognizer_for(audio_wav, language) as recognizer:
        pa_config.apply_to(recognizer)
        segments, canceled = recognize_continuous(
            recognizer, config.PA_TIMEOUT_S
        )

    if canceled is not None:
        raise PaError(f"Azure PA canceled {cancellation_message(canceled)}")

    # Nothing scorable in the audio — degrade to no scores rather than fail.
    return _to_score(segments)


async def assess(
    audio_wav: bytes, reference_text: str, language: str = "zh-CN"
) -> Optional[PronunciationScore]:
    """Assess ``audio_wav`` against ``reference_text`` and return tone scores.

    Blocking recognition runs in a worker thread to stay async-correct,
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
    except (asyncio.TimeoutError, RecognitionTimeout) as exc:
        # Two deadlines, one message — see `stt.transcribe`. One frees the
        # streamed response, the other frees the recognition thread.
        raise PaError(
            f"Azure PA timed out after {config.PA_TIMEOUT_S:g}s"
        ) from exc
