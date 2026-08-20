"""The end of a session, in a browser (M2-C + M2-D).

The unit suite proves the server computes the right outcome. What it cannot see
is the shape of the ending the learner actually meets: whether the mic really
goes dead, whether anything explains why, whether a locked phone comes back to
the same card or a blank finished app, and whether the one button on that card
strands them.

Every failure mode pinned here is one where the app still "works" — no error, no
exception — and the learner is simply stuck.
"""

import json

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import SESSION_START, TURN_TEXT

pytestmark = pytest.mark.smoke

COMPLETE = {
    "filled_at": {"self_name": 1, "partner_name": 1, "wellbeing": 1},
    "consecutive_closes": 0,
    "status": "complete",
    "goal_met": True,
    "end_reason": "goal",
}

ACTIVE = {"filled_at": {"self_name": 1}, "consecutive_closes": 0, "status": "active",
          "goal_met": False, "end_reason": None}

CARD = {
    "goal_met": True,
    "end_reason": "goal",
    "missing": [],
    "explanation": "You said your name and asked for theirs.",
    "model_exchange": [],
    "turns_taken": 3,
}

UNMET_CARD = {
    "goal_met": False,
    "end_reason": "cap",
    "missing": [{"id": "partner_name", "description": "Find out their name"}],
    "explanation": "You never found out their name.",
    "model_exchange": [
        {"zh": "你叫什么名字？", "pinyin": "nǐ jiào shénme míngzi?",
         "english": "What is your name?"},
    ],
    "turns_taken": 7,
}


def seed(page, *, state=None, verdict=None, dialogue=None):
    """Prime localStorage before the page script runs, then load it."""
    payload = json.dumps([dialogue or [], SESSION_START, state, verdict])
    page.add_init_script(
        "(([d, s, st, v]) => {"
        "  localStorage.setItem('convo.dialogue', JSON.stringify(d));"
        "  localStorage.setItem('convo.mode', 'text');"
        "  localStorage.setItem('convo.session', JSON.stringify(s));"
        "  if (st) localStorage.setItem('convo.state', JSON.stringify(st));"
        "  if (v) localStorage.setItem('convo.verdict', JSON.stringify(v));"
        f"}})({payload})"
    )
    page.goto("/")


def stub_turn(page, *, state, card=CARD):
    """Point the canned text turn at a given end state, and can the verdict."""
    page.evaluate(
        "([turn, card]) => {"
        "  window.__stub.responses['/api/turn/text'] = turn;"
        "  window.__stub.responses['/api/verdict'] = card;"
        "}",
        [{**TURN_TEXT, "state": state}, card],
    )


def send(page, text="ni hao"):
    page.fill("#text-input", text)
    page.click("#send")


def card(page):
    return page.locator("#thread .verdict")


# --- The session ends -----------------------------------------------------


def test_completing_the_goal_disables_both_controls(page):
    """And stays disabled — the turn's own `finally` runs right after.

    That `finally` used to re-arm the controls unconditionally, so the mic would
    come back to life one tick after the session ended.
    """
    seed(page)
    stub_turn(page, state=COMPLETE)
    send(page)

    expect(card(page)).to_have_count(1)
    expect(page.locator("#talk")).to_be_disabled()
    expect(page.locator("#text-input")).to_be_disabled()
    expect(page.locator("#send")).to_be_disabled()


def test_an_active_session_leaves_the_controls_alone(page):
    seed(page)
    stub_turn(page, state=ACTIVE)
    send(page)

    expect(page.locator("#thread .bubble.partner")).to_have_count(2)
    expect(card(page)).to_have_count(0)
    expect(page.locator("#talk")).to_be_enabled()


def test_the_card_is_pending_before_it_is_written(page):
    """The mic dies the instant the session ends. Something has to say why.

    Without this the learner sits in front of a dead app for the length of an
    uncached Sonnet call, with no HUD to have warned them a cap existed.
    """
    seed(page)
    stub_turn(page, state=COMPLETE)
    page.evaluate("window.__stub.manual = true")
    send(page)

    # Release the turn but not the verdict: this is the window in between.
    page.evaluate("window.__stub.releaseNext()")
    expect(card(page)).to_have_class("verdict pending")
    expect(card(page)).to_contain_text("writing your feedback")

    page.evaluate("window.__stub.release()")
    expect(card(page)).to_contain_text("You did it")
    expect(card(page)).not_to_have_class("verdict pending")


