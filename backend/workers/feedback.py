"""The verdict worker — explains a computed outcome, once, at `complete` (M2-D).

The payoff call: *"at the end, I'm told whether I achieved the goal, and if not,
what I should have asked instead."*

What makes it trustworthy is what it is **not** asked. `goal_met` and `missing`
are recomputed here from the KB and the session's filled set, then stated to the
model as fact; the worker only explains them and demonstrates the missing ask. A
judge asked "did they succeed?" grades generously, our partner replies and our
grader come from the same model family, and prompting a judge out of a known bias
is documented not to work — so the decision is removed rather than argued with
(`docs/SCENARIOS.md`, "Runtime: three tiers").

It sits **beside** the turn loop, not inside it: one call per session, after the
last turn has already been rendered. Nothing the learner is waiting on gets
slower because this exists. It is also deliberately uncached — a cache write
costs 1.25x and break-even is two reads, and there is never a second read.
"""
import functools
import logging
from typing import List, Optional, Set

import anthropic
from anthropic import AsyncAnthropic
from pydantic import ValidationError

from backend import config, kb, termination
from backend.models import (
    MissingSlot,
    SessionState,
    VerdictCard,
    VerdictRequest,
    VerdictResult,
)
from backend.pinyin import annotate_hanzi
from backend.workers import grader
from backend.prompts import render_verdict_prompt

logger = logging.getLogger(__name__)

_client: Optional[AsyncAnthropic] = None


class FeedbackError(Exception):
    """Claude refused, timed out, or returned output we could not parse."""


def _get_client() -> AsyncAnthropic:
    """Same construction as the other workers — notably `max_retries=0`.

    The SDK default of 2 would turn `VERDICT_TIMEOUT_S` into three of itself,
    and the learner is watching a pending card for the whole of it.
    """
    global _client
    if _client is None:
        _client = AsyncAnthropic(
            api_key=config.ANTHROPIC_API_KEY,
            max_retries=config.CLAUDE_MAX_RETRIES,
        )
    return _client


@functools.lru_cache(maxsize=None)
def in_band_characters(topic_id: str) -> Set[str]:
    """Every 汉字 the learner can be expected to read for this topic.

    The topic's `target_vocab` and `proper_names`, as characters. This is what
    makes "the model answer stays in band" a real assertion rather than an eval:
    a demonstration built from words the learner has never seen teaches nothing,
    and that is checkable without judging the wording.

    Deliberately character-level, not word-level. The exchange composes taught
    words into new sentences — which is the point — so the check has to be about
    what they can *read*, not about matching whole vocabulary entries.
    """
    topic = kb.load_topic(topic_id)
    return {ch for word in topic.target_vocab + topic.proper_names for ch in word}


def _missing_slots(scenario: Optional[kb.Scenario], state: SessionState) -> List[MissingSlot]:
    """The authored slots this session never established.

    Computed from the KB, so an id the client invented is simply not one of the
    scenario's slots and cannot appear here — nor break the lookup by being
    absent from it.
    """
    if scenario is None:
        return []
    return [
        MissingSlot(id=slot.id, description=slot.description)
        for slot in scenario.slots
        if slot.id not in state.filled
    ]


