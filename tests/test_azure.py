"""Shared Azure Speech setup — contract tests, SDK mocked.

Covers what the three boundary modules delegate to: the credentialed
`SpeechConfig` (`stt`, `pronunciation`, `tts`), the recognizer built from it
plus a WAV blob (`stt`, `pronunciation`), and the cancellation formatting they
all use for error messages. We assert the request we build and that the temp WAV
holds the audio for the block's lifetime; we never hit Azure.

The credential guard lives here, in one place, precisely because it is the check
each new boundary is most likely to forget: without it Azure fails an empty key
as a bare `RuntimeError: 5` from deep inside the SDK.
"""
import os
import types

import pytest

from backend.speech import _azure
from tests.fakes_speech import canceled_event, make_recognizer_class, recognized_event


def _make_fake_speechsdk(recorder, recognizer_class=None):
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

    class FakeRecognizer:
        def __init__(self, speech_config, audio_config):
            recorder["speech_config"] = speech_config
            recorder["audio_config"] = audio_config

    # Real SDK (1.42.0) exposes CancellationDetails as a *constructor* taking the
    # result — there is no `.from_result`. Mirror that here so the contract test
    # matches the live SDK rather than a wished-for API.
    class FakeCancellationDetails:
        def __init__(self, result):
            self.reason = "Error"
            self.error_details = getattr(result, "error_details", "")

    return types.SimpleNamespace(
        SpeechConfig=FakeSpeechConfig,
        audio=types.SimpleNamespace(AudioConfig=FakeAudioConfig),
        SpeechRecognizer=recognizer_class or FakeRecognizer,
        CancellationDetails=FakeCancellationDetails,
        ResultReason=types.SimpleNamespace(
            RecognizedSpeech="RecognizedSpeech", NoMatch="NoMatch"
        ),
        CancellationReason=types.SimpleNamespace(EndOfStream="EndOfStream", Error="Error"),
        PropertyId=types.SimpleNamespace(
            Speech_SegmentationSilenceTimeoutMs="SegmentationSilenceTimeoutMs"
        ),
    )


@pytest.fixture
def patched(monkeypatch):
    monkeypatch.setattr(_azure.config, "AZURE_SPEECH_KEY", "test-key")
    monkeypatch.setattr(_azure.config, "AZURE_SPEECH_REGION", "test-region")
    recorder = {}
    monkeypatch.setattr(
        _azure, "speechsdk", _make_fake_speechsdk(recorder)
    )
    return recorder


def test_recognizer_built_from_config_and_wav(patched):
    with _azure.recognizer_for(b"FAKEWAV", "zh-CN") as recognizer:
        assert recognizer is not None  # yielded inside the temp-file block
        cfg = patched["speech_config"]
        assert cfg.subscription == "test-key"
        assert cfg.region == "test-region"
        assert cfg.speech_recognition_language == "zh-CN"

        path = patched["audio_config"].filename
        assert path.endswith(".wav")
        # The WAV bytes are handed to the SDK via the temp file for the block.
        with open(path, "rb") as f:
            assert f.read() == b"FAKEWAV"

    # NamedTemporaryFile is cleaned up once the block exits.
    assert not os.path.exists(path)


def test_speech_config_carries_the_credentials(patched):
    """The piece `tts` shares with the recognizers — no audio, no language."""
    cfg = _azure.speech_config()

    assert cfg.subscription == "test-key"
    assert cfg.region == "test-region"


def test_speech_config_refuses_missing_credentials(monkeypatch):
    monkeypatch.setattr(_azure.config, "AZURE_SPEECH_KEY", "")

    with pytest.raises(_azure.SpeechConfigError, match="not configured"):
        _azure.speech_config()


def test_cancellation_message_formats_reason_and_details(patched):
    result = types.SimpleNamespace(error_details="bad key")

    assert _azure.cancellation_message(result) == "(Error): bad key"


def test_missing_credentials_raise_clean_error(monkeypatch):
    # Empty key would otherwise reach the SDK as a bare RuntimeError(5); guard it.
    monkeypatch.setattr(_azure.config, "AZURE_SPEECH_KEY", "")
    monkeypatch.setattr(_azure.config, "AZURE_SPEECH_REGION", "eastus")

    with pytest.raises(_azure.SpeechConfigError, match="not configured"):
        with _azure.recognizer_for(b"FAKEWAV", "zh-CN"):
            pass


def test_recognizer_widens_the_segmentation_silence_window(patched):
    """A beginner's mid-sentence pause must not read as end-of-utterance.

    Continuous recognition is what makes the pause survivable at all; this is
    the quality half of it. At Azure's ~500 ms default a two-second think splits
    one sentence into two segments, and each segment is punctuated and decoded on
    its own — so the learner reads `你好。你好。` where they said one thing. A wider
    window keeps ordinary hesitation inside a single segment.
    """
    with _azure.recognizer_for(b"FAKEWAV", "zh-CN"):
        props = patched["speech_config"].properties

    assert (
        props["SegmentationSilenceTimeoutMs"]
        == str(_azure.SEGMENTATION_SILENCE_MS)
    )
    assert _azure.SEGMENTATION_SILENCE_MS > 500  # wider than Azure's default


