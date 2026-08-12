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

from backend import config, kb
from backend.models import (
    MissingSlot,
    SessionState,
    VerdictCard,
    VerdictRequest,
    VerdictResult,
)
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
        "model": config.CONVERSATION_MODEL,
        "max_tokens": 1024,
        # Unlike the turn loop, this one call is allowed to think: it is off the
        # hot path, it reads a whole transcript, and composing an in-band
        # exchange from a constrained vocabulary is the kind of task where the
        # extra tokens buy something. Bounded by `VERDICT_TIMEOUT_S` regardless.
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

    missing = _missing_slots(scenario, req.state)
    goal_met = not missing
    turns_taken = len(req.dialogue) // 2
    end_reason = _consistent_end_reason(
        req.state, scenario=scenario, missing=missing, turns_taken=turns_taken
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

    if getattr(response, "stop_reason", None) == "refusal":
        raise FeedbackError("verdict worker refused the session")
    result = response.parsed_output
    if result is None:
        raise FeedbackError("verdict worker returned unparseable output")

    return VerdictCard(
        # The server's numbers, not the model's — it was never asked for them.
        goal_met=goal_met,
        end_reason=end_reason,
        missing=missing,
        turns_taken=turns_taken,
        explanation=result.explanation,
        # A demonstration on a session the learner already passed would be
        # noise; the prompt asks for none, and this makes it structural.
        model_exchange=[] if goal_met else result.model_exchange,
    )
