"""Azure STT boundary — contract tests with the SDK fully mocked.

We assert the *request we build* (subscription/region from config, recognition
language) and that we *parse* each SDK result reason correctly. We never hit
Azure and never assert real recognition text (that's a manual/live check).
"""
import types

import pytest

from backend.speech import stt


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
    """Install fake credentials; return a helper that wires a fake SDK + result."""
    monkeypatch.setattr(stt.config, "AZURE_SPEECH_KEY", "test-key")
    monkeypatch.setattr(stt.config, "AZURE_SPEECH_REGION", "test-region")

    def install(result):
        recorder = {}
        monkeypatch.setattr(
            stt, "speechsdk", _make_fake_speechsdk(result, recorder)
        )
        return recorder

    return install


async def test_builds_config_from_settings_and_returns_text(patched):
    recorder = patched(_result("RecognizedSpeech", text="你好老师"))

    out = await stt.transcribe(b"FAKEWAV")

    assert out == "你好老师"
    cfg = recorder["speech_config"]
    assert cfg.subscription == "test-key"
    assert cfg.region == "test-region"
    assert cfg.speech_recognition_language == "zh-CN"
    # The WAV bytes are handed to the SDK via a temp .wav file.
    assert recorder["audio_config"].filename.endswith(".wav")


async def test_language_is_overridable(patched):
    recorder = patched(_result("RecognizedSpeech", text="hi"))

    await stt.transcribe(b"FAKEWAV", language="en-US")

    assert recorder["speech_config"].speech_recognition_language == "en-US"


async def test_no_match_returns_empty_string(patched):
    patched(_result("NoMatch"))

    assert await stt.transcribe(b"FAKEWAV") == ""


async def test_canceled_raises_stt_error(patched):
    patched(_result("Canceled", error_details="bad key"))

    with pytest.raises(stt.SttError, match="bad key"):
        await stt.transcribe(b"FAKEWAV")
