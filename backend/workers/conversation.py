"""The conversation worker — Claude on the per-turn hot path (Phase 3a).

Builds one `messages.parse` request whose cacheable prefix (frozen system prompt
+ topic KB + session sketch) sits before a `cache_control` breakpoint, with the
volatile per-turn data (client dialogue + the latest utterance) after it. The
model is constrained via structured output, so we never parse free text —
to `ConversationResult` in text mode, and to the shorter
`SpokenConversationResult` on the audio path, which already has the learner's
汉字 from STT and would only throw the worker's reading away.

`respond` takes an optional `client` so contract tests can inject a fake; in
production it lazily builds a shared `AsyncAnthropic`.
"""
from typing import Dict, List, Optional, Tuple

import anthropic
from anthropic import AsyncAnthropic
from pydantic import ValidationError

from backend import config
from backend.models import (
    ConversationResult,
    DialogueTurn,
    SpokenConversationResult,
    Utterance,
    ConverserAnnotation,
)
from backend.prompts import render_system_prompt

_ROLE_MAP = {"user": "user", "partner": "assistant"}

_client: Optional[AsyncAnthropic] = None


class ConversationError(Exception):
    """Claude refused, or returned output we could not parse into our schema."""


def _get_client() -> AsyncAnthropic:
    """Lazily build a shared async client (key from env via config).

    `max_retries` is pinned rather than left at the SDK's default of 2: a
    timeout is a retryable error, so the default turns `CLAUDE_TIMEOUT_S` into
    three of itself and the deadline stops bounding the turn. See
    `config.CLAUDE_MAX_RETRIES` for why zero is the right number here.
    """
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
    *,
    kb_block: str,
    sketch: str,
    dialogue: List,
    user_text: str,
    forgiveness_level: float,
    want_reading: bool = True,
    hint: Optional[str] = None,
) -> Dict:
    """Assemble the exact `messages.parse` kwargs for one turn.

    The `system` list is the cacheable prefix; only the last block carries the
    `cache_control` breakpoint, so the frozen system prompt + KB + sketch cache
    together. Everything that varies per turn lives in `messages`, after the
    breakpoint — keeping the prefix byte-identical across a session.

    `want_reading` picks the output schema. The spoken path already has the
    learner's 汉字 from STT and throws the worker's reading away, so it asks for
    the shorter shape; text mode needs the reading and asks for the full one.
    The system prefix is byte-identical either way — the two schemas are the
    only difference between the requests.

    `sketch` is omitted as its own block when empty — a turn sent before
    `POST /api/session` has run (`TextTurnRequest.sketch` defaults to `""`) —
    rather than sent as an empty `text` block: the API rejects those outright.
    The `cache_control` breakpoint moves to the KB block in that case, so the
    prefix still caches; it just doesn't include a sketch to freeze.

    `hint` is M2-C's stage direction (`termination.pressure_hint`) — the one
    genuinely volatile instruction in the turn, so it goes in `messages`, after
    the breakpoint, and the frozen prefix stays byte-identical with or without
    it. It rides the final user message as its *own* content block rather than
    being concatenated onto the learner's words: the system prompt spends a
    paragraph teaching the model to read messy pinyin as what the learner meant,
    and splicing English instructions into that same string poisons exactly that
    read. Prior turns never carry one — the direction is about this turn.
    """
    system = [
        {"type": "text", "text": render_system_prompt(forgiveness_level)},
        {"type": "text", "text": kb_block},
    ]
    if sketch:
        system.append({"type": "text", "text": sketch})
    system[-1]["cache_control"] = {"type": "ephemeral"}
    messages = [
        {"role": _ROLE_MAP[_as_dict(t)["role"]], "content": _as_dict(t)["zh"]}
        for t in dialogue
    ]
    messages.append(
        {
            "role": "user",
            "content": (
                [
                    {"type": "text", "text": f"[Stage direction: {hint}]"},
                    {"type": "text", "text": user_text},
                ]
                if hint
                else user_text
            ),
        }
    )

    # Omitted entirely when unset, not sent as a default: `effort` is a
    # parameter only some models take (Haiku 4.5 rejects it outright), and the
    # comparison this dial exists for is worth being able to run.
    effort = (
        {"output_config": {"effort": config.CONVERSATION_EFFORT}}
        if config.CONVERSATION_EFFORT
        else {}
    )

    return {
        "model": config.CONVERSATION_MODEL,
        "max_tokens": 1024,
        # Thinking off, deliberately. Sonnet 5 runs adaptive thinking whenever the
        # field is omitted, and `max_tokens` caps thinking *plus* output — so a
        # budget sized for the JSON alone gets eaten by reasoning and the turn dies
        # with `stop_reason: max_tokens` and no parsed output. One short in-band
        # reply plus an annotation doesn't need deliberation, and this is the
        # per-turn hot path where latency is the thing we're trying to protect.
        "thinking": {"type": "disabled"},
        # Effort governs overall token spend, not just thinking depth, so it
        # still bites with thinking off — and left unset every turn runs at the
        # `high` default. One short in-band reply plus an annotation, off a
        # frozen prompt, is exactly the task `low` is for. Nested inside
        # `output_config`, which `messages.parse` merges the schema into.
        **effort,
        "system": system,
        "messages": messages,
        "output_format": (
            ConversationResult if want_reading else SpokenConversationResult
        ),
    }


