"""M2-C termination tests — pure logic, real red-green TDD.

`backend/termination.py` is the one part of the evaluation component that never
touches a model, a socket, or a file: it takes the session's state, the authored
scenario, and what the worker observed this turn, and returns the next state.
That makes "is the goal met?" a set comparison in Python rather than a judgment
(`docs/SCENARIOS.md`, "The core idea"), and it makes every rule below assertable
without spending a token.

The scenarios here are built in-process rather than loaded from `kb/`, because
these are tests of the *rules*, not of the authored content — a topic edit must
never turn a termination test red.
"""
import logging

import pytest

from backend import kb, termination
from backend.models import SessionState

# The fruit stall from `docs/SCENARIOS.md` — 3 slots, 1 request, max_turns 6.
FRUIT = kb.Scenario(
    situation="You're at a fruit stall. The vendor greets you.",
    goal="Buy three pieces of fruit, and find out what they cost.",
    slots=(
        kb.Slot(id="item", kind="inform", description="Say you want fruit"),
        kb.Slot(id="quantity", kind="inform", description="Say how many — three"),
        kb.Slot(
            id="price",
            kind="request",
            description="Find out what they cost",
        ),
    ),
    max_turns=6,
)


def advance(state=None, *, scenario=FRUIT, filled=(), closed=False, turn=1):
    """Call `advance` with the fixture's defaults; keyword-only like the real one."""
    return termination.advance(
        state if state is not None else SessionState(),
        scenario=scenario,
        slots_filled=list(filled),
        learner_closed=closed,
        turn=turn,
    )


# --- End conditions ------------------------------------------------------


def test_active_while_slots_outstanding():
    state = advance(filled=["item"], turn=1)
    assert state.status == "active"
    assert state.end_reason is None
    assert state.filled_at == {"item": 1}


def test_condition_1_all_slots_filled_completes_as_met():
    state = advance(filled=["item", "quantity", "price"], turn=3)
    assert state.status == "complete"
    assert state.goal_met is True
    assert state.end_reason == "goal"


def test_condition_2_cap_completes_unmet_when_slots_outstanding():
    state = advance(SessionState(filled_at={"item": 1}), filled=[], turn=6)
    assert state.status == "complete"
    assert state.goal_met is False
    assert state.end_reason == "cap"


def test_cap_turn_that_fills_the_last_slot_is_a_pass():
    """Finishing *on* the cap turn is success, not a miss.

    Condition 1 is evaluated before condition 2 precisely so a learner who lands
    the last slot on their final turn is not failed for finishing on time.
    """
    state = advance(
        SessionState(filled_at={"item": 1, "quantity": 2}), filled=["price"], turn=6
    )
    assert state.status == "complete"
    assert state.goal_met is True
    assert state.end_reason == "goal"


def test_condition_3_two_consecutive_closes_completes_unmet():
    state = advance(
        SessionState(filled_at={"item": 1}, consecutive_closes=1), closed=True, turn=4
    )
    assert state.status == "complete"
    assert state.goal_met is False
    assert state.end_reason == "closed"


def test_one_close_does_not_end_the_session():
    state = advance(SessionState(filled_at={"item": 1}), closed=True, turn=2)
    assert state.status == "active"
    assert state.consecutive_closes == 1


def test_close_counter_resets_on_a_non_close_turn():
    """#31 says *consecutive*: a close, then talk, then a close must not end it."""
    state = advance(SessionState(consecutive_closes=1), closed=False, turn=3)
    assert state.consecutive_closes == 0
    state = advance(state, closed=True, turn=4)
    assert state.status == "active"
    assert state.consecutive_closes == 1


def test_a_close_that_also_fills_a_slot_resets_the_counter():
    """再见 plus real content is a learner still working, not one disengaging.

    Matters most on `greetings`, where 再见 is the vocabulary the topic teaches:
    the taught utterance must not double as the terminating one.
    """
    state = advance(
        SessionState(consecutive_closes=1), filled=["item"], closed=True, turn=3
    )
    assert state.consecutive_closes == 0
    assert state.status == "active"


