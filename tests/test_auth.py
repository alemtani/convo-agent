"""Passcode gate — token issuing/verification and the HTTP surface it protects.

The gate exists for one reason: the deployed URL is public, and every `/api/*`
call behind it spends real Anthropic and Azure money. So the tests that matter
are the *negative* ones — a wrong passcode, a tampered token, an expired token,
and a token minted under a passcode that has since been rotated must all fail
closed.

`backend.auth` reads `config.APP_PASSCODE` at call time (not import time) so
these tests can set it with `monkeypatch` instead of reloading modules.
"""
import time

import pytest
from fastapi.testclient import TestClient

from backend import auth, config, orchestrator
from backend.main import app

PASSCODE = "open-sesame"


@pytest.fixture
def gated(monkeypatch):
    """Turn the gate on with a known passcode."""
    monkeypatch.setattr(config, "APP_PASSCODE", PASSCODE)
    return PASSCODE


@pytest.fixture
def ungated(monkeypatch):
    """Turn the gate off — the local-development / CI default."""
    monkeypatch.setattr(config, "APP_PASSCODE", "")


# --- enablement -----------------------------------------------------------


def test_gate_is_disabled_when_no_passcode_is_configured(ungated):
    """An unset passcode means local dev, where a login screen is pure friction."""
    assert auth.is_enabled() is False


def test_gate_is_enabled_when_a_passcode_is_configured(gated):
    assert auth.is_enabled() is True


# --- passcode check -------------------------------------------------------


def test_correct_passcode_is_accepted(gated):
    assert auth.check_passcode(PASSCODE) is True


@pytest.mark.parametrize("wrong", ["", "open-sesam", "OPEN-SESAME", "x" * 64])
def test_wrong_passcode_is_rejected(gated, wrong):
    assert auth.check_passcode(wrong) is False


# --- token lifecycle ------------------------------------------------------


def test_issued_token_verifies(gated):
    assert auth.verify_token(auth.issue_token()) is True


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "garbage",
        "9999999999",            # no signature
        "9999999999.deadbeef",   # signature that isn't ours
        "not-a-number.abc123",   # unparseable expiry
    ],
)
def test_malformed_or_unsigned_tokens_are_rejected(gated, bad):
    assert auth.verify_token(bad) is False


def test_tampered_expiry_is_rejected(gated):
    """The expiry is inside the signed payload, so extending it breaks the MAC."""
    expiry, signature = auth.issue_token().split(".")
    forged = f"{int(expiry) + 86400}.{signature}"
    assert auth.verify_token(forged) is False


def test_expired_token_is_rejected(gated, monkeypatch):
    token = auth.issue_token()
    # Capture the real clock *before* patching: reading `time.time()` inside the
    # replacement would call the replacement.
    later = time.time() + config.SESSION_TTL_DAYS * 86400 + 60
    monkeypatch.setattr(time, "time", lambda: later)
    assert auth.verify_token(token) is False


def test_token_from_a_previous_passcode_is_rejected(gated, monkeypatch):
    """Rotating the passcode must invalidate outstanding sessions.

    The signing key is derived from the passcode precisely so that changing it
    is a working revocation lever — otherwise a leaked cookie outlives the
    only credential we have.
    """
    token = auth.issue_token()
    monkeypatch.setattr(config, "APP_PASSCODE", "a-different-passcode")
    assert auth.verify_token(token) is False


# --- HTTP surface ---------------------------------------------------------


def test_api_requires_a_session_when_gated(gated):
    with TestClient(app) as client:
        assert client.get("/api/hello").status_code == 401


def test_login_then_api_succeeds(gated):
    with TestClient(app) as client:
        resp = client.post("/api/auth", json={"passcode": PASSCODE})
        assert resp.status_code == 200
        assert auth.COOKIE_NAME in resp.cookies
        # The cookie is carried by the client from here on.
        assert client.get("/api/hello").status_code == 200


def test_login_with_wrong_passcode_sets_no_cookie(gated):
    with TestClient(app) as client:
        resp = client.post("/api/auth", json={"passcode": "nope"})
        assert resp.status_code == 401
        assert auth.COOKIE_NAME not in resp.cookies
        assert client.get("/api/hello").status_code == 401


