"""Phase 0 route tests: health, hello-world API, and static page serving.

Workers/Azure are not involved here — these are pure HTTP-surface assertions
via FastAPI's TestClient.
"""
from fastapi.testclient import TestClient

from backend import kb, orchestrator
from backend.main import app
from backend.models import ScenarioCard, SessionStartResponse, Utterance
from backend.workers import sketch

client = TestClient(app)


def test_health_ok():
    resp = client.get("/health")
    assert resp.status_code == 200
    # `auth` rides along on the liveness probe so a deploy can be checked from
    # outside; its two states are asserted in `test_auth.py`.
    assert resp.json()["status"] == "ok"


def test_hello_returns_hello_world():
    resp = client.get("/api/hello")
    assert resp.status_code == 200
    assert resp.json() == {"message": "hello world"}


def test_root_serves_static_page():
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    # The page is the visible surface; it must reference the turn API it calls
    # (Phase 1 deepened the page from the hello round-trip to push-to-talk).
    assert "/api/turn" in resp.text


def test_static_page_is_not_cached():
    """The page must never be served from a stale cache.

    All the client logic lives in `index.html`, so a cached copy silently hides
    every frontend fix — the browser keeps running yesterday's JavaScript while
    the server has today's. That is invisible (the page loads fine, it's just
    wrong), and on a phone there's no easy hard-reload. `no-store` costs nothing
    here: one small file, served locally or over a dev tunnel.
    """
    resp = client.get("/")
    assert resp.headers["cache-control"] == "no-store"


# --- M2-B: `POST /api/session` ----------------------------------------------


def _session_response():
    return SessionStartResponse(
        scenario_card=ScenarioCard(
            situation="You meet a classmate on campus.", goal="Introduce yourself."
        ),
        opening_line=Utterance(zh="你好！", pinyin="nǐ hǎo!"),
        sketch="The classmate is friendly and a little shy.",
    )


def test_session_start_returns_card_opening_line_and_sketch(monkeypatch):
    captured = {}

    async def fake_start(topic_id, client=None):
        captured["topic_id"] = topic_id
        return _session_response()

    monkeypatch.setattr(orchestrator, "start_session", fake_start)

    resp = client.post("/api/session", json={"topic_id": "greetings"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["scenario_card"] == {
        "situation": "You meet a classmate on campus.",
        "goal": "Introduce yourself.",
    }
    assert body["opening_line"] == {"zh": "你好！", "pinyin": "nǐ hǎo!"}
    assert body["sketch"] == "The classmate is friendly and a little shy."
    assert captured["topic_id"] == "greetings"


def test_session_start_unknown_topic_is_404(monkeypatch):
    async def fake_start(topic_id, client=None):
        raise kb.KbError("unknown topic: 'nope'")

    monkeypatch.setattr(orchestrator, "start_session", fake_start)

    resp = client.post("/api/session", json={"topic_id": "nope"})
    assert resp.status_code == 404
    assert "nope" in resp.json()["detail"]


def test_session_start_worker_refusal_is_502(monkeypatch):
    async def fake_start(topic_id, client=None):
        raise sketch.SketchError("sketch worker refused the session")

    monkeypatch.setattr(orchestrator, "start_session", fake_start)

    resp = client.post("/api/session", json={"topic_id": "greetings"})
    assert resp.status_code == 502
    assert "refused" in resp.json()["detail"]


def test_session_start_requires_topic_id():
    resp = client.post("/api/session", json={})
    assert resp.status_code == 422
