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

import json

import pytest

from backend import config
from backend import kb
from backend.prompts import render_system_prompt
from backend.models import (
    GraderResult,
    ConversationResult,
    SpokenConversationResult,
    Utterance,
    ConverserAnnotation,
)
from backend.workers import conversation

KB = "VOCAB block bytes"
SKETCH = "SKETCH bytes"


def _build(
    dialogue=None,
    user_text="你好",
    forgiveness=0.8,
    want_reading=True,
    sketch=SKETCH,
    hint=None,
):
    return conversation.build_request(
        kb_block=KB,
        sketch=sketch,
        dialogue=dialogue or [],
        user_text=user_text,
        forgiveness_level=forgiveness,
        want_reading=want_reading,
        hint=hint,
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


def test_forgiveness_is_not_in_the_partner_prompt():
    """A2 cut. Forgiveness was a session constant baked in as a literal, and
    it asked the partner to hold a tutoring stance it is not. The worker
    still takes the arg so the orchestrator contract does not move; it no
    longer reaches the frozen prefix."""
    req = _build(forgiveness=0.8)
    assert "0.8" not in req["system"][0]["text"]
    assert "Forgiveness" not in req["system"][0]["text"]
    # The learner's words never leak into the cached system prefix.
    for block in req["system"]:
        assert "我叫小明" not in block["text"]


def test_the_scene_rides_the_cached_prefix_not_the_per_turn_messages():
    """V2: the *scene* is authored, so it costs cached tokens once.

    It cannot change mid-session, so it belongs inside the KB block behind the
    breakpoint. What is volatile — whether this is the final turn — arrives
    after it, in `messages`. Asserted on the real greetings block, because the
    point is that the loader put it there.
    """
    real_kb = kb.load_converser_block("greetings")
    req = conversation.build_request(
        kb_block=real_kb,
        sketch=SKETCH,
        dialogue=[{"role": "user", "zh": "你好"}],
        user_text="我叫小明",
        forgiveness_level=0.8,
        want_reading=True,
    )
    assert "# SCENE" in req["system"][1]["text"]
    assert "never volunteer their own name" in req["system"][1]["text"]
    # Still cached as one frozen unit: the breakpoint sits after the sketch.
    assert "cache_control" not in req["system"][1]
    assert req["system"][2]["cache_control"] == {"type": "ephemeral"}
    for message in req["messages"]:
        assert "SCENE" not in message["content"]


def test_no_part_of_the_request_tells_the_partner_what_is_scored():
    """The invariant V2 exists for, asserted on the assembled request rather
    than on any one component — a blind partner is only blind if *nothing* in
    the turn carries the rubric, including the system prompt itself."""
    scenario = kb.load_scenario("greetings")
    req = conversation.build_request(
        kb_block=kb.load_converser_block("greetings"),
        sketch=SKETCH,
        dialogue=[{"role": "user", "zh": "你好"}],
        user_text="我叫小明",
        forgiveness_level=0.8,
        want_reading=True,
    )
    everything = "\n".join(block["text"] for block in req["system"])
    everything += "\n".join(
        m["content"] if isinstance(m["content"], str)
        else "".join(b["text"] for b in m["content"])
        for m in req["messages"]
    )
    # **The output schema is part of the request.** `messages.parse` renders it
    # into the call — field names, and the model docstrings Pydantic emits as
    # schema `description`. An earlier version of this test joined `system` and
    # `messages` only, and passed while `ConversationResult` still nested
    # `GraderResult`: the partner was handed `slots_filled`, `learner_closed`,
    # `coherence` and a docstring spelling out the credit rule, in its cached
    # prefix, by the one component the assertion skipped.
    everything += json.dumps(
        req["output_format"].model_json_schema(), ensure_ascii=False
    )
    assert scenario.goal not in everything
    for slot in scenario.slots:
        assert slot.id not in everything
        assert slot.description not in everything
    # The words the old prompt used to teach the rubric with.
    for word in ("slot", "SCENARIO", "scenario", "criteri", "scored"):
        assert word not in everything


def test_the_converser_is_not_asked_to_annotate_what_it_cannot_see():
    """The system prompt taught `slots_filled` and `learner_closed`. Both are
    the grader's now, and a prompt that still asked for them would have the
    partner reasoning about a rubric it was not given — the worst of both
    designs.

    `coherent` is not one of them and is asked for by name (A4). It names no
    slot and no goal, so answering it tells the partner nothing about what is
    being scored — which is exactly why it could move back."""
    prompt = render_system_prompt(0.8)
    for field in ("slots_filled", "learner_closed"):
        assert field not in prompt
    assert "`coherent`" in prompt


def test_the_partner_is_told_not_to_judge_the_learners_chinese():
    """The gate's failure direction is asymmetric: a turn wrongly called
    incoherent silently costs a point the learner earned. The target learner is
    HSK 1–2, so "I did not understand" must mean the turn went somewhere else,
    never that the grammar was wrong."""
    prompt = render_system_prompt(0.8).lower()
    assert "beginner" in prompt
    assert "followed" in prompt


def test_the_partner_prompt_is_persona_scene_band_and_pinyin():
    """A2: the partner holds four things, not a coaching brief.

    Reciprocity, stay-in-character, forgiveness, and the annotation dump
    were extra jobs. The scene block already says what the place does not
    hand over. Schema carries `learner_said_goodbye`."""
    prompt = render_system_prompt(0.8)
    assert "conversation partner" in prompt
    assert "HSK 3.0 band 2" in prompt
    assert "pinyin" in prompt.lower()
    assert "scene" in prompt.lower()
    for gone in (
        "grammar_notes",
        "topic_tags",
        "should_give_feedback",
        "Stay in character",
        "Answer what you are asked",
        "turn_annotation",
    ):
        assert gone not in prompt


def test_empty_sketch_is_omitted_rather_than_sent_as_an_empty_block():
    """A turn sent before `POST /api/session` has run defaults `sketch` to
    `""` (`TextTurnRequest.sketch`) — that must not become an empty `text`
    block, which the API rejects outright. The breakpoint moves to the KB
    block instead, so the prefix still caches.
    """
    system = _build(user_text="你好", sketch="")["system"]
    assert len(system) == 2
    assert system[1]["text"] == KB
    assert "cache_control" not in system[0]
    assert system[1]["cache_control"] == {"type": "ephemeral"}
    assert not any(block["text"] == "" for block in system)


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


def test_the_spoken_shape_asks_for_the_schema_without_the_reading():
    assert _build(want_reading=False)["output_format"] is SpokenConversationResult


def test_the_two_output_shapes_share_a_byte_identical_cached_prefix():
    """The prompt-cache invariant across the split, asserted rather than assumed.

    Two schemas is the whole cost of the output cut, and the way it could go
    wrong is silent: a prefix that differs between the paths turns every switch
    into a cache write nobody notices until the bill. The schema itself does ride
    in the cached span — measured, `cache_creation_input_tokens` differs by shape
    for byte-identical system blocks — so each path keeps its own entry. What
    must not *also* differ is anything we control here.
    """
    full = _build(want_reading=True)
    spoken = _build(want_reading=False)

    assert full["system"] == spoken["system"]
    # The breakpoint stays on the last stable block in both shapes — a moved
    # breakpoint would shorten the cached prefix without changing a byte of it.
    assert "cache_control" not in spoken["system"][0]
    assert "cache_control" not in spoken["system"][1]
    assert spoken["system"][2]["cache_control"] == {"type": "ephemeral"}
    # Everything before the schema is the same request, too.
    assert full["messages"] == spoken["messages"]
    assert full["model"] == spoken["model"]
    assert full["output_config"] == spoken["output_config"]
    assert full["thinking"] == spoken["thinking"]


def test_effort_is_pinned_low_on_the_hot_path():
    """Unset means `high` — the API default — on every turn of the loop.

    Effort governs total token spend, not only thinking depth, so it still bites
    with thinking disabled: `high` buys deliberation this turn has no use for.
    One short in-band reply plus an annotation off a frozen prompt is the
    canonical low-effort task, and this is the per-turn hot path.

    Asserted because the cost of getting it wrong is invisible: an unset field
    is a valid request that quietly runs at the expensive default.
    """
    assert _build()["output_config"] == {"effort": "low"}


def test_effort_rides_output_config_not_the_top_level():
    """`effort` is nested inside `output_config`; passed top-level it is not a
    parameter the API knows, and the SDK merges `output_format` into the same
    `output_config` object — so the two must not clobber each other."""
    req = _build()
    assert "effort" not in req
    assert req["output_format"] is ConversationResult
    assert "format" not in req["output_config"]


def test_effort_is_omitted_rather_than_defaulted_when_unset(monkeypatch):
    """Not every model takes the parameter — Haiku 4.5 rejects it — so an empty
    setting has to send *nothing*, not a guessed default. Without this the model
    dial can't be turned far enough to run the comparison it exists for."""
    monkeypatch.setattr(config, "CONVERSATION_EFFORT", "")
    assert "output_config" not in _build()


# --- Claude boundary (contract test, mocked SDK) --------------------------


def _recorded_result():
    return ConversationResult(
        partner_response=Utterance(zh="你好！你叫什么名字？", pinyin="nǐ hǎo! nǐ jiào shénme míngzi?"),
        turn_annotation=ConverserAnnotation(),
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
    assert annotation.learner_said_goodbye is False
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

    def fake_ctor(*, api_key, max_retries=None):
        built.append(api_key)
        return SimpleNamespace(api_key=api_key)

    monkeypatch.setattr(conversation, "AsyncAnthropic", fake_ctor)
    monkeypatch.setattr(conversation.config, "ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(conversation, "_client", None)

    first = conversation._get_client()
    second = conversation._get_client()

    assert first is second           # cached — built once, reused
    assert built == ["test-key"]     # constructed from the configured key


def test_client_is_built_with_retries_off_so_the_deadline_bounds_the_turn(monkeypatch):
    """The SDK retries twice by default, and a timeout is a retryable error —
    which quietly makes `CLAUDE_TIMEOUT_S` mean "10s, three times". Replay caught
    a 13.9s turn under a 10s deadline for exactly this reason.

    Asserted rather than trusted: the retry is invisible from our side (one
    `await`, one exception class) and only shows up as a turn that outlives the
    budget it was supposedly held to.
    """
    kwargs = {}

    def fake_ctor(**kw):
        kwargs.update(kw)
        return SimpleNamespace()

    monkeypatch.setattr(conversation, "AsyncAnthropic", fake_ctor)
    monkeypatch.setattr(conversation.config, "CLAUDE_MAX_RETRIES", 0)
    monkeypatch.setattr(conversation, "_client", None)

    conversation._get_client()

    assert kwargs["max_retries"] == 0


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


# --- M2-C: the per-turn stage direction ----------------------------------


HINT = "The learner has not yet established: price — Find out what they cost."


def test_a_hint_leaves_the_cached_prefix_byte_identical():
    """The whole point of injecting after the breakpoint, asserted.

    The hint is the one genuinely volatile thing M2-C adds to the turn. If it
    ever reached `system`, every turn would write a new prefix instead of
    reading the frozen one — the single most expensive mistake available here,
    and invisible without this assertion.
    """
    assert _build(hint=HINT)["system"] == _build()["system"]


def test_the_hint_rides_the_final_user_message_as_its_own_block():
    req = _build(user_text="三个", hint=HINT)
    content = req["messages"][-1]["content"]
    assert content == [
        {"type": "text", "text": f"[Stage direction: {HINT}]"},
        {"type": "text", "text": "三个"},
    ]


def test_the_learner_text_is_never_spliced_into_the_directive():
    """The prompt teaches the model to read messy pinyin as the learner's
    meaning; English instructions inside that same string poison that read.
    """
    req = _build(user_text="san ge", hint=HINT)
    directive, utterance = req["messages"][-1]["content"]
    assert "san ge" not in directive["text"]
    assert utterance["text"] == "san ge"


def test_no_hint_keeps_the_plain_string_content():
    """Turn 1 of every session has nothing outstanding to steer toward yet."""
    assert _build(user_text="你好")["messages"][-1]["content"] == "你好"


def test_prior_dialogue_turns_never_carry_a_hint():
    """The directive is about *this* turn; replaying it would compound."""
    req = _build(
        dialogue=[
            {"role": "user", "zh": "我要水果"},
            {"role": "partner", "zh": "好！你要多少个？"},
        ],
        user_text="三个",
        hint=HINT,
    )
    assert req["messages"][0]["content"] == "我要水果"
    assert req["messages"][1]["content"] == "好！你要多少个？"
    assert isinstance(req["messages"][2]["content"], list)


# --- V2: the tracker is not the converser's to report ----------------------
#
# It used to be folded into the annotation to avoid a second call. Those tests
# lived here and now live in `tests/test_grader.py`, against the call that
# actually makes the judgment. What is left here is the invariant that replaced
# them: the converser is not asked, in any component of the request.


def test_the_tracker_fields_default_to_a_no_op():
    """A turn that establishes nothing must parse, not fail — most turns do."""
    grade = GraderResult(coherence="on_track")
    assert grade.slots_filled == []
    assert grade.slots_filled_previously == []


def test_neither_conversation_schema_carries_the_grade():
    """The schema is rendered into the request, so a `GraderResult` nested in
    either result model would hand the partner the rubric in its cached prefix —
    the exact route stripping the system prompt was meant to close."""
    for schema in (ConversationResult, SpokenConversationResult):
        assert "grade" not in schema.model_fields
        assert "GraderResult" not in json.dumps(schema.model_json_schema())


async def test_a_truncated_response_becomes_a_conversation_error():
    """`messages.parse` validates inside the SDK, so truncation raises here.

    Uncaught it is a 500 on a turn; as a `ConversationError` it is the failure
    class the stream already reports in-band. Found on the verdict worker in
    production and fixed across all three by the same reasoning.
    """
    client, parse = _fake_client(_recorded_result())
    try:
        ConversationResult.model_validate_json('{"partner_response":{"zh":"你')
    except Exception as truncated:
        parse.side_effect = truncated

    with pytest.raises(conversation.ConversationError, match="unparseable"):
        await conversation.respond(
            kb_block=KB, sketch=SKETCH, dialogue=[], user_text="你好",
            forgiveness_level=0.8, client=client,
        )


async def test_hitting_the_token_cap_becomes_a_conversation_error():
    client, _ = _fake_client(None, stop_reason="max_tokens")
    with pytest.raises(conversation.ConversationError):
        await conversation.respond(
            kb_block=KB, sketch=SKETCH, dialogue=[], user_text="你好",
            forgiveness_level=0.8, client=client,
        )
