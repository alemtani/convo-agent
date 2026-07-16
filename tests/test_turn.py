"""Phase 3b `POST /api/turn` route tests — Azure + worker mocked, no tokens spent.

The route is a thin wrapper over `orchestrator.run_audio_turn`. We patch STT, PA,
and the conversation worker so the route is exercised in isolation: upload →
transcript + tone scores + real-shaped reply + annotation. Real recognition /
scoring / generation is a manual/live check.
"""
import json

import pytest
from fastapi.testclient import TestClient

from backend import orchestrator
from backend.main import app
from backend.models import (
    PronunciationScore,
    SyllableScore,
    TurnAnnotation,
    TurnResponse,
    Utterance,
)
from backend.speech import pronunciation, stt
from backend.workers import conversation

client = TestClient(app)


def _upload(data=b"FAKEWAV"):
    return {"audio": ("turn.wav", data, "audio/wav")}


def _fake_score(*syllables):
    syllables = syllables or (SyllableScore(hanzi="老", pinyin="lǎo", accuracy=97.0),)
    return PronunciationScore(overall=80.0, syllables=list(syllables))


@pytest.fixture(autouse=True)
def stub_worker_and_pa(monkeypatch):
    """Defaults: PA returns a score; the worker returns a fixed reply + annotation.
    Individual tests override as needed. Keeps the route off real Azure/Claude."""

    async def fake_assess(audio_wav, reference_text, language="zh-CN"):
        return _fake_score()

    async def fake_respond(*, kb_block, sketch, dialogue, user_text, forgiveness_level, client=None):
        return (
            Utterance(zh="你好！你叫什么名字？", pinyin="nǐ hǎo! nǐ jiào shénme míngzi?"),
            TurnAnnotation(coherence="on_track", topic_tags=["greetings"]),
            object(),
        )

    monkeypatch.setattr(pronunciation, "assess", fake_assess)
    monkeypatch.setattr(conversation, "respond", fake_respond)


def test_turn_returns_transcript_reply_scores_and_annotation(monkeypatch):
    async def fake_transcribe(audio_wav, language="zh-CN"):
        assert audio_wav == b"FAKEWAV"
        return "你好老师"

    captured = {}

    async def fake_assess(audio_wav, reference_text, language="zh-CN"):
        captured["reference_text"] = reference_text
        return _fake_score(
            SyllableScore(hanzi="你", pinyin="nǐ", accuracy=40.0),  # below threshold
            SyllableScore(hanzi="好", pinyin="hǎo", accuracy=95.0),
            SyllableScore(hanzi="老", pinyin="lǎo", accuracy=97.0),
            SyllableScore(hanzi="师", pinyin="shī", accuracy=96.0),
        )

    monkeypatch.setattr(stt, "transcribe", fake_transcribe)
    monkeypatch.setattr(pronunciation, "assess", fake_assess)

    resp = client.post("/api/turn", files=_upload())

    assert resp.status_code == 200
    # PA is assessed against the STT transcript (two-pass).
    assert captured["reference_text"] == "你好老师"
    body = resp.json()
    assert body["transcript"] == {"zh": "你好老师", "pinyin": "nǐ hǎo lǎo shī"}
    # The reply is the worker's output, not a hardcoded constant.
    assert body["reply"]["zh"] == "你好！你叫什么名字？"
    assert body["pronunciation"]["overall"] == 80.0
    # tone_errors are merged into the annotation from PA (only 你 was below 60).
    assert body["annotation"]["tone_errors"] == [
        {"syllable": "你", "expected": 3, "said": 0}
    ]


def test_turn_empty_recognition_short_circuits(monkeypatch):
    async def fake_transcribe(audio_wav, language="zh-CN"):
        return ""

    called = {"assess": False, "respond": False}

    async def fake_assess(audio_wav, reference_text, language="zh-CN"):
        called["assess"] = True
        return _fake_score()

    async def fake_respond(**kwargs):
        called["respond"] = True
        raise AssertionError("worker should not run on empty recognition")

    monkeypatch.setattr(stt, "transcribe", fake_transcribe)
    monkeypatch.setattr(pronunciation, "assess", fake_assess)
    monkeypatch.setattr(conversation, "respond", fake_respond)

    resp = client.post("/api/turn", files=_upload())
    assert resp.status_code == 200
    body = resp.json()
    assert body["transcript"] == {"zh": "", "pinyin": ""}
    assert body["pronunciation"] is None
    assert body["annotation"] is None
    assert body["reply"]["zh"]  # a gentle re-prompt
    assert called == {"assess": False, "respond": False}


