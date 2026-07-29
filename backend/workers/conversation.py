"""The conversation worker — Claude on the per-turn hot path (Phase 3a).

Builds one `messages.parse` request whose cacheable prefix (frozen system prompt
+ topic KB + session sketch) sits before a `cache_control` breakpoint, with the
volatile per-turn data (client dialogue + the latest utterance) after it. The
model is constrained to `ConversationResult` via structured output, so we never
parse free text.

`respond` takes an optional `client` so contract tests can inject a fake; in
production it lazily builds a shared `AsyncAnthropic`.
"""
from typing import Dict, List, Optional, Tuple

import anthropic
from anthropic import AsyncAnthropic

from backend import config
from backend.models import ConversationResult, DialogueTurn, TurnAnnotation, Utterance
from backend.prompts import render_system_prompt

_ROLE_MAP = {"user": "user", "partner": "assistant"}

_client: Optional[AsyncAnthropic] = None


class ConversationError(Exception):
    """Claude refused, or returned output we could not parse into our schema."""


def _get_client() -> AsyncAnthropic:
    """Lazily build a shared async client (key from env via config)."""
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
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
) -> Dict:
    """Assemble the exact `messages.parse` kwargs for one turn.

    The `system` list is the cacheable prefix; only the last block carries the
    `cache_control` breakpoint, so the frozen system prompt + KB + sketch cache
    together. Everything that varies per turn lives in `messages`, after the
    breakpoint — keeping the prefix byte-identical across a session.
    """
    system = [
        {"type": "text", "text": render_system_prompt(forgiveness_level)},
        {"type": "text", "text": kb_block},
        {"type": "text", "text": sketch, "cache_control": {"type": "ephemeral"}},
    ]
    messages = [
        {"role": _ROLE_MAP[_as_dict(t)["role"]], "content": _as_dict(t)["zh"]}
        for t in dialogue
    ]
    messages.append({"role": "user", "content": user_text})

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
        "system": system,
        "messages": messages,
        "output_format": ConversationResult,
    }


async def respond(
    *,
    kb_block: str,
    sketch: str,
    dialogue: List,
    user_text: str,
    forgiveness_level: float,
    client: Optional[AsyncAnthropic] = None,
) -> Tuple[Utterance, TurnAnnotation, Utterance, object]:
    """Run one conversation turn; return (reply, annotation, reading, usage).

    `reading` is the worker's rendering of the learner's own turn as 汉字 + pinyin
    — the seam that lets a beginner type pinyin. `usage` is passed through so
    callers (and the live cache test) can assert `cache_read_input_tokens`. Raises
    `ConversationError` on a refusal or any response we can't parse into
    `ConversationResult`.
    """
    client = client or _get_client()
    request = build_request(
        kb_block=kb_block,
        sketch=sketch,
        dialogue=dialogue,
        user_text=user_text,
        forgiveness_level=forgiveness_level,
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

    if getattr(response, "stop_reason", None) == "refusal":
        raise ConversationError("conversation worker refused the turn")
    result: Optional[ConversationResult] = response.parsed_output
    if result is None:
        raise ConversationError("conversation worker returned unparseable output")

    return (
        result.partner_response,
        result.turn_annotation,
        result.user_reading,
        response.usage,
    )
