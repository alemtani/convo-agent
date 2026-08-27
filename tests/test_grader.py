"""The grader — V2's scoring half, on its own goal-blind call.

`docs/VALIDITY.md`. The converser cannot judge what it is playing: a partner
that holds the rubric stops being a person in a scene and becomes a proctor who
wants you to pass. So the judgment moves to a call that holds no character,
writes no reply, and has no reason to be generous.

Contract tests only — the request we build and a recorded response we parse.
Never the model's wording.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import anthropic
import pytest
from pydantic import ValidationError

from backend import config, kb
from backend.models import DialogueTurn, GraderResult
from backend.prompts import render_grader_prompt
from backend.workers import grader

SCENARIO = kb.load_scenario("greetings")


def _build(**overrides):
    kwargs = dict(
        scenario=SCENARIO,
        dialogue=[
            DialogueTurn(role="partner", zh="你好！"),
            DialogueTurn(role="user", zh="你好，我叫小明。"),
        ],
        user_text="你叫什么名字？",
    )
    kwargs.update(overrides)
    return grader.build_request(**kwargs)


def _system_text(req):
    return "\n".join(block["text"] for block in req["system"])


# --- the request we build -------------------------------------------------


def test_the_grader_reads_the_rubric_the_converser_no_longer_sees():
    text = _system_text(_build())
    assert SCENARIO.goal in text
    for slot in SCENARIO.slots:
        assert slot.id in text
        assert slot.description in text


def test_the_grader_holds_no_character():
    """No persona, no sketch, no opening line. It is not playing anyone, and a
    judge given a character has something to be loyal to."""
    text = _system_text(_build())
    for word in ("persona", "in character", "brisk", "flavour"):
        assert word not in text


def test_the_grader_runs_on_its_own_model_with_thinking_on():
    req = _build()
    assert req["model"] == config.GRADER_MODEL
    assert req["model"] != config.CONVERSATION_MODEL
    # The other three workers disable thinking; this one is the judgment.
    assert "thinking" not in req or req["thinking"]["type"] != "disabled"
    assert req["max_tokens"] == config.GRADER_MAX_TOKENS


def test_the_grader_prefix_is_byte_identical_across_turns():
    """Its own cached prefix — the scenario cannot change mid-session."""
    first = _system_text(_build(user_text="你叫什么名字？"))
    second = _system_text(_build(user_text="你最近怎么样？", dialogue=[]))
    assert first == second
    assert _build()["system"][-1]["cache_control"] == {"type": "ephemeral"}


def test_the_learners_turn_and_the_history_ride_after_the_breakpoint():
    req = _build()
    assert "你叫什么名字？" not in _system_text(req)
    assert "你叫什么名字？" in str(req["messages"])


def test_the_previous_partner_turn_is_what_the_slot_judgment_needs():
    """A slot is filled by an answer to something. Whether 你呢 bounced a
    question back is legible from the partner's previous turn and the learner's
    — and from nothing else."""
    req = _build()
    assert "你好！" in str(req["messages"])


def test_the_grader_is_never_asked_whether_the_turn_followed():
    """A4. Coherence is a question about what the partner meant by its own last
    line, and the partner is the only party that knows. Leaving the instruction
    here would spend tokens on a field `GraderResult` has nowhere to put."""
    text = _system_text(_build())
    assert "coherence" not in text
    assert "on_track" not in text
    schema = _build()["output_format"].model_json_schema()
    assert "coherence" not in schema["properties"]
    # And not smuggled in through the docstring, which `messages.parse` renders
    # into the request as the schema `description`.
    assert "coherence" not in str(schema).lower()


def test_the_grader_is_constrained_to_the_grade_and_nothing_else():
    assert _build()["output_format"] is GraderResult


def test_the_grader_credits_a_request_slot_on_the_ask():
    """The authored rule reads *asked AND answered*. The grader deliberately
    does not wait for the answer: the slot is a claim about the learner's
    Chinese, and the partner's reply is the partner's performance."""
    text = _system_text(_build())
    assert "ask" in text
    # It must not be told to wait for the partner's reply.
    assert "your reply answers it" not in text


# --- the response we parse ------------------------------------------------


def _fake_client(parsed_output, *, stop_reason="end_turn"):
    msg = SimpleNamespace(
        parsed_output=parsed_output,
        stop_reason=stop_reason,
        usage=SimpleNamespace(cache_read_input_tokens=0),
    )
    parse = AsyncMock(return_value=msg)
    return SimpleNamespace(messages=SimpleNamespace(parse=parse)), parse


async def test_grade_parses_a_recorded_response():
    recorded = GraderResult(
        slots_filled=["partner_name"]
    )
    client, parse = _fake_client(recorded)

    grade, usage = await grader.grade(
        scenario=SCENARIO, dialogue=[], user_text="你叫什么名字？", client=client
    )

    assert grade.slots_filled == ["partner_name"]
    assert parse.await_count == 1
    # The judgment's cost comes back too: it runs on a different model at a
    # different price, so a turn reporting only the reply's would hide it.
    assert usage.cache_read_input_tokens == 0


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
async def test_a_failed_grade_raises_rather_than_inventing_one(stop_reason):
    client, _ = _fake_client(None, stop_reason=stop_reason)
    with pytest.raises(grader.GraderError):
        await grader.grade(
            scenario=SCENARIO, dialogue=[], user_text="你好", client=client
        )


async def test_an_unparseable_grade_raises():
    client, _ = _fake_client(None)
    with pytest.raises(grader.GraderError):
        await grader.grade(
            scenario=SCENARIO, dialogue=[], user_text="你好", client=client
        )


async def test_a_timeout_is_a_grader_error_not_a_five_hundred():
    client = SimpleNamespace(
        messages=SimpleNamespace(
            parse=AsyncMock(side_effect=anthropic.APITimeoutError(request=None))
        )
    )
    with pytest.raises(grader.GraderError):
        await grader.grade(
            scenario=SCENARIO, dialogue=[], user_text="你好", client=client
        )


async def test_the_grader_is_given_the_timeout_that_bounds_it():
    client, parse = _fake_client(GraderResult())
    await grader.grade(
        scenario=SCENARIO, dialogue=[], user_text="你好", client=client
    )
    assert parse.await_args.kwargs["timeout"] == config.GRADER_TIMEOUT_S


async def test_any_api_error_degrades_rather_than_escaping():
    """`CLAUDE_MAX_RETRIES` is 0, so a rate limit or a 5xx arrives here with no
    retry layer beneath it. Uncaught it escapes into a stream that has already
    sent a 200, and the turn ends with no terminal event."""
    client = SimpleNamespace(
        messages=SimpleNamespace(
            parse=AsyncMock(
                side_effect=anthropic.APIConnectionError(request=None)
            )
        )
    )
    with pytest.raises(grader.GraderError):
        await grader.grade(
            scenario=SCENARIO, dialogue=[], user_text="你好", client=client
        )


# --- turn 1 has no partner line in `dialogue` -----------------------------


def test_turn_one_still_shows_the_partner_what_the_learner_answered():
    """The opening line costs the learner none of their turn budget, so it is
    never in `dialogue`. On turn 1 that leaves the grader with the learner's
    words and nothing they are a response to — and turn 1 is the turn most
    likely to be answering a greeting."""
    req = _build(dialogue=[], user_text="你好，我叫小明。", opening_line="你好！")
    assert "你好！" in str(req["messages"])
    # As a prefix on the first *user* message: the Messages API requires
    # `messages[0]` to be `user`, and a lone leading assistant turn reads as
    # prefill.
    assert req["messages"][0]["role"] == "user"
    assert len(req["messages"]) == 1


def test_the_opening_line_is_not_repeated_once_it_is_in_the_history():
    """From turn 2 the partner's lines are in `dialogue`, so prefixing it again
    would show the model the same line twice and invite it to read the second as
    a fresh turn."""
    req = _build(opening_line="你好！")
    assert "[The partner opened" not in str(req["messages"])


def test_the_grader_reads_the_scene_because_it_is_the_evidence():
    """"The partner volunteered this unasked" cannot be judged without knowing
    what the scene hands over unprompted."""
    assert SCENARIO.situation in _system_text(_build())


# --- the window: settling turns whose grade never landed ------------------


def test_a_healthy_turn_spends_no_tokens_on_the_window():
    """Window 1 is every turn where the previous grade landed. There is nothing
    to say, so nothing is said."""
    assert "never judged" not in str(_build(window=1)["messages"])


def test_an_owed_turn_is_named_in_the_message_not_the_cached_prefix():
    """Which grades failed is volatile, so it must not touch the frozen prefix —
    the grader's cache has to keep hitting whether or not a turn is settling."""
    healthy = _build(window=1)
    settling = _build(window=3)
    assert _system_text(healthy) == _system_text(settling)
    assert "never judged" in str(settling["messages"])


