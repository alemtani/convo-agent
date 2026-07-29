"""Phase 3a conversation-worker tests.

Two tiers, no tokens spent:
- **Prefix assembly (pure):** assert the request we *build* — byte-frozen system
  prefix, the `cache_control` breakpoint after the stable block, role mapping.
  This is the prompt-cache invariant asserted without a live call.
- **Claude boundary (contract):** a fake SDK client whose `messages.parse`
  returns a recorded `ParsedMessage`-shaped object; assert we send the right
  request and correctly read a parsed response. Never assert model wording.
"""
import anthropic
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend import config
from backend.models import ConversationResult, TurnAnnotation, Utterance
from backend.workers import conversation

KB = "VOCAB block bytes"
SKETCH = "SKETCH bytes"


def _build(dialogue=None, user_text="你好", forgiveness=0.8):
    return conversation.build_request(
        kb_block=KB,
        sketch=SKETCH,
        dialogue=dialogue or [],
        user_text=user_text,
        forgiveness_level=forgiveness,
    )


# --- Prefix assembly (the cache invariant, no tokens) ---------------------


def test_system_prefix_is_three_blocks_with_breakpoint_on_last():
    req = _build()
    system = req["system"]
    assert len(system) == 3
    # KB and sketch are the per-session blocks; the breakpoint sits on the last
    # stable block so system prompt + KB + sketch all cache together.
    assert system[1]["text"] == KB
    assert system[2]["text"] == SKETCH
    assert "cache_control" not in system[0]
    assert "cache_control" not in system[1]
    assert system[2]["cache_control"] == {"type": "ephemeral"}


def test_system_prefix_is_byte_identical_across_turns():
    # Same kb/sketch/forgiveness, different volatile turn data -> identical prefix.
    a = _build(dialogue=[], user_text="你好")
    b = _build(
        dialogue=[
            {"role": "user", "zh": "你好"},
            {"role": "partner", "zh": "你好！你叫什么名字？"},
        ],
        user_text="我叫小明",
    )
    assert a["system"] == b["system"]


def test_forgiveness_literal_is_in_frozen_block_not_volatile():
    req = _build(forgiveness=0.8)
    assert "0.8" in req["system"][0]["text"]
    # The learner's words never leak into the cached system prefix.
    for block in req["system"]:
        assert "我叫小明" not in block["text"]


def test_model_is_held_fixed():
    assert _build()["model"] == config.CONVERSATION_MODEL == "claude-sonnet-5"


def test_thinking_is_disabled_with_room_for_the_structured_output():
    """Sonnet 5 thinks by default, and `max_tokens` caps thinking *plus* output.

    Left implicit, adaptive thinking consumes the whole budget and the turn ends
    `stop_reason: max_tokens` with `parsed_output is None` — a 502 on a perfectly
    valid request. Asserted because the failure is silent at build time and only
    shows up as a live-call failure.
    """
    req = _build()
    assert req["thinking"] == {"type": "disabled"}
    assert req["max_tokens"] >= 1024


def test_dialogue_maps_roles_and_appends_latest_user_turn():
    req = _build(
        dialogue=[
            {"role": "user", "zh": "你好"},
            {"role": "partner", "zh": "你好！你叫什么名字？"},
        ],
        user_text="我叫小明",
    )
    assert req["messages"] == [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好！你叫什么名字？"},
        {"role": "user", "content": "我叫小明"},
    ]


def test_request_constrains_output_to_conversation_result():
    assert _build()["output_format"] is ConversationResult


# --- Claude boundary (contract test, mocked SDK) --------------------------


def _recorded_result():
    return ConversationResult(
        partner_response=Utterance(zh="你好！你叫什么名字？", pinyin="nǐ hǎo! nǐ jiào shénme míngzi?"),
        turn_annotation=TurnAnnotation(coherence="on_track", topic_tags=["greetings"]),
        # Text mode: the learner typed pinyin, the worker reports what it read.
        user_reading=Utterance(zh="我叫小明", pinyin="wǒ jiào xiǎo míng"),
    )