def test_a_second_close_that_fills_the_last_slot_still_passes():
    """The learner says goodbye twice, and the second goodbye completes the goal.

    They pass — and by a route worth pinning, because it is not the obvious one.
    The second close *resets* `consecutive_closes` to 0, since a close carrying
    real content is a learner still working. What makes this a pass is the order
    of the checks: goal completion is evaluated **before** the close counter, so
    the session ends `goal`, not `closed`.

    Reordering those two checks would fail a learner who established everything
    on the way out — a false negative at the last possible moment, with no turn
    left to correct it.
    """
    state = advance(SessionState(consecutive_closes=1), closed=True, turn=2)
    assert state.consecutive_closes == 2 and state.status == "complete"

    # The same second close, but this time it fills the outstanding slot.
    state = advance(
        SessionState(filled_at={"item": 1, "quantity": 1}, consecutive_closes=1),
        filled=["price"],
        closed=True,
        turn=3,
    )
    assert state.status == "complete"
    assert state.goal_met is True
    assert state.end_reason == "goal"
    # Reset, not incremented — and irrelevant, because the goal check ran first.
    assert state.consecutive_closes == 0


def test_one_turn_clear_completes_cleanly():
    """A learner who packs every slot into one utterance passes; nothing raises."""
    state = advance(filled=["item", "quantity", "price"], turn=1)
    assert state.status == "complete"
    assert state.goal_met is True
    assert state.filled_at == {"item": 1, "quantity": 1, "price": 1}


# --- Monotonicity + fill order -------------------------------------------


@pytest.mark.parametrize("prefix_len", [0, 1, 2, 3])
def test_replaying_any_prefix_never_unfills(prefix_len):
    """Property: filled slots are monotone across every prefix of a transcript."""
    transcript = [["item"], [], ["quantity"], ["price"]]
    state = SessionState()
    seen = set()
    for turn, filled in enumerate(transcript[:prefix_len], start=1):
        state = advance(state, filled=filled, turn=turn)
        assert seen <= set(state.filled_at), "a filled slot un-filled"
        seen = set(state.filled_at)


def test_a_refilled_slot_keeps_its_original_turn():
    """`filled_at` is the fill *order* #32 explains from — first wins."""
    state = advance(filled=["item"], turn=1)
    state = advance(state, filled=["item", "quantity"], turn=2)
    assert state.filled_at == {"item": 1, "quantity": 2}


# --- Id validation -------------------------------------------------------


def test_an_invented_slot_id_is_dropped_and_warned(caplog):
    with caplog.at_level(logging.WARNING):
        state = advance(filled=["item", "payment"], turn=1)
    assert state.filled_at == {"item": 1}
    assert "payment" in caplog.text


def test_an_inherited_unknown_id_is_dropped(caplog):
    """Validation covers the state we were handed, not only this turn's fills.

    A stale id from another topic would otherwise ride the inherited half into
    the verdict, where the id→description lookup has nothing to find.
    """
    with caplog.at_level(logging.WARNING):
        state = advance(SessionState(filled_at={"stale": 1, "item": 1}), turn=2)
    assert state.filled_at == {"item": 1}
    assert "stale" in caplog.text


# --- Guards --------------------------------------------------------------


def test_everything_at_once_is_info_not_an_error(caplog):
    with caplog.at_level(logging.INFO):
        advance(filled=["item", "quantity", "price"], turn=1)
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


# --- A topic with no authored scenario -----------------------------------


def test_no_scenario_leaves_the_state_untouched():
    """Topics can land before their scenario does (#29) — sessions just run on."""
    before = SessionState(filled_at={"item": 1})
    after = termination.advance(
        before, scenario=None, slots_filled=["item"], learner_closed=True, turn=99
    )
    assert after == before


def test_no_scenario_has_no_hint():
    assert termination.closing_hint(scenario=None, turn=1) is None


# --- V2: the hint stopped naming the rubric ------------------------------
#
# `pressure_hint` steered the partner toward whichever slot was outstanding.
# A goal-blind partner cannot be told that and must not be — naming the missing
# fact is the rubric, in a stage direction instead of a prompt. The scene
# creates the gap now (`docs/SCENARIOS.md`, "The scene has to create the gap").


def test_there_is_no_hint_before_the_cap():
    """Every ordinary turn now sends none at all, so there is nothing to leak."""
    for turn in range(1, FRUIT.max_turns):
        assert termination.closing_hint(scenario=FRUIT, turn=turn) is None


def test_the_cap_hint_names_no_slot():
    hint = termination.closing_hint(scenario=FRUIT, turn=FRUIT.max_turns)
    for slot in FRUIT.slots:
        assert slot.id not in hint
        assert slot.description not in hint


def test_cap_hint_instructs_answering_as_well_as_closing():
    """The learner who finally speaks on the cap turn must still get an answer —
    a hint that only said "close" would cut them off mid-exchange."""
    hint = termination.closing_hint(scenario=FRUIT, turn=FRUIT.max_turns)
    assert "answer" in hint.lower()
    assert "close" in hint.lower()


