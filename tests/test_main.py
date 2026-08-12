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
        topic_id="greetings",
        scenario_card=ScenarioCard(
            situation="You meet a classmate on campus.", goal="Introduce yourself."
        ),
        opening_line=Utterance(zh="你好！", pinyin="nǐ hǎo!"),
        sketch="The classmate is friendly and a little shy.",
    )


def test_session_start_returns_topic_card_opening_line_and_sketch(monkeypatch):
    called = []

    async def fake_start(client=None):
        called.append(True)
        return _session_response()

    monkeypatch.setattr(orchestrator, "start_session", fake_start)

    # No body: the server picks the topic, not the caller.
    resp = client.post("/api/session")

    assert resp.status_code == 200
    body = resp.json()
    assert body["topic_id"] == "greetings"
    assert body["scenario_card"] == {
        "situation": "You meet a classmate on campus.",
        "goal": "Introduce yourself.",
    }
    assert body["opening_line"] == {"zh": "你好！", "pinyin": "nǐ hǎo!"}
    assert body["sketch"] == "The classmate is friendly and a little shy."
    assert called == [True]


def test_session_start_no_scenario_topic_is_404(monkeypatch):
    async def fake_start(client=None):
        raise kb.KbError("no topic has an authored scenario")

    monkeypatch.setattr(orchestrator, "start_session", fake_start)

    resp = client.post("/api/session")
    assert resp.status_code == 404
    assert "no topic has an authored scenario" in resp.json()["detail"]


def test_session_start_worker_refusal_is_502(monkeypatch):
    async def fake_start(client=None):
        raise sketch.SketchError("sketch worker refused the session")

    monkeypatch.setattr(orchestrator, "start_session", fake_start)

    resp = client.post("/api/session")
    assert resp.status_code == 502
    assert "refused" in resp.json()["detail"]


def test_session_start_ignores_a_body_if_one_is_sent(monkeypatch):
    """No request model to validate against any more — a stray body (an old
    client, or a curl copied from before this change) must not 422."""
    async def fake_start(client=None):
        return _session_response()

    monkeypatch.setattr(orchestrator, "start_session", fake_start)

    resp = client.post("/api/session", json={"topic_id": "greetings"})
    assert resp.status_code == 200
