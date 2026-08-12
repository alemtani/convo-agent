"""Frontend smoke tests: the playback AudioContext unlock seam.

The seam itself. What M4 built on top of it — autoplay, replay, reveal — is in
`test_reply_audio.py`.

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

import json

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import SESSION_START

pytestmark = pytest.mark.smoke

# One already-restored exchange. Seeded so `dialogue.length !== 0` and the
# opening line (M2-B) never renders — it speaks itself, same as any other
# reply, and this file is about the *seam*, not that one extra attempt. See
# `load()`.
DUMMY_HISTORY = [
    {"role": "user", "zh": "你好", "pinyin": "nǐ hǎo"},
    {"role": "partner", "zh": "你好！", "pinyin": "nǐ hǎo!"},
]


def load(page):
    """Load with a session and a one-turn history already seeded — see
    `tests/smoke/test_reply_audio.py`'s `load()` for why, including why the
    seed is guarded on the session key not already existing."""
    page.add_init_script(
        "(([s, d]) => {"
        "if (localStorage.getItem('convo.session.greetings')) return;"
        "localStorage.setItem('convo.session.greetings', JSON.stringify(s));"
        "localStorage.setItem('convo.dialogue.greetings', JSON.stringify(d)); })"
        f"({json.dumps([SESSION_START, DUMMY_HISTORY])})"
    )
    page.goto("/")


def test_no_audio_context_before_the_first_gesture(page):
    """Constructing one at load would waste a device audio unit every visit.

    Seeded with a non-empty history (`load()`) so the opening line (which
    speaks itself, M2-B) doesn't render at all — this test is about the
    *seam* staying lazy absent any reason to speak, not about that feature.
    """
    load(page)
    assert page.evaluate("window.__audio.state()") is None


def test_the_first_gesture_unlocks_the_context(page):
    """The whole point: one gesture, one context, running for the session."""
    load(page)
    page.locator("#talk").dispatch_event("pointerdown")
    assert page.evaluate("window.__audio.state()") == "running"


def test_the_context_survives_the_gesture_that_made_it(page):
    """M4 plays ~4 s later. A context that only lives for the gesture is useless."""
    load(page)
    page.locator("#talk").dispatch_event("pointerdown")
    page.wait_for_timeout(250)
    assert page.evaluate("window.__audio.state()") == "running"


def test_a_suspension_rearms_the_unlock(page):
    """iOS interruption recovery: suspended ⇒ the next tap must bring it back."""
    load(page)
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
    load(page)
    page.locator("#talk").dispatch_event("pointerdown")
    played = page.evaluate(
        "new Promise((r) => setTimeout(() => r(window.__audio.playTone(880, 50)), 50))"
    )
    assert played is True


def test_play_pcm_renders_int16_samples(page):
    """Int16 in, no caller-side convert.

    Written expecting M4 to return raw PCM; it returns MP3, so the real partner
    audio goes through `playUrl`/`decodeAudioData` instead (see
    `test_reply_audio.py`). Kept because the seam still offers this path and a
    tested capability is cheaper to keep than to re-derive.
    """
    load(page)
    page.locator("#talk").dispatch_event("pointerdown")
    ok = page.evaluate(
        "window.__audio.playPcm(new Int16Array(2400).fill(1000), 24000)"
    )
    assert ok is True


def test_play_pcm_ignores_an_empty_buffer(page):
    """A zero-length createBuffer throws; a silent turn must not break the page."""
    load(page)
    page.locator("#talk").dispatch_event("pointerdown")
    assert page.evaluate("window.__audio.playPcm(new Int16Array(0), 24000)") is False
