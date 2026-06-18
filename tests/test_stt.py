"""Azure STT boundary — contract tests with the SDK fully mocked.

We assert that we *parse* each SDK result reason correctly (text / NoMatch /
Canceled). The shared recognizer construction is covered in `test_recognizer.py`;
here we only check what `stt` adds on top. We never hit Azure and never assert
real recognition text (that's a manual/live check).
"""
import types

import pytest

from backend.speech import _recognizer, stt


def _make_fake_speechsdk(result, recorder):
    """Build a stand-in for the `speechsdk` module wired to `result`."""

    class FakeSpeechConfig:
        def __init__(self, subscription, region):
            self.subscription = subscription
            self.region = region
            self.speech_recognition_language = None

    class FakeAudioConfig:
        def __init__(self, filename):
            self.filename = filename

    class FakeRecognizer:
        def __init__(self, speech_config, audio_config):
            recorder["speech_config"] = speech_config
            recorder["audio_config"] = audio_config

        def recognize_once(self):
            return result

    reasons = types.SimpleNamespace(
        RecognizedSpeech="RecognizedSpeech",
        NoMatch="NoMatch",
        Canceled="Canceled",
    )
    cancellation = types.SimpleNamespace(
        from_result=lambda r: types.SimpleNamespace(
            reason="Error", error_details=getattr(r, "error_details", "")
        )
    )
    return types.SimpleNamespace(
        SpeechConfig=FakeSpeechConfig,
        audio=types.SimpleNamespace(AudioConfig=FakeAudioConfig),
        SpeechRecognizer=FakeRecognizer,
        ResultReason=reasons,
        CancellationDetails=cancellation,
    )


def _result(reason, text="", error_details=""):
    return types.SimpleNamespace(
        reason=reason, text=text, error_details=error_details
    )


@pytest.fixture
def patched(monkeypatch):
    """Install fake credentials; return a helper that wires a fake SDK + result.

    The same fake serves both `stt` (result dispatch) and `_recognizer` (the
    shared construction `stt` now delegates to).
    """
    monkeypatch.setattr(_recognizer.config, "AZURE_SPEECH_KEY", "test-key")
    monkeypatch.setattr(_recognizer.config, "AZURE_SPEECH_REGION", "test-region")

    def install(result):
        recorder = {}
        fake = _make_fake_speechsdk(result, recorder)
        monkeypatch.setattr(stt, "speechsdk", fake)
        monkeypatch.setattr(_recognizer, "speechsdk", fake)
        return recorder

    return install


async def test_recognized_speech_returns_text(patched):
    recorder = patched(_result("RecognizedSpeech", text="你好老师"))

    out = await stt.transcribe(b"FAKEWAV")

    assert out == "你好老师"
    # transcribe forwards its default language through to the recognizer.
    assert recorder["speech_config"].speech_recognition_language == "zh-CN"


async def test_language_is_overridable(patched):
    recorder = patched(_result("RecognizedSpeech", text="hi"))

    out = await stt.transcribe(b"FAKEWAV", language="en-US")

    assert out == "hi"
    assert recorder["speech_config"].speech_recognition_language == "en-US"


async def test_no_match_returns_empty_string(patched):
    patched(_result("NoMatch"))

    assert await stt.transcribe(b"FAKEWAV") == ""


async def test_canceled_raises_stt_error(patched):
    patched(_result("Canceled", error_details="bad key"))

    with pytest.raises(stt.SttError, match="bad key"):
        await stt.transcribe(b"FAKEWAV")
