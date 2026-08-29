"""The grader — V2's scoring judgment, on its own goal-blind call.

`docs/VALIDITY.md`. The conversation worker used to do two jobs that fight: stay
in character, and annotate the rubric it was being scored against. The conflict
is epistemic rather than mechanical — a partner that can see the checkbox behind
a question stops being a person in a scene and becomes a proctor who wants you to
pass. It steers, it accepts near-misses, and it answers an irrelevant question as
though it were relevant.

So the judgment moves here: a call that holds no character, writes no reply, and
has no reason to be generous. It reads the previous partner turn plus the
learner's turn — exactly the pair that answers both questions it is asked — and
returns a `GraderResult`.

It joins the turn's fan-out rather than waiting on the converser, so nothing the
learner waits on waits on it.

`grade` takes an optional `client` so contract tests can inject a fake; in
production it lazily builds a shared `AsyncAnthropic`.
"""
from typing import Dict, List, Optional, Tuple

import anthropic
from anthropic import AsyncAnthropic
from pydantic import ValidationError

from backend import config
from backend.kb import Scenario
from backend.models import DialogueTurn, GraderResult
from backend.prompts import (
    render_filled_note,
    render_grader_prompt,
    render_review_note,
    render_window_note,
)

# The transcript's speaker labels. Deliberately *not* API roles: the grader is
# neither party. Replaying the partner as `assistant` told the model those lines
# were its own prior output, seated it inside the conversation, and left it
# ending on a user turn it was asked to grade rather than answer.
_ROLE_LABEL = {"user": "learner", "partner": "partner"}

_client: Optional[AsyncAnthropic] = None


class GraderError(Exception):
    """Claude refused, or returned output we could not parse into our schema."""


def _get_client() -> AsyncAnthropic:
    """Lazily build a shared async client — same posture as the other workers."""
    global _client
    if _client is None:
        _client = AsyncAnthropic(
            api_key=config.ANTHROPIC_API_KEY,
            max_retries=config.CLAUDE_MAX_RETRIES,
        )
    return _client


def _as_dict(turn) -> dict:
    """Accept either a `DialogueTurn` or a plain dict (what the client sends)."""
    return turn.model_dump() if isinstance(turn, DialogueTurn) else turn


def _render_transcript(lines: List[Tuple[str, str]], *, complete: bool) -> str:
    """Render the conversation as a numbered record, in one block of text.

    Numbering is not decoration. `slots_filled_previously` asks the model to
    report what *earlier* turns established, and before this the turns had no
    names — the note said "shown above" over an unlabelled thread. A numbered
    line is something a judgment can point at.

    `complete` says whether the lines are the whole conversation or its tail. A
    window is a tail, and telling the model it is reading from the beginning
    would invite it to read turn 7 as turn 1.
    """
    head = (
        "[The conversation, from its first line. Every line is numbered, and "
        "the learner's lines are the ones you judge.]"
        if complete
        else "[The last lines of the conversation. Every line is numbered, and "
        "the learner's lines are the ones you judge.]"
    )
    body = "\n".join(
        f"{n}. {who}: {zh}" for n, (who, zh) in enumerate(lines, start=1)
    )
    return f"{head}\n{body}"


