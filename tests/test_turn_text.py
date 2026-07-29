"""Phase 3a `POST /api/turn/text` route tests — orchestrator mocked, no tokens.

The text endpoint is the speech-free path that proves the conversation worker +
cached prefix. We patch the orchestrator so the route is exercised in isolation:
JSON in -> reply + annotation; unknown topic -> 404; worker failure -> 502.
"""
import pytest
from fastapi.testclient import TestClient

from backend import kb, orchestrator
from backend.main import app
from backend.models import ConversationTurnResponse, TurnAnnotation, Utterance
from backend.workers import conversation

client = TestClient(app)


def _reply():
    return ConversationTurnResponse(
        transcript=Utterance(zh="我叫小明", pinyin="wǒ jiào xiǎo míng"),
        reply=Utterance(zh="你好！你叫什么名字？", pinyin="nǐ hǎo! nǐ jiào shénme míngzi?"),
        annotation=TurnAnnotation(coherence="on_track", topic_tags=["greetings"]),
    )


def test_turn_text_returns_reply_and_annotation(monkeypatch):
    captured = {}

    async def fake_run(req, client=None):
        captured["topic_id"] = req.topic_id
        captured["text"] = req.text
        captured["dialogue"] = req.dialogue
        return _reply()

    monkeypatch.setattr(orchestrator, "run_text_turn", fake_run)

    resp = client.post(
        "/api/turn/text",
        json={
            "topic_id": "greetings",
            "text": "我叫小明",
            "dialogue": [{"role": "user", "zh": "你好"}],
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    # The learner's own turn comes back with derived pinyin so the client renders
    # a typed turn exactly like a spoken one.
    assert body["transcript"] == {"zh": "我叫小明", "pinyin": "wǒ jiào xiǎo míng"}
    assert body["reply"] == {"zh": "你好！你叫什么名字？", "pinyin": "nǐ hǎo! nǐ jiào shénme míngzi?"}
    assert body["annotation"]["coherence"] == "on_track"
    assert body["annotation"]["topic_tags"] == ["greetings"]
    # The route forwarded the client-held transcript to the orchestrator.
    assert captured["topic_id"] == "greetings"
    assert captured["dialogue"][0].zh == "你好"


def test_turn_text_unknown_topic_is_404(monkeypatch):
    async def fake_run(req, client=None):
        raise kb.KbError("unknown topic: 'nope'")

    monkeypatch.setattr(orchestrator, "run_text_turn", fake_run)

    resp = client.post("/api/turn/text", json={"topic_id": "nope", "text": "你好"})
    assert resp.status_code == 404
    assert "nope" in resp.json()["detail"]


def test_turn_text_worker_failure_is_502(monkeypatch):
    async def fake_run(req, client=None):
        raise conversation.ConversationError("conversation worker refused the turn")

    monkeypatch.setattr(orchestrator, "run_text_turn", fake_run)

    resp = client.post("/api/turn/text", json={"topic_id": "greetings", "text": "你好"})
    assert resp.status_code == 502
    assert "refused" in resp.json()["detail"]


def test_turn_text_requires_topic_and_text():
    assert client.post("/api/turn/text", json={"text": "你好"}).status_code == 422
    assert client.post("/api/turn/text", json={"topic_id": "greetings"}).status_code == 422


@pytest.mark.parametrize("text", ["", "   "])
def test_turn_text_rejects_blank_input(text, monkeypatch):
    async def fake_run(req, client=None):
        raise AssertionError("worker should not run for an empty turn")

    monkeypatch.setattr(orchestrator, "run_text_turn", fake_run)

    resp = client.post("/api/turn/text", json={"topic_id": "greetings", "text": text})
    assert resp.status_code == 422


@pytest.mark.parametrize("text", ["nihao", "ni3hao3", "wo jiao xiao ming"])
def test_turn_text_accepts_typed_pinyin(text, monkeypatch):
    """Pinyin reaches the worker untouched — the route never gatekeeps romanization."""
    captured = {}

    async def fake_run(req, client=None):
        captured["text"] = req.text
        return _reply()

    monkeypatch.setattr(orchestrator, "run_text_turn", fake_run)

    resp = client.post("/api/turn/text", json={"topic_id": "greetings", "text": text})
    assert resp.status_code == 200
    assert captured["text"] == text


def test_turn_text_dialogue_defaults_to_empty(monkeypatch):
    async def fake_run(req, client=None):
        assert req.dialogue == []
        return _reply()

    monkeypatch.setattr(orchestrator, "run_text_turn", fake_run)
    resp = client.post("/api/turn/text", json={"topic_id": "greetings", "text": "你好"})
    assert resp.status_code == 200
