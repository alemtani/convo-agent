"""Azure Speech-to-Text boundary.

Takes a WAV byte blob (16 kHz mono PCM, encoded client-side) and returns the
recognized transcript. This is the first pass of the two-pass speech flow: the
transcript it returns becomes the reference text for `pronunciation.assess`.
"""
import asyncio

import azure.cognitiveservices.speech as speechsdk

from backend.speech._recognizer import cancellation_message, recognizer_for


class SttError(RuntimeError):
    """Azure recognition failed (canceled or an unexpected result reason)."""


def _recognize_sync(audio_wav: bytes, language: str) -> str:
    """Blocking recognition: run one pass over the WAV and return its text."""
    with recognizer_for(audio_wav, language) as recognizer:
        result = recognizer.recognize_once()

    reason = result.reason
    if reason == speechsdk.ResultReason.RecognizedSpeech:
        return result.text
    if reason == speechsdk.ResultReason.NoMatch:
        # Audio was processed but nothing intelligible was found.
        return ""

    # Canceled (bad key/region, network, malformed audio) or anything else.
    raise SttError(f"Azure STT canceled {cancellation_message(result)}")


async def transcribe(audio_wav: bytes, language: str = "zh-CN") -> str:
    """Recognize Mandarin speech in ``audio_wav`` and return the transcript.

    ``recognize_once`` is blocking, so it runs in a worker thread to stay
    async-correct inside the FastAPI event loop.
    """
    return await asyncio.to_thread(_recognize_sync, audio_wav, language)