def test_the_window_asks_for_the_two_lists_kept_apart():
    """Unioning them would let a slot credited late reset the close counter, and
    swallow a goodbye the learner actually said."""
    note = str(_build(window=2)["messages"])
    assert "slots_filled_previously" in note
    assert "do not merge" in note


def test_a_longer_dialogue_reaches_the_grader_in_order():
    """Requested in review of PR #76. The history is what makes the previous
    partner turn legible, so its order and roles are the contract."""
    dialogue = [
        DialogueTurn(role="partner", zh="你好！"),
        DialogueTurn(role="user", zh="你好，我叫小明。"),
        DialogueTurn(role="partner", zh="我也很高兴认识你。"),
        DialogueTurn(role="user", zh="你叫什么名字？"),
        DialogueTurn(role="partner", zh="我叫小王。"),
    ]
    req = _build(dialogue=dialogue, user_text="你最近怎么样？")

    assert [m["role"] for m in req["messages"]] == [
        "assistant", "user", "assistant", "user", "assistant", "user",
    ]
    assert [m["content"] for m in req["messages"]] == [
        "你好！", "你好，我叫小明。", "我也很高兴认识你。", "你叫什么名字？",
        "我叫小王。", "你最近怎么样？",
    ]
    # The learner's turn is last, and the partner's previous line is the one
    # before it — the pair the gaming check reads.
    assert req["messages"][-1]["content"] == "你最近怎么样？"
    assert req["messages"][-2]["content"] == "我叫小王。"


def test_the_opening_line_is_not_prefixed_once_history_exists():
    """It is already in `dialogue` from turn 2 on. Prefixing it again would show
    the model the same line twice and invite reading the second as a new turn."""
    dialogue = [
        DialogueTurn(role="partner", zh="你好！"),
        DialogueTurn(role="user", zh="你好。"),
    ]
    req = _build(dialogue=dialogue, opening_line="你好！")
    assert "[The partner opened" not in str(req["messages"])


async def test_a_response_cut_off_mid_json_is_a_grader_error():
    """A truncated body is validated *inside* `messages.parse`, so it arrives as
    an exception rather than as `parsed_output is None` — a different path from
    the unparseable case above, and uncaught it escapes a committed stream."""
    client = SimpleNamespace(
        messages=SimpleNamespace(
            parse=AsyncMock(
                side_effect=ValidationError.from_exception_data("GraderResult", [])
            )
        )
    )
    with pytest.raises(grader.GraderError):
        await grader.grade(
            scenario=SCENARIO, dialogue=[], user_text="你好", client=client
        )
