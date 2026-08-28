"""A0.6: structural invariants over worker output, replayed from cassettes.

These carried `@pytest.mark.live` and therefore never ran. Every one of them
was broken — see the PR — and nothing noticed, because an excluded suite rots.
They are here now, in the default run, as a merge gate that spends nothing.

What each asserts is *structure*, never wording: a schema that parses, a reply
with both halves, a slot credited, a slot withheld. That is what a recording
reproduces faithfully, which is why these could move and the cache tests could
not. `tests/test_conversation_live.py` holds what stayed live.

A missing recording fails loudly rather than calling the API. Re-record with
`python -m evals.behavior.record --record --samples 3`.
"""
import pytest

from backend.models import ConverserAnnotation, SketchResult, Utterance
from evals import cassette
from evals.behavior import cases

# Five draws, matching what the scheduled job records (`--samples 5`). A slot
# decision is a model's output and not a function, so one draw says only what
# happened once.
#
# Unlike the dense cases in `test_coherence_eval.py`, these are asserted at
# **every** draw rather than as a rate. They are invariants, not measurements
# with a known miss rate: a bare 你好 must never credit a slot, and a reply must
# always parse. A rate gate on those would be a licence to be wrong sometimes.
DRAWS = 5


@pytest.fixture
def client():
    return cassette.CassetteClient()


async def _draws(client, case_id):
    """`DRAWS` results for one case — and proof they are `DRAWS` real draws.

    A replay *cycles* the samples it has, so a cassette recorded at
    `--samples 1` would hand the same answer back five times and read as five
    passes. The depth check is what makes the loop mean what it says.
    """
    case = cases.BY_ID[case_id]
    results = [await case.run(client) for _ in range(DRAWS)]
    (key,) = client.used
    recorded = len(client.store.load(key).samples)
    assert recorded >= DRAWS, (
        f"{case_id}: cassette holds {recorded} samples, this asserts over "
        f"{DRAWS}; re-record with --samples {DRAWS}"
    )
    return results


async def test_the_partner_reply_parses_into_the_shape_the_client_renders(client):
    """Valid schema, a reply with both halves, a well-formed annotation.

    Length is deliberately not asserted — brevity is shaped by the prompt, and
    a hard character ceiling on a generated reply is brittle.
    """
    for reply, annotation, reading, _usage in await _draws(client, "converser-reply"):
        assert isinstance(reply, Utterance)
        assert reply.zh and reply.pinyin
        assert isinstance(annotation, ConverserAnnotation)
        assert isinstance(annotation.learner_said_goodbye, bool)
        assert isinstance(annotation.coherent, bool)
        # The reading is the seam that lets a beginner type pinyin; it is asked
        # for here (`want_reading` defaults to True) so it must come back.
        assert reading is not None and reading.zh
        # Tone is never the model's to judge, so the field it would go in is not
        # in the schema at all — the server adds it downstream.
        assert not hasattr(annotation, "tone_errors")


async def test_the_partner_is_never_asked_to_judge_the_goal(client):
    """V2's blindness, seen from the output side.

    `slots_filled` is the grader's and must never come back on an annotation: a
    converser that reported it would mean the split had come undone, and it is
    the reason the live version of this test raised.

    Coherence is a different case and no longer belongs on that list. A4 gave it
    back to the partner as `coherent`, because it asks what the partner meant by
    its own last line rather than what the rubric wants — it names no slot and
    no goal, so answering it reveals nothing about what is being scored.
    """
    _reply, annotation, _reading, _usage = (await _draws(client, "converser-reply"))[0]

    assert not hasattr(annotation, "slots_filled")
    assert isinstance(annotation.coherent, bool)


async def test_the_sketch_worker_produces_an_opening_line_and_flavour(client):
    for result in await _draws(client, "sketch-result"):
        assert isinstance(result, SketchResult)
        assert result.opening_line.zh and result.opening_line.pinyin
        assert result.sketch.strip()


# --- what the grader credits, and what it must not ---------------------------


async def test_a_request_slot_is_credited_on_the_ask(client):
    """The bug behind a session on a phone that would not end.

    The learner asked 你叫什么名字 on turn 1 and `partner_name` was never
    credited — not that turn, not any later one — so the goal could not
    complete and the session ran to its cap.

    Under V2 the credit is owed on the ask alone (`GraderResult`): whether the
    partner answered is the partner's performance, and grading the learner on
    it grades the wrong party. The live version of this test still asserted the
    old asked-and-answered rule, against a `dialogue` with a leading partner
    turn that no client sends.
    """
    for grade, _usage in await _draws(client, "grade-asked-for-a-name"):
        assert "partner_name" in grade.slots_filled, (
            "the learner asked for the name; "
            f"grader reported {list(grade.slots_filled)}"
        )


async def test_an_elliptical_question_fills_its_slot(client):
    """你呢 counts — turning the question back is skill, not a shortcut.

    A guard rather than a fix: this must not later be tightened into demanding
    the canonical 你最近怎么样.
    """
    for grade, _usage in await _draws(client, "grade-elliptical-question"):
        assert "wellbeing" in grade.slots_filled, (
            "turning the question back with 你呢 is how a real learner asks this; "
            f"grader reported {list(grade.slots_filled)}"
        )


async def test_a_fact_the_learner_never_asked_for_is_never_credited(client):
    """The mitigation must not become leniency.

    Loosening the grader toward meaning is exactly the change that could start
    crediting facts the *partner* gave away — the mirror-image failure
    `docs/SCENARIOS.md` calls the worse one, because it turns every session
    into a pass. The learner here says 你好 and asks nothing.
    """
    for grade, _usage in await _draws(client, "grade-a-bare-greeting"):
        assert "partner_name" not in grade.slots_filled
        assert "wellbeing" not in grade.slots_filled


# --- the layer under the layer -----------------------------------------------


def test_every_case_the_tests_assert_on_is_one_the_recorder_records():
    """The two readers of `cases.py` must see the same set.

    A case the test knows and the recorder does not is a permanent cassette
    miss; the reverse is a recording nobody checks.
    """
    assert set(cases.BY_ID) == {case.id for case in cases.CASES}
    assert len(cases.CASES) == len(cases.BY_ID)


async def test_a_missing_recording_fails_instead_of_calling_the_api(tmp_path):
    """The property that makes this suite a gate rather than a bill."""
    empty = cassette.CassetteClient(cassette.CassetteStore(tmp_path))

    with pytest.raises(cassette.CassetteMiss):
        await cases.BY_ID["converser-reply"].run(empty)
