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
        display_name="Greetings (你好)",
        scenario_card=ScenarioCard(
            situation="You meet a classmate on campus.", goal="Introduce yourself."
        ),
        opening_line=Utterance(zh="你好！", pinyin="nǐ hǎo!"),
        sketch="The classmate is friendly and a little shy.",
    )


def test_session_start_returns_topic_card_opening_line_and_sketch(monkeypatch):
    called = []

    async def fake_start(*, topic_id=None, client=None):
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
    async def fake_start(*, topic_id=None, client=None):
        raise kb.KbError("no topic has an authored scenario")

    monkeypatch.setattr(orchestrator, "start_session", fake_start)

    resp = client.post("/api/session")
    assert resp.status_code == 404
    assert "no topic has an authored scenario" in resp.json()["detail"]


def test_session_start_worker_refusal_is_502(monkeypatch):
    async def fake_start(*, topic_id=None, client=None):
        raise sketch.SketchError("sketch worker refused the session")

    monkeypatch.setattr(orchestrator, "start_session", fake_start)

    resp = client.post("/api/session")
    assert resp.status_code == 502
    assert "refused" in resp.json()["detail"]


# --- M2-E: `GET /api/topics` (#29) ------------------------------------------


def test_topics_lists_the_catalog():
    resp = client.get("/api/topics")
    assert resp.status_code == 200
    topics = resp.json()["topics"]
    by_id = {t["id"]: t for t in topics}
    assert "greetings" in by_id
    assert by_id["greetings"]["display_name"] == "Greetings (你好)"
    assert by_id["greetings"]["summary"]


def test_topics_lists_everything_a_session_can_draw():
    """The catalog and the draw pool must not disagree.

    `/api/session` draws from `kb.list_topic_ids`. A topic in that pool but
    absent here would start a session the learner has no name for.
    """
    resp = client.get("/api/topics")
    assert [t["id"] for t in resp.json()["topics"]] == kb.list_topic_ids()


# --- A1: restarting the same scenario (#66) ---------------------------------


def test_session_start_honors_a_topic_id_in_the_body(monkeypatch):
    """"Try this again" restarts the scenario the learner just failed.

    The client already holds `topic_id` as an opaque server-issued string and
    echoes it on every turn; echoing it once more to restart is that same
    pattern one step earlier. Replaces the old test that asserted a body was
    *ignored* — A1 inverts exactly that contract.
    """
    captured = {}

    async def fake_start(*, topic_id=None, client=None):
        captured["topic_id"] = topic_id
        return _session_response()

    monkeypatch.setattr(orchestrator, "start_session", fake_start)

    resp = client.post("/api/session", json={"topic_id": "greetings"})
    assert resp.status_code == 200
    assert captured["topic_id"] == "greetings"


def test_session_start_still_takes_no_body(monkeypatch):
    """The bodyless POST is what every current client sends. It must not 422."""
    captured = {}

    async def fake_start(*, topic_id=None, client=None):
        captured["topic_id"] = topic_id
        return _session_response()

    monkeypatch.setattr(orchestrator, "start_session", fake_start)

    resp = client.post("/api/session")
    assert resp.status_code == 200
    assert captured["topic_id"] is None


def test_session_start_ignores_an_unknown_field(monkeypatch):
    """A stray field (an old client, a curl copied from before this) is not an
    error — only `topic_id` is read."""
    async def fake_start(*, topic_id=None, client=None):
        return _session_response()

    monkeypatch.setattr(orchestrator, "start_session", fake_start)

    resp = client.post("/api/session", json={"mood": "hopeful"})
    assert resp.status_code == 200


def test_session_start_404s_an_unknown_topic_id(monkeypatch):
    async def fake_start(*, topic_id=None, client=None):
        raise kb.KbError(f"unknown topic: {topic_id}")

    monkeypatch.setattr(orchestrator, "start_session", fake_start)

    resp = client.post("/api/session", json={"topic_id": "no-such-topic"})
    assert resp.status_code == 404
