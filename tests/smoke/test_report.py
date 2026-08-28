"""Filing a report, and contesting a grade, in a browser (A7).

The backend suite proves the route builds the right issue. What it cannot see is
whether the learner can *reach* it: whether the flag is on the turn they are
looking at, whether the turn it names is the turn they tapped, and whether the
sheet sends the session that is actually on screen.

The one that matters most is the turn number. A contest that files the wrong
turn is worse than no contest at all — it manufactures a labelled disagreement
about a turn nobody disputed, and `gold.json` is the last place that should
receive one.
"""

import json

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import SESSION_START, TURN_TEXT

pytestmark = pytest.mark.smoke

DIALOGUE = [
    {"role": "user", "zh": "你好，我叫小明。", "pinyin": "nǐ hǎo, wǒ jiào Xiǎomíng."},
    {"role": "partner", "zh": "我叫小王。", "pinyin": "wǒ jiào Xiǎowáng."},
    {"role": "user", "zh": "我很好，你呢？", "pinyin": "wǒ hěn hǎo, nǐ ne?"},
    {"role": "partner", "zh": "我也很好。", "pinyin": "wǒ yě hěn hǎo."},
]

STATE = {"filled_at": {"self_name": 1}, "consecutive_closes": 0,
         "status": "active", "goal_met": False, "end_reason": None}

UNMET_CARD = {
    "goal_met": False,
    "end_reason": "cap",
    "missing": [{"id": "wellbeing", "description": "Find out how they have been lately"}],
    "explanation": "You never asked how they had been.",
    "model_exchange": [],
    "turns_taken": 3,
}

FILED = {"url": "https://github.com/alemtani/convo-agent/issues/12", "number": 12}


def seed(page, *, dialogue=None, state=None, verdict=None):
    # `dialogue or DIALOGUE` would silently substitute the default for an
    # empty thread, which is exactly the case one test needs.
    if dialogue is None:
        dialogue = DIALOGUE
    payload = json.dumps([dialogue, SESSION_START, state or STATE, verdict])
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
    page.evaluate(
        "(filed) => { window.__stub.responses['/api/feedback'] = filed; }", FILED
    )


def sent(page):
    """The `/api/feedback` body the page posted, or None."""
    bodies = page.evaluate(
        "() => window.__stub.sent.filter((s) => s.path === '/api/feedback')"
        "                        .map((s) => s.body)"
    )
    return bodies[-1] if bodies else None


def file_report(page, message="the counter never moved"):
    page.fill("#feedback-message", message)
    page.click("#feedback-send")
    page.wait_for_function("window.__stub.requests.includes('/api/feedback')")


# --- Reaching it ------------------------------------------------------------


def test_every_learner_bubble_carries_a_flag(page):
    """Restored turns included — a reload does not end a disagreement."""
    seed(page)
    expect(page.locator("#thread .bubble.user .contest")).to_have_count(2)
    expect(page.locator("#thread .bubble.partner .contest")).to_have_count(0)


def test_a_live_turn_gets_its_flag_too(page):
    """The bubble is repainted twice on a typed turn — optimistic echo, then the
    worker's reading. The flag has to survive the upgrade, not just the create.
    """
    seed(page, dialogue=[])
    page.evaluate(
        "(turn) => { window.__stub.responses['/api/turn/text'] = turn; }",
        {**TURN_TEXT, "state": STATE},
    )
    page.fill("#text-input", "ni hao")
    page.click("#send")

    expect(page.locator("#thread .bubble.user .contest")).to_have_count(1)
    expect(page.locator("#thread .bubble.user .zh")).to_have_text("你好")


