"""Frontend smoke tests: the thread behaves like a chat app.

Every check here is a race the unit suite can't see and "click around and look"
is worst at catching — frames lost before the button turns red, a bubble
duplicated instead of upgraded, a smooth scroll that lands in the wrong place.
They are deterministic (fake mic, stubbed fetch, no timers) and so run in CI,
but they need a browser, so the default `pytest -q` deselects the `smoke`
marker; run them with `pytest -m smoke`.
"""

import json
import re

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.smoke

HISTORY = [
    turn
    for i in range(15)
    for turn in (
        {"role": "user", "zh": f"你好{i}", "pinyin": f"nǐ hǎo {i}"},
        {"role": "partner", "zh": f"你好！第{i}句。", "pinyin": f"nǐ hǎo! dì {i} jù."},
    )
]


def seed(page, *, dialogue=None, mode="speak"):
    """Prime localStorage before the page's script runs, then load it."""
    page.add_init_script(
        "(([d, m]) => { localStorage.setItem('convo.dialogue.greetings', JSON.stringify(d));"
        "localStorage.setItem('convo.mode', m); })"
        f"({json.dumps([dialogue or [], mode])})"
    )
    page.goto("/")


def bubbles(page, selector=".bubble"):
    return page.locator("#thread " + selector)


# --- Mic ------------------------------------------------------------------


def test_press_captures_frames_from_frame_zero(page):
    """The button goes red because frames arrived, not because it was pressed.

    `framesAtFlip` is set inside the frame handler, so a non-null value proves
    the flip was driven by audio; the old code flipped before the worklet was
    wired and could drop everything captured up to that point.
    """
    seed(page)
    page.hover("#talk")
    page.mouse.down()
    expect(page.locator("#talk")).to_have_class(re.compile(r"\brecording\b"))

    assert page.evaluate("window.__convo.framesAtFlip") is not None
    assert page.evaluate("window.__convo.framesCaptured") >= 1

    page.wait_for_timeout(200)
    page.mouse.up()
    page.wait_for_function("window.__convo.lastSampleCount !== null")
    assert page.evaluate("window.__convo.lastSampleCount") > 0
    expect(page.locator("#status")).to_have_text("")

    # The spoken path has no optimistic echo — the transcript only exists once
    # the server answers — so it paints the pending reply first and inserts the
    # transcript *above* it. Order in the thread must still read user-then-partner.
    expect(bubbles(page)).to_have_count(2)
    expect(bubbles(page).nth(0)).to_have_class(re.compile(r"\buser\b"))
    expect(bubbles(page).nth(0).locator(".syl")).to_have_count(4)   # 2 syllables × 2 rows
    expect(bubbles(page).nth(1)).to_have_class(re.compile(r"\bpartner\b"))
    expect(bubbles(page).nth(1)).to_contain_text("你好")

    # The timings line is appended, while the transcript is *inserted* above the
    # pending bubble — so it has to end up last, not stranded between the turns.
    expect(page.locator("#thread > *").last).to_have_class(re.compile(r"\btimings\b"))


def test_graph_stays_warm_between_presses(page):
    """Release parks the mic graph instead of tearing it down.

    The lag this suite exists to prevent is `getUserMedia` + `AudioContext` +
    `addModule()` being redone on every press.
    """
    seed(page)
    page.hover("#talk")
    page.mouse.down()
    page.wait_for_function("window.__convo.framesCaptured > 0")
    page.mouse.up()
    page.wait_for_function("window.__convo.lastSampleCount !== null")

    assert page.evaluate("window.__convo.warm()") is True

    # A second press reuses it and still captures from the first frame.
    page.evaluate("window.__convo.lastSampleCount = null")
    page.mouse.down()
    page.wait_for_function("window.__convo.framesCaptured > 0")
    assert page.evaluate("window.__convo.framesAtFlip") == 1
    page.mouse.up()
    page.wait_for_function("window.__convo.lastSampleCount !== null")
    assert page.evaluate("window.__convo.lastSampleCount") > 0


# --- Pending bubble -------------------------------------------------------


def test_pending_bubble_exists_between_submit_and_response(page):
    """A turn in flight shows a partner-side placeholder, replaced in place."""
    seed(page, mode="text")
    page.evaluate("window.__stub.manual = true")
    page.fill("#text-input", "ni hao")
    page.click("#send")

    pending = bubbles(page, ".bubble.partner.pending")
    expect(pending).to_have_count(1)
    expect(pending.locator(".dot")).to_have_count(3)
    handle = pending.element_handle()

    page.evaluate("window.__stub.release()")
    expect(bubbles(page, ".bubble.partner.pending")).to_have_count(0)
    # Same node, now carrying the reply: replaced in place, not re-added.
    expect(bubbles(page, ".bubble.partner")).to_have_count(1)
    assert "很高兴认识你" in handle.inner_text()