def test_the_unmet_card_shows_what_to_say_instead(page):
    seed(page)
    stub_turn(page, state={**COMPLETE, "goal_met": False}, card=UNMET_CARD)
    send(page)

    expect(card(page)).to_contain_text("Not quite")
    expect(card(page)).to_contain_text("Find out their name")
    expect(card(page).locator(".line .zh")).to_have_text("你叫什么名字？")


# --- Coming back to a finished session ------------------------------------


def test_a_reload_shows_the_same_card_without_re_billing_it(page):
    """A phone locks and reloads. The card must be the one they were reading.

    Re-fetching would spend a second uncached Sonnet call and reword the
    feedback under someone mid-sentence.
    """
    seed(page, state=COMPLETE, verdict=CARD)
    expect(card(page)).to_contain_text("You did it")
    assert "/api/verdict" not in page.evaluate("window.__stub.requests")
    expect(page.locator("#talk")).to_be_disabled()


def test_a_reload_mid_session_keeps_the_controls_live(page):
    seed(page, state=ACTIVE)
    expect(page.locator("#talk")).to_be_enabled()
    expect(card(page)).to_have_count(0)


def test_a_failed_verdict_offers_a_retry(page):
    """One blip must not permanently cost the learner the only teaching here."""
    # Installed as an init script, not after load: the fetch fires during the
    # page's own startup, so an override applied afterwards would arrive late.
    page.add_init_script("window.__stub.status['/api/verdict'] = 502;")
    seed(page, state=COMPLETE)

    expect(card(page)).to_contain_text("Couldn't load your feedback")
    retry = card(page).locator("[data-action='retry-verdict']")
    expect(retry).to_have_count(1)

    page.evaluate(
        "([c]) => { window.__stub.status['/api/verdict'] = 200;"
        "           window.__stub.responses['/api/verdict'] = c; }",
        [CARD],
    )
    retry.click()
    expect(card(page)).to_contain_text("You did it")


# --- Starting again -------------------------------------------------------


def test_a_new_session_re_enables_the_controls(page):
    """The card's only button must not hand back a dead app.

    Left uncleared, the completed `status` survives the reset: mic disabled on a
    scene nobody has spoken into, every turn 409ing, and the one recovery
    affordance is the button that just did this.
    """
    seed(page, state=COMPLETE, verdict=CARD)
    expect(page.locator("#talk")).to_be_disabled()

    page.click("#thread .verdict [data-action='new-session']")

    expect(page.locator("#talk")).to_be_enabled()
    expect(page.locator("#text-input")).to_be_enabled()
    assert page.evaluate("localStorage.getItem('convo.state')") is None


def test_a_new_session_clears_the_card(page):
    """A fresh conversation starts clean.

    An earlier version kept the previous card pinned above the new scene. On a
    phone it just crowded the top of the thread with the last session's result.
    """
    seed(page, state=COMPLETE, verdict=UNMET_CARD)
    expect(card(page)).to_have_count(1)

    page.click("#thread .verdict [data-action='new-session']")

    expect(card(page)).to_have_count(0)
    assert page.evaluate("localStorage.getItem('convo.verdict')") is None


# --- The server disagrees --------------------------------------------------


def test_a_409_shows_the_card_not_a_raw_http_error(page):
    """Reachable from a second tab or a restored one.

    The learner would otherwise read `error 409:` where their feedback belongs.
    """
    seed(page, state=ACTIVE)
    page.evaluate(
        "([c]) => { window.__stub.status['/api/turn/text'] = 409;"
        "           window.__stub.responses['/api/verdict'] = c; }",
        [CARD],
    )
    send(page)

    expect(card(page)).to_contain_text("You did it")
    expect(page.locator("#thread .bubble.failed")).to_have_count(0)
    expect(page.locator("#talk")).to_be_disabled()


def test_state_is_stamped_with_its_topic(page):
    """So a stale store from another topic can be recognised as stale (#29)."""
    seed(page)
    stub_turn(page, state=ACTIVE)
    send(page)

    page.wait_for_function("localStorage.getItem('convo.state') !== null")
    stored = json.loads(page.evaluate("localStorage.getItem('convo.state')"))
    assert stored["topic_id"] == "greetings"


# --- A1: the learner's own exit (#66) -------------------------------------
#
# The session the app was built for ended with the learner stumped, and the
# only two moves available were to guess or to say goodbye twice. These pin the
# third one: stop now, and read what you were missing.


STUCK_CARD = {
    "goal_met": False,
    "end_reason": "stuck",
    "missing": [{"id": "partner_name", "description": "Find out their name"}],
    "explanation": "You got your own name across.",
    "model_exchange": [],
    "turns_taken": 2,
}


