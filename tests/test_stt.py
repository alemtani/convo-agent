"""Azure STT boundary — contract tests with the SDK fully mocked.

We assert that we *parse* each SDK outcome correctly (segments / nothing /
canceled). The shared recognizer construction and the continuous-recognition
plumbing are covered in `test_azure.py`; here we only check what `stt` adds on
top — joining the segments into one transcript. We never hit Azure and never
assert real recognition text (that's a manual/live check).
"""
import io
import types
import wave

import pytest

from backend import config
from backend.speech import _azure, stt
from tests.fakes_speech import canceled_event, make_recognizer_class, recognized_event


def _make_fake_speechsdk(recognizer_class, recorder):
    """Build a stand-in for the `speechsdk` module around ``recognizer_class``."""

    class FakeSpeechConfig:
        def __init__(self, subscription, region):
            self.subscription = subscription
            self.region = region
            self.speech_recognition_language = None
            self.properties = {}

        def set_property(self, property_id, value):
            self.properties[property_id] = value

    class FakeAudioConfig:
        def __init__(self, filename):
            self.filename = filename

    def cancellation(r):  # SDK 1.42.0: constructor, not `.from_result`
        return types.SimpleNamespace(
            reason="Error", error_details=getattr(r, "error_details", "")
        )

    return types.SimpleNamespace(
        SpeechConfig=FakeSpeechConfig,
        audio=types.SimpleNamespace(AudioConfig=FakeAudioConfig),
        SpeechRecognizer=recognizer_class,
        ResultReason=types.SimpleNamespace(
            RecognizedSpeech="RecognizedSpeech", NoMatch="NoMatch", Canceled="Canceled"
        ),
        CancellationReason=types.SimpleNamespace(
            EndOfStream="EndOfStream", Error="Error"
        ),
        PropertyId=types.SimpleNamespace(
            Speech_SegmentationSilenceTimeoutMs="SegmentationSilenceTimeoutMs"
        ),
        CancellationDetails=cancellation,
    )


def _speech(text):
    return types.SimpleNamespace(reason="RecognizedSpeech", text=text)


@pytest.fixture
def patched(monkeypatch):
    """Install fake credentials; return a helper that wires a fake SDK + events.

    The same fake serves both `stt` (result dispatch) and `_azure` (the shared
    construction and the continuous-recognition loop `stt` delegates to).
    """
    monkeypatch.setattr(_azure.config, "AZURE_SPEECH_KEY", "test-key")
    monkeypatch.setattr(_azure.config, "AZURE_SPEECH_REGION", "test-region")

    def install(*texts, canceled=None):
        events = [("recognized", recognized_event(_speech(t))) for t in texts]
        if canceled is None:
            events.append(("canceled", canceled_event("EndOfStream")))
        else:
            events.append(("canceled", canceled_event("Error", result=canceled)))
        events.append(("session_stopped", None))

        recorder = {}
        fake = _make_fake_speechsdk(
            make_recognizer_class(events, recorder), recorder
        )
        monkeypatch.setattr(_azure, "speechsdk", fake)
        return recorder

    return install


async def test_recognized_speech_returns_text(patched):
    recorder = patched("你好老师")

    out = await stt.transcribe(b"FAKEWAV")

    assert out == "你好老师"
    # transcribe forwards its default language through to the recognizer.
    assert recorder["speech_config"].speech_recognition_language == "zh-CN"


async def test_speech_after_a_pause_is_kept(patched):
    """The bug this module exists to prevent.

    A beginner assembles a sentence out loud, stops to think, then finishes.
    `recognize_once` returned the first phrase and discarded the rest — the
    learner saw half of what they said and Claude was answering half a sentence.
    Every segment of the blob belongs to the same push-to-talk utterance, so
    every segment belongs in the transcript, in the order it was spoken.
    """
    patched("我想要", "一杯咖啡")

    assert await stt.transcribe(b"FAKEWAV") == "我想要一杯咖啡"


