"""Azure Speech-to-Text boundary.

Takes a WAV byte blob (16 kHz mono PCM, encoded client-side) and returns the
recognized transcript. This is the first pass of the two-pass speech flow: the
transcript it returns becomes the reference text for `pronunciation.assess`.
"""
import asyncio

from backend import config
from backend.speech._azure import (
    RecognitionTimeout,
    cancellation_message,
    recognize_continuous,
    recognizer_for,
)


class SttError(RuntimeError):
    """Azure recognition failed (canceled or an unexpected result reason)."""


def _join(segments, language: str) -> str:
    """Join recognized segments back into the one utterance they came from.

    Chinese is written without spaces, so its segments abut. Everything else
    needs a separator or the join glues two words into one.
    """
    texts = [s for s in (seg.text.strip() for seg in segments) if s]
    separator = "" if language.startswith("zh") else " "
    return separator.join(texts)


def _recognize_sync(audio_wav: bytes, language: str) -> str:
    """Blocking recognition: read the whole WAV and return the joined transcript.

    Every segment belongs to the same push-to-talk utterance — the learner held
    the button through their own pauses — so every segment belongs in the
    transcript.
    """
    with recognizer_for(audio_wav, language) as recognizer:
        segments, canceled = recognize_continuous(
            recognizer, config.STT_TIMEOUT_S
        )

    if canceled is not None:
        # Canceled (bad key/region, network, malformed audio). Raise even when
        # some segments arrived first: half a sentence handed on as if it were
        # the whole one earns the learner a confident answer to something they
        # did not say, which is worse than a failure they can retry.
        raise SttError(f"Azure STT canceled {cancellation_message(canceled)}")

    # No segments means the audio was processed but nothing intelligible was
    # found — the same "" that a NoMatch result used to produce.
    return _join(segments, language)


async def transcribe(audio_wav: bytes, language: str = "zh-CN") -> str:
    """Recognize Mandarin speech in ``audio_wav`` and return the transcript.

    Recognition is blocking, so it runs in a worker thread to stay async-correct
    inside the FastAPI event loop.

    A stall here is the worst kind: STT sits in front of everything, before the
    response has committed to a status, so an unbounded wait holds the request
    open without even a transcript to show for it. `STT_TIMEOUT_S` bounds it and
    the timeout surfaces as `SttError`, which the route already maps to 502.

    Caveat worth knowing: `wait_for` cancels the *await*, not the thread — a
    Python thread can't be interrupted, so a wedged SDK call keeps a thread-pool
    slot until Azure returns or the process exits. This frees the request and
    the connection, which is the part that was unbounded; it is not a way to
    reclaim the thread.
    """
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_recognize_sync, audio_wav, language),
            timeout=config.STT_TIMEOUT_S,
        )
    except (asyncio.TimeoutError, RecognitionTimeout) as exc:
        # Two deadlines, one message: `wait_for` frees the request, and the
        # thread-side bound in `recognize_continuous` frees the thread. Whichever
        # fires first, the learner's turn failed the same way.
        raise SttError(
            f"Azure STT timed out after {config.STT_TIMEOUT_S:g}s"
        ) from exc
    except RuntimeError as exc:
        # The SDK raises out of *recognizer construction* for a body that isn't
        # a WAV (`SPXERR_INVALID_HEADER`) — before any result exists, so the
        # `ResultReason` handling below never sees it. A client can upload
        # anything, so unwrapped this is a 500 on bad input rather than the 502
        # the route maps `SttError` to.
        raise SttError(f"Azure STT could not read the audio: {exc}") from exc
