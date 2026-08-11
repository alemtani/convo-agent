"""Frontend smoke tests: the playback AudioContext unlock seam.

M4 plays partner audio from a callback that fires seconds after the
push-to-talk release, with no user gesture on the stack. Safari only lets an
AudioContext leave "suspended" if `resume()` runs inside a live gesture, so a
context created at that moment starts locked and never makes a sound. The page
therefore unlocks one context on the first interaction and reuses it.

Chromium cannot reproduce Safari's *policy* — the suite launches it with
`--autoplay-policy=no-user-gesture-required` so the mic tests aren't at the
mercy of it — and there is no way to assert "a sound came out" in a headless
browser. So these tests pin the part that is real logic rather than platform
behaviour: the context is lazy, a gesture unlocks it, and a suspension re-arms
the unlock so the next tap recovers.

That last one is the one worth having. iOS re-suspends a running context on
interruption — an incoming call, the silent switch, backgrounding — and a seam
that unlocks once and never re-arms goes permanently quiet in a way that only
shows up on a real phone, minutes into a real session.
"""

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.smoke


def test_no_audio_context_before_the_first_gesture(page):
    """Constructing one at load would waste a device audio unit every visit."""
    page.goto("/")
    assert page.evaluate("window.__audio.state()") is None


def test_the_first_gesture_unlocks_the_context(page):
    """The whole point: one gesture, one context, running for the session."""
    page.goto("/")
    page.locator("#talk").dispatch_event("pointerdown")
    assert page.evaluate("window.__audio.state()") == "running"


def test_the_context_survives_the_gesture_that_made_it(page):
    """M4 plays ~4 s later. A context that only lives for the gesture is useless."""
    page.goto("/")
    page.locator("#talk").dispatch_event("pointerdown")
    page.wait_for_timeout(250)
    assert page.evaluate("window.__audio.state()") == "running"


def test_a_suspension_rearms_the_unlock(page):
    """iOS interruption recovery: suspended ⇒ the next tap must bring it back."""
    page.goto("/")
    page.locator("#talk").dispatch_event("pointerdown")
    assert page.evaluate("window.__audio.state()") == "running"

    # Stand in for the interruption iOS delivers on a call or the silent switch.
    page.evaluate("window.__audio._suspend()")
    assert page.evaluate("window.__audio.state()") == "suspended"

    # A fresh gesture anywhere on the page, not just the mic button, recovers it.
    page.locator("body").dispatch_event("pointerdown")
    page.wait_for_function("window.__audio.state() === 'running'")


def test_tone_plays_from_a_non_gesture_callback(page):
    """The acceptance criterion, minus the ears: a timer-fired tone is accepted."""
    page.goto("/")
    page.locator("#talk").dispatch_event("pointerdown")
    played = page.evaluate(
        "new Promise((r) => setTimeout(() => r(window.__audio.playTone(880, 50)), 50))"
    )
    assert played is True


def test_play_pcm_renders_int16_samples(page):
    """M4 hands back Int16 PCM; the seam must accept it without a caller convert."""
    page.goto("/")
    page.locator("#talk").dispatch_event("pointerdown")
    ok = page.evaluate(
        "window.__audio.playPcm(new Int16Array(2400).fill(1000), 24000)"
    )
    assert ok is True


def test_play_pcm_ignores_an_empty_buffer(page):
    """A zero-length createBuffer throws; a silent turn must not break the page."""
    page.goto("/")
    page.locator("#talk").dispatch_event("pointerdown")
    assert page.evaluate("window.__audio.playPcm(new Int16Array(0), 24000)") is False
