"""Frontend smoke tests: the passcode gate raises and clears correctly.

The gate is the only thing standing between a public URL and a stranger
spending the Anthropic/Azure quota, and its whole surface is client-side
decisions about *when* to appear. Those decisions are exactly what "load the
page and look" can't check: the interesting cases are a stale cookie
(`/health` says gated, the probe says 401) and a mid-session expiry, neither of
which you can produce by clicking.

Same harness as `test_thread.py` — the stub answers `fetch`, so `/health`,
`/api/hello` and `/api/auth` are all canned per test.
"""

import json

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.smoke

GATED = {"status": "ok", "auth": "enabled"}
UNGATED = {"status": "ok", "auth": "disabled"}


@pytest.fixture
def gated_page(page):
    """A page whose `/health` reports the gate on and whose probe 401s."""
    page.add_init_script(
        "(() => { const t = setInterval(() => {"
        "  if (!window.__stub) return; clearInterval(t);"
        "  window.__stub.responses['/health'] = " + json.dumps(GATED) + ";"
        "  window.__stub.status['/api/hello'] = 401;"
        "}, 0); })()"
    )
    return page


def test_no_gate_when_the_server_reports_auth_disabled(page):
    """Local development must not show a login screen it has no passcode for."""
    page.goto("/")
    expect(page.locator("#gate")).not_to_be_visible()


def test_gate_appears_when_the_session_probe_is_unauthorized(gated_page):
    """Gated server + no valid cookie ⇒ the overlay, before any turn is spent."""
    gated_page.goto("/")
    expect(gated_page.locator("#gate")).to_be_visible()
    expect(gated_page.locator("#gate-input")).to_be_focused()


def test_no_gate_when_the_cookie_is_still_good(page):
    """A gated server the browser already has a session for shows no overlay.

    The distinction that matters: `auth: enabled` alone must not raise the
    gate, or every reload would demand the passcode again despite a perfectly
    valid 30-day cookie.
    """
    page.add_init_script(
        "(() => { const t = setInterval(() => {"
        "  if (!window.__stub) return; clearInterval(t);"
        "  window.__stub.responses['/health'] = " + json.dumps(GATED) + ";"
        "}, 0); })()"          # /api/hello defaults to 200 — the cookie works
    )
    page.goto("/")
    expect(page.locator("#gate")).not_to_be_visible()


def test_correct_passcode_clears_the_gate(gated_page):
    gated_page.goto("/")
    expect(gated_page.locator("#gate")).to_be_visible()

    gated_page.fill("#gate-input", "open-sesame")     # stub answers /api/auth 200
    gated_page.click("#gate-form button")

    expect(gated_page.locator("#gate")).not_to_be_visible()
    assert "/api/auth" in gated_page.evaluate("window.__stub.requests")


def test_wrong_passcode_keeps_the_gate_up_with_an_error(gated_page):
    gated_page.add_init_script(
        "(() => { const t = setInterval(() => {"
        "  if (!window.__stub) return; clearInterval(t);"
        "  window.__stub.status['/api/auth'] = 401;"
        "}, 0); })()"
    )
    gated_page.goto("/")

    gated_page.fill("#gate-input", "wrong")
    gated_page.click("#gate-form button")

    expect(gated_page.locator("#gate")).to_be_visible()
    expect(gated_page.locator("#gate-error")).to_contain_text("Wrong passcode")


def test_empty_passcode_is_not_submitted(gated_page):
    """Don't spend a request on a blank field — and don't show a scary error."""
    gated_page.goto("/")
    gated_page.click("#gate-form button")
    assert "/api/auth" not in gated_page.evaluate("window.__stub.requests")
    expect(gated_page.locator("#gate-error")).to_have_text("")


def test_expired_session_mid_turn_re_prompts_without_losing_the_thread(page):
    """A 401 on a turn re-raises the gate and leaves the transcript intact.

    The failure this pins: treating a mid-session expiry as an ordinary turn
    error leaves the learner staring at "error 401" with no way back in, and a
    reload would be their only recourse.
    """
    page.add_init_script(
        "(([d]) => { localStorage.setItem('convo.dialogue.greetings', JSON.stringify(d));"
        "localStorage.setItem('convo.mode', 'text'); })"
        f"({json.dumps([[{'role': 'user', 'zh': '你好', 'pinyin': 'nǐ hǎo'}]])})"
    )
    page.add_init_script(
        "(() => { const t = setInterval(() => {"
        "  if (!window.__stub) return; clearInterval(t);"
        "  window.__stub.status['/api/turn/text'] = 401;"
        "}, 0); })()"
    )
    page.goto("/")

    expect(page.locator("#gate")).not_to_be_visible()
    page.fill("#text-input", "ni hao")
    page.click("#send")

    expect(page.locator("#gate")).to_be_visible()
    expect(page.locator("#gate-error")).to_contain_text("Session expired")
    # The thread survived the 401 — the seeded turn is still rendered.
    expect(page.locator("#thread .bubble.user")).to_have_count(1)
