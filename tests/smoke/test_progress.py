"""Frontend smoke tests: the progress HUD on the scenario card (A2).

The unit suite proves `ScenarioCard` now carries `n_slots` and `max_turns`.
What it cannot see is whether the learner actually gets a "2 of 3" / "3 of 7"
during the session — the whole of note 9 in `docs/ACCESSIBILITY.md`. Failing
with no sense of progress is what the first real session found.

Counts, not names: the HUD paints how many slots have filled and how many
turns have been used. Slot ids and descriptions stay off the card.
"""

import json
import re

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import SESSION_START
from tests.smoke.test_verdict import send, stub_turn

pytestmark = pytest.mark.smoke

ONE_TURN = [
    {"role": "user", "zh": "我叫小明", "pinyin": "wǒ jiào xiǎo míng"},
    {"role": "partner", "zh": "你好！", "pinyin": "nǐ hǎo!"},
]

NEXT_SESSION = {
    "topic_id": "family",
    "display_name": "Family (家人)",
    "scenario_card": {
        "situation": "You are meeting a friend's parent.",
        "goal": "Ask how many people are in their family.",
        "n_slots": 3,
        "max_turns": 7,
    },
    "opening_line": {"zh": "你好！", "pinyin": "nǐ hǎo!"},
    "sketch": "Warm.",
}


def seed(page, *, dialogue=None, state=None, session=SESSION_START):
    payload = json.dumps([dialogue or [], session, state])
    page.add_init_script(
        "(([d, s, st]) => {"
        "  localStorage.setItem('convo.dialogue', JSON.stringify(d));"
        "  localStorage.setItem('convo.mode', 'text');"
        "  localStorage.setItem('convo.session', JSON.stringify(s));"
        "  if (st) localStorage.setItem('convo.state', JSON.stringify(st));"
        f"}})({payload})"
    )
    page.goto("/")


def test_a_fresh_session_starts_the_hud_at_zero(page):
    """Before any turn, the card already says how big the goal is and how
    long the session can run — the opening line does not spend a turn."""
    seed(page)

    expect(page.locator("#scenario-slots")).to_have_text("0 of 3")
    expect(page.locator("#scenario-turns")).to_have_text("0 of 7")
    expect(page.locator("#scenario-progress")).to_be_visible()


def test_restored_progress_paints_from_filled_at_and_dialogue(page):
    """Reload must not reset the HUD: the client already holds both halves."""
    seed(
        page,
        dialogue=ONE_TURN,
        state={"filled_at": {"self_name": 1}, "status": "active"},
    )

    expect(page.locator("#scenario-slots")).to_have_text("1 of 3")
    expect(page.locator("#scenario-turns")).to_have_text("1 of 7")


def test_a_turn_that_fills_a_slot_advances_both_counts(page):
    seed(page)
    stub_turn(
        page,
        state={"filled_at": {"self_name": 1}, "status": "active",
               "goal_met": False, "end_reason": None},
    )
    send(page)

    expect(page.locator("#scenario-slots")).to_have_text("1 of 3")
    expect(page.locator("#scenario-turns")).to_have_text("1 of 7")


def test_the_hud_does_not_name_the_slots(page):
    """A count is still not the slots. `self_name` is the filled id; it must
    not appear on the card, or the HUD has become the rubric."""
    seed(
        page,
        dialogue=ONE_TURN,
        state={"filled_at": {"self_name": 1}, "status": "active"},
    )

    expect(page.locator("#scenario-card")).not_to_contain_text("self_name")


def test_an_old_cached_session_without_counts_does_not_print_undefined(page):
    """A session cached before this shipped has situation + goal only."""
    old = {
        "topic_id": "greetings",
        "display_name": "Greetings (你好)",
        "scenario_card": {
            "situation": SESSION_START["scenario_card"]["situation"],
            "goal": SESSION_START["scenario_card"]["goal"],
        },
        "opening_line": SESSION_START["opening_line"],
        "sketch": SESSION_START["sketch"],
    }
    seed(page, session=old)

    expect(page.locator("#scenario-progress")).to_be_hidden()
    expect(page.locator("#scenario-card")).not_to_contain_text("undefined")
    expect(page.locator("#scenario-situation")).to_have_text(
        SESSION_START["scenario_card"]["situation"]
    )


def test_a_restart_does_not_keep_the_old_counts_while_loading(page):
    """The HUD is live progress, not a leftover from the scene being left."""
    seed(
        page,
        dialogue=ONE_TURN,
        state={"filled_at": {"self_name": 1}, "status": "active"},
    )
    expect(page.locator("#scenario-slots")).to_have_text("1 of 3")

    page.evaluate(
        "([next]) => {"
        "  window.__stub.manual = true;"
        "  window.__stub.responses['/api/session'] = next;"
        "}",
        [NEXT_SESSION],
    )
    page.click("#more > summary")
    page.click("#reset")

    expect(page.locator("#scenario-card")).to_have_class(re.compile(r"\bloading\b"))
    expect(page.locator("#scenario-progress")).to_be_hidden()

    page.evaluate("window.__stub.release()")
    expect(page.locator("#scenario-slots")).to_have_text("0 of 3")
    expect(page.locator("#scenario-turns")).to_have_text("0 of 7")