def build_request(
    *,
    scenario: Scenario,
    dialogue: List,
    user_text: str,
    opening_line: Optional[str] = None,
    window: int = 1,
    filled_slots: Optional[List[str]] = None,
    review: bool = False,
) -> Dict:
    """Assemble the exact `messages.parse` kwargs for one grade.

    The `system` block is the authored rubric — goal and slots — and is
    byte-stable within a session, so it caches like the converser's prefix. It is
    a *separate* prefix, not a variant of one: caches are per-model and this call
    runs on `GRADER_MODEL`. Opus 5 caches from 512 tokens where Sonnet 5 needs
    1024, which suits the smaller block.

    **The window (A5).** The grader has one job since A4 — which of the named
    facts did *this* turn establish — and for that it needs the partner's last
    line, the learner's turn, and which slots are already filled. It does not
    need the transcript. Ten turns of history is ten chances to credit something
    from turn 3, and it is Stream B's largest single latency lever. So `messages`
    carries only the tail of `dialogue`: `2*window - 1` entries — the partner's
    last line for the current turn, plus a `(partner, learner)` pair for each
    earlier turn still owed a grade. `filled_slots` stands in for the rest.

    **The encoding.** Whatever the window holds arrives as *one user message*:
    a numbered transcript, `1. partner: … / 2. learner: …`, with the notes after
    it. It used to arrive as replayed `messages` — the partner's lines as
    `assistant`, the learner's as `user`. That is the API's word for the model's
    own prior output, so it seated the grader inside the conversation and left
    it ending on a user turn, which every instinct says to answer rather than
    to judge. The recall numbers had the shape that predicts: the nearest turn
    graded, the ones behind it not, and no wording fixed it because the request
    was arguing with the prose. A record is read; a thread is joined.

    Two things the encoding gives back for free. The API's "`messages[0]` must
    be `user`" rule is gone, so the partner's last line and the opening line
    stop needing bracketed folds. And the turns have numbers, which is what
    `slots_filled_previously` was always asking the model to name.

    **`review` (A6)** is the end-of-session pass: `window` is the whole session,
    so the transcript is the whole conversation — from the partner's opening
    line — and the note asks for a re-reading with hindsight rather than for
    turns a grading failure lost. It is off the turn loop.
    """
    windowed = dialogue[-(2 * window - 1):] if dialogue else []
    lines = [
        (_ROLE_LABEL[_as_dict(t)["role"]], _as_dict(t)["zh"]) for t in windowed
    ]
    lines.append(("learner", user_text))

    # The opening line belongs to the transcript whenever the transcript starts
    # at the start. It costs the learner none of their budget, so it is never in
    # `dialogue` — and the learner's first turn is a response to it and to
    # nothing else. Before the numbered transcript it was folded in only when
    # `dialogue` was empty, so the review, whose window covers the whole
    # session, judged the oldest turn with nothing it was answering.
    complete = len(windowed) >= len(dialogue)
    if opening_line and complete:
        lines.insert(0, ("partner", opening_line))

    # Instruction last. The transcript is the evidence and the note is the job,
    # and the position a model reads last is the one the old encoding was
    # spending on "answer this trailing user turn". The note also says the turns
    # are shown *above*, which is now true.
    note = render_review_note(window) if review else render_window_note(window)
    parts = [
        _render_transcript(lines, complete=complete),
        render_filled_note(filled_slots),
        note,
    ]
    messages = [{"role": "user", "content": "\n\n".join(p for p in parts if p)}]

    return {
        "model": config.GRADER_MODEL,
        # `thinking` is deliberately *omitted*, which on Opus 5 means adaptive
        # thinking is on — the opposite of the hot path's explicit `disabled`.
        # `max_tokens` below is sized to service it: the budget caps thinking
        # plus output together.
        # Real headroom, because thinking is on. `max_tokens` caps thinking plus
        # output, so the conversation worker's 1024 would return
        # `stop_reason: max_tokens` with nothing parsed exactly when the
        # judgment was hardest.
        "max_tokens": config.GRADER_MAX_TOKENS,
        "output_config": {"effort": config.GRADER_EFFORT},
        "system": [
            {
                "type": "text",
                "text": render_grader_prompt(scenario),
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": messages,
        "output_format": GraderResult,
    }


async def grade(
    *,
    scenario: Scenario,
    dialogue: List,
    user_text: str,
    opening_line: Optional[str] = None,
    window: int = 1,
    filled_slots: Optional[List[str]] = None,
    review: bool = False,
    timeout: Optional[float] = None,
    client: Optional[AsyncAnthropic] = None,
) -> Tuple[GraderResult, object]:
    """Judge one learner turn; return `(grade, usage)`.

    `usage` comes back so the turn can report what the *grade* cost. The two
    calls run on different models at different prices, and a turn that reported
    only the converser's would hide the more expensive half.

    Raises `GraderError` on a refusal, a timeout, or any response we cannot parse.
    The caller's job on that error is to echo the previous `SessionState`
    unchanged — no slot credited, no close counted — never to default a grade
    into existence.
    """
    client = client or _get_client()
    request = build_request(
        scenario=scenario, dialogue=dialogue, user_text=user_text,
        opening_line=opening_line, window=window, filled_slots=filled_slots,
        review=review,
    )

    try:
        deadline = config.GRADER_TIMEOUT_S if timeout is None else timeout
        response = await client.messages.parse(**request, timeout=deadline)
    except anthropic.APITimeoutError as exc:
        raise GraderError(f"grader timed out after {deadline:g}s") from exc
    except anthropic.APIError as exc:
        # Everything else the SDK raises — rate limits, 5xx, a dropped
        # connection. `CLAUDE_MAX_RETRIES` is 0, so there is no retry layer
        # absorbing a transient failure first, and an uncaught `APIError` escapes
        # into a stream that has already sent a 200. Wrapped, it degrades the way
        # a timeout does.
        raise GraderError(f"grader call failed: {exc}") from exc
    except ValidationError as exc:
        # A response cut off mid-JSON is validated inside `messages.parse`, so
        # it arrives as an exception rather than as `parsed_output is None`.
        raise GraderError("grader returned unparseable output") from exc

    stop_reason = getattr(response, "stop_reason", None)
    if stop_reason == "refusal":
        raise GraderError("grader refused the turn")
    if stop_reason == "max_tokens":
        # Thinking plus output ran past the budget. Nothing parsed, and a
        # default here would credit or withhold on no evidence at all.
        raise GraderError("grader ran past max_tokens")
    result = response.parsed_output
    if result is None:
        raise GraderError("grader returned unparseable output")
    return result, response.usage
