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


def test_the_previous_partner_turn_is_what_the_gaming_check_needs():
    """*Did the learner respond to what was actually said?* is legible from the
    partner's previous turn and the learner's — and from nothing else."""
    req = _build()
    assert "你好！" in str(req["messages"])


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
        coherence="on_track", slots_filled=["partner_name"], learner_closed=False
    )
    client, parse = _fake_client(recorded)

    grade = await grader.grade(
        scenario=SCENARIO, dialogue=[], user_text="你叫什么名字？", client=client
    )

    assert grade.slots_filled == ["partner_name"]
    assert parse.await_count == 1


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
    client, parse = _fake_client(GraderResult(coherence="on_track"))
    await grader.grade(
        scenario=SCENARIO, dialogue=[], user_text="你好", client=client
    )
    assert parse.await_args.kwargs["timeout"] == config.GRADER_TIMEOUT_S
