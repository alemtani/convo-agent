"""The turn runner: the eval that finally runs the *partner*.

`evals/coherence/replay.py` calls `grader.grade` directly, so every eval in the
repo measures the judge and none of them measures the thing being judged. The
partner's prompt has no coverage at all — which is how A2 cut three fields and a
third of the system prompt with nothing to replay.

This runner drives `orchestrator.run_text_turn`, the seam that threads **one**
client into both workers, so a single cassette-backed call exercises the reply,
the grade and the state advance the way a real turn does.

Two questions it is built to answer, from the observed failures:

- **The grade computed against the converser's own reading**, as production
  does. The grader-only runner feeds it a fixture's 汉字.
- **Over-volunteering.** The partner handing over a `request` slot before the
  learner asks means the learner cannot earn that point. `withholding` is the
  authored constraint against it; this measures whether the partner honours it.
"""
from types import SimpleNamespace

import pytest

from backend import kb
from backend.models import (
    ConversationResult,
    ConverserAnnotation,
    GraderResult,
    Utterance,
)
from evals.coherence.cases import Case, load_cases
from evals.turn import replay, withholding

SESSION_CASES_DIR = "tests/fixtures/sessions"
PROBE_CASES_DIR = "evals/turn/cases"


def _case(**overrides):
    payload = dict(
        id="asks-nothing",
        topic_id="food-ordering",
        sketch="The server is brisk.",
        dialogue=(),
        learner_turn="你好",
        opening_line={"zh": "你好！你要点什么？", "pinyin": "nǐ hǎo!"},
    )
    payload.update(overrides)
    return Case(**payload)


class _Client:
    """One fake standing in for every worker, exactly as the real seam does."""

    def __init__(self, reply="您好！", volunteered=(), slots=()):
        self.schemas = []
        self.reply = reply
        self.volunteered = tuple(volunteered)
        self.slots = tuple(slots)
        self.messages = SimpleNamespace(parse=self._parse)

    async def _parse(self, **request):
        schema = request["output_format"]
        self.schemas.append(schema)
        if schema is GraderResult:
            parsed = GraderResult(coherence="on_track", slots_filled=list(self.slots))
        elif schema is ConversationResult:
            parsed = ConversationResult(
                partner_response=Utterance(zh=self.reply, pinyin=""),
                turn_annotation=ConverserAnnotation(),
                user_reading=Utterance(zh="你好", pinyin="nǐ hǎo"),
            )
        elif schema is withholding.WithholdingVerdict:
            # A real judge can only answer about the slots it was shown, so the
            # fake does too. A test whose fake names a slot the request never
            # carried would be asserting against a judge nobody could build.
            shown = str(request["system"])
            parsed = withholding.WithholdingVerdict(
                volunteered=[s for s in self.volunteered if s in shown],
                rationale="fake",
            )
        else:  # pragma: no cover
            raise AssertionError(f"unexpected schema {schema}")
        return SimpleNamespace(
            stop_reason="end_turn",
            parsed_output=parsed,
            usage=SimpleNamespace(
                input_tokens=1, output_tokens=1,
                cache_read_input_tokens=0, cache_creation_input_tokens=0,
            ),
        )


# --- The runner drives the whole turn ------------------------------------


async def test_one_client_covers_the_partner_and_the_grader():
    # The point of running `run_text_turn` rather than `grade`: both workers are
    # on the same client, so one cassette-backed run covers both.
    client = _Client()
    await replay.replay_case(_case(), client=client)
    assert ConversationResult in client.schemas
    assert GraderResult in client.schemas


async def test_the_observation_carries_the_reply_the_grader_judged():
    client = _Client(reply="我们有茶。")
    observation = await replay.replay_case(_case(), client=client)
    assert observation.reply_zh == "我们有茶。"
    assert observation.reading_zh == "你好"
    # No `coherence`: the turn response carries none — it is the grader's, and
    # the client sees only its consequence. Coherence accuracy stays the
    # grader-only runner's job until A4 puts the field on the annotation.
    assert not hasattr(observation, "coherence")


# --- Over-volunteering ---------------------------------------------------


async def test_a_partner_that_answers_an_unasked_slot_is_a_violation():
    # 你好 asks for nothing, so a reply naming the day's dish hands over
    # `recommendation` — a point the learner can now never earn.
    client = _Client(reply="今天的饺子最好吃！", volunteered=["recommendation"])
    observation = await replay.replay_case(_case(), client=client)
    assert observation.volunteered == ("recommendation",)


