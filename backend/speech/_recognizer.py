"""Shared Azure recognizer construction for the speech boundary modules.

`stt` and `pronunciation` each run one `recognize_once` over a client-encoded
WAV blob; the only difference is `pronunciation` applies a PA config to the
recognizer first. This centralizes the identical setup — `SpeechConfig` from
`config`, the temp-file `AudioConfig`, the `SpeechRecognizer` — and the shared
cancellation-detail extraction, so neither module re-derives it.
"""
import tempfile
from contextlib import contextmanager

import azure.cognitiveservices.speech as speechsdk

from backend import config


class SpeechConfigError(RuntimeError):
    """Azure Speech credentials are missing/empty.

    Constructing `SpeechConfig` with an empty key raises a bare `RuntimeError: 5`
    (Azure's `SPXERR_INVALID_ARG`) deep in the SDK. We check first and raise this
    instead so the boundary fails clean — typically when the server was started
    before `.env` had the key (restart to reload it)."""


@contextmanager
def recognizer_for(audio_wav: bytes, language: str):
    """Yield a `SpeechRecognizer` over ``audio_wav``, valid for the `with` block.

    The WAV is written to a temp file read via ``AudioConfig(filename=...)`` so
    the SDK parses the RIFF/WAV header itself (more robust than hand-feeding a
    raw PCM push stream). The temp file lives until the block exits, so callers
    must run ``recognize_once`` *inside* the `with`.
    """
    if not config.AZURE_SPEECH_KEY or not config.AZURE_SPEECH_REGION:
        raise SpeechConfigError(
            "Azure Speech credentials not configured — set AZURE_SPEECH_KEY and "
            "AZURE_SPEECH_REGION in .env and restart the server so it reloads them."
        )
    speech_config = speechsdk.SpeechConfig(
        subscription=config.AZURE_SPEECH_KEY,
        region=config.AZURE_SPEECH_REGION,
    )
    speech_config.speech_recognition_language = language

    with tempfile.NamedTemporaryFile(suffix=".wav") as tmp:
        tmp.write(audio_wav)
        tmp.flush()
        audio_config = speechsdk.audio.AudioConfig(filename=tmp.name)
        yield speechsdk.SpeechRecognizer(
            speech_config=speech_config, audio_config=audio_config
        )


def cancellation_message(result) -> str:
    """Format a canceled result's reason + details for an error message."""
    details = speechsdk.CancellationDetails(result)
    return f"({details.reason}): {details.error_details}"
