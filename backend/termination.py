"""When a session ends, and what the partner is pushed toward until it does.

The M2-C half of the evaluation component (`docs/SCENARIOS.md`, "Runtime: three
tiers"). Two pure functions over values — no model call, no I/O, no clock — so
"did the learner achieve the goal?" is a set comparison rather than a judgment.
That is the whole point of the slot reframe: a judge asked *"did they succeed?"*
grades generously, and our partner and any grader would come from the same model
family.

The tracker (which facts this turn established) is folded into the conversation
worker's structured output; this module consumes what it reports and owns
everything downstream of it. The verdict worker (`workers/feedback.py`) is handed
the outcome computed here and only ever explains it.

Kept out of `orchestrator.py` on purpose: this is the one part of the turn with
no dependency on FastAPI, Anthropic, or Azure, and it should stay testable
without importing any of them.
"""
import logging
from typing import Dict, List, Optional, Set

from backend.kb import Scenario
from backend.models import SessionState

logger = logging.getLogger(__name__)

# How many times in a row the learner can close the scene before we let them
# out. Two, because one 再见 is often just the vocabulary the topic teaches
# (`greetings` authors it into `target_vocab`), while two in a row is a person
# who has left the conversation. Holding them in the scene to satisfy a turn
# budget is worse than letting them fail and read the verdict.
CLOSES_TO_END = 2


def advance(
    state: SessionState,
    *,
    scenario: Optional[Scenario],
    slots_filled: List[str],
    learner_closed: bool,
    turn: int,
) -> SessionState:
    """Fold one turn's observations into the session state.

    `turn` is the 1-based index of the turn just taken, derived by the caller
    from the submitted history length — there is no server-side counter to
    desync. `slots_filled` and `learner_closed` come from the worker's
    annotation; everything else here is arithmetic.

    Returns a new state; never mutates the one passed in. A topic with no
    authored scenario (topics can land before their scenario does, #29) returns
    the state untouched, so those sessions simply run on as they do today.
    """
    if scenario is None:
        return state

    known = {slot.id for slot in scenario.slots}
    filled_at = _validated(state.filled_at, known)
    newly = [sid for sid in _validated_ids(slots_filled, known) if sid not in filled_at]
    filled_at.update({sid: turn for sid in newly})

    _check_guards(scenario, filled_at, newly, turn)

    # A close that carries real content is a learner still working, not one
    # disengaging — so it takes the reset branch. Otherwise, on a topic that
    # teaches 再见, the taught utterance would double as the terminating one.
    closes = state.consecutive_closes + 1 if learner_closed and not newly else 0

    missing = {slot.id for slot in scenario.slots} - set(filled_at)
    status, goal_met, end_reason = "active", False, None
    if not missing:
        status, goal_met, end_reason = "complete", True, "goal"
    elif turn >= scenario.max_turns:
        status, end_reason = "complete", "cap"
    elif closes >= CLOSES_TO_END:
        status, end_reason = "complete", "closed"

    return state.model_copy(
        update={
            "filled_at": filled_at,
            "consecutive_closes": closes,
            "status": status,
            "goal_met": goal_met,
            "end_reason": end_reason,
        }
    )


def pressure_hint(
    state: SessionState, *, scenario: Optional[Scenario], turn: int
) -> Optional[str]:
    """The stage direction for this turn, or `None` if there is nothing to say.

    A state rule, not a turn schedule (`docs/SCENARIOS.md`, "Pressure"): the
    partner is steered toward whichever fact is still outstanding, and never
    toward goodbye — you steer toward the *goal*, and the moment the goal is met
    the session is over anyway.

    The caller injects this after the `cache_control` breakpoint, so nothing
    here touches the frozen prefix. Written as stage direction rather than as a
    question the partner asks: a fruit vendor does not say 你还要问什么吗？, and
    an out-of-character prompt would leak that a criterion is outstanding.
    """
    if scenario is None:
        return None
    missing = [slot for slot in scenario.slots if slot.id not in state.filled]
    if not missing:
        return None

    target = missing[0]
    lines = [
        f"The learner has not yet established: {target.id} — {target.description}. "
        "Leave the scene unresolved at that point so the gap is where they need "
        "the words. Stay fully in character; never ask what they want to ask, "
        "and never state or hint at what is outstanding.",
        "Do not volunteer the answer to anything they have not asked you.",
    ]
    if turn >= scenario.max_turns:
        # Additive, not a replacement. A request slot fills only when the
        # partner answers, so a hint that merely said "close" would fail the
        # learner who finally asked on their last turn.
        lines.append(
            "This is the final turn: answer the learner's turn normally, then "
            "close the scene in character."
        )
    return " ".join(lines)


def _validated_ids(ids: List[str], known: Set[str]) -> List[str]:
    """Drop ids the scenario never authored, warning once per unknown id."""
    unknown = [sid for sid in ids if sid not in known]
    if unknown:
        logger.warning("dropping unknown slot ids from tracker output: %s", unknown)
    return [sid for sid in ids if sid in known]


def _validated(filled_at: Dict[str, int], known: Set[str]) -> Dict[str, int]:
    """Same check for the state we were handed, not just this turn's fills.

    The state arrives from the client, so a stale id (a topic switch, an old
    `localStorage` record) would otherwise ride the inherited half straight into
    the verdict, where the id→description lookup has nothing to find.
    """
    unknown = [sid for sid in filled_at if sid not in known]
    if unknown:
        logger.warning("dropping unknown slot ids from submitted state: %s", unknown)
    return {sid: turn for sid, turn in filled_at.items() if sid in known}


def _check_guards(
    scenario: Scenario, filled_at: Dict[str, int], newly: List[str], turn: int
) -> None:
    """Sanity-check the tracker's output. Logs only — never raises, never drops.

    A `depends_on` violation (`price` credited before `item`) is nonsense under
    any pacing and is the sharp hallucination signal. It is still recorded: the
    learner should not fail because our extractor misfired, and the log is for
    us. Filling everything on turn 1 is *not* a fault — a learner who packs an
    utterance demonstrated more competence, not less — so it is info.
    """
    for slot in scenario.slots:
        if slot.id in newly:
            unmet = [dep for dep in slot.depends_on if dep not in filled_at]
            if unmet:
                logger.error(
                    "slot %r filled on turn %d before its dependencies %s — "
                    "likely tracker hallucination",
                    slot.id,
                    turn,
                    unmet,
                )
    if turn == 1 and len(newly) == scenario.n_slots and scenario.n_slots > 1:
        logger.info(
            "all %d slots filled on turn 1 — a packed utterance, not a fault",
            scenario.n_slots,
        )