async def test_segments_are_joined_by_a_space_for_spaced_languages(patched):
    """Chinese runs together; English would be glued into one word without this."""
    patched("I would like", "a coffee.")

    assert (
        await stt.transcribe(b"FAKEWAV", language="en-US") == "I would like a coffee."
    )


async def test_language_is_overridable(patched):
    recorder = patched("hi")

    out = await stt.transcribe(b"FAKEWAV", language="en-US")

    assert out == "hi"
    assert recorder["speech_config"].speech_recognition_language == "en-US"


async def test_no_match_returns_empty_string(patched):
    """Audio was processed, nothing intelligible came back — not an error."""
    patched()

    assert await stt.transcribe(b"FAKEWAV") == ""


async def test_canceled_raises_stt_error(patched):
    patched(canceled=types.SimpleNamespace(error_details="bad key"))

    with pytest.raises(stt.SttError, match="bad key"):
        await stt.transcribe(b"FAKEWAV")


async def test_a_late_cancellation_fails_the_turn_rather_than_half_answering(patched):
    """If Azure fails mid-blob we still failed the turn — say so, don't half-answer.

    Half a sentence handed to Claude as if it were the whole one is worse than a
    502 the client can retry: the learner gets a confident reply to something
    they did not say.
    """
    patched("我想要", canceled=types.SimpleNamespace(error_details="network"))

    with pytest.raises(stt.SttError, match="network"):
        await stt.transcribe(b"FAKEWAV")


async def test_a_stalled_recognizer_times_out_as_an_stt_error(monkeypatch):
    """STT sits in front of everything, before the response picks a status.

    Unbounded, a wedged Azure call holds the request open with nothing to show
    for it — no transcript, no reply, no status line spent. The deadline turns
    that into the failure the route already maps to 502.
    """
    import time

    def never_returns(audio_wav, language):
        time.sleep(30)
        raise AssertionError("the timeout should have fired long before this")

    monkeypatch.setattr(stt, "_recognize_sync", never_returns)
    monkeypatch.setattr(stt.config, "STT_TIMEOUT_S", 0.05)

    with pytest.raises(stt.SttError, match="timed out"):
        await stt.transcribe(b"FAKEWAV")


async def test_a_session_that_never_stops_times_out_as_an_stt_error(monkeypatch):
    """The inner, thread-side deadline — the one continuous recognition added.

    `wait_for` cancels the await, not the thread, so without a bound of its own
    the recognition thread would sit on the session event forever after the
    request had already given up.
    """
    monkeypatch.setattr(_azure.config, "AZURE_SPEECH_KEY", "test-key")
    monkeypatch.setattr(_azure.config, "AZURE_SPEECH_REGION", "test-region")
    recorder = {}
    fake = _make_fake_speechsdk(
        make_recognizer_class([], recorder, silent=True), recorder
    )
    monkeypatch.setattr(_azure, "speechsdk", fake)
    monkeypatch.setattr(stt.config, "STT_TIMEOUT_S", 0.05)

    with pytest.raises(stt.SttError, match="timed out"):
        await stt.transcribe(b"FAKEWAV")


async def test_malformed_audio_becomes_an_stt_error(monkeypatch):
    """A client can upload anything. Azure raises from the SDK, not our code.

    Building the recognizer over a non-WAV body raises `RuntimeError`
    (`SPXERR_INVALID_HEADER`) *before* any result exists, so it never reached
    the result handling below — it escaped `transcribe` unwrapped and the route
    returned 500 instead of the 502 it maps `SttError` to.
    """
    def boom(*args, **kwargs):
        raise RuntimeError("Exception with an error code: 0xa (SPXERR_INVALID_HEADER)")

    monkeypatch.setattr(stt, "recognizer_for", boom)

    with pytest.raises(stt.SttError, match="SPXERR_INVALID_HEADER"):
        await stt.transcribe(b"not-a-wav")