async def settle_outstanding_grades(
    req: VerdictRequest, *, scenario, grader_client=None
) -> SessionState:
    """One last grader pass for turns whose grade never landed, before the card.

    The verdict is computed from state, so an unsettled debt means telling the
    learner they missed something they established — the false negative
    `ACCESSIBILITY.md` exists to prevent, at the moment it is most visible.

    This is the "session-end pass" `VALIDITY.md` contemplated and dropped, and
    it is **recovery, not the rule**. The rule is still one grade per turn,
    credited on the ask; nothing here re-opens the AND rule or re-audits turns
    that were graded. It judges only what was never judged.

    Returns the submitted state untouched when nothing is owed — which is every
    healthy session, and so costs nothing — or when the pass itself fails. A
    broken grader at the end of a broken session is not a reason to invent a
    grade.
    """
    turns_taken = len(req.dialogue) // 2
    if (
        scenario is None
        # No watermark reported is not a debt — see `SessionState`.
        or req.state.last_graded_turn is None
        or req.state.last_graded_turn >= turns_taken
    ):
        return req.state
    window = turns_taken - req.state.last_graded_turn
    logger.warning(
        "settling %d ungraded turn(s) before the verdict (last graded %d of %d)",
        window, req.state.last_graded_turn, turns_taken,
    )

    # **The grader must be handed the same shape a live turn hands it**: the
    # history *up to* the learner's last turn, and that turn separately. At
    # verdict time `dialogue` holds everything, including the partner's final
    # reply — so the split is at the last `user` entry, not at the end. Passing
    # the whole history plus the last learner turn would show that turn twice,
    # after a partner line it actually preceded.
    last_user = next(
        (i for i in range(len(req.dialogue) - 1, -1, -1)
         if req.dialogue[i].role == "user"),
        None,
    )
    if last_user is None:
        return req.state
    try:
        grade, _usage = await grader.grade(
            scenario=scenario,
            dialogue=req.dialogue[:last_user],
            user_text=req.dialogue[last_user].zh,
            opening_line=req.opening_line.zh if req.opening_line else None,
            window=window,
            filled_slots=sorted(req.state.filled),
            timeout=config.VERDICT_RECOVERY_TIMEOUT_S,
            # Its own client. The verdict's is forwarded nowhere: two workers
            # sharing one injected fake lets a test fabricate a grade it never
            # meant to stub.
            client=grader_client,
        )
    except grader.GraderError as exc:
        logger.warning("final grading pass failed: %s", exc)
        return req.state

    # **Not `termination.advance`.** The session has already ended, and advance
    # recomputes `status`/`end_reason` from scratch — it would overwrite the real
    # ending (`stuck`, `closed`, `ungraded`) with whatever a fresh evaluation of
    # a finished session produces. Only the credit is new, so only `filled_at`
    # moves. Whether that credit completes the goal is decided downstream, where
    # `missing` is recomputed from the KB anyway.
    known = {slot.id for slot in scenario.slots}
    filled_at = dict(req.state.filled_at)
    for sid in list(grade.slots_filled) + list(grade.slots_filled_previously):
        if sid in known and sid not in filled_at:
            filled_at[sid] = turns_taken
    return req.state.model_copy(
        update={"filled_at": filled_at, "last_graded_turn": turns_taken}
    )


def _consistent_end_reason(
    state: SessionState, *, scenario: Optional[kb.Scenario], missing, turns_taken
) -> Optional[str]:
    """`end_reason` if it squares with what we can check, else `None`.

    It is the one field on `SessionState` we cannot recompute — it needs the
    transition history a stateless server doesn't keep — so it is trusted, but
    only where a check exists. Two implications are checkable: "goal" means
    nothing outstanding, and "cap" means the budget was actually reached.
    "closed" is unverifiable and passes through.

    Dropping to `None` beats repeating a wrong reason back: on `greetings`, where
    再见 is taught vocabulary, "you ended the conversation early" is the most
    confusing thing we could say to someone who didn't.
    """
    reason = state.end_reason
    if not missing and reason not in (None, "goal"):
        # The final pass filled the last slot. Whatever button the learner
        # pressed, someone who established everything did not leave unfinished
        # and did not run out of road — `stuck` least of all (`A1`).
        logger.info("end_reason %r superseded by a late pass completing the goal", reason)
        return "goal"
    if reason == "goal" and missing:
        logger.warning("end_reason 'goal' with %d slots missing — dropping", len(missing))
        return None
    if reason == "cap" and scenario is not None and turns_taken < scenario.max_turns:
        logger.warning(
            "end_reason 'cap' at turn %d of %d — dropping", turns_taken, scenario.max_turns
        )
        return None
    return reason


