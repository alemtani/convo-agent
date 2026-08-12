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

from tests.smoke.conftest import SESSION_START

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
    """Prime localStorage before the page's script runs, then load it.

    Also seeds `convo.session.greetings` (M2-B), so the opening-line bubble
    renders synchronously from localStorage instead of racing a `/api/session`
    fetch — this file's bubble-count assertions need that render to have
    already happened (or not) by the time they run, not "eventually."
    """
    page.add_init_script(
        "(([d, m, s]) => { localStorage.setItem('convo.dialogue.greetings', JSON.stringify(d));"
        "localStorage.setItem('convo.mode', m);"
        "localStorage.setItem('convo.session.greetings', JSON.stringify(s)); })"
        f"({json.dumps([dialogue or [], mode, SESSION_START])})"
    )
    page.goto("/")


def bubbles(page, selector=".bubble:not(.opening)"):
    """Turn bubbles only, by default.

    The seeded opening line is a `.bubble.partner.opening` — scene-setting
    that doesn't consume the turn budget (`docs/SCENARIOS.md`) — so it is
    excluded here the same way it's excluded from the count the learner reads
    as "the conversation so far." Pass an explicit `selector` to see it.
    """
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

    # Both bubbles go up on release: the echo holds the learner's place while
    # STT runs, so the thread never shows a lone loading bubble with nothing
    # above it. Order must still read user-then-partner.
    expect(bubbles(page)).to_have_count(2)
    expect(bubbles(page).nth(0)).to_have_class(re.compile(r"\buser\b"))
    expect(bubbles(page).nth(0).locator(".syl")).to_have_count(4)   # 2 syllables × 2 rows
    expect(bubbles(page).nth(1)).to_have_class(re.compile(r"\bpartner\b"))
    # The reply is audio-only by default (M4), so its controls — not its text —
    # are what say it landed. What the controls do is `test_reply_audio.py`.
    expect(bubbles(page).nth(1).locator(".controls")).to_have_count(1)

    # The timings line is appended, while the transcript is *inserted* above the
    # pending bubble — so it has to end up last, not stranded between the turns.
    expect(page.locator("#thread > *").last).to_have_class(re.compile(r"\btimings\b"))


def _speak(page):
    """Press, capture a little audio, release — leaving the turn in flight."""
    page.hover("#talk")
    page.mouse.down()
    page.wait_for_function("window.__convo.framesCaptured > 0")
    page.wait_for_timeout(200)
    page.mouse.up()
    page.wait_for_function("window.__convo.lastSampleCount !== null")


def test_transcript_renders_while_the_reply_is_still_pending(page):
    """The whole point of staging, asserted where a user would see it.

    The unit suite proves the *server* flushes the transcript early. This proves
    the page paints it early — that it reads the body as a stream instead of
    awaiting it whole. A page that awaited `resp.text()` would sit here with two
    pending bubbles until the `done` line, and pass every count-based assertion
    in this file.
    """
    seed(page)
    page.evaluate("window.__stub.manual = true")
    _speak(page)

    # Nothing released yet: the echo holds the learner's place while STT runs,
    # and it is the *only* bubble. The partner isn't thinking about anything yet
    # — a reply placeholder above a message that doesn't exist reads backwards.
    expect(bubbles(page, ".bubble.user.pending")).to_have_count(1)
    expect(bubbles(page, ".bubble.partner:not(.opening)")).to_have_count(0)

    page.evaluate("window.__stub.releaseNext()")   # transcript

    # The learner's words are on screen while the reply is still being written.
    user = bubbles(page, ".bubble.user")
    expect(user).to_contain_text("你好")
    expect(bubbles(page, ".bubble.user.pending")).to_have_count(0)
    expect(bubbles(page, ".bubble.partner.pending")).to_have_count(1)
    # Unscored so far — the underlines belong to the score event, not this one.
    expect(user.locator(".syl")).to_have_count(0)


def test_the_partner_bubble_waits_for_the_transcript(page):
    """Order in the thread, not just presence: you, then the partner.

    The bubbles used to go up together on mic release, which meant watching a
    reply be composed before your own sentence had appeared. Now the partner's
    placeholder is created by the `transcript` event, so the thread never shows
    the partner reacting to a message the learner can't see.
    """
    seed(page)
    page.evaluate("window.__stub.manual = true")
    _speak(page)

    expect(bubbles(page)).to_have_count(1)
    expect(bubbles(page).nth(0)).to_have_class(re.compile(r"\buser\b"))

    page.evaluate("window.__stub.releaseNext()")   # transcript

    expect(bubbles(page)).to_have_count(2)
    expect(bubbles(page).nth(1)).to_have_class(re.compile(r"\bpartner\b"))


def test_a_pre_transcript_failure_leaves_no_partner_bubble(page):
    """A 502 from STT means the partner never saw the turn.

    Everything that maps to a status is settled before the first byte, so there
    is no transcript and no reply — inventing a failed partner bubble would
    claim the partner tried and couldn't.
    """
    seed(page)
    page.evaluate("window.__stub.status['/api/turn'] = 502")
    _speak(page)

    failed = bubbles(page, ".bubble.user.failed")
    expect(failed).to_have_count(1)
    expect(failed).to_contain_text("error 502")
    expect(bubbles(page, ".bubble.partner:not(.opening)")).to_have_count(0)


