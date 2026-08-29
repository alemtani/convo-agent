"""A6.5: the logic under the session review's measurement.

A6 shipped `feedback.review_session` and measured its recall by hand — four
waves of five draws on one topic. That is not a baseline: at five draws a fix
and a lucky wave look the same, which is exactly the mistake A1 made and A3
paid for.

So the corpus is built the way the grader's was: finished sessions in their own
files, gold labels held apart from them, and a rate asserted with the sample
count that produced it. Everything here is deterministic — the loader, the
metric, and the shape of the fixtures. The one test that touches a model
replays committed cassettes and spends nothing.
"""
import json

import pytest

from backend import kb
from evals.review.cases import (
    ReviewCaseError,
    ReviewGold,
    load_cases,
    load_gold,
    paired,
)
from evals.review.recall import ReviewObservation, recall

CASES_DIR = "evals/review/cases"


def _write_case(dirpath, case_id, **overrides):
    payload = {
        "id": case_id,
        "topic_id": "greetings",
        "opening_line": {"zh": "你好！", "pinyin": "nǐ hǎo!"},
        "dialogue": [
            {"role": "user", "zh": "你好，我叫小明。"},
            {"role": "partner", "zh": "你好！"},
        ],
        "state": {"filled_at": {"self_name": 1}, "last_graded_turn": 1},
    }
    payload.update(overrides)
    (dirpath / f"{case_id}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    return payload


def _write_gold(dirpath, entries):
    (dirpath / "gold.json").write_text(
        json.dumps(entries, ensure_ascii=False), encoding="utf-8"
    )


# --- loading -----------------------------------------------------------------


def test_load_cases_reads_every_case_sorted_by_id(tmp_path):
    _write_case(tmp_path, "b-case")
    _write_case(tmp_path, "a-case")

    cases = load_cases(str(tmp_path))

    assert [c.id for c in cases] == ["a-case", "b-case"]
    assert cases[0].topic_id == "greetings"
    assert cases[0].turns_taken == 1
    assert cases[0].filled == frozenset({"self_name"})


def test_load_cases_ignores_every_label_file(tmp_path):
    _write_case(tmp_path, "only-case")
    _write_gold(tmp_path, {"only-case": {"recoverable": []}})
    (tmp_path / "gold.second-opinion.json").write_text("{}", encoding="utf-8")

    assert [c.id for c in load_cases(str(tmp_path))] == ["only-case"]


def test_load_cases_rejects_a_filename_that_disagrees_with_its_id(tmp_path):
    _write_case(tmp_path, "named-one", id="named-two")

    with pytest.raises(ReviewCaseError, match="named-one"):
        load_cases(str(tmp_path))


def test_load_cases_accepts_a_bare_opening_string(tmp_path):
    _write_case(tmp_path, "a-case", opening_line="你好！")

    assert load_cases(str(tmp_path))[0].opening_line["zh"] == "你好！"


def test_load_gold_reads_labels_and_defaults_the_optional_fields(tmp_path):
    _write_gold(
        tmp_path,
        {
            "owed": {
                "recoverable": ["partner_name"],
                "already_credited": ["self_name"],
                "never_established": ["wellbeing"],
                "rationale": "asked on turn 2",
            },
            "clean": {"recoverable": []},
        },
    )

    gold = load_gold(str(tmp_path / "gold.json"))

    assert gold["owed"] == ReviewGold(
        case_id="owed",
        recoverable=("partner_name",),
        already_credited=("self_name",),
        never_established=("wellbeing",),
        rationale="asked on turn 2",
    )
    assert gold["clean"].recoverable == ()
    assert gold["clean"].never_established == ()


def test_load_gold_rejects_a_label_that_never_says_what_is_owed(tmp_path):
    """An empty list is a real label. A missing field is an unanswered question,
    and defaulting it to empty would manufacture a clean sheet nobody wrote."""
    _write_gold(tmp_path, {"bad": {"already_credited": ["self_name"]}})

    with pytest.raises(ReviewCaseError, match="recoverable"):
        load_gold(str(tmp_path / "gold.json"))


def test_load_gold_rejects_a_slot_that_is_both_owed_and_already_held(tmp_path):
    """Credit the learner already has cannot be recovered. Scoring it would
    count the same point twice and inflate the rate this corpus reports."""
    _write_gold(
        tmp_path,
        {"bad": {"recoverable": ["self_name"], "already_credited": ["self_name"]}},
    )

    with pytest.raises(ReviewCaseError, match="self_name"):
        load_gold(str(tmp_path / "gold.json"))


def test_load_gold_rejects_a_label_field_that_is_not_a_list(tmp_path):
    _write_gold(tmp_path, {"bad": {"recoverable": "partner_name"}})

    with pytest.raises(ReviewCaseError, match="recoverable"):
        load_gold(str(tmp_path / "gold.json"))


def test_paired_rejects_a_case_with_no_label(tmp_path):
    _write_case(tmp_path, "unlabelled")
    _write_gold(tmp_path, {"other": {"recoverable": []}})

    with pytest.raises(ReviewCaseError, match="unlabelled"):
        paired(load_cases(str(tmp_path)), load_gold(str(tmp_path / "gold.json")))


def test_paired_rejects_a_label_with_no_case(tmp_path):
    _write_case(tmp_path, "present")
    _write_gold(tmp_path, {"present": {"recoverable": []}, "ghost": {"recoverable": []}})

    with pytest.raises(ReviewCaseError, match="ghost"):
        paired(load_cases(str(tmp_path)), load_gold(str(tmp_path / "gold.json")))


# --- the metric --------------------------------------------------------------


def _gold(case_id, recoverable=(), never_established=()):
    return ReviewGold(
        case_id=case_id,
        recoverable=tuple(recoverable),
        never_established=tuple(never_established),
    )


def test_recall_is_a_rate_over_slots_and_draws():
    gold = {"a": _gold("a", recoverable=("x", "y"))}
    observations = [
        ReviewObservation(case_id="a", recovered=("x", "y")),
        ReviewObservation(case_id="a", recovered=("x",)),
    ]

    (report,) = recall(observations, gold)

    assert report.runs == 2
    # Two owed slots, two draws: four chances, three taken.
    assert report.owed == 4
    assert report.recovered == 3
    assert report.rate == 0.75
    assert report.per_slot == {"x": 2, "y": 1}


def test_recall_counts_the_draws_that_recovered_everything_owed():
    """The harsher number, and the one the learner feels: a card is right or it
    is not. A draw that finds one of two owed slots is still a wrong card."""
    gold = {"a": _gold("a", recoverable=("x", "y"))}
    observations = [
        ReviewObservation(case_id="a", recovered=("x", "y")),
        ReviewObservation(case_id="a", recovered=("x",)),
    ]

    (report,) = recall(observations, gold)

    assert report.complete == 1
    assert report.complete_rate == 0.5


def test_recall_names_credit_no_turn_in_the_session_earned():
    """The expensive failure. The review is add-only, so a slot invented here
    cannot be taken back before the learner reads the card."""
    gold = {"a": _gold("a", recoverable=(), never_established=("z",))}
    observations = [
        ReviewObservation(case_id="a", recovered=("z",)),
        ReviewObservation(case_id="a", recovered=()),
    ]

    (report,) = recall(observations, gold)

    assert report.spurious == {"z": 1}
    assert report.owed == 0
    assert report.rate == 0.0
    # Nothing was owed, so both draws recovered everything owed.
    assert report.complete == 2


def test_recall_ignores_the_order_slots_came_back_in():
    gold = {"a": _gold("a", recoverable=("y", "x"))}

    (report,) = recall([ReviewObservation(case_id="a", recovered=("x", "y"))], gold)

    assert report.complete == 1


def test_load_observations_reads_what_replay_wrote(tmp_path):
    from evals.review.recall import load_observations

    path = tmp_path / "observations.json"
    path.write_text(
        json.dumps(
            {
                "model": "claude-opus-5",
                "repeat": 20,
                "observations": [{"case_id": "a", "recovered": ["partner_name"]}],
            }
        ),
        encoding="utf-8",
    )

    run = load_observations(str(path))

    # The model id travels with the numbers: the judgment is that model's.
    assert run.model == "claude-opus-5"
    assert run.repeat == 20
    assert run.observations == [
        ReviewObservation(case_id="a", recovered=("partner_name",))
    ]


def test_report_renders_the_rate_with_its_sample_count():
    from evals.review.report import render

    gold = {"a": _gold("a", recoverable=("partner_name",))}
    observations = [
        ReviewObservation(case_id="a", recovered=("partner_name",)),
        ReviewObservation(case_id="a", recovered=()),
    ]

    text = render(observations, gold, model="claude-opus-5")

    assert "partner_name" in text
    # A rate with no denominator is not a measurement.
    assert "1/2" in text


# --- the shipped corpus ------------------------------------------------------


def test_the_corpus_pairs_with_its_gold_labels():
    cases = load_cases(CASES_DIR)

    assert len(cases) >= 6
    paired(cases, load_gold(f"{CASES_DIR}/gold.json"))


def test_the_corpus_spans_more_than_one_topic():
    """A6's measurement was four waves on `greetings`. One topic's wording is
    one topic's wording, and a recall number read off it is a guess about the
    others."""
    topics = {case.topic_id for case in load_cases(CASES_DIR)}

    assert len(topics) >= 3, sorted(topics)


def test_every_case_carries_the_dialogue_shape_the_client_actually_sends():
    """The measurement is worthless if it replays a history no client submits.
    The opening line is its own field and is never part of `dialogue`."""
    for case in load_cases(CASES_DIR):
        roles = [turn["role"] for turn in case.dialogue]
        assert roles == ["user", "partner"] * (len(roles) // 2), f"{case.id}: {roles}"
        assert case.opening_line and case.opening_line["zh"], case.id


def test_every_label_accounts_for_every_slot_in_the_scenario():
    """A slot in none of the three lists is one nobody judged. Recall computed
    over an incomplete labelling reads as evidence while being arithmetic over
    a hole — and the metric would score a real miss as neither."""
    gold = load_gold(f"{CASES_DIR}/gold.json")
    for case in load_cases(CASES_DIR):
        label = gold[case.id]
        authored = {slot.id for slot in kb.load_scenario(case.topic_id).slots}
        labelled = (
            set(label.recoverable)
            | set(label.already_credited)
            | set(label.never_established)
        )
        assert labelled == authored, (
            f"{case.id}: labelled {sorted(labelled)}, scenario has {sorted(authored)}"
        )


def test_already_credited_is_what_the_submitted_state_actually_holds():
    """The label restates the state so a labeller has to look at it. If the two
    disagree, one of them is wrong and the numbers are about the wrong session."""
    gold = load_gold(f"{CASES_DIR}/gold.json")
    for case in load_cases(CASES_DIR):
        assert set(gold[case.id].already_credited) == set(case.filled), case.id


def test_no_case_claims_a_slot_was_filled_on_a_turn_the_session_never_took():
    for case in load_cases(CASES_DIR):
        for slot_id, turn in case.state.get("filled_at", {}).items():
            assert 1 <= turn <= case.turns_taken, f"{case.id}: {slot_id} at {turn}"


def test_every_case_leaves_the_review_something_it_could_add():
    """`review_session` skips a session with every slot filled — an add-only
    pass has nothing to add. A case in that state would record a cassette of
    nothing and score as a perfect draw."""
    for case in load_cases(CASES_DIR):
        authored = {slot.id for slot in kb.load_scenario(case.topic_id).slots}
        assert authored - set(case.filled), f"{case.id}: nothing left to add"


def test_the_corpus_holds_a_session_with_nothing_to_recover():
    """Without one, spurious credit is unmeasurable: every slot the review
    reports would be one gold agrees with."""
    gold = load_gold(f"{CASES_DIR}/gold.json")

    assert any(not label.recoverable for label in gold.values())
    assert any(label.never_established for label in gold.values())


def test_the_corpus_exercises_a_session_whose_last_grades_never_landed():
    """The debt path. `review_session` logs and reports differently when
    `last_graded_turn` is behind the turn count, and a corpus where every
    session was fully graded never reaches it."""
    behind = [
        case
        for case in load_cases(CASES_DIR)
        if (case.state.get("last_graded_turn") or 0) < case.turns_taken
    ]

    assert behind, "no case owes an ungraded turn"


def test_the_corpus_moves_one_slot_through_every_position():
    """A6 read its finding as "questions come back worse than statements". The
    only way to know is to hold the slot, the topic and the wording still and
    move the turn — otherwise a difference in wording and a difference in
    position are the same column of numbers."""
    ids = {case.id for case in load_cases(CASES_DIR)}

    assert {
        "greetings-name-question-oldest-turn",
        "greetings-name-question-mid-session",
        "greetings-name-question-final-turn",
    } <= ids
    # And the statement at the same distance, which is the wording control.
    assert "greetings-name-statement-oldest-turn" in ids


# --- the runner --------------------------------------------------------------


def test_replay_refuses_a_used_manifest_from_a_subset_run():
    from evals.review import replay

    with pytest.raises(SystemExit, match="--case"):
        replay._check_manifest_is_a_full_sweep(used_out="/tmp/used.json", cases=["x"])


def test_replay_builds_the_verdict_request_the_route_receives():
    from evals.review.replay import request_for

    case = next(
        c for c in load_cases(CASES_DIR) if c.id == "greetings-name-question-mid-session"
    )

    request = request_for(case)

    assert request.topic_id == "greetings"
    assert [t.role for t in request.dialogue] == ["user", "partner"] * 3
    assert request.opening_line.zh == "你好！"
    assert request.state.filled == {"self_name", "wellbeing"}
    assert request.state.last_graded_turn == 3


async def test_replay_reports_the_credit_the_review_added_and_nothing_else(monkeypatch):
    """The observation is a *diff*. A pass that returned the state unchanged and
    one that re-reported what the learner already had would otherwise look the
    same, and only one of them helped."""
    from unittest.mock import AsyncMock

    from backend.models import SessionState
    from evals.review.replay import replay_case

    case = next(
        c for c in load_cases(CASES_DIR) if c.id == "greetings-name-question-mid-session"
    )
    reviewed = SessionState(
        filled_at={"self_name": 1, "wellbeing": 3, "partner_name": 3},
        last_graded_turn=3,
    )
    monkeypatch.setattr(
        "backend.workers.feedback.review_session", AsyncMock(return_value=reviewed)
    )

    observation = await replay_case(case)

    assert observation.recovered == ("partner_name",)


async def test_a_failed_review_scores_as_a_draw_that_recovered_nothing(monkeypatch):
    """`review_session` swallows a `GraderError` and echoes the submitted state.
    That is the shipped degradation, and the learner's card is the poorer for
    it — so it must count as a miss, never be skipped out of the denominator."""
    from unittest.mock import AsyncMock

    from evals.review.replay import replay_case

    case = next(
        c for c in load_cases(CASES_DIR) if c.id == "greetings-name-question-mid-session"
    )
    monkeypatch.setattr(
        "backend.workers.feedback.review_session",
        AsyncMock(side_effect=lambda req, **kw: req.state),
    )

    observation = await replay_case(case)

    assert observation.recovered == ()


# --- the gate ----------------------------------------------------------------
#
# **A rate, with the sample count that produced it.** A6 measured this pass at
# five draws and read a fix out of a wave. Twenty is what the corpus records and
# what the gate judges, and the depth check below is what stops a shallower
# cassette *cycling* its samples and reading as twenty independent draws.
#
# Two assertions, and they are deliberately different in kind:
#
# - **Spurious credit is asserted at every draw, not as a rate.** The review is
#   add-only, so a slot it invents cannot be taken back before the learner reads
#   the card. A rate gate there would be a licence to be wrong sometimes about
#   the one thing that cannot be undone.
# - **Recall is a floor, set at the measured baseline.** It is a regression
#   guard, not a target: A8's job is to raise it, and a floor set where the
#   prompt already is means a cut that grades worse fails the build.

REVIEW_DRAWS = 20

# **Raised from 0.80 to 0.95 by A6.6**, which took the corpus to 220/220 — every
# owed slot, every draw. A floor left at the old baseline would have let the
# review fall the whole way back to A6.5's 86% and still go green, which is a
# gate that only detects a catastrophe. Still set under the measurement rather
# than at it: 100% is not a rate anyone can promise, the corpus is re-recorded
# weekly, and 0.95 leaves eleven misses of 220 for noise while failing on any
# real return of the earlier-turn bug.
REVIEW_MIN_RECALL = 0.95


async def _draws_for(case):
    """`REVIEW_DRAWS` reviews of one session, and proof they are that many
    distinct draws.

    A fresh client per case, so `used` holds exactly one key and the depth check
    below means what it says — a client shared across cases accumulates keys and
    the check would unpack a tuple of several. A replay *cycles* the samples it
    has, so a cassette recorded shallower than this gate would hand the same
    answer back twenty times and read as twenty passes.
    """
    from evals import cassette
    from evals.review.replay import replay_case

    client = cassette.CassetteClient()
    observations = [await replay_case(case, client=client) for _ in range(REVIEW_DRAWS)]
    (key,) = client.used
    recorded = len(client.store.load(key).samples)
    assert recorded >= REVIEW_DRAWS, (
        f"{case.id}: cassette holds {recorded} samples, this gate judges "
        f"{REVIEW_DRAWS}; re-record with --samples {REVIEW_DRAWS}"
    )
    return observations


async def test_the_review_never_invents_credit_no_turn_earned():
    """The failure that cannot be undone. `greetings-nothing-to-recover` is a
    session where the learner never asks the partner's name and never asks how
    they have been; anything the review reports there lands on a card the
    learner reads as truth."""
    gold = load_gold(f"{CASES_DIR}/gold.json")
    for case in load_cases(CASES_DIR):
        never = set(gold[case.id].never_established)
        if not never:
            continue
        for observation in await _draws_for(case):
            assert not (set(observation.recovered) & never), (
                f"{case.id}: review credited {sorted(set(observation.recovered) & never)}, "
                "which no turn of that session establishes"
            )


async def test_the_review_gives_back_most_of_the_credit_it_owes():
    """The baseline this stream's next step is measured against."""
    from evals.review.recall import recall

    gold = load_gold(f"{CASES_DIR}/gold.json")
    observations = []
    for case in load_cases(CASES_DIR):
        observations += await _draws_for(case)

    reports = recall(observations, gold)
    owed = sum(r.owed for r in reports)
    found = sum(r.recovered for r in reports)

    misses = {r.case_id: sorted(set(r.expected) - set(r.per_slot)) for r in reports}
    assert found / owed >= REVIEW_MIN_RECALL, (
        f"{found}/{owed} owed slots recovered over {REVIEW_DRAWS} draws, "
        f"need {REVIEW_MIN_RECALL:.0%}. Never recovered: "
        f"{ {k: v for k, v in misses.items() if v} }"
    )