def _fake_client(parsed_output, *, stop_reason="end_turn"):
    msg = SimpleNamespace(
        parsed_output=parsed_output,
        stop_reason=stop_reason,
        usage=SimpleNamespace(
            cache_read_input_tokens=0, cache_creation_input_tokens=123, input_tokens=10
        ),
    )
    parse = AsyncMock(return_value=msg)
    return SimpleNamespace(messages=SimpleNamespace(parse=parse)), parse


async def test_respond_sends_built_request_and_parses_recorded_response():
    client, parse = _fake_client(_recorded_result())

    reply, annotation, reading, usage = await conversation.respond(
        kb_block=KB,
        sketch=SKETCH,
        dialogue=[{"role": "user", "zh": "你好"}],
        user_text="wo jiao xiao ming",
        forgiveness_level=0.8,
        client=client,
    )

    # We parsed the recorded response into our models.
    assert reply == Utterance(zh="你好！你叫什么名字？", pinyin="nǐ hǎo! nǐ jiào shénme míngzi?")
    assert annotation.coherence == "on_track"
    assert annotation.topic_tags == ["greetings"]
    # The reading is surfaced separately from the reply — it's the learner's turn.
    assert reading == Utterance(zh="我叫小明", pinyin="wǒ jiào xiǎo míng")
    assert usage.cache_creation_input_tokens == 123

    # We sent the request we build: right model, breakpoint on last system block.
    kwargs = parse.call_args.kwargs
    assert kwargs["model"] == "claude-sonnet-5"
    assert kwargs["output_format"] is ConversationResult
    assert kwargs["system"][-1]["cache_control"] == {"type": "ephemeral"}
    # The learner's raw input goes to the worker as typed — pinyin included. It is
    # the worker that resolves it, so nothing romanizes or rewrites it on the way.
    assert kwargs["messages"][-1] == {"role": "user", "content": "wo jiao xiao ming"}


async def test_respond_raises_on_refusal():
    client, _ = _fake_client(None, stop_reason="refusal")
    with pytest.raises(conversation.ConversationError):
        await conversation.respond(
            kb_block=KB, sketch=SKETCH, dialogue=[], user_text="你好",
            forgiveness_level=0.8, client=client,
        )


async def test_respond_raises_when_output_unparsed():
    client, _ = _fake_client(None)  # parsed_output is None but no refusal
    with pytest.raises(conversation.ConversationError):
        await conversation.respond(
            kb_block=KB, sketch=SKETCH, dialogue=[], user_text="你好",
            forgiveness_level=0.8, client=client,
        )


def test_get_client_is_a_singleton_built_from_config_key(monkeypatch):
    built = []

    def fake_ctor(*, api_key):
        built.append(api_key)
        return SimpleNamespace(api_key=api_key)

    monkeypatch.setattr(conversation, "AsyncAnthropic", fake_ctor)
    monkeypatch.setattr(conversation.config, "ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(conversation, "_client", None)

    first = conversation._get_client()
    second = conversation._get_client()

    assert first is second           # cached — built once, reused
    assert built == ["test-key"]     # constructed from the configured key


async def test_respond_passes_the_configured_deadline_to_the_sdk():
    """The SDK's own `timeout`, not an outer `asyncio.wait_for`.

    This is a real async HTTP client, so `timeout` aborts the request and
    releases the connection; cancelling from outside would leave the SDK to
    clean up behind us.
    """
    client, parse = _fake_client(_recorded_result())

    await conversation.respond(
        kb_block=KB,
        sketch=SKETCH,
        dialogue=[],
        user_text="wo jiao xiao ming",
        forgiveness_level=0.8,
        client=client,
    )

    assert parse.call_args.kwargs["timeout"] == config.CLAUDE_TIMEOUT_S


async def test_a_timed_out_call_becomes_a_conversation_error():
    """Claude is 73% of the turn and the branch the reply waits on.

    Unbounded, a stalled call is a pending bubble that never resolves. As a
    `ConversationError` it is the same failure class as a refusal, which the
    stream already reports in-band — its status line is long spent by then.
    """
    import httpx

    client, parse = _fake_client(_recorded_result())
    parse.side_effect = anthropic.APITimeoutError(
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    )

    with pytest.raises(conversation.ConversationError, match="timed out"):
        await conversation.respond(
            kb_block=KB,
            sketch=SKETCH,
            dialogue=[],
            user_text="wo jiao xiao ming",
            forgiveness_level=0.8,
            client=client,
        )