def build_request(*, kb_block: str, dialogue, prompt: str) -> dict:
    """Assemble the one-off `messages.parse` kwargs for a session's verdict.

    No `cache_control` breakpoint anywhere: this runs once per session, so there
    is no second call to read a cached prefix back.
    """
    transcript = "\n".join(
        f"{'Learner' if turn.role == 'user' else 'Partner'}: {turn.zh}"
        for turn in dialogue
    )
    return {
        "model": config.VERDICT_MODEL,
        # Room for the paragraph of English plus a four-line exchange this
        # worker always produced, *and* the thinking that precedes it —
        # `max_tokens` caps thinking plus output together. 2048 was sized for
        # the output alone and left no headroom; this is that budget plus
        # explicit room for deliberation.
        "max_tokens": 4096,
        # Adaptive, explicit, so the choice reads at the call site rather
        # than depending on omission. Sonnet 5 thinks when the field is
        # left off (`workers/conversation.py`); this call wants that on.
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": config.VERDICT_EFFORT},
        "system": [{"type": "text", "text": prompt}],
        "messages": [
            {
                "role": "user",
                "content": (
                    f"{kb_block}\n\n# TRANSCRIPT\n\n{transcript}"
                    if transcript
                    else kb_block
                ),
            }
        ],
        "output_format": VerdictResult,
    }


async def verdict(
    req: VerdictRequest, *, client: Optional[AsyncAnthropic] = None
) -> VerdictCard:
    """Explain how one finished session went; return the card.

    Raises `kb.KbError` for an unknown topic and `FeedbackError` on a refusal,
    timeout, or unparseable reply — the same failure shapes as the other workers,
    so the route maps them the same way.
    """
    scenario = kb.load_scenario(req.topic_id)
    kb_block = kb.load_kb_block(req.topic_id)

    state = await settle_outstanding_grades(req, scenario=scenario)
    missing = _missing_slots(scenario, state)
    goal_met = not missing
    turns_taken = len(req.dialogue) // 2
    end_reason = _consistent_end_reason(
        state, scenario=scenario, missing=missing, turns_taken=turns_taken
    )
    # What the recovery pass could not settle. Both a failed pass and a session
    # stopped for repeated grading failures land here.
    unchecked = (
        max(0, turns_taken - state.last_graded_turn)
        if state.last_graded_turn is not None
        else 0
    )

    request = build_request(
        kb_block=kb_block,
        dialogue=req.dialogue,
        prompt=render_verdict_prompt(
            goal_met=goal_met,
            missing=missing,
            turns_taken=turns_taken,
            end_reason=end_reason,
            notes=req.notes,
            unchecked_turns=unchecked,
        ),
    )

    client = client or _get_client()
    try:
        response = await client.messages.parse(
            **request, timeout=config.VERDICT_TIMEOUT_S
        )
    except anthropic.APITimeoutError as exc:
        raise FeedbackError(
            f"verdict worker timed out after {config.VERDICT_TIMEOUT_S:g}s"
        ) from exc
    except ValidationError as exc:
        # `messages.parse` validates inside the SDK, so a response that is cut
        # off mid-JSON surfaces here rather than as `parsed_output is None`.
        # Uncaught it is a 500 and the learner reads "Internal Server Error" on
        # the card; as a `FeedbackError` it is the 502 the client already
        # degrades from, with a Try again button.
        raise FeedbackError("verdict worker returned unparseable output") from exc

    stop_reason = getattr(response, "stop_reason", None)
    if stop_reason == "refusal":
        raise FeedbackError("verdict worker refused the session")
    if stop_reason == "max_tokens":
        raise FeedbackError("verdict worker's answer was too long to finish")
    result = response.parsed_output
    if result is None:
        raise FeedbackError("verdict worker returned unparseable output")

    return VerdictCard(
        # The server's numbers, not the model's — it was never asked for them.
        goal_met=goal_met,
        end_reason=end_reason,
        missing=missing,
        turns_taken=turns_taken,
        # The explanation quotes the learner's own phrases back at them, and a
        # band-1 learner cannot read bare 汉字 — which is the whole audience for
        # this card. Romanization is the server's job here as everywhere else.
        explanation=annotate_hanzi(result.explanation),
        # A demonstration on a session the learner already passed would be
        # noise; the prompt asks for none, and this makes it structural.
        model_exchange=[] if goal_met else result.model_exchange,
    )
