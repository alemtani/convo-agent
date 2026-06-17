"""Phase 1–2 `POST /api/turn` route tests — Azure mocked, no tokens spent.

We patch STT and PA so the route is exercised in isolation: upload → transcript
+ tone scores → fixed reply. Real recognition/scoring is a manual/live check.
"""
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.models import PronunciationScore, SyllableScore
from backend.speech import pronunciation, stt

client = TestClient(app)


def _upload(data=b"FAKEWAV"):
    return {"audio": ("turn.wav", data, "audio/wav")}


def _fake_score():
    return PronunciationScore(
        overall=80.0,
        syllables=[SyllableScore(hanzi="老", pinyin="lǎo", accuracy=97.0)],
    )


@pytest.fixture(autouse=True)
def stub_pa(monkeypatch):
    """Default: PA returns a score. Individual tests override as needed."""

    async def fake_assess(audio_wav, reference_text, language="zh-CN"):
        return _fake_score()

    monkeypatch.setattr(pronunciation, "assess", fake_assess)


def test_turn_returns_transcript_reply_and_scores(monkeypatch):
    async def fake_transcribe(audio_wav, language="zh-CN"):
        assert audio_wav == b"FAKEWAV"
        return "你好老师"

    captured = {}

    async def fake_assess(audio_wav, reference_text, language="zh-CN"):
        captured["reference_text"] = reference_text
        return _fake_score()

    monkeypatch.setattr(stt, "transcribe", fake_transcribe)
    monkeypatch.setattr(pronunciation, "assess", fake_assess)

    resp = client.post("/api/turn", files=_upload())

    assert resp.status_code == 200
    # PA is assessed against the STT transcript (two-pass).
    assert captured["reference_text"] == "你好老师"
    body = resp.json()
    assert body["transcript"] == {"zh": "你好老师", "pinyin": "nǐ hǎo lǎo shī"}
    assert body["reply"] == {"zh": "你好", "pinyin": "nǐ hǎo"}
    assert body["pronunciation"]["overall"] == 80.0
    assert body["pronunciation"]["syllables"][0]["hanzi"] == "老"


def test_turn_empty_recognition_skips_pronunciation(monkeypatch):
    async def fake_transcribe(audio_wav, language="zh-CN"):
        return ""

    called = {"assess": False}

    async def fake_assess(audio_wav, reference_text, language="zh-CN"):
        called["assess"] = True
        return _fake_score()

    monkeypatch.setattr(stt, "transcribe", fake_transcribe)
    monkeypatch.setattr(pronunciation, "assess", fake_assess)

    resp = client.post("/api/turn", files=_upload())
    assert resp.status_code == 200
    body = resp.json()
    assert body["transcript"] == {"zh": "", "pinyin": ""}
    # No transcript → nothing to assess against.
    assert body["pronunciation"] is None
    assert called["assess"] is False


def test_turn_degrades_when_pa_fails(monkeypatch):
    async def fake_transcribe(audio_wav, language="zh-CN"):
        return "你好老师"

    async def boom(audio_wav, reference_text, language="zh-CN"):
        raise pronunciation.PaError("azure PA canceled: timeout")

    monkeypatch.setattr(stt, "transcribe", fake_transcribe)
    monkeypatch.setattr(pronunciation, "assess", boom)

    resp = client.post("/api/turn", files=_upload())

    # A PA failure must not cost the user their transcript.
    assert resp.status_code == 200
    body = resp.json()
    assert body["transcript"]["zh"] == "你好老师"
    assert body["pronunciation"] is None


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