def test_the_flag_files_the_turn_it_sits_on(page):
    """The second bubble is turn 2. Off-by-one here files a disagreement about
    a turn the learner never disputed."""
    seed(page)
    page.locator("#thread .bubble.user .contest").nth(1).click()
    expect(page.locator("#feedback-hint")).to_contain_text("Turn 2")

    file_report(page, "你呢 asks it back")

    body = sent(page)
    assert body["kind"] == "contest"
    assert body["turn"] == 2
    assert body["topic_id"] == "greetings"
    assert body["message"] == "你呢 asks it back"
    assert body["dialogue"] == [
        {"role": "user", "zh": "你好，我叫小明。"},
        {"role": "partner", "zh": "我叫小王。"},
        {"role": "user", "zh": "我很好，你呢？"},
        {"role": "partner", "zh": "我也很好。"},
    ]
    assert body["state"]["filled_at"] == {"self_name": 1}
    assert body["sketch"] == SESSION_START["sketch"]


def test_mid_session_contest_names_no_slot(page):
    """Slot ids are never rendered during a session, so there is nothing to
    pick — the turn alone is the claim, and the server accepts that."""
    seed(page)
    page.locator("#thread .bubble.user .contest").first.click()
    expect(page.locator("#feedback-slot-row")).to_be_hidden()

    file_report(page)
    assert sent(page)["slot_id"] is None


def test_the_card_offers_the_slots_it_named(page):
    """After the card, `missing` carries real ids — so the claim can be exact."""
    complete = {**STATE, "status": "complete", "end_reason": "cap"}
    seed(page, state=complete, verdict=UNMET_CARD)

    page.click("#thread .verdict [data-action='contest-verdict']")
    expect(page.locator("#feedback-slot-row")).to_be_visible()
    page.select_option("#feedback-slot", "wellbeing")

    file_report(page, "I asked with 你呢")
    body = sent(page)
    assert body["kind"] == "contest"
    assert body["slot_id"] == "wellbeing"
    # No turn was tapped, so the contest lands on the last one taken.
    assert body["turn"] == 2


def test_the_menu_files_a_bug(page):
    seed(page)
    page.click("#more summary")
    page.click("#report")

    expect(page.locator("#feedback-slot-row")).to_be_hidden()
    file_report(page, "the mic button stopped working")

    body = sent(page)
    assert body["kind"] == "bug"
    assert body["turn"] is None


# --- What comes back --------------------------------------------------------


def test_a_filed_report_shows_its_issue(page):
    """The repo is public and the report is the learner's own. A filed issue
    they cannot see is indistinguishable from a swallowed one."""
    seed(page)
    page.click("#more summary")
    page.click("#report")
    file_report(page)

    expect(page.locator("#feedback-status a")).to_have_text("#12")
    expect(page.locator("#feedback-status a")).to_have_attribute("href", FILED["url"])
    expect(page.locator("#feedback-send")).to_be_hidden()


def test_an_empty_report_is_not_sent(page):
    seed(page)
    page.click("#more summary")
    page.click("#report")
    page.click("#feedback-send")

    expect(page.locator("#feedback-status")).to_contain_text("Say what went wrong")
    assert sent(page) is None


def test_a_rate_limited_report_says_come_back(page):
    """Each refusal reads differently, because each means something different
    to the learner. "Something went wrong" is the same as no recourse."""
    seed(page)
    page.evaluate("() => { window.__stub.status['/api/feedback'] = 429; }")
    page.click("#more summary")
    page.click("#report")
    file_report(page)

    expect(page.locator("#feedback-status")).to_contain_text("Too many reports")
    expect(page.locator("#feedback-send")).to_be_visible()


def test_an_unconfigured_server_says_so(page):
    seed(page)
    page.evaluate("() => { window.__stub.status['/api/feedback'] = 503; }")
    page.click("#more summary")
    page.click("#report")
    file_report(page)

    expect(page.locator("#feedback-status")).to_contain_text("can't file reports")


def test_cancel_closes_without_filing(page):
    seed(page)
    page.click("#more summary")
    page.click("#report")
    page.fill("#feedback-message", "never mind")
    page.click("#feedback-cancel")

    expect(page.locator("#feedback")).to_be_hidden()
    assert sent(page) is None