def test_scores_repaint_the_transcript_bubble_instead_of_adding_one(page):
    """Tone underlines land on the bubble already up, seconds before the reply.

    The race worth pinning: `score` arrives while the transcript bubble exists,
    so a renderer that appends instead of replacing leaves the learner with two
    copies of their own sentence.
    """
    seed(page)
    page.evaluate("window.__stub.manual = true")
    _speak(page)

    page.evaluate("window.__stub.releaseNext()")   # transcript
    expect(bubbles(page, ".bubble.user")).to_have_count(1)
    handle = bubbles(page, ".bubble.user").element_handle()

    page.evaluate("window.__stub.releaseNext()")   # score

    # Same bubble, now scored — one node, not a second one appended. The scores
    # must be inside the node captured *before* they arrived; a renderer that
    # replaced the bubble would leave this handle detached and empty.
    expect(bubbles(page, ".bubble.user")).to_have_count(1)
    assert len(handle.query_selector_all(".syl")) == 4, \
        "a new node replaced the transcript bubble instead of repainting it"
    # And the reply still hasn't arrived — the underlines did not wait on it.
    expect(bubbles(page, ".bubble.partner.pending")).to_have_count(1)


def test_timings_line_waits_for_the_done_event(page):
    """`reply` is not terminal, so the timings line hangs off `done`.

    Rendering it on `reply` would strand it mid-thread whenever `score` is the
    slower branch and arrives after the reply.
    """
    seed(page)
    page.evaluate("window.__stub.manual = true")
    _speak(page)

    for _ in range(3):   # transcript, score, reply
        page.evaluate("window.__stub.releaseNext()")

    # The seeded opening line also renders through `renderReply` (M4), so it
    # carries `.controls` too — scope past it to the turn's own reply.
    expect(bubbles(page, ".bubble.partner:not(.opening) .controls")).to_have_count(1)
    expect(page.locator("#thread .timings")).to_have_count(0)

    page.evaluate("window.__stub.releaseNext()")   # done
    expect(page.locator("#thread .timings")).to_have_count(1)
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
    expect(bubbles(page, ".bubble.partner:not(.opening)")).to_have_count(1)
    # The reply is audio-only by default (M4), so reveal it to check *which*
    # reply this node ended up holding — otherwise any repaint would pass.
    # Scoped past `.opening`: the seeded opening line has its own reveal button.
    page.click(".bubble.partner:not(.opening) button.reveal")
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


# --- M2-B: session start (scenario card + opening line) --------------------


def test_session_start_renders_the_scenario_card_and_opening_line(page):
    """A fresh thread pins the card and shows the opening line — before any
    turn has happened, and without it counting as one."""
    seed(page)

    expect(page.locator("#scenario-card")).to_be_visible()
    expect(page.locator("#scenario-situation")).to_have_text(
        SESSION_START["scenario_card"]["situation"]
    )
    expect(page.locator("#scenario-goal")).to_have_text(SESSION_START["scenario_card"]["goal"])

    opening = bubbles(page, ".bubble.partner.opening")
    expect(opening).to_have_count(1)
    # Audio-only by default (M4), same as any other partner reply: reveal it
    # to check which line this bubble actually holds.
    expect(opening.locator(".controls")).to_have_count(1)
    opening.locator("button.reveal").click()
    expect(opening).to_contain_text("你好！你叫什么名字？")
    # Scene-setting, not a turn: excluded from the count a learner would read
    # as "the conversation so far."
    expect(bubbles(page)).to_have_count(0)


def test_session_start_failure_shows_a_status_message_and_does_not_crash(page):
    """A 502 from `/api/session` (a sketch-worker refusal or timeout) must not
    leave a blank thread with no explanation — the learner can still talk, but
    they need to be told nothing's wrong with *them*."""
    page.add_init_script(
        "(() => { const t = setInterval(() => {"
        "  if (!window.__stub) return; clearInterval(t);"
        "  window.__stub.status['/api/session'] = 502;"
        "}, 0); })()"
    )
    page.goto("/")

    expect(page.locator("#status")).to_have_text("couldn't load the scenario — you can still talk")
    expect(page.locator("#scenario-card")).not_to_be_visible()
    expect(bubbles(page, ".bubble.opening")).to_have_count(0)


def test_reload_with_history_does_not_repeat_the_opening_line(page):
    """A running conversation must not re-open the scene on every reload —
    the opening line only ever leads an *empty* thread."""
    seed(page, dialogue=HISTORY)

    expect(bubbles(page, ".bubble.opening")).to_have_count(0)
    expect(bubbles(page)).to_have_count(len(HISTORY))


def test_new_conversation_clears_the_card_and_fetches_a_fresh_session(page):
    """`reset` starts a new scene, which needs its own opening line and card —
    not the one still sitting in localStorage from the last conversation."""
    seed(page, dialogue=HISTORY)
    expect(bubbles(page)).to_have_count(len(HISTORY))

    page.click("#reset")

    expect(bubbles(page)).to_have_count(0)
    expect(bubbles(page, ".bubble.opening")).to_have_count(1)
    expect(page.locator("#scenario-card")).to_be_visible()


def test_double_clicking_reset_does_not_duplicate_the_opening_line(page):
    """Two clicks close enough together must not fire two concurrent
    `POST /api/session` calls — each would independently see an empty thread
    and append its own opening bubble, breaking "exactly one opening line."
    Dispatched from one `evaluate` so both handlers fire before either fetch
    can resolve, which is the race a real double-click can hit."""
    seed(page, dialogue=HISTORY)
    expect(bubbles(page)).to_have_count(len(HISTORY))

    page.evaluate(
        "document.getElementById('reset').click();"
        "document.getElementById('reset').click();"
    )

    expect(bubbles(page, ".bubble.opening")).to_have_count(1)