def test_failed_turn_leaves_an_error_bubble_and_keeps_the_text(page):
    seed(page, mode="text")
    page.evaluate("window.__stub.status['/api/turn/text'] = 502")
    page.fill("#text-input", "ni hao")
    page.click("#send")

    failed = bubbles(page, ".bubble.partner.failed")
    expect(failed).to_have_count(1)
    expect(failed).to_contain_text("error 502")
    # The optimistic echo is withdrawn and the box keeps the text, so retrying
    # can't leave two copies of the same message in the thread.
    expect(bubbles(page, ".bubble.user")).to_have_count(0)
    expect(page.locator("#text-input")).to_have_value("ni hao")


# --- Scrolling ------------------------------------------------------------


def test_reload_with_history_lands_at_the_bottom_without_animating(page):
    """Restoring a thread jumps to the newest message within a single frame.

    A smooth scroll would still be in flight one frame in — that's the
    discriminator, and N racing per-bubble smooth scrolls used to leave the
    thread parked mid-history.
    """
    seed(page, dialogue=HISTORY)
    expect(bubbles(page)).to_have_count(len(HISTORY))

    at_bottom = page.evaluate(
        """() => new Promise((resolve) => requestAnimationFrame(() => {
             const t = document.getElementById('thread');
             resolve({ gap: t.scrollHeight - t.scrollTop - t.clientHeight,
                       scrollable: t.scrollHeight > t.clientHeight });
           }))"""
    )
    assert at_bottom["scrollable"], "seed too short to scroll; the test proves nothing"
    assert at_bottom["gap"] <= 2, f"not at bottom after one frame: {at_bottom}"


def test_scrolled_up_user_is_not_yanked_down(page):
    seed(page, dialogue=HISTORY, mode="text")
    expect(bubbles(page)).to_have_count(len(HISTORY))
    page.evaluate("document.getElementById('thread').scrollTop = 0")

    before = page.evaluate("document.getElementById('thread').scrollTop")
    page.fill("#text-input", "ni hao")
    page.click("#send")
    expect(bubbles(page)).to_have_count(len(HISTORY) + 2)
    page.wait_for_timeout(400)   # long enough for a smooth scroll to have run

    assert page.evaluate("document.getElementById('thread').scrollTop") == before


def test_new_message_follows_when_already_at_the_bottom(page):
    seed(page, dialogue=HISTORY, mode="text")
    expect(bubbles(page)).to_have_count(len(HISTORY))
    page.fill("#text-input", "ni hao")
    page.click("#send")
    expect(bubbles(page)).to_have_count(len(HISTORY) + 2)

    # Polled rather than `wait_for_function` so a failure reports the gap it got
    # stuck at — a bare timeout says nothing about whether the follow scroll
    # never started or just landed short.
    gap = None
    for _ in range(20):
        gap = page.evaluate(
            """() => { const t = document.getElementById('thread');
                       return t.scrollHeight - t.scrollTop - t.clientHeight; }"""
        )
        if gap <= 2:
            break
        page.wait_for_timeout(100)
    assert gap is not None and gap <= 2, f"follow scroll landed {gap}px short of the bottom"


# --- Optimistic echo ------------------------------------------------------


def test_optimistic_bubble_upgrades_in_place(page):
    """Your own message appears on send and becomes 汉字 — one bubble, not two."""
    seed(page, mode="text")
    page.evaluate("window.__stub.manual = true")
    page.fill("#text-input", "ni hao")
    page.click("#send")

    echo = bubbles(page, ".bubble.user")
    expect(echo).to_have_count(1)
    expect(echo).to_have_class(re.compile(r"\boptimistic\b"))
    expect(echo).to_contain_text("ni hao")
    handle = echo.element_handle()

    page.evaluate("window.__stub.release()")
    expect(bubbles(page, ".bubble.user")).to_have_count(1)
    expect(bubbles(page, ".bubble.user.optimistic")).to_have_count(0)
    assert handle.inner_text().startswith("你好"), "a new node replaced the echo"
    expect(page.locator("#text-input")).to_have_value("")
