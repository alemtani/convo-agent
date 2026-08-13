"""Frontend smoke tests: a cold server still opens a scene.

The app is deployed to a Fly machine that scales to zero. The first visit of
the day wakes it, and the wake-up window is the one moment the client is most
likely to lose a request: the navigation succeeds (the proxy holds it until the
machine answers), the page's `POST /api/session` goes out on a connection the
proxy may still drop, and a POST that dies at the connection layer is not
replayed by the browser the way an idempotent GET is.

Observed on a real phone, 2026-08-13: the machine woke, `GET /` returned 200,
`GET /api/hello` returned 200 sixteen seconds later — and `POST /api/session`
never reached the server at all. The learner got an empty thread, no scenario
card, and "couldn't reach the server" that nothing on the page could clear.

These tests pin the recovery: one lost session request must not end the
session, and a later success must clear the error it printed.
"""

import json

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import SESSION_START

pytestmark = pytest.mark.smoke

# Retries are seconds apart by design (a cold machine needs the time), so these
# assertions wait longer than Playwright's 5 s default.
SLOW = 20_000


def flaky_session(page, *, fails, kind="network"):
    """Make the first `fails` calls to `/api/session` fail, then behave.

    `kind="network"` rejects the promise the way a dropped connection does —
    a `TypeError`, no response object, which is the failure the phone hit.
    `kind="503"` answers with a status instead, which is what the Fly proxy
    returns when it gives up waiting on a machine.

    Wraps the conftest stub rather than replacing it: everything else on the
    page (`/health`, `/api/tts`) must keep answering normally.
    """
    page.add_init_script(
        """
        (([fails, kind]) => {
          const inner = window.fetch;
          let left = fails;
          window.__sessionAttempts = 0;
          window.fetch = (input, init) => {
            const path = new URL(input, location.href).pathname;
            if (path !== "/api/session") return inner(input, init);
            window.__sessionAttempts += 1;
            if (left <= 0) return inner(input, init);
            left -= 1;
            if (kind === "network") return Promise.reject(new TypeError("Load failed"));
            return Promise.resolve(new Response("stubbed failure", { status: 503 }));
          };
        })
        """
        f"({json.dumps([fails, kind])})"
    )


def test_a_dropped_session_request_still_opens_the_scene(page):
    """The exact phone failure: the first POST dies, the retry lands."""
    flaky_session(page, fails=1)
    page.goto("/")

    expect(page.locator("#scenario-card")).to_be_visible(timeout=SLOW)
    expect(page.locator("#scenario-goal")).to_have_text(
        SESSION_START["scenario_card"]["goal"], timeout=SLOW
    )
    expect(page.locator("#thread .bubble[data-opening]")).to_have_count(1, timeout=SLOW)
    assert page.evaluate("window.__sessionAttempts") >= 2


def test_the_error_line_clears_when_a_retry_succeeds(page):
    """A stale "couldn't reach the server" over a working scene is a lie."""
    flaky_session(page, fails=1)
    page.goto("/")

    expect(page.locator("#scenario-card")).to_be_visible(timeout=SLOW)
    expect(page.locator("#thread .bubble[data-opening]")).to_have_count(1, timeout=SLOW)
    expect(page.locator("#status")).not_to_contain_text("couldn't reach", timeout=SLOW)


def test_a_cold_start_503_is_retried_too(page):
    """The proxy's "not up yet" is a wake-up, not a verdict.

    A 503 takes the `!resp.ok` branch, which is a *different* branch from the
    dropped connection above — so it needs its own test, or half the cold-start
    window stays unhandled.
    """
    flaky_session(page, fails=2, kind="503")
    page.goto("/")

    expect(page.locator("#scenario-card")).to_be_visible(timeout=SLOW)
    expect(page.locator("#thread .bubble[data-opening]")).to_have_count(1, timeout=SLOW)
    assert page.evaluate("window.__sessionAttempts") >= 3


def test_the_controls_come_back_when_the_server_never_answers(page):
    """Giving up must still leave a usable page, not a disabled one.

    "You can still talk" has to be true: `applyControls` is what re-arms the
    mic, and a retry loop that forgot its `finally` would leave the learner
    looking at a dead button under an error message that says otherwise.
    """
    flaky_session(page, fails=99)
    page.goto("/")

    expect(page.locator("#status")).to_contain_text("couldn't reach", timeout=SLOW)
    expect(page.locator("#talk")).to_be_enabled(timeout=SLOW)


def test_a_permanent_failure_is_not_retried(page):
    """A 401 is the gate's business and a 422 is ours; neither is transient.

    Retrying either burns the cold-start budget on a request whose answer will
    not change, and — for the 401 — hides the passcode overlay behind seconds
    of pointless waiting.
    """
    page.add_init_script(
        "(() => { const t = setInterval(() => {"
        "  if (!window.__stub) return; clearInterval(t);"
        "  window.__stub.status['/api/session'] = 401;"
        "}, 0); })()"
    )
    page.goto("/")

    expect(page.locator("#gate")).to_be_visible(timeout=SLOW)
    page.wait_for_timeout(2000)
    assert page.evaluate(
        "window.__stub.requests.filter((p) => p === '/api/session').length"
    ) == 1
