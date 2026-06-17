"""Azure Speech-to-Text boundary.

The only module that imports the Azure Speech SDK. Takes a WAV byte blob
(16 kHz mono PCM, encoded client-side) and returns the recognized transcript.

Phase 1 scope: speech-to-text only. Pronunciation assessment (tone scores) is
Phase 2 and will run in parallel with this call.
"""
import asyncio
import tempfile

import azure.cognitiveservices.speech as speechsdk

from backend import config


class SttError(RuntimeError):
    """Azure recognition failed (canceled or an unexpected result reason)."""


def _recognize_sync(audio_wav: bytes, language: str) -> str:
    """Blocking recognition: write the WAV to a temp file and run one pass.

    Using a temp file + ``AudioConfig(filename=...)`` lets the SDK parse the
    RIFF/WAV header itself — more robust than hand-feeding a raw PCM push stream.
    """
    speech_config = speechsdk.SpeechConfig(
        subscription=config.AZURE_SPEECH_KEY,
        region=config.AZURE_SPEECH_REGION,
    )
    speech_config.speech_recognition_language = language

    with tempfile.NamedTemporaryFile(suffix=".wav") as tmp:
        tmp.write(audio_wav)
        tmp.flush()
        audio_config = speechsdk.audio.AudioConfig(filename=tmp.name)
        recognizer = speechsdk.SpeechRecognizer(
            speech_config=speech_config, audio_config=audio_config
        )
        result = recognizer.recognize_once()

    reason = result.reason
    if reason == speechsdk.ResultReason.RecognizedSpeech:
        return result.text
    if reason == speechsdk.ResultReason.NoMatch:
        # Audio was processed but nothing intelligible was found.
        return ""

    # Canceled (bad key/region, network, malformed audio) or anything else.
    details = speechsdk.CancellationDetails.from_result(result)
    raise SttError(
        f"Azure STT canceled ({details.reason}): {details.error_details}"
    )


async def transcribe(audio_wav: bytes, language: str = "zh-CN") -> str:
    """Recognize Mandarin speech in ``audio_wav`` and return the transcript.

    ``recognize_once`` is blocking, so it runs in a worker thread to stay
    async-correct inside the FastAPI event loop.
    """
    return await asyncio.to_thread(_recognize_sync, audio_wav, language)
