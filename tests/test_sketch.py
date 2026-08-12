"""M2-B sketch-worker tests, mirroring `tests/test_conversation.py`'s split:

- **Request assembly (pure):** assert the one-off request we *build* — the
  authored `situation`/`goal` interpolated in, no `cache_control` (this is a
  single call, not a per-turn hot path).
- **Claude boundary (contract):** a fake SDK client whose `messages.parse`
  returns a recorded `ParsedMessage`-shaped object; assert we send the right
  request and correctly read a parsed response. Never assert model wording.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import anthropic
import pytest

from backend import config, kb
from backend.models import SketchResult, Utterance
from backend.workers import sketch

_SCENARIO = kb.Scenario(
    situation="You're at a fruit stall. The vendor greets you.",
    goal="Buy three pieces of fruit, and find out what they cost.",
    slots=(
        kb.Slot(id="item", kind="inform", description="Say you want fruit"),
        kb.Slot(id="price", kind="request", description="Find out what they cost"),
    ),
    max_turns=6,
)


# --- Request assembly (pure) -------------------------------------------


def test_request_has_no_cache_breakpoint():
    """One call per session — nothing here for a second call to read back."""
    req = sketch.build_request(kb_block="VOCAB block bytes", scenario=_SCENARIO)
    for block in req["system"]:
        assert "cache_control" not in block


def test_request_interpolates_the_authored_situation_and_goal():
    req = sketch.build_request(kb_block="VOCAB block bytes", scenario=_SCENARIO)
    prompt = req["system"][0]["text"]
    assert _SCENARIO.situation in prompt
    assert _SCENARIO.goal in prompt


def test_request_carries_the_kb_block_as_a_user_message():
    req = sketch.build_request(kb_block="VOCAB block bytes", scenario=_SCENARIO)
    assert req["messages"] == [{"role": "user", "content": "VOCAB block bytes"}]


def test_request_constrains_output_to_sketch_result():
    req = sketch.build_request(kb_block="x", scenario=_SCENARIO)
    assert req["output_format"] is SketchResult


def test_request_disables_thinking():
    req = sketch.build_request(kb_block="x", scenario=_SCENARIO)
    assert req["thinking"] == {"type": "disabled"}


def test_prompt_never_states_max_turns_or_slot_ids():
    """The sketch worker generates flavour only — the turn budget and the
    slot graph are not its business (`docs/SCENARIOS.md`)."""
    req = sketch.build_request(kb_block="x", scenario=_SCENARIO)
    prompt = req["system"][0]["text"]
    assert "6" not in prompt  # max_turns
    assert "item" not in prompt
    assert "price" not in prompt


# --- Claude boundary (contract test, mocked SDK) ------------------------


def _recorded_result():
    return SketchResult(
        opening_line=Utterance(zh="你好！你要买什么？", pinyin="nǐ hǎo! nǐ yào mǎi shénme?"),
        sketch="The vendor is brisk and busy. Fresh fruit is piled on the cart today.",
    )


def _fake_client(parsed_output, *, stop_reason="end_turn"):
    msg = SimpleNamespace(parsed_output=parsed_output, stop_reason=stop_reason)
    parse = AsyncMock(return_value=msg)
    return SimpleNamespace(messages=SimpleNamespace(parse=parse)), parse


async def test_generate_sends_built_request_and_parses_recorded_response(monkeypatch):
    client, parse = _fake_client(_recorded_result())
    monkeypatch.setattr(kb, "load_vocab_block", lambda topic_id, root=kb.KB_ROOT: "VOCAB block bytes")

    result = await sketch.generate("shopping", _SCENARIO, client=client)

    assert result.opening_line.zh == "你好！你要买什么？"
    assert "brisk" in result.sketch

    kwargs = parse.call_args.kwargs
    assert kwargs["output_format"] is SketchResult
    assert kwargs["messages"] == [{"role": "user", "content": "VOCAB block bytes"}]


async def test_generate_raises_on_refusal(monkeypatch):
    client, _ = _fake_client(None, stop_reason="refusal")
    monkeypatch.setattr(kb, "load_vocab_block", lambda topic_id, root=kb.KB_ROOT: "x")

    with pytest.raises(sketch.SketchError):
        await sketch.generate("shopping", _SCENARIO, client=client)


async def test_generate_raises_when_output_unparsed(monkeypatch):
    client, _ = _fake_client(None)
    monkeypatch.setattr(kb, "load_vocab_block", lambda topic_id, root=kb.KB_ROOT: "x")

    with pytest.raises(sketch.SketchError):
        await sketch.generate("shopping", _SCENARIO, client=client)


async def test_generate_raises_kb_error_for_an_unknown_topic():
    with pytest.raises(kb.KbError):
        await sketch.generate("nope", _SCENARIO)


async def test_generate_never_sends_the_scenario_block_to_the_model(monkeypatch):
    """The KB block handed to the sketch worker must never carry `# SCENARIO`
    — `load_kb_block` does (it's what the conversation worker freezes), but
    the sketch worker uses `load_vocab_block` precisely so slots never reach
    a model call at all, per `docs/SCENARIOS.md`. Asserted against the real
    greetings KB, because the point is that the loader excludes it."""
    client, parse = _fake_client(_recorded_result())
    real_scenario = kb.load_topic("greetings").scenario

    await sketch.generate("greetings", real_scenario, client=client)

    kb_block_sent = parse.call_args.kwargs["messages"][0]["content"]
    assert "SCENARIO" not in kb_block_sent
    assert "partner_name" not in kb_block_sent   # a greetings slot id
    # Sanity: it's still the real vocab content, not an empty stand-in.
    assert kb_block_sent == kb.load_vocab_block("greetings")
    assert kb_block_sent != kb.load_kb_block("greetings")


async def test_a_timed_out_call_becomes_a_sketch_error(monkeypatch):
    import httpx

    client, parse = _fake_client(_recorded_result())
    parse.side_effect = anthropic.APITimeoutError(
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    )
    monkeypatch.setattr(kb, "load_vocab_block", lambda topic_id, root=kb.KB_ROOT: "x")

    with pytest.raises(sketch.SketchError, match="timed out"):
        await sketch.generate("shopping", _SCENARIO, client=client)


def test_get_client_is_a_singleton_built_from_config_key(monkeypatch):
    built = []

    def fake_ctor(*, api_key, max_retries=None):
        built.append(api_key)
        return SimpleNamespace(api_key=api_key)

    monkeypatch.setattr(sketch, "AsyncAnthropic", fake_ctor)
    monkeypatch.setattr(sketch.config, "ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(sketch, "_client", None)

    first = sketch._get_client()
    second = sketch._get_client()

    assert first is second
    assert built == ["test-key"]