# --- continuous recognition -------------------------------------------------
#
# `recognize_once` returned only the FIRST utterance and stopped at the first
# end-of-speech silence, so everything a learner said after a pause was uploaded,
# paid for and discarded. Push-to-talk already knows where the utterance ends —
# it is the end of the blob — so we recognize the whole blob and keep every
# segment.


def _continuous(monkeypatch, events, **kwargs):
    """Install a fake SDK whose recognizer replays ``events``; return recorder."""
    monkeypatch.setattr(_azure.config, "AZURE_SPEECH_KEY", "test-key")
    monkeypatch.setattr(_azure.config, "AZURE_SPEECH_REGION", "test-region")
    recorder = {}
    recognizer_class = make_recognizer_class(events, recorder, **kwargs)
    monkeypatch.setattr(
        _azure, "speechsdk", _make_fake_speechsdk(recorder, recognizer_class)
    )
    return recorder


def _speech(text):
    return types.SimpleNamespace(reason="RecognizedSpeech", text=text)


def _ordinary_finish(*texts):
    """The event sequence a healthy file recognition produces."""
    return (
        [("recognized", recognized_event(_speech(t))) for t in texts]
        + [("canceled", canceled_event("EndOfStream"))]
        + [("session_stopped", None)]
    )


def test_every_segment_is_collected_in_order(monkeypatch):
    _continuous(monkeypatch, _ordinary_finish("你好", "老师", "再见"))

    with _azure.recognizer_for(b"FAKEWAV", "zh-CN") as recognizer:
        results, canceled = _azure.recognize_continuous(recognizer, 5.0)

    assert [r.text for r in results] == ["你好", "老师", "再见"]
    assert canceled is None


def test_a_single_segment_still_works(monkeypatch):
    _continuous(monkeypatch, _ordinary_finish("你好"))

    with _azure.recognizer_for(b"FAKEWAV", "zh-CN") as recognizer:
        results, canceled = _azure.recognize_continuous(recognizer, 5.0)

    assert [r.text for r in results] == ["你好"]
    assert canceled is None


def test_unintelligible_segments_are_dropped(monkeypatch):
    """A silent stretch fires `recognized` with NoMatch; it is not a segment."""
    events = [
        ("recognized", recognized_event(_speech("你好"))),
        ("recognized", recognized_event(types.SimpleNamespace(reason="NoMatch", text=""))),
        ("canceled", canceled_event("EndOfStream")),
        ("session_stopped", None),
    ]
    _continuous(monkeypatch, events)

    with _azure.recognizer_for(b"FAKEWAV", "zh-CN") as recognizer:
        results, canceled = _azure.recognize_continuous(recognizer, 5.0)

    assert [r.text for r in results] == ["你好"]
    assert canceled is None


def test_end_of_stream_is_a_clean_finish_not_a_cancellation(monkeypatch):
    """Every file recognition ends with a `canceled(EndOfStream)`.

    Treating that as failure would turn every successful turn into an `SttError`.
    """
    _continuous(monkeypatch, _ordinary_finish("你好"))

    with _azure.recognizer_for(b"FAKEWAV", "zh-CN") as recognizer:
        _, canceled = _azure.recognize_continuous(recognizer, 5.0)

    assert canceled is None


def test_a_real_cancellation_is_reported_to_the_caller(monkeypatch):
    bad = types.SimpleNamespace(reason="Canceled", error_details="bad key")
    events = [
        ("canceled", canceled_event("Error", result=bad)),
        ("session_stopped", None),
    ]
    _continuous(monkeypatch, events)

    with _azure.recognizer_for(b"FAKEWAV", "zh-CN") as recognizer:
        results, canceled = _azure.recognize_continuous(recognizer, 5.0)

    assert results == []
    assert canceled is bad


def test_recognition_finishes_before_the_temp_wav_is_removed(monkeypatch):
    """The lifetime rule `recognizer_for` states, now that recognition is async.

    `start_continuous_recognition` returns immediately. If we left the `with`
    block without waiting for the session to stop, the temp WAV would be deleted
    while Azure was still reading it — a fix that works on a fast machine and
    fails on a slow one.
    """
    seen = {}

    def while_running(recorder):
        seen["wav_exists"] = os.path.exists(recorder["audio_config"].filename)

    _continuous(monkeypatch, _ordinary_finish("你好"), observer=while_running)

    with _azure.recognizer_for(b"FAKEWAV", "zh-CN") as recognizer:
        _azure.recognize_continuous(recognizer, 5.0)

    assert seen["wav_exists"] is True


def test_the_recognizer_is_stopped_even_when_it_fails(monkeypatch):
    """A session left running holds an open Azure connection per turn."""
    bad = types.SimpleNamespace(reason="Canceled", error_details="bad key")
    recorder = _continuous(
        monkeypatch,
        [("canceled", canceled_event("Error", result=bad)), ("session_stopped", None)],
    )

    with _azure.recognizer_for(b"FAKEWAV", "zh-CN") as recognizer:
        _azure.recognize_continuous(recognizer, 5.0)

    assert recorder["stopped"] is True


def test_a_session_that_never_stops_times_out(monkeypatch):
    """The wait is bounded, so a wedged session cannot outlive the turn's deadline."""
    _continuous(monkeypatch, [], silent=True)

    with _azure.recognizer_for(b"FAKEWAV", "zh-CN") as recognizer:
        with pytest.raises(_azure.RecognitionTimeout):
            _azure.recognize_continuous(recognizer, 0.05)
