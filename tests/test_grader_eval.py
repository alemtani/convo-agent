"""Behavioral evals of the grader, off cassettes.

These three cases used to call `conversation.respond` and read
`annotation.slots_filled`. V2 moved the extractor onto the grader
(`GraderResult`), and a `request` slot fills when the learner asks — the
partner's reply is the partner's performance, not the learner's. The live
file was still looking at the converser, so even after the unpack was
fixed it would have asserted the wrong object's missing field.

Distribution, not one draw: `--samples` recordings of the same request,
asserted on every sample. A lucky single fill is how a prompt "passes".
"""
import pytest

from backend import kb
from backend.workers import grader
from tests.helpers import cassette_draw_count

pytestmark = pytest.mark.cassette

SCENARIO = kb.load_scenario("greetings")


async def _grade(client, *, dialogue, user_text):
    grade, _usage = await grader.grade(
        scenario=SCENARIO,
        dialogue=dialogue,
        user_text=user_text,
        client=client,
    )
    return grade


async def test_a_request_slot_fills_when_asked(cassette_client):
    """The bug behind a session on a phone that would not end.

    The learner asked 你叫什么名字 and `partner_name` was never credited, so
    the goal could not complete and the session ran to its cap. The grader
    credits a request slot on the ask; it does not wait for the partner to
    answer.
    """
    n = cassette_draw_count(cassette_client)
    for _ in range(n):
        grade = await _grade(
            cassette_client,
            dialogue=[{"role": "partner", "zh": "早上好！"}],
            user_text="早上好，你叫什么名字？",
        )
        assert "partner_name" in grade.slots_filled, (
            "the learner asked for the name; "
            f"grader reported {grade.slots_filled}"
        )


async def test_an_elliptical_question_fills_its_slot(cassette_client):
    """你呢 counts — turning the question back is skill, not a shortcut."""
    n = cassette_draw_count(cassette_client)
    for _ in range(n):
        grade = await _grade(
            cassette_client,
            dialogue=[
                {"role": "user", "zh": "我叫亚当。"},
                {"role": "partner", "zh": "认识你很高兴！你最近怎么样？"},
            ],
            user_text="我很好，你呢？",
        )
        assert "wellbeing" in grade.slots_filled, (
            "turning the question back with 你呢 is how a real learner asks this; "
            f"grader reported {grade.slots_filled}"
        )


async def test_a_volunteered_fact_is_still_never_credited(cassette_client):
    """Loosening the extractor toward meaning must not credit a partner giveaway.

    The learner here asks nothing.
    """
    n = cassette_draw_count(cassette_client)
    for _ in range(n):
        grade = await _grade(
            cassette_client,
            dialogue=[{"role": "partner", "zh": "你好！"}],
            user_text="你好。",
        )
        assert "partner_name" not in grade.slots_filled
        assert "wellbeing" not in grade.slots_filled
