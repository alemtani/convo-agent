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
from typing import Dict, List, Optional

import anthropic
from anthropic import AsyncAnthropic
from pydantic import ValidationError

from backend import config
from backend.kb import Scenario
from backend.models import DialogueTurn, GraderResult
from backend.prompts import render_grader_prompt

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


def build_request(
    *, scenario: Scenario, dialogue: List, user_text: str
) -> Dict:
    """Assemble the exact `messages.parse` kwargs for one grade.

    The `system` block is the authored rubric — goal and slots — and is
    byte-stable within a session, so it caches like the converser's prefix. It is
    a *separate* prefix, not a variant of one: caches are per-model and this call
    runs on `GRADER_MODEL`. Opus 5 caches from 512 tokens where Sonnet 5 needs
    1024, which suits the smaller block.

    `dialogue` and the learner's turn ride `messages`, after the breakpoint. The
    partner's most recent line is simply the last entry of that history, which is
    why the grader needs nothing the turn does not already have — and why it can
    start the moment the learner's 汉字 exists rather than waiting on the reply.
    """
    messages = [
        {"role": _ROLE_MAP[_as_dict(t)["role"]], "content": _as_dict(t)["zh"]}
        for t in dialogue
    ]
    messages.append({"role": "user", "content": user_text})

    return {
        "model": config.GRADER_MODEL,
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
    client: Optional[AsyncAnthropic] = None,
) -> GraderResult:
    """Judge one learner turn; return the grade.

    Raises `GraderError` on a refusal, a timeout, or any response we cannot parse.
    The caller's job on that error is to echo the previous `SessionState`
    unchanged — no slot credited, no close counted — never to default a grade
    into existence.
    """
    client = client or _get_client()
    request = build_request(
        scenario=scenario, dialogue=dialogue, user_text=user_text
    )

    try:
        response = await client.messages.parse(
            **request, timeout=config.GRADER_TIMEOUT_S
        )
    except anthropic.APITimeoutError as exc:
        raise GraderError(
            f"grader timed out after {config.GRADER_TIMEOUT_S:g}s"
        ) from exc
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
    return result