def test_the_cap_hint_holds_past_the_cap():
    """A session can run a turn beyond its cap when state says so; the close
    must not vanish because the index went one further."""
    assert termination.closing_hint(scenario=FRUIT, turn=FRUIT.max_turns + 1)


# --- V2: a turn is graded or it is owed -----------------------------------
#
# `UNGRADED_TURNS_SPEC.md`. A grade can fail — a timeout, a 5xx, a dropped
# `state` event — and the learner did the work either way. The window carries it
# forward instead of dropping it.


def _advanced(state, *, slots=(), previously=(), closed=False, turn, graded=True):
    return termination.advance(
        state,
        scenario=FRUIT,
        slots_filled=list(slots),
        slots_filled_previously=list(previously),
        learner_closed=closed,
        turn=turn,
        graded=graded,
    )


def test_a_graded_turn_moves_the_watermark():
    after = _advanced(SessionState(), slots=["item"], turn=1)
    assert after.last_graded_turn == 1


def test_an_ungraded_turn_leaves_the_watermark_behind():
    after = _advanced(SessionState(), turn=1, graded=False)
    assert after.last_graded_turn == 0
    assert after.filled_at == {}


def test_the_window_covers_every_turn_the_grader_missed():
    state = SessionState()
    for turn in (1, 2, 3):
        state = _advanced(state, turn=turn, graded=False)
    # Three turns taken, none graded: the next grade owes all three plus itself.
    assert termination.grading_window(state, turn=4) == 4


def test_the_window_is_one_on_a_healthy_turn():
    state = _advanced(SessionState(), slots=["item"], turn=1)
    assert termination.grading_window(state, turn=2) == 1


def test_a_window_never_grades_nothing():
    """A client echoing a watermark ahead of the turn index is confused or
    lying; grading zero turns would silently stop scoring the session."""
    assert termination.grading_window(SessionState(last_graded_turn=9), turn=2) == 1


def test_late_credit_fills_the_slot_and_counts_toward_the_goal():
    state = _advanced(SessionState(), turn=1, graded=False)
    after = _advanced(state, slots=["price"], previously=["item"], turn=2)
    assert set(after.filled_at) == {"item", "price"}
    assert after.last_graded_turn == 2


def test_late_credit_does_not_reset_the_close_counter():
    """`advance` resets closes on a turn that carried content, so a topic
    teaching 再见 does not have its own vocabulary end the session. A slot
    credited late is not content *this* turn carried — and swallowing the
    learner's goodbye because an old grade finally landed is the bug the
    attributed window exists to prevent."""
    # Turn 1: the learner says goodbye and the grade never lands.
    state = _advanced(SessionState(), closed=True, turn=1, graded=False)
    assert state.consecutive_closes == 1
    # Turn 2: they say goodbye again, and turn 1's slot finally lands with it.
    after = _advanced(state, previously=["item"], closed=True, turn=2)
    assert after.consecutive_closes == 2
    assert "item" in after.filled_at
    assert after.end_reason == "closed"


def test_current_turn_credit_still_resets_the_close_counter():
    """The original rule is untouched for the turn actually being graded."""
    state = SessionState(consecutive_closes=1)
    after = _advanced(state, slots=["item"], closed=True, turn=2)
    assert after.consecutive_closes == 0


def test_a_close_is_counted_even_when_the_grade_never_landed():
    """`learner_closed` is the converser's, and the converser cannot fail
    independently of the reply — so a goodbye lands on time through a total
    grader outage."""
    after = _advanced(SessionState(consecutive_closes=1), closed=True, turn=1,
                      graded=False)
    assert after.consecutive_closes == 2
    assert after.status == "complete"
    assert after.end_reason == "closed"


def test_the_session_ends_when_the_grader_falls_too_far_behind():
    """Not a debt to keep servicing: three failures running is an outage, and
    spending the learner's remaining turns on a session that cannot grade them
    is worse than stopping."""
    state = SessionState()
    for turn in (1, 2, 3):
        state = _advanced(state, turn=turn, graded=False)
    assert state.status == "complete"
    assert state.end_reason == "ungraded"
    assert state.goal_met is False


def test_a_healthy_session_never_trips_the_ungraded_end():
    state = SessionState()
    for turn in (1, 2, 3):
        state = _advanced(state, turn=turn)
    assert state.status == "active"