def test_turn_degrades_when_pa_fails(monkeypatch):
    async def fake_transcribe(audio_wav, language="zh-CN"):
        return "你好"

    async def boom(audio_wav, reference_text, language="zh-CN"):
        raise pronunciation.PaError("azure PA canceled: timeout")

    monkeypatch.setattr(stt, "transcribe", fake_transcribe)
    monkeypatch.setattr(pronunciation, "assess", boom)

    resp = client.post("/api/turn", files=_upload())

    # A PA failure must not cost the user their transcript or reply.
    assert resp.status_code == 200
    body = resp.json()
    assert body["transcript"]["zh"] == "你好"
    assert body["reply"]["zh"] == "你好！你叫什么名字？"
    assert body["pronunciation"] is None
    assert body["annotation"]["tone_errors"] == []


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


def test_turn_surfaces_worker_failure_as_502(monkeypatch):
    async def fake_transcribe(audio_wav, language="zh-CN"):
        return "你好"

    async def boom(**kwargs):
        raise conversation.ConversationError("worker refused the turn")

    monkeypatch.setattr(stt, "transcribe", fake_transcribe)
    monkeypatch.setattr(conversation, "respond", boom)

    resp = client.post("/api/turn", files=_upload())
    assert resp.status_code == 502
    assert "refused" in resp.json()["detail"]


def test_turn_missing_azure_credentials_is_502(monkeypatch):
    # Server started before .env had the key: empty creds must fail clean (502),
    # not surface Azure's raw RuntimeError(5) as a 500.
    from backend.speech import _recognizer

    monkeypatch.setattr(_recognizer.config, "AZURE_SPEECH_KEY", "")

    resp = client.post("/api/turn", files=_upload())
    assert resp.status_code == 502
    assert "not configured" in resp.json()["detail"]


def test_turn_threads_dialogue_history_to_orchestrator(monkeypatch):
    # The client resubmits prior turns as `dialogue`; the route parses them into
    # DialogueTurn and hands them to the orchestrator so the partner has memory.
    captured = {}

    async def fake_run(audio_bytes, *, topic_id="greetings", dialogue=None, client=None):
        captured["dialogue"] = dialogue
        return TurnResponse(
            transcript=Utterance(zh="我叫小明", pinyin="wǒ jiào xiǎomíng"),
            reply=Utterance(zh="认识你很高兴", pinyin="rènshi nǐ hěn gāoxìng"),
        )

    monkeypatch.setattr(orchestrator, "run_audio_turn", fake_run)

    history = json.dumps(
        [
            {"role": "user", "zh": "你好"},
            {"role": "partner", "zh": "你好！你叫什么名字？"},
        ]
    )
    resp = client.post("/api/turn", files=_upload(), data={"dialogue": history})

    assert resp.status_code == 200
    assert [d.model_dump() for d in captured["dialogue"]] == [
        {"role": "user", "zh": "你好"},
        {"role": "partner", "zh": "你好！你叫什么名字？"},
    ]
    # The route surfaces the orchestrator's TurnResponse unchanged.
    body = resp.json()
    assert body["transcript"] == {"zh": "我叫小明", "pinyin": "wǒ jiào xiǎomíng"}
    assert body["reply"] == {"zh": "认识你很高兴", "pinyin": "rènshi nǐ hěn gāoxìng"}


def test_turn_defaults_to_empty_dialogue(monkeypatch):
    captured = {}

    async def fake_run(audio_bytes, *, topic_id="greetings", dialogue=None, client=None):
        captured["dialogue"] = dialogue
        return TurnResponse(
            transcript=Utterance(zh="你好", pinyin="nǐ hǎo"),
            reply=Utterance(zh="你好", pinyin="nǐ hǎo"),
        )

    monkeypatch.setattr(orchestrator, "run_audio_turn", fake_run)

    resp = client.post("/api/turn", files=_upload())
    assert resp.status_code == 200
    assert captured["dialogue"] == []


def test_turn_rejects_malformed_dialogue():
    resp = client.post("/api/turn", files=_upload(), data={"dialogue": "not json"})
    assert resp.status_code == 422
    assert "invalid dialogue" in resp.json()["detail"]


def test_turn_unknown_topic_is_404(monkeypatch):
    async def fake_transcribe(audio_wav, language="zh-CN"):
        return "你好"

    monkeypatch.setattr(stt, "transcribe", fake_transcribe)

    resp = client.post("/api/turn", files=_upload(), data={"topic_id": "nope"})
    assert resp.status_code == 404
