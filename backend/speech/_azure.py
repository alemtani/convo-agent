"""Shared Azure Speech setup for the boundary modules.

Two layers, because the three callers need different amounts of it.

`speech_config` is the credentialed `SpeechConfig` every module starts from —
including `tts`, which synthesizes and so has no recognizer and no input audio
at all. It owns the credential guard: one check, in the place a new boundary
cannot avoid calling.

`recognizer_for` builds on it for the two modules that *recognize*: `stt` and
`pronunciation` each run `recognize_continuous` over a client-encoded WAV blob,
differing only in that `pronunciation` applies a PA config first.
"""
import tempfile
import threading
from contextlib import contextmanager

import azure.cognitiveservices.speech as speechsdk

from backend import config

# How long a pause may last before Azure calls the utterance finished and starts
# a new segment. Azure's default is ~500 ms, which is shorter than a beginner's
# thinking pause — so one sentence arrived as several, each decoded and
# punctuated in isolation. `recognize_continuous` keeps every segment either way;
# widening the window is what keeps ordinary hesitation inside *one* of them, so
# Azure gets the whole phrase as context. Azure accepts 100–5000 ms.
SEGMENTATION_SILENCE_MS = 2000


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


class RecognitionTimeout(RuntimeError):
    """A recognition session never reached end-of-stream in time."""


@contextmanager
def recognizer_for(audio_wav: bytes, language: str):
    """Yield a `SpeechRecognizer` over ``audio_wav``, valid for the `with` block.

    The WAV is written to a temp file read via ``AudioConfig(filename=...)`` so
    the SDK parses the RIFF/WAV header itself (more robust than hand-feeding a
    raw PCM push stream). The temp file lives until the block exits, so callers
    must run ``recognize_continuous`` *inside* the `with` — and must not leave
    the block until it returns, which it does only once Azure has stopped.
    """
    cfg = speech_config()
    cfg.speech_recognition_language = language
    cfg.set_property(
        speechsdk.PropertyId.Speech_SegmentationSilenceTimeoutMs,
        str(SEGMENTATION_SILENCE_MS),
    )

    with tempfile.NamedTemporaryFile(suffix=".wav") as tmp:
        tmp.write(audio_wav)
        tmp.flush()
        audio_config = speechsdk.audio.AudioConfig(filename=tmp.name)
        yield speechsdk.SpeechRecognizer(
            speech_config=cfg, audio_config=audio_config
        )


def recognize_continuous(recognizer, timeout_s: float):
    """Recognize the whole audio stream; return ``(results, canceled)``.

    ``results`` holds every `RecognizedSpeech` result in the order Azure produced
    it; ``canceled`` is the canceled result if the session failed, else `None`.

    Why continuous rather than `recognize_once`: `recognize_once` returns the
    *first* utterance and stops at the first end-of-speech silence, so a learner
    who pauses to assemble a sentence had everything after the pause uploaded,
    paid for, and discarded. Push-to-talk already knows where the utterance ends
    — it is the end of the blob — so there is nothing to infer from silence.
    Raising the silence timeout alone would only move the cliff; a learner who
    thinks for six seconds would still be cut off.

    Every file session ends with a `canceled(EndOfStream)`, which is the normal
    finish, not a failure — only `CancellationReason.Error` is reported back.

    Blocking by design. The SDK delivers events on its own thread and this waits
    for `session_stopped`, so the caller stays inside `recognizer_for`'s block
    and the temp WAV outlives the read. ``timeout_s`` bounds that wait: the
    request's own `wait_for` cancels an *await*, never a thread, so without a
    deadline here a wedged session would hold a thread-pool slot for good.
    """
    results = []
    canceled = []
    finished = threading.Event()

    def on_recognized(evt):
        if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech:
            results.append(evt.result)

    def on_canceled(evt):
        if evt.reason != speechsdk.CancellationReason.EndOfStream:
            canceled.append(evt.result)
            # A failed session may never report `session_stopped`; don't wait.
            finished.set()

    def on_stopped(evt):
        finished.set()

    recognizer.recognized.connect(on_recognized)
    recognizer.canceled.connect(on_canceled)
    recognizer.session_stopped.connect(on_stopped)

    recognizer.start_continuous_recognition()
    try:
        if not finished.wait(timeout_s):
            raise RecognitionTimeout(
                f"recognition did not reach end of stream in {timeout_s:g}s"
            )
    finally:
        # Always stop: a session left running holds an Azure connection open.
        recognizer.stop_continuous_recognition()

    return results, (canceled[0] if canceled else None)


def cancellation_message(result) -> str:
    """Format a canceled result's reason + details for an error message."""
    details = speechsdk.CancellationDetails(result)
    return f"({details.reason}): {details.error_details}"
