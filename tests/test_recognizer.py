"""Shared Azure recognizer construction — contract tests, SDK mocked.

Covers what `stt` and `pronunciation` both delegate to: building the recognizer
from config + the WAV blob, and formatting a canceled result. We assert the
request we build and that the temp WAV holds the audio for the block's lifetime;
we never hit Azure.
"""
import os
import types

import pytest

from backend.speech import _recognizer


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

    cancellation = types.SimpleNamespace(
        from_result=lambda r: types.SimpleNamespace(
            reason="Error", error_details=getattr(r, "error_details", "")
        )
    )
    return types.SimpleNamespace(
        SpeechConfig=FakeSpeechConfig,
        audio=types.SimpleNamespace(AudioConfig=FakeAudioConfig),
        SpeechRecognizer=FakeRecognizer,
        CancellationDetails=cancellation,
    )


@pytest.fixture
def patched(monkeypatch):
    monkeypatch.setattr(_recognizer.config, "AZURE_SPEECH_KEY", "test-key")
    monkeypatch.setattr(_recognizer.config, "AZURE_SPEECH_REGION", "test-region")
    recorder = {}
    monkeypatch.setattr(
        _recognizer, "speechsdk", _make_fake_speechsdk(recorder)
    )
    return recorder


def test_recognizer_built_from_config_and_wav(patched):
    with _recognizer.recognizer_for(b"FAKEWAV", "zh-CN") as recognizer:
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


def test_cancellation_message_formats_reason_and_details(patched):
    result = types.SimpleNamespace(error_details="bad key")

    assert _recognizer.cancellation_message(result) == "(Error): bad key"