def test_session_cookie_is_httponly_and_samesite(gated):
    """The cookie is the credential; script must not be able to read it."""
    with TestClient(app) as client:
        resp = client.post("/api/auth", json={"passcode": PASSCODE})
    header = resp.headers["set-cookie"].lower()
    assert "httponly" in header
    assert "samesite=lax" in header


def test_health_is_reachable_without_a_session(gated):
    """`/health` is the platform's liveness probe — gating it fails the deploy."""
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200


def test_health_reports_whether_the_gate_is_on(gated):
    """One curl after deploying answers 'did I actually set the passcode?'.

    A gate that silently defaults to off is exactly the failure this milestone
    exists to prevent, so the answer has to be observable from outside.
    """
    with TestClient(app) as client:
        assert client.get("/health").json()["auth"] == "enabled"


def test_health_reports_a_disabled_gate(ungated):
    with TestClient(app) as client:
        assert client.get("/health").json()["auth"] == "disabled"


def test_static_page_is_reachable_without_a_session(gated):
    """The page *is* the login screen, so it must render before you have a session.

    It carries no keys and no learner data — everything sensitive is behind
    `/api/*`, which stays gated.
    """
    with TestClient(app) as client:
        assert client.get("/").status_code == 200


def test_api_is_open_when_the_gate_is_disabled(ungated):
    with TestClient(app) as client:
        assert client.get("/api/hello").status_code == 200


# --- the endpoints that actually cost money -------------------------------
#
# `/api/hello` is free, so gating it proves the middleware runs but not that it
# protects anything worth protecting. The turn routes are the reason this gate
# exists: each one spends Azure STT, Azure PA and a Claude call. What follows
# asserts the property that matters — an unauthenticated request is refused
# *before* any upstream work is started, not merely refused.


def test_spoken_turn_is_refused_without_spending_azure_or_claude(gated, monkeypatch):
    """A 401 on `/api/turn` must cost nothing upstream.

    Asserting the status alone would still pass if the gate ran *after* the
    route body — which is exactly the regression that would make the gate
    decorative: strangers couldn't read the reply, but they'd still be buying
    it. The spy is the real assertion.
    """
    called = []

    async def spy(*args, **kwargs):
        called.append(kwargs)
        raise AssertionError("upstream work started on an unauthenticated turn")

    monkeypatch.setattr(orchestrator, "prepare_audio_turn", spy)

    with TestClient(app) as client:
        resp = client.post("/api/turn", files={"audio": ("a.wav", b"FAKEWAV", "audio/wav")})

    assert resp.status_code == 401
    assert called == []


def test_text_turn_is_refused_without_spending_claude(gated, monkeypatch):
    called = []

    async def spy(*args, **kwargs):
        called.append(kwargs)
        raise AssertionError("worker ran on an unauthenticated turn")

    monkeypatch.setattr(orchestrator, "run_text_turn", spy)

    with TestClient(app) as client:
        resp = client.post(
            "/api/turn/text", json={"topic_id": "greetings", "text": "你好"}
        )

    assert resp.status_code == 401
    assert called == []


def test_gate_runs_before_request_validation(gated):
    """An unauthenticated caller can't even probe the request schema.

    `/api/turn` needs a multipart file; unauthenticated, the missing body must
    surface as 401 rather than 422. Ordering the other way would leak the shape
    of the API to anyone who asks, and — worse — means the gate sits downstream
    of parsing, where an upload has already been read into memory.
    """
    with TestClient(app) as client:
        assert client.post("/api/turn").status_code == 401
        assert client.post("/api/turn/text", json={}).status_code == 401


def test_every_api_route_is_gated_unless_explicitly_public(gated):
    """Enumerate the app's routes so a *future* one is covered by default.

    The per-endpoint tests above pin today's surface; this pins the rule. Adding
    a route that quietly serves unauthenticated traffic should fail here rather
    than in production, and the only way to opt out is to name the path in
    `_PUBLIC_API_PATHS` — a visible, reviewable edit.
    """
    from backend.main import _PUBLIC_API_PATHS

    with TestClient(app) as client:
        checked = 0
        for route in app.routes:
            path = getattr(route, "path", "")
            if not path.startswith("/api/") or path in _PUBLIC_API_PATHS:
                continue
            for method in sorted(getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}):
                resp = client.request(method, path)
                assert resp.status_code == 401, f"{method} {path} is not gated"
                checked += 1

    # Guard against the loop silently checking nothing if the route table moves.
    assert checked >= 3, f"expected to check the turn routes, only saw {checked}"