async def respond(
    *,
    kb_block: str,
    sketch: str,
    dialogue: List,
    user_text: str,
    forgiveness_level: float,
    want_reading: bool = True,
    hint: Optional[str] = None,
    client: Optional[AsyncAnthropic] = None,
) -> Tuple[Utterance, ConverserAnnotation, Optional[Utterance], object]:
    """Run one conversation turn; return (reply, annotation, reading, usage).

    `reading` is the worker's rendering of the learner's own turn as 汉字 + pinyin
    — the seam that lets a beginner type pinyin — and is `None` when the caller
    said it doesn't need it (`want_reading=False`), because then it was never
    asked for. `usage` is passed through so callers (and the live cache test) can
    assert `cache_read_input_tokens`. Raises `ConversationError` on a refusal or
    any response we can't parse into the requested schema.
    """
    client = client or _get_client()
    request = build_request(
        kb_block=kb_block,
        sketch=sketch,
        dialogue=dialogue,
        user_text=user_text,
        forgiveness_level=forgiveness_level,
        want_reading=want_reading,
        hint=hint,
    )

    # The SDK's own deadline rather than `asyncio.wait_for`: this is a real async
    # HTTP client, so `timeout` aborts the request and releases the connection,
    # where an outer cancel would leave the SDK to clean up behind us. Claude is
    # 73% of the turn and the branch the reply waits on, so an unbounded call
    # here is a pending bubble that never resolves.
    try:
        response = await client.messages.parse(
            **request, timeout=config.CLAUDE_TIMEOUT_S
        )
    except anthropic.APITimeoutError as exc:
        # Same failure class as a refusal from the turn's point of view: the
        # stream reports it in-band, because its status line is long spent.
        raise ConversationError(
            f"conversation worker timed out after {config.CLAUDE_TIMEOUT_S:g}s"
        ) from exc
    except anthropic.APIError as exc:
        # Everything else the SDK raises — rate limits, 5xx, a dropped
        # connection. `CLAUDE_MAX_RETRIES` is 0, so there is no retry layer
        # absorbing a transient failure first, and an uncaught `APIError` escapes
        # into a stream that has already sent a 200. Wrapped, it degrades the way
        # a timeout does.
        raise ConversationError(f"conversation worker call failed: {exc}") from exc
    except ValidationError as exc:
        # A response cut off mid-JSON is validated *inside* `messages.parse`, so
        # it arrives as an exception rather than as `parsed_output is None`.
        # Uncaught, it is a 500 instead of the in-band turn error the client
        # knows how to render.
        raise ConversationError(
            "conversation worker returned unparseable output"
        ) from exc

    stop_reason = getattr(response, "stop_reason", None)
    if stop_reason == "refusal":
        raise ConversationError("conversation worker refused the turn")
    if stop_reason == "max_tokens":
        raise ConversationError("conversation worker's reply ran past max_tokens")
    result = response.parsed_output
    if result is None:
        raise ConversationError("conversation worker returned unparseable output")

    return (
        result.partner_response,
        result.turn_annotation,
        getattr(result, "user_reading", None),
        response.usage,
    )
