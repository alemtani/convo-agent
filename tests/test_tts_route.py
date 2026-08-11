"""`POST /api/tts` route tests — the Azure boundary mocked, no tokens (M4).

Its own endpoint, deliberately not a stage of `/api/turn`: keyed on text, so a
replay is free and the turn does not get longer by the length of a synthesis.
That shape is what these tests pin — bytes in the body, failures mapped to
statuses the client can act on, and a bound on what one request may ask for.
"""
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.models import TTS_MAX_CHARS
from backend.speech import tts
from backend.speech._azure import SpeechConfigError

client = TestClient(app)


@pytest.fixture
def synthesized(monkeypatch):
    """Patch the boundary; return the recorder of what the route asked for."""
    captured = {}

    async def fake_synthesize(text):
        captured["text"] = text
        return b"ID3-mp3-bytes"

    monkeypatch.setattr(tts, "synthesize", fake_synthesize)
    return captured


def test_tts_returns_mp3_bytes(synthesized):
    resp = client.post("/api/tts", json={"text": "你好！很高兴认识你。"})

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "audio/mpeg"
    assert resp.content == b"ID3-mp3-bytes"
    assert synthesized["text"] == "你好！很高兴认识你。"


def test_tts_response_is_not_stored_by_intermediaries(synthesized):
    """Replay is the client's in-memory buffer and the server's cache, by design.

    Nothing in between should hold learner audio: a POST is not cached by
    default, and saying `no-store` keeps it that way if the endpoint ever moves.
    """
    resp = client.post("/api/tts", json={"text": "你好"})
    assert resp.headers["cache-control"] == "no-store"


def test_tts_requires_text():
    assert client.post("/api/tts", json={}).status_code == 422


@pytest.mark.parametrize("text", ["", "   ", "\n"])
def test_tts_rejects_blank_text(text, monkeypatch):
    async def fake_synthesize(text):
        raise AssertionError("Azure should not be called for an empty line")

    monkeypatch.setattr(tts, "synthesize", fake_synthesize)

    assert client.post("/api/tts", json={"text": text}).status_code == 422


def test_tts_rejects_an_over_long_line(monkeypatch):
    """A partner reply is a sentence; the cap is what stops it being a novel.

    Synthesis is billed per character, so an unbounded body is an unbounded bill
    behind a single shared passcode. Refuse it here, before Azure.
    """
    async def fake_synthesize(text):
        raise AssertionError("Azure should not be called past the cap")

    monkeypatch.setattr(tts, "synthesize", fake_synthesize)

    resp = client.post("/api/tts", json={"text": "你" * (TTS_MAX_CHARS + 1)})
    assert resp.status_code == 422


def test_tts_accepts_a_line_at_the_cap(synthesized):
    resp = client.post("/api/tts", json={"text": "你" * TTS_MAX_CHARS})
    assert resp.status_code == 200


def test_tts_azure_failure_is_502(monkeypatch):
    async def fake_synthesize(text):
        raise tts.TtsError("Azure TTS canceled (Error): quota exceeded")

    monkeypatch.setattr(tts, "synthesize", fake_synthesize)

    resp = client.post("/api/tts", json={"text": "你好"})
    assert resp.status_code == 502
    assert "quota" in resp.json()["detail"]


def test_tts_missing_credentials_is_502(monkeypatch):
    """Same status as any other upstream failure — the client's fallback is the
    same either way: reveal the text rather than leave an empty bubble."""
    async def fake_synthesize(text):
        raise SpeechConfigError("Azure Speech credentials not configured")

    monkeypatch.setattr(tts, "synthesize", fake_synthesize)

    resp = client.post("/api/tts", json={"text": "你好"})
    assert resp.status_code == 502