def stuck_btn(page):
    return page.locator("#stuck-btn")


def open_more(page):
    """"Try something else" lives one tap down, in the overflow menu."""
    page.click("#more > summary")


def test_the_bail_out_is_dead_before_the_learner_has_said_anything(page):
    """A verdict over a transcript with no learner turn in it teaches nothing.

    The opening partner line is on screen at this point, so this also pins that
    the gate reads `dialogue` — which holds only real turns — rather than what
    the thread happens to be showing.
    """
    seed(page, state=ACTIVE)

    expect(stuck_btn(page)).to_be_disabled()


def test_the_bail_out_is_dead_while_a_turn_is_in_flight(page):
    """The other false condition, and the one that is easy to miss.

    `adoptState` replaces the whole state object when the reply lands, so a
    bail-out pressed mid-turn would be silently overwritten: the learner taps
    it, the partner answers, and the exit they asked for disappears.
    """
    seed(page, state=ACTIVE)
    page.evaluate("window.__stub.manual = true")
    stub_turn(page, state=ACTIVE)

    send(page)
    expect(stuck_btn(page)).to_be_disabled()

    page.evaluate("window.__stub.release()")
    expect(stuck_btn(page)).to_be_enabled()


def test_the_bail_out_ends_the_session_and_says_why(page):
    seed(page, state=ACTIVE)
    stub_turn(page, state=ACTIVE, card=STUCK_CARD)

    send(page)
    expect(stuck_btn(page)).to_be_enabled()
    stuck_btn(page).click()

    expect(card(page)).to_be_visible()
    # The reason rides the request the learner never sees: `POST /api/verdict`
    # is what carries it, because there is no turn to carry it on.
    sent = page.evaluate(
        "window.__stub.sent.filter((r) => r.path === '/api/verdict')"
    )
    assert sent[0]["body"]["state"]["end_reason"] == "stuck"
    # And the mic closes behind it, like any other ending.
    expect(page.locator("#text-input")).to_be_disabled()


def test_try_this_again_replays_the_same_scenario(page):
    """The 409 regression this chunk exists to avoid.

    Both buttons write `status: complete`, and the server refuses a turn against
    a completed session. The load-time guard only drops state when the *topic*
    changes, so a same-topic restart that skipped the clear would sail past it
    and 409 on its first turn — with the only recovery being the button that
    just did it.
    """
    seed(page, state=ACTIVE)
    stub_turn(page, state=ACTIVE, card=STUCK_CARD)
    send(page)
    stuck_btn(page).click()
    expect(card(page)).to_be_visible()

    page.click("[data-action='retry-topic']")

    # The same scenario, asked for by the id the server issued us.
    sent = page.evaluate(
        "window.__stub.sent.filter((r) => r.path === '/api/session')"
    )
    assert sent[-1]["body"] == {"topic_id": "greetings"}
    # And the session is live again: cleared state, no card, controls open.
    expect(card(page)).to_have_count(0)
    expect(page.locator("#text-input")).to_be_enabled()
    # Nothing terminal survives the restart. The store is cleared outright and
    # only rewritten when the next turn reports state, so "no stored state" and
    # "stored state says active" are both fine; a stored `complete` is the 409.
    stored = page.evaluate("localStorage.getItem('convo.state')")
    assert stored is None or json.loads(stored)["status"] == "active"


def test_try_something_else_lets_the_server_draw(page):
    seed(page, state=ACTIVE)
    stub_turn(page, state=ACTIVE, card=STUCK_CARD)
    send(page)
    stuck_btn(page).click()
    expect(card(page)).to_be_visible()

    page.click("[data-action='new-session']")

    sent = page.evaluate(
        "window.__stub.sent.filter((r) => r.path === '/api/session')"
    )
    assert sent[-1]["body"] is None


def test_a_late_verdict_cannot_land_on_the_next_session(page):
    """The card is fetched for the session that ended, not for the thread that
    happens to be on screen when it arrives."""
    seed(page, state=ACTIVE)
    stub_turn(page, state=ACTIVE, card=STUCK_CARD)
    send(page)

    page.evaluate("window.__stub.manual = true")
    stuck_btn(page).click()
    # The verdict is in flight. Leave for a different scenario, then let it land.
    open_more(page)
    page.click("#reset")
    page.evaluate("window.__stub.release()")

    expect(card(page)).to_have_count(0)
    assert page.evaluate("localStorage.getItem('convo.verdict')") is None
