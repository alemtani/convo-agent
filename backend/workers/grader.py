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

_ROLE_MAP = {"user": "user", "partner": "assistant"}

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


def _prefix_text(message: Dict, text: str) -> None:
    """Prepend a note to a `user` message's content, in place.

    Content is a bare string or a list of blocks. A string joins with a newline
    — the shape the turn-1 opener has always used, so a turn that gains no window
    or filled note keeps a byte-identical request and its cassette. A list (a
    breakpoint already sits inside it) gets a block prepended. Either way the new
    text lands first, so context reads before the words it is context for.
    """
    body = message["content"]
    message["content"] = (
        [{"type": "text", "text": text}] + body
        if isinstance(body, list)
        else f"{text}\n{body}"
    )


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

    The tail opens on the partner's last line, an assistant turn, but the
    Messages API requires `messages[0]` to be `user`. That leading line folds
    into the learner turn it precedes — the same shape turn 1 already uses for
    the opening line.

    **`review` (A6)** is the end-of-session pass: `window` is the whole session,
    so the tail is the whole conversation, and the note asks for a re-reading
    with hindsight rather than for turns a grading failure lost. It is the only
    caller that sends the transcript back, and it is off the turn loop.
    """
    windowed = dialogue[-(2 * window - 1):] if dialogue else []
    messages = [
        {"role": _ROLE_MAP[_as_dict(t)["role"]], "content": _as_dict(t)["zh"]}
        for t in windowed
    ]
    messages.append({"role": "user", "content": user_text})

    # Volatile, so they ride the final user message rather than the frozen
    # prefix: whether earlier turns are owed depends on which grades failed, and
    # what is already filled changes every turn — the cached system block must
    # stay byte-identical across the session. The window note goes on last, so it
    # reads first, ahead of the filled-slot context.
    filled_note = render_filled_note(filled_slots)
    if filled_note:
        _prefix_text(messages[-1], filled_note)
    note = render_review_note(window) if review else render_window_note(window)
    if note:
        _prefix_text(messages[-1], note)

    # The window opens on the partner's last line — an assistant turn the API
    # will not take as `messages[0]`. Fold it into the user turn it precedes.
    # `messages[1]` is that user turn: the mapping alternates, so a leading
    # assistant is always followed by a user.
    if messages[0]["role"] == "assistant":
        line = messages.pop(0)["content"]
        _prefix_text(messages[0], f"[The partner's last line was: {line}]")

    # On turn 1 the window is empty, because the partner's opening line costs the
    # learner none of their budget and so is never part of `dialogue`. The
    # learner's first words are a response to that line and to nothing else, so
    # without it a turn-1 slot is judged with nothing to have answered. It rides
    # as a prefix on the first (and only) user message, the same fold.
    if opening_line and not windowed:
        _prefix_text(
            messages[0],
            f"[The partner opened the conversation with: {opening_line}]",
        )

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