def _spliced_halves(first: str, second: str, gap_s: float) -> bytes:
    """Synthesize two half-sentences with ``gap_s`` of silence between them.

    One blob, the way push-to-talk uploads it: a learner who started a sentence,
    stopped to think, and finished. Built rather than checked in so the gap is a
    parameter — the whole point is which pause lengths survive.
    """
    import tempfile
    import wave

    import azure.cognitiveservices.speech as speechsdk

    from backend import config

    def synth(text):
        with tempfile.NamedTemporaryFile(suffix=".wav") as tmp:
            cfg = speechsdk.SpeechConfig(
                subscription=config.AZURE_SPEECH_KEY,
                region=config.AZURE_SPEECH_REGION,
            )
            cfg.speech_synthesis_voice_name = "zh-CN-XiaoxiaoNeural"
            # Hold the synthesizer in a local: an inline one is collected while
            # the SDK future is still resolving, which segfaults the interpreter.
            synthesizer = speechsdk.SpeechSynthesizer(
                speech_config=cfg,
                audio_config=speechsdk.audio.AudioOutputConfig(filename=tmp.name),
            )
            synthesizer.speak_text_async(text).get()
            with wave.open(tmp.name, "rb") as w:
                return w.getparams(), w.readframes(w.getnframes())

    (params, head), (_, tail) = synth(first), synth(second)
    silence = b"\x00" * (
        int(gap_s * params.framerate) * params.sampwidth * params.nchannels
    )
    with tempfile.NamedTemporaryFile(suffix=".wav") as out:
        with wave.open(out.name, "wb") as o:
            o.setnchannels(params.nchannels)
            o.setsampwidth(params.sampwidth)
            o.setframerate(params.framerate)
            o.writeframes(head + silence + tail)
        with open(out.name, "rb") as f:
            return f.read()


@pytest.mark.live
@pytest.mark.parametrize("gap_s", [1.0, 2.0])
async def test_live_speech_on_both_sides_of_a_pause_is_transcribed(gap_s):
    """The regression, against real Azure. `pytest -m live`.

    `recognize_once` returned only the first half at any gap of a second or
    more. Asserts coverage, not exact text: a distinctive character from each
    half has to reach the transcript.
    """
    from backend import config

    if not (config.AZURE_SPEECH_KEY and config.AZURE_SPEECH_REGION):
        pytest.skip("Azure Speech credentials not configured")

    wav = _spliced_halves("我想要一杯咖啡", "谢谢你老师", gap_s)

    text = await stt.transcribe(wav)

    assert "咖啡" in text, f"first half missing from {text!r}"
    assert "老师" in text, f"second half was discarded from {text!r}"


def _silent_wav(*, seconds: float, rate: int = 16000) -> bytes:
    """A WAV header with `seconds` of silence behind it — only the duration
    matters to `deadline_for`."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))
    return buf.getvalue()

# --- The deadline has to fit the recording (#63) ----------------------------


def test_the_deadline_grows_with_the_audio():
    """A flat budget fails the learner who hesitates most.

    Push-to-talk holds the button *through* a pause, so a turn spent thinking
    uploads a longer WAV — and each pause becomes its own segment to decode.
    A fixed 5s covered the fluent turns and timed out the hesitant ones, which
    is precisely backwards: the learner who needs time is the one it cut off.
    """
    short = stt.deadline_for(_silent_wav(seconds=2))
    long = stt.deadline_for(_silent_wav(seconds=20))

    assert long > short
    assert short >= config.STT_TIMEOUT_S


def test_the_deadline_is_capped():
    """Bounded above: a wedged session must not hold a request open forever,
    and no real turn approaches this."""
    assert stt.deadline_for(_silent_wav(seconds=600)) == config.STT_TIMEOUT_MAX_S


def test_unreadable_audio_falls_back_to_the_base_deadline():
    """Azure decides whether the bytes are a WAV; this must not be a second
    place that rejects them."""
    assert stt.deadline_for(b"not a wav at all") == config.STT_TIMEOUT_S
