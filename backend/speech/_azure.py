"""Shared Azure Speech setup for the boundary modules.

Two layers, because the three callers need different amounts of it.

`speech_config` is the credentialed `SpeechConfig` every module starts from —
including `tts`, which synthesizes and so has no recognizer and no input audio
at all. It owns the credential guard: one check, in the place a new boundary
cannot avoid calling.

`recognizer_for` builds on it for the two modules that *recognize*: `stt` and
`pronunciation` each run one `recognize_once` over a client-encoded WAV blob,
differing only in that `pronunciation` applies a PA config first.
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


def speech_config():
    """Return a credentialed `SpeechConfig`, or raise if the keys are missing.

    Checked here rather than at import so a server started before `.env` had the
    key fails per call with a message that says what to do, instead of at boot
    with a stack trace — and so tests can set credentials with `monkeypatch`.
    """
    if not config.AZURE_SPEECH_KEY or not config.AZURE_SPEECH_REGION:
        raise SpeechConfigError(
            "Azure Speech credentials not configured — set AZURE_SPEECH_KEY and "
            "AZURE_SPEECH_REGION in .env and restart the server so it reloads them."
        )
    return speechsdk.SpeechConfig(
        subscription=config.AZURE_SPEECH_KEY,
        region=config.AZURE_SPEECH_REGION,
    )


@contextmanager
def recognizer_for(audio_wav: bytes, language: str):
    """Yield a `SpeechRecognizer` over ``audio_wav``, valid for the `with` block.

    The WAV is written to a temp file read via ``AudioConfig(filename=...)`` so
    the SDK parses the RIFF/WAV header itself (more robust than hand-feeding a
    raw PCM push stream). The temp file lives until the block exits, so callers
    must run ``recognize_once`` *inside* the `with`.
    """
    cfg = speech_config()
    cfg.speech_recognition_language = language

    with tempfile.NamedTemporaryFile(suffix=".wav") as tmp:
        tmp.write(audio_wav)
        tmp.flush()
        audio_config = speechsdk.audio.AudioConfig(filename=tmp.name)
        yield speechsdk.SpeechRecognizer(
            speech_config=cfg, audio_config=audio_config
        )


def cancellation_message(result) -> str:
    """Format a canceled result's reason + details for an error message."""
    details = speechsdk.CancellationDetails(result)
    return f"({details.reason}): {details.error_details}"
