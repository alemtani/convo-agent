"""`POST /api/feedback` — the HTTP half of A7.

The GitHub client is mocked at the network boundary (`httpx.MockTransport`), so
these are ordinary route tests: no key, no token, nothing leaves the process.
What they pin is the mapping from a refusal to a status code, because that is
the contract the client renders — a 429 tells the learner to come back, a 422
tells them the report is wrong, and a 503 tells them the server cannot file at
all. Rendering all three as "something went wrong" would be the same as having
no recourse, which is the thing this feature exists to end.
"""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from backend import issues
from backend.main import app

client = TestClient(app)

DIALOGUE = [
    {"role": "user", "zh": "你好，我叫小明。"},
    {"role": "partner", "zh": "我叫小王。你最近怎么样？"},
    {"role": "user", "zh": "我很好，你呢？"},
    {"role": "partner", "zh": "我也很好。"},
]


def _body(**overrides):
    payload = {
        "kind": "bug",
        "topic_id": "greetings",
        "message": "the counter never moved",
        "dialogue": DIALOGUE,
        "state": {"filled_at": {"self_name": 1}, "status": "complete"},
        "sketch": "The classmate is cheerful.",
        "opening_line": {"zh": "你好！", "pinyin": "nǐ hǎo!"},
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def github(monkeypatch):
    """A configured server whose GitHub calls answer from a recorded 201.

    Yields the list of requests GitHub saw, so a test can assert the body we
    built rather than only the status we returned.
    """
    monkeypatch.setattr(issues.config, "GITHUB_ISSUE_TOKEN", "ghp_secret")
    monkeypatch.setattr(issues.config, "GITHUB_ISSUE_REPO", "alemtani/convo-agent")
    issues.reset_limiter()
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(
            201,
            json={"html_url": "https://github.com/alemtani/convo-agent/issues/12",
                  "number": 12},
        )

    real_create = issues.create_issue

    async def create(title, body, *, labels=(), client=None):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await real_create(title, body, labels=labels, client=c)

    monkeypatch.setattr(issues, "create_issue", create)
    yield seen
    issues.reset_limiter()


def test_bug_report_files_an_issue_and_returns_its_url(github):
    resp = client.post("/api/feedback", json=_body())

    assert resp.status_code == 200
    assert resp.json() == {
        "url": "https://github.com/alemtani/convo-agent/issues/12",
        "number": 12,
    }
    assert len(github) == 1
    assert "[bug] greetings" in github[0]["title"]
    assert "the counter never moved" in github[0]["body"]
    # The body must be replayable, not just readable.
    assert "```json" in github[0]["body"]


def test_contest_files_the_turn_it_names(github):
    resp = client.post(
        "/api/feedback",
        json=_body(kind="contest", turn=2, slot_id="wellbeing",
                   message="你呢 asks it back"),
    )

    assert resp.status_code == 200
    body = github[0]["body"]
    assert "[contest]" in github[0]["title"]
    assert "turn 2" in github[0]["title"]
    assert '"learner_turn": "我很好，你呢？"' in body


def test_contest_naming_an_unknown_slot_is_422(github):
    resp = client.post(
        "/api/feedback", json=_body(kind="contest", turn=2, slot_id="nonesuch")
    )

    assert resp.status_code == 422
    assert "nonesuch" in resp.json()["detail"]
    assert not github, "a refused contest must never reach GitHub"


def test_contest_past_the_transcript_is_422(github):
    resp = client.post("/api/feedback", json=_body(kind="contest", turn=11))

    assert resp.status_code == 422
    assert not github


def test_oversized_transcript_is_422(github):
    huge = [{"role": "user", "zh": "你" * 5000}] * 3
    resp = client.post("/api/feedback", json=_body(dialogue=huge))

    assert resp.status_code == 422
    assert not github


def test_unknown_topic_is_404(github):
    resp = client.post("/api/feedback", json=_body(topic_id="no-such-topic"))

    assert resp.status_code == 404
    assert not github


def test_rate_limit_refuses_the_next_report(monkeypatch, github):
    """The limit is part of the feature: this route writes to a public repo and,
    with no `APP_PASSCODE`, does it for whoever finds the hostname."""
    monkeypatch.setattr(issues.config, "FEEDBACK_RATE_LIMIT", 2)
    issues.reset_limiter()

    assert client.post("/api/feedback", json=_body()).status_code == 200
    assert client.post("/api/feedback", json=_body()).status_code == 200

    refused = client.post("/api/feedback", json=_body())
    assert refused.status_code == 429
    assert int(refused.headers["retry-after"]) > 0
    assert len(github) == 2


def test_unconfigured_server_says_so(monkeypatch):
    """503, not 500: nothing is broken, the deploy just has no token."""
    monkeypatch.setattr(issues.config, "GITHUB_ISSUE_TOKEN", "")
    issues.reset_limiter()

    resp = client.post("/api/feedback", json=_body())
    assert resp.status_code == 503
    assert "not configured" in resp.json()["detail"]


def test_github_failure_is_502_without_the_token(monkeypatch):
    monkeypatch.setattr(issues.config, "GITHUB_ISSUE_TOKEN", "ghp_secret")
    monkeypatch.setattr(issues.config, "GITHUB_ISSUE_REPO", "alemtani/convo-agent")
    issues.reset_limiter()

    async def create(title, body, *, labels=(), client=None):
        raise issues.IssueError("GitHub refused the report (401)")

    monkeypatch.setattr(issues, "create_issue", create)

    resp = client.post("/api/feedback", json=_body())
    assert resp.status_code == 502
    assert "ghp_secret" not in resp.text
    issues.reset_limiter()
