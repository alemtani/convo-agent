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


def _make_fake_speechsdk(recorder):
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
        SpeechRecognizer=FakeRecognizer,
        CancellationDetails=FakeCancellationDetails,
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
