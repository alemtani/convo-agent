"""Did the partner give away a point the learner had to earn?

The failure this measures was seen twice in real sessions: the learner says
你好, and the partner brightly names the day's best dish. The `recommendation`
slot is a `request` — it is credited on *the learner's ask* — so a partner that
answers it unasked has not been helpful, it has removed a point from the board.
The learner cannot ask for something they have already been told, and repeating
yourself to a conversational partner does not work.

`withholding` in `topic.md` is the authored constraint against exactly this, and
it reaches the partner as a constraint on the persona (`prompts.py`). Nothing
has ever checked that the partner honours it. This is that check.

**Only unasked, unfilled `request` slots are candidates.** A partner answering
what the learner just asked is the scene working, not a violation — so a slot
the grader credited on this turn is out, and so is one already filled. That
composition is why the check lives on the turn runner: it needs the grade and
the reply from the same call.

**The judge is an eval instrument, not a worker.** It lives here rather than in
`backend/` because nothing in the request path may depend on it, and its model
is pinned here for the same reason `replay.py` pins its timeout: an eval whose
instrument changes under it is measuring two things at once.
"""
from typing import Any, Dict, List, Sequence

from pydantic import BaseModel

from backend.kb import Scenario, Slot

# Pinned here, not read from `config`. This judges the partner; if it moved when
# the *partner's* model moved, a regression and an instrument change would
# arrive in the same number.
JUDGE_MODEL = "claude-sonnet-5"

MAX_TOKENS = 512

_SYSTEM = """\
You check one line of dialogue against a rule the scene is supposed to follow.

The scene:
{situation}

What this scene does not hand over unless it is asked:
{withholding}

Below are facts the learner is supposed to have to ask for. For each one, decide
whether the partner's line states it outright — not whether it hints at it,
invites it, or would lead a curious person to it. Only an answer counts.

{slots}

Report the ids of the facts the line states, and nothing else. An empty list is
the common and correct answer.\
"""

_SLOT_LINE = "- {id}: {description}"


class WithholdingError(Exception):
    """The judge answered about something it was not asked about."""


class WithholdingVerdict(BaseModel):
    """Which withheld facts the partner's line stated outright."""

    volunteered: List[str] = []
    rationale: str = ""


def candidates(
    scenario: Scenario, *, filled: Sequence[str], credited: Sequence[str]
) -> List[Slot]:
    """The `request` slots this line could still give away.

    Not `inform` slots: those are facts the *learner* conveys, and a partner
    cannot say them on the learner's behalf. Not a filled slot, which is already
    earned. Not a slot credited on this very turn — the learner asked, and being
    answered is the point.
    """
    spent = set(filled) | set(credited)
    return [
        slot
        for slot in scenario.slots
        if slot.kind == "request" and slot.id not in spent
    ]


def build_request(
    *, scenario: Scenario, reply_zh: str, candidates: Sequence[Slot]
) -> Dict[str, Any]:
    """The judge call: this line, this scene's rule, these facts."""
    return {
        "model": JUDGE_MODEL,
        "max_tokens": MAX_TOKENS,
        "system": [
            {
                "type": "text",
                "text": _SYSTEM.format(
                    situation=scenario.situation,
                    withholding=scenario.withholding or "(nothing stated)",
                    slots="\n".join(
                        _SLOT_LINE.format(id=slot.id, description=slot.description)
                        for slot in candidates
                    ),
                ),
            }
        ],
        "messages": [
            {"role": "user", "content": f"The partner's line:\n{reply_zh}"}
        ],
        "output_format": WithholdingVerdict,
    }


def checked(
    verdict: WithholdingVerdict, *, candidates: Sequence[Slot]
) -> tuple:
    """The verdict's ids, refused if it named one it was not shown.

    The judge is a model, and a hallucinated id would land in the report as a
    violation against a slot nobody asked about — an eval that invents its own
    failures is worse than no eval.
    """
    allowed = {slot.id for slot in candidates}
    unknown = sorted(set(verdict.volunteered) - allowed)
    if unknown:
        raise WithholdingError(
            f"judge named {', '.join(unknown)}, which was not among the "
            f"candidates ({', '.join(sorted(allowed)) or 'none'})"
        )
    return tuple(sorted(set(verdict.volunteered)))


async def judge(
    *, scenario: Scenario, reply_zh: str, candidates: Sequence[Slot], client, timeout=None
) -> tuple:
    """Ask, and return the ids the line gave away. No candidates, no call."""
    if not candidates:
        return ()
    request = build_request(
        scenario=scenario, reply_zh=reply_zh, candidates=candidates
    )
    kwargs = {} if timeout is None else {"timeout": timeout}
    response = await client.messages.parse(**request, **kwargs)
    parsed = response.parsed_output
    if parsed is None:
        raise WithholdingError("judge returned nothing parseable")
    return checked(parsed, candidates=candidates)
