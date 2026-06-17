"""Phase 1 `POST /api/turn` route tests — Azure mocked, no tokens spent.

We patch the STT call so the route is exercised in isolation: upload → transcript
→ fixed reply. Real recognition is a manual/live check, not asserted here.
"""
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.speech import stt

client = TestClient(app)


def _upload(data=b"FAKEWAV"):
    return {"audio": ("turn.wav", data, "audio/wav")}


def test_turn_returns_transcript_and_fixed_reply(monkeypatch):
    async def fake_transcribe(audio_wav, language="zh-CN"):
        assert audio_wav == b"FAKEWAV"
        return "你好老师"

    monkeypatch.setattr(stt, "transcribe", fake_transcribe)

    resp = client.post("/api/turn", files=_upload())

    assert resp.status_code == 200
    assert resp.json() == {
        "transcript": "你好老师",
        "reply": {"zh": "你好", "pinyin": "nǐ hǎo"},
    }


def test_turn_requires_audio_field():
    resp = client.post("/api/turn")
    assert resp.status_code == 422


def test_turn_surfaces_stt_failure_as_502(monkeypatch):
    async def boom(audio_wav, language="zh-CN"):
        raise stt.SttError("azure canceled: bad key")

    monkeypatch.setattr(stt, "transcribe", boom)

    resp = client.post("/api/turn", files=_upload())
    assert resp.status_code == 502
    assert "bad key" in resp.json()["detail"]