async def test_answering_a_slot_the_learner_just_asked_for_is_not_volunteering():
    # The learner asked and the grader credited it, so the partner answering is
    # the scene working. Only unasked slots are candidates.
    client = _Client(
        reply="饺子最好吃！", volunteered=["recommendation"], slots=["recommendation"]
    )
    observation = await replay.replay_case(
        _case(learner_turn="什么菜最好吃？"), client=client
    )
    assert observation.volunteered == ()


async def test_a_filled_slot_is_not_a_candidate_either():
    # Already earned, so there is nothing left to give away.
    client = _Client(reply="饺子最好吃！", volunteered=["recommendation"])
    case = _case(state={"filled_at": {"recommendation": 1}, "consecutive_closes": 0})
    observation = await replay.replay_case(case, client=client)
    assert observation.volunteered == ()


async def test_no_candidate_slots_means_no_judge_call_at_all():
    # The judge is a real call. A turn with nothing left to give away must not
    # spend one — on a recording run that is money, on a replay it is a cassette
    # nobody needs.
    client = _Client(reply="好的。")
    case = _case(
        state={
            "filled_at": {"recommendation": 1, "drinks": 1},
            "consecutive_closes": 0,
        }
    )
    await replay.replay_case(case, client=client)
    assert withholding.WithholdingVerdict not in client.schemas


# --- The judge's request -------------------------------------------------


def test_the_judge_is_asked_about_the_reply_not_the_learner():
    scenario = kb.load_scenario("food-ordering")
    request = withholding.build_request(
        scenario=scenario,
        reply_zh="今天的饺子最好吃！",
        candidates=[s for s in scenario.slots if s.id == "recommendation"],
    )
    prompt = str(request["messages"])
    assert "今天的饺子最好吃！" in prompt
    assert request["output_format"] is withholding.WithholdingVerdict
    # The scene's own words for what it holds back are the standard being
    # applied, so they have to be in the request.
    assert scenario.withholding is not None
    assert scenario.withholding in str(request["system"])


def test_the_judge_only_ever_sees_the_candidate_slots():
    scenario = kb.load_scenario("food-ordering")
    request = withholding.build_request(
        scenario=scenario,
        reply_zh="您好。",
        candidates=[s for s in scenario.slots if s.id == "drinks"],
    )
    blob = str(request["system"]) + str(request["messages"])
    assert "drinks" in blob
    # Naming a slot it must not report on invites it to report on it.
    assert "recommendation" not in blob


def test_a_verdict_naming_a_slot_that_was_not_a_candidate_is_refused():
    # The judge is a model. A hallucinated id would silently become a violation
    # against a slot nobody asked about.
    scenario = kb.load_scenario("food-ordering")
    with pytest.raises(withholding.WithholdingError):
        withholding.checked(
            withholding.WithholdingVerdict(volunteered=["nope"], rationale=""),
            candidates=[s for s in scenario.slots if s.id == "drinks"],
        )


# --- Cassette replay: the merge gate once recordings exist ---------------
#
# A1.5. The runner shipped in PR #93 with no cassettes, so it could not be a
# gate. These tests replay the committed recordings. A miss is a stale prompt,
# the same CassetteMiss A1's dense-turn tests raise when the grader prompt
# moves.


def _case_ids(directory):
    return [case.id for case in load_cases(directory)]


@pytest.mark.parametrize("case_id", _case_ids(SESSION_CASES_DIR))
async def test_a_session_case_replays_off_cassettes(case_id):
    from evals import cassette

    case = next(c for c in load_cases(SESSION_CASES_DIR) if c.id == case_id)
    client = cassette.CassetteClient()
    for _ in range(3):
        await replay.replay_case(case, client=client)


@pytest.mark.parametrize("case_id", _case_ids(PROBE_CASES_DIR))
async def test_a_probe_does_not_hand_over_an_unasked_slot(case_id):
    """The learner asks for nothing, so any slot the reply establishes was
    volunteered — a point they can now never earn.
    """
    from evals import cassette

    case = next(c for c in load_cases(PROBE_CASES_DIR) if c.id == case_id)
    client = cassette.CassetteClient()
    for _ in range(3):
        observation = await replay.replay_case(case, client=client)
        assert observation.volunteered == (), (
            f"{case_id}: partner volunteered {list(observation.volunteered)} "
            f"in {observation.reply_zh!r}"
        )
