"""V0/A1: the logic under the `coherence` measurement (`docs/VALIDITY.md`).

Two halves, both deterministic and both tested here: loading a recorded case
set with its gold labels held *separately* from the transcripts, and turning a
set of observed tags into a gate recommendation — including the recommendation
that no gate is safe.

Replay itself runs off cassettes (`evals/cassettes/`). A1's dense-turn cases
assert slot credit against those recordings, and all three pass since A3
rewrote the grader's `slots_filled` instruction.
"""
import json

import pytest

from backend import termination
from evals.coherence.cases import (
    CaseError,
    Gold,
    load_cases,
    load_gold,
    paired,
)
from evals.coherence.matrix import (
    Observation,
    confusion,
    evaluate_gates,
    recommend,
)


def _write_case(dirpath, case_id, **overrides):
    payload = {
        "id": case_id,
        "topic_id": "food-ordering",
        "sketch": "A brisk server.",
        "dialogue": [{"role": "partner", "zh": "你要喝什么？", "pinyin": "nǐ yào hē shénme?"}],
        "learner_turn": "什么菜最好吃？",
        "notes": "",
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
    assert cases[0].topic_id == "food-ordering"
    assert cases[0].learner_turn == "什么菜最好吃？"
    assert cases[0].dialogue[0]["zh"] == "你要喝什么？"


def test_load_cases_ignores_the_gold_file(tmp_path):
    _write_case(tmp_path, "only-case")
    _write_gold(tmp_path, {"only-case": {"coherence": "drifting", "credit_ok": False}})

    assert [c.id for c in load_cases(str(tmp_path))] == ["only-case"]


def test_load_cases_ignores_every_label_file_not_just_the_default_one(tmp_path):
    """A second opinion is a label set, not a case. It must not become one."""
    _write_case(tmp_path, "only-case")
    (tmp_path / "gold.second-opinion.json").write_text(
        json.dumps({"only-case": {"coherence": "on_track", "credit_ok": True}}),
        encoding="utf-8",
    )

    assert [c.id for c in load_cases(str(tmp_path))] == ["only-case"]


def test_load_cases_rejects_a_filename_that_disagrees_with_its_id(tmp_path):
    _write_case(tmp_path, "named-one", id="named-two")

    with pytest.raises(CaseError, match="named-one"):
        load_cases(str(tmp_path))


def test_load_gold_reads_labels_and_defaults_the_optional_fields(tmp_path):
    _write_gold(
        tmp_path,
        {
            "nonsequitur": {
                "coherence": "drifting",
                "credit_ok": False,
                "slots_established": ["recommendation"],
                "rationale": "answered nothing that was asked",
            },
            "clean": {"coherence": "on_track", "credit_ok": True},
        },
    )

    gold = load_gold(str(tmp_path / "gold.json"))

    assert gold["nonsequitur"] == Gold(
        case_id="nonsequitur",
        coherence="drifting",
        credit_ok=False,
        slots_established=("recommendation",),
        rationale="answered nothing that was asked",
    )
    assert gold["clean"].slots_established == ()
    assert gold["clean"].rationale == ""


def test_load_gold_rejects_a_label_outside_the_schema(tmp_path):
    _write_gold(tmp_path, {"bad": {"coherence": "vibes", "credit_ok": True}})

    with pytest.raises(CaseError, match="vibes"):
        load_gold(str(tmp_path / "gold.json"))


def test_load_gold_rejects_a_missing_credit_decision(tmp_path):
    _write_gold(tmp_path, {"bad": {"coherence": "on_track"}})

    with pytest.raises(CaseError, match="credit_ok"):
        load_gold(str(tmp_path / "gold.json"))


def test_paired_rejects_a_case_with_no_label(tmp_path):
    _write_case(tmp_path, "unlabelled")
    _write_gold(tmp_path, {"other": {"coherence": "on_track", "credit_ok": True}})

    with pytest.raises(CaseError, match="unlabelled"):
        paired(load_cases(str(tmp_path)), load_gold(str(tmp_path / "gold.json")))


def test_paired_rejects_a_label_with_no_case(tmp_path):
    _write_case(tmp_path, "present")
    _write_gold(
        tmp_path,
        {
            "present": {"coherence": "on_track", "credit_ok": True},
            "ghost": {"coherence": "on_track", "credit_ok": True},
        },
    )

    with pytest.raises(CaseError, match="ghost"):
        paired(load_cases(str(tmp_path)), load_gold(str(tmp_path / "gold.json")))


# --- the matrix --------------------------------------------------------------


def _gold(case_id, coherence, credit_ok):
    return Gold(
        case_id=case_id,
        coherence=coherence,
        credit_ok=credit_ok,
        slots_established=(),
        rationale="",
    )


def test_confusion_counts_gold_against_observed():
    gold = {
        "a": _gold("a", "on_track", True),
        "b": _gold("b", "drifting", False),
    }
    observations = [
        Observation(case_id="a", coherence="on_track", slots_filled=()),
        Observation(case_id="a", coherence="drifting", slots_filled=()),
        Observation(case_id="b", coherence="on_track", slots_filled=()),
    ]

    counts = confusion(observations, gold)

    assert counts[("on_track", "on_track")] == 1
    assert counts[("on_track", "drifting")] == 1
    assert counts[("drifting", "on_track")] == 1
    assert counts[("drifting", "off_track")] == 0


def test_a_gate_blocks_gaming_when_the_tag_falls_outside_it():
    gold = {"gaming": _gold("gaming", "drifting", False)}
    observations = [
        Observation(case_id="gaming", coherence="drifting", slots_filled=("recommendation",))
    ]

    strict = _report(evaluate_gates(observations, gold), {"on_track"})

    assert strict.gaming_blocked == 1
    assert strict.gaming_total == 1
    assert strict.rescues_suppressed == 0


def test_a_gate_that_suppresses_an_earned_turn_is_not_safe():
    """The A2 case: a turn the learner earned, tagged as if they had not."""
    gold = {
        "earned": _gold("earned", "on_track", True),
        "gaming": _gold("gaming", "drifting", False),
    }
    observations = [
        Observation(case_id="earned", coherence="drifting", slots_filled=()),
        Observation(case_id="gaming", coherence="drifting", slots_filled=("order",)),
    ]

    strict = _report(evaluate_gates(observations, gold), {"on_track"})

    assert strict.rescues_suppressed == 1
    assert strict.safe is False


def test_recommend_picks_the_strictest_safe_gate_that_blocks_something():
    gold = {
        "earned": _gold("earned", "on_track", True),
        "gaming": _gold("gaming", "off_track", False),
    }
    observations = [
        Observation(case_id="earned", coherence="on_track", slots_filled=()),
        Observation(case_id="gaming", coherence="off_track", slots_filled=("order",)),
    ]

    assert recommend(evaluate_gates(observations, gold)).allow == frozenset({"on_track"})


def test_recommend_prefers_a_looser_gate_over_suppressing_an_earned_turn():
    gold = {
        "earned": _gold("earned", "on_track", True),
        "gaming": _gold("gaming", "off_track", False),
    }
    observations = [
        Observation(case_id="earned", coherence="drifting", slots_filled=()),
        Observation(case_id="gaming", coherence="off_track", slots_filled=("order",)),
    ]

    assert recommend(evaluate_gates(observations, gold)).allow == frozenset(
        {"on_track", "drifting"}
    )


def test_recommend_reports_none_when_no_gate_is_safe():
    """The outcome V0 is allowed to reach: the signal carries nothing."""
    gold = {
        "earned": _gold("earned", "on_track", True),
        "gaming": _gold("gaming", "drifting", False),
    }
    observations = [
        Observation(case_id="earned", coherence="off_track", slots_filled=()),
        Observation(case_id="gaming", coherence="on_track", slots_filled=("order",)),
    ]

    assert recommend(evaluate_gates(observations, gold)) is None


def test_recommend_reports_none_when_a_safe_gate_blocks_no_gaming():
    """A gate that never fires is not a gate; shipping it is pure risk."""
    gold = {
        "earned": _gold("earned", "on_track", True),
        "gaming": _gold("gaming", "drifting", False),
    }
    observations = [
        Observation(case_id="earned", coherence="on_track", slots_filled=()),
        Observation(case_id="gaming", coherence="on_track", slots_filled=("order",)),
    ]

    assert recommend(evaluate_gates(observations, gold)) is None


def test_one_bad_run_out_of_many_makes_a_gate_unsafe():
    """Stochastic tags: a gate that suppresses an earned turn 1 run in 3 is not safe."""
    gold = {
        "earned": _gold("earned", "on_track", True),
        "gaming": _gold("gaming", "off_track", False),
    }
    observations = [
        Observation(case_id="earned", coherence="on_track", slots_filled=()),
        Observation(case_id="earned", coherence="on_track", slots_filled=()),
        Observation(case_id="earned", coherence="drifting", slots_filled=()),
        Observation(case_id="gaming", coherence="off_track", slots_filled=("order",)),
    ]

    strict = _report(evaluate_gates(observations, gold), {"on_track"})

    assert strict.rescues_suppressed == 1
    assert strict.rescues_total == 3
    assert strict.safe is False
    assert recommend(evaluate_gates(observations, gold)).allow == frozenset(
        {"on_track", "drifting"}
    )


def _report(reports, allow):
    return next(r for r in reports if r.allow == frozenset(allow))


def test_gaming_counts_only_runs_where_credit_was_actually_granted():
    """A wandering turn that credits nothing is not a gate's win.

    Counting it would let a gate look useful for stopping something that was
    never going to happen, and V1 would ship on that number.
    """
    gold = {
        "wandered": _gold("wandered", "drifting", False),
        "gaming": _gold("gaming", "drifting", False),
    }
    observations = [
        Observation(case_id="wandered", coherence="drifting", slots_filled=()),
        Observation(
            case_id="gaming", coherence="drifting", slots_filled=("recommendation",)
        ),
    ]

    strict = _report(evaluate_gates(observations, gold), {"on_track"})

    assert strict.gaming_total == 1
    assert strict.gaming_blocked == 1


def test_an_earned_turn_counts_as_a_rescue_even_when_the_tracker_credited_it():
    """The gate sits under both paths, so a suppression is a suppression."""
    gold = {"earned": _gold("earned", "on_track", True)}
    observations = [
        Observation(case_id="earned", coherence="drifting", slots_filled=("self_name",)),
        Observation(case_id="earned", coherence="drifting", slots_filled=()),
    ]

    strict = _report(evaluate_gates(observations, gold), {"on_track"})

    assert strict.rescues_total == 2
    assert strict.rescues_suppressed == 2


# --- the report --------------------------------------------------------------


def test_load_observations_reads_what_replay_wrote(tmp_path):
    from evals.coherence.matrix import load_observations

    path = tmp_path / "observations.json"
    path.write_text(
        json.dumps(
            {
                "model": "claude-sonnet-5",
                "repeat": 1,
                "observations": [
                    {
                        "case_id": "a",
                        "coherence": "drifting",
                        "slots_filled": ["order"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    run = load_observations(str(path))

    assert run.model == "claude-sonnet-5"
    assert run.observations == [
        Observation(case_id="a", coherence="drifting", slots_filled=("order",))
    ]


def test_report_names_the_recommendation_and_shows_the_matrix():
    from evals.coherence.report import render

    gold = {
        "earned": _gold("earned", "on_track", True),
        "gaming": _gold("gaming", "off_track", False),
    }
    observations = [
        Observation(case_id="earned", coherence="on_track", slots_filled=()),
        Observation(case_id="gaming", coherence="off_track", slots_filled=("order",)),
    ]

    text = render(observations, gold, model="claude-sonnet-5")

    assert "claude-sonnet-5" in text
    assert "on_track" in text
    assert "Recommended gate" in text


def test_report_says_so_when_no_gate_is_safe():
    from evals.coherence.report import render

    gold = {
        "earned": _gold("earned", "on_track", True),
        "gaming": _gold("gaming", "drifting", False),
    }
    observations = [
        Observation(case_id="earned", coherence="off_track", slots_filled=()),
        Observation(case_id="gaming", coherence="on_track", slots_filled=("order",)),
    ]

    text = render(observations, gold, model="claude-sonnet-5")

    assert "No safe gate" in text


def test_report_lists_the_per_case_disagreements():
    """The rows a reader needs: which case, what it deserved, what it got."""
    from evals.coherence.report import render

    gold = {"earned": _gold("earned", "on_track", True)}
    observations = [
        Observation(case_id="earned", coherence="drifting", slots_filled=()),
    ]

    text = render(observations, gold, model="claude-sonnet-5")

    assert "earned" in text
    assert "drifting" in text


def test_report_distinguishes_a_gate_that_never_fires_from_one_that_misjudges():
    """Two very different failures; a reader must not have to guess which happened."""
    from evals.coherence.report import render

    gold = {
        "earned": _gold("earned", "on_track", True),
        "gaming": _gold("gaming", "drifting", False),
    }
    never_fires = [
        Observation(case_id="earned", coherence="on_track", slots_filled=()),
        Observation(case_id="gaming", coherence="on_track", slots_filled=("order",)),
    ]
    misjudges = [
        Observation(case_id="earned", coherence="off_track", slots_filled=()),
        Observation(case_id="gaming", coherence="on_track", slots_filled=("order",)),
    ]

    assert "never fires" in render(never_fires, gold, model="m")
    assert "never fires" not in render(misjudges, gold, model="m")


# --- the real corpus ---------------------------------------------------------

CASES_DIR = "tests/fixtures/sessions"

# Stream A table. All three pass on the committed cassettes as of A3, which
# rewrote the `slots_filled` instruction. They were fail-to-pass cases; the
# xfail marks came off in the same PR, because an xfail left on a green test
# is a silent skip of the bug it was recording.
A1_DENSE_CASES = (
    "milk-and-biscuits",
    "computer-work-ni-ne",
    "clip-and-tea",
)


def test_the_shipped_corpus_pairs_with_its_gold_labels():
    """Guard the real fixtures, not just tmp_path copies of their shape."""
    cases = load_cases(CASES_DIR)

    assert len(cases) >= 6
    paired(cases, load_gold(f"{CASES_DIR}/gold.json"))


def test_the_corpus_includes_the_recorded_multi_slot_misses():
    ids = {case.id for case in load_cases(CASES_DIR)}
    assert {"milk-and-biscuits", "computer-work-ni-ne", "clip-and-tea"} <= ids


def test_every_case_carries_the_dialogue_shape_the_client_actually_sends():
    """The measurement is worthless if it replays a history no client submits.

    The live client sends `[]` on turn 1 and strict `user`/`partner` pairs after
    it — the opening line is its own field and is never part of `dialogue`.
    A fixture with a leading partner turn builds a different `messages` array
    than production.
    """
    for case in load_cases(CASES_DIR):
        roles = [turn["role"] for turn in case.dialogue]
        assert len(roles) % 2 == 0, f"{case.id}: dangling turn in {roles}"
        assert roles == ["user", "partner"] * (len(roles) // 2), f"{case.id}: {roles}"


def test_every_turn_one_case_carries_the_opening_line_the_grade_is_judged_against():
    """On turn 1 `dialogue` is empty, so without this field the grader sees
    the learner's words and nothing they answered.
    """
    for case in load_cases(CASES_DIR):
        if case.dialogue:
            continue
        assert case.opening_line and case.opening_line.get("zh"), (
            f"{case.id}: turn 1 with no opening_line"
        )


def test_load_cases_reads_the_opening_line(tmp_path):
    _write_case(
        tmp_path,
        "a-case",
        opening_line={"zh": "你好！", "pinyin": "nǐ hǎo!"},
    )

    assert load_cases(str(tmp_path))[0].opening_line == {
        "zh": "你好！",
        "pinyin": "nǐ hǎo!",
    }


def test_load_cases_accepts_a_bare_opening_string(tmp_path):
    """A fixture written by hand should not be rejected for missing pinyin
    the grader never reads.
    """
    _write_case(tmp_path, "a-case", opening_line="你好！")

    assert load_cases(str(tmp_path))[0].opening_line["zh"] == "你好！"


def test_no_case_claims_a_slot_was_filled_on_a_turn_not_yet_taken():
    """Prior state must be reachable from prior history, or the hint is a fiction."""
    from evals.coherence.replay import _turn_index

    for case in load_cases(CASES_DIR):
        turn = _turn_index(case.dialogue)
        for slot_id, filled_turn in case.state.get("filled_at", {}).items():
            assert filled_turn < turn, (
                f"{case.id}: {slot_id} filled on turn {filled_turn}, "
                f"but the turn under test is {turn}"
            )


def test_the_second_opinion_labels_the_same_cases():
    """A second labeller's pass is only comparable if it covers the same set."""
    cases = load_cases(CASES_DIR)

    paired(cases, load_gold(f"{CASES_DIR}/gold.second-opinion.json"))


def test_replay_derives_the_turn_index_the_orchestrator_does():
    """A third copy of the rule is a third thing that can drift."""
    from backend.orchestrator import _turn_index as shipped
    from evals.coherence.replay import _turn_index as replayed

    for case in load_cases(CASES_DIR):
        assert replayed(case.dialogue) == shipped(case.dialogue), case.id


def test_replay_refuses_to_prune_a_subset_run():
    """`--case` reaches a handful of keys. Pruning off one deletes the rest."""
    from evals.coherence import replay

    with pytest.raises(SystemExit, match="--case"):
        replay._check_prune_is_a_full_sweep(prune=True, cases=["milk-and-biscuits"])


def test_replay_allows_a_prune_of_a_whole_run():
    from evals.coherence import replay

    assert replay._check_prune_is_a_full_sweep(prune=True, cases=None) is None


def test_replay_does_not_call_the_retired_pressure_hint():
    """The name that raised before this runner reached the network."""
    import inspect

    from evals.coherence import replay

    source = inspect.getsource(replay)
    assert "pressure_hint" not in source
    assert "grader_worker.grade" in source


async def test_replay_reads_coherence_and_slots_from_the_grader(monkeypatch):
    """V2 moved both off the converser. A replay that still reads the annotation
    raises before it reaches the network (`termination.pressure_hint` is gone
    too). The measurement has to come from `GraderResult`.
    """
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from backend.models import GraderResult
    from evals.coherence.cases import Case
    from evals.coherence.replay import replay_case

    recorded = GraderResult(
        coherence="drifting", slots_filled=["recommendation", "drinks"]
    )
    grade = AsyncMock(return_value=(recorded, SimpleNamespace()))
    monkeypatch.setattr("backend.workers.grader.grade", grade)

    observation = await replay_case(
        Case(
            id="packed",
            topic_id="food-ordering",
            sketch="A brisk server.",
            dialogue=(),
            learner_turn="什么菜最好吃？有什么喝的？",
            opening_line={"zh": "你好！你要点什么？", "pinyin": "nǐ hǎo!"},
        )
    )

    assert observation.coherence == "drifting"
    assert observation.slots_filled == ("recommendation", "drinks")
    assert grade.await_count == 1
    kwargs = grade.await_args.kwargs
    assert kwargs["user_text"] == "什么菜最好吃？有什么喝的？"
    assert kwargs["opening_line"] == "你好！你要点什么？"
    assert kwargs["dialogue"] == []


# --- the owed-turn path ------------------------------------------------------
#
# The prefix says only this turn goes in `slots_filled`. `render_window_note`
# says judge the earlier ones too. Those two have to compose, and until A3 the
# corpus could not tell you whether they did: every case had
# `last_graded_turn: None`, so `grading_window` was 1 everywhere.


def test_the_corpus_exercises_a_turn_that_owes_an_earlier_grade():
    """Without one case where the window is wider than 1, the note that settles
    a grading debt is prompt nobody has ever measured.
    """
    from backend.models import SessionState
    from evals.coherence.replay import _turn_index

    windows = []
    for case in load_cases(CASES_DIR):
        state = SessionState(**case.state) if case.state else SessionState()
        windows.append(
            termination.grading_window(state, turn=_turn_index(case.dialogue))
        )

    assert max(windows) > 1, "no case makes the grader settle an earlier turn"


def test_an_observation_carries_what_the_owed_turn_established():
    """`slots_filled` alone cannot see the bug. A grader that merged the two
    lists, or dropped the earlier one, looks identical on that field.
    """
    from evals.coherence.matrix import Observation

    assert Observation(case_id="a", coherence="on_track").slots_filled_previously == ()


async def test_replay_reads_both_slot_lists_off_the_grader(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from backend.models import GraderResult
    from evals.coherence.cases import Case
    from evals.coherence.replay import replay_case

    recorded = GraderResult(
        coherence="on_track",
        slots_filled=["order"],
        slots_filled_previously=["drinks"],
    )
    grade = AsyncMock(return_value=(recorded, SimpleNamespace()))
    monkeypatch.setattr("backend.workers.grader.grade", grade)

    observation = await replay_case(
        Case(
            id="owed",
            topic_id="food-ordering",
            sketch="A brisk server.",
            dialogue=(),
            learner_turn="我要一个鱼",
            opening_line={"zh": "你好！", "pinyin": "nǐ hǎo!"},
        )
    )

    assert observation.slots_filled == ("order",)
    assert observation.slots_filled_previously == ("drinks",)


async def test_a_turn_settling_a_debt_credits_the_earlier_turn_separately():
    """The A3 regression guard. The prefix's "only this turn" paragraph must
    not talk the grader out of `slots_filled_previously`, and the earlier
    turn's slots must not be merged into `slots_filled` either.
    """
    from evals import cassette
    from evals.coherence.replay import replay_case

    case = next(c for c in load_cases(CASES_DIR) if c.id == "owed-drinks-then-order")
    client = cassette.CassetteClient()
    for _ in range(3):
        observation = await replay_case(case, client=client)
        assert set(observation.slots_filled) == {"order"}, (
            f"final turn credited {list(observation.slots_filled)}"
        )
        assert set(observation.slots_filled_previously) == {"drinks"}, (
            f"owed turn credited {list(observation.slots_filled_previously)}"
        )


# --- what a failed measurement is allowed to claim ---------------------------


def test_load_gold_rejects_a_credit_decision_that_is_not_a_boolean(tmp_path):
    """`bool("false")` is `True`. A coerced label is a yes nobody wrote."""
    _write_gold(tmp_path, {"bad": {"coherence": "on_track", "credit_ok": "false"}})

    with pytest.raises(CaseError, match="credit_ok"):
        load_gold(str(tmp_path / "gold.json"))


def test_load_observations_rejects_an_unknown_tag(tmp_path):
    """An unknown tag falls outside every gate, including the open one."""
    from evals.coherence.matrix import load_observations

    path = tmp_path / "observations.json"
    path.write_text(
        json.dumps(
            {
                "model": "m",
                "observations": [{"case_id": "a", "coherence": "on-track"}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(CaseError, match="on-track"):
        load_observations(str(path))


def test_report_says_the_signal_is_both_silent_and_harsh_when_it_is():
    """Silent on gaming *and* harsh on earned turns is its own finding."""
    from evals.coherence.report import render

    gold = {
        "earned": _gold("earned", "on_track", True),
        "gaming": _gold("gaming", "drifting", False),
    }
    observations = [
        Observation(case_id="earned", coherence="off_track", slots_filled=()),
        Observation(case_id="gaming", coherence="on_track", slots_filled=("order",)),
    ]

    text = render(observations, gold, model="m")

    assert "harmful as well as silent" in text
    assert "never fires" not in text


def test_report_blames_suppression_only_when_a_gate_actually_blocks_gaming():
    """The 'blocks gaming only by punishing rescues' sentence needs a useful gate."""
    from evals.coherence.report import render

    gold = {
        "earned": _gold("earned", "on_track", True),
        "gaming": _gold("gaming", "off_track", False),
    }
    observations = [
        Observation(case_id="earned", coherence="off_track", slots_filled=()),
        Observation(case_id="gaming", coherence="off_track", slots_filled=("order",)),
    ]

    text = render(observations, gold, model="m")

    assert "also suppresses a turn the learner earned" in text


# --- slot accuracy: the metric V2 is judged on -------------------------------


def test_slot_accuracy_counts_exact_agreement_per_case():
    from evals.coherence.matrix import slot_accuracy

    gold = {"a": _gold_with_slots("a", ("self_name",))}
    observations = [
        Observation(case_id="a", coherence="on_track", slots_filled=("self_name",)),
        Observation(case_id="a", coherence="on_track", slots_filled=()),
    ]

    (report,) = slot_accuracy(observations, gold)

    assert report.case_id == "a"
    assert report.runs == 2
    assert report.exact == 1


def test_slot_accuracy_names_credit_the_learner_never_earned():
    """The gaming failure: a slot credited that gold says was not established."""
    from evals.coherence.matrix import slot_accuracy

    gold = {"gaming": _gold_with_slots("gaming", ())}
    observations = [
        Observation(
            case_id="gaming", coherence="on_track", slots_filled=("recommendation",)
        ),
        Observation(
            case_id="gaming", coherence="on_track", slots_filled=("recommendation",)
        ),
    ]

    (report,) = slot_accuracy(observations, gold)

    assert report.spurious == {"recommendation": 2}
    assert report.missed == {}
    assert report.exact == 0


def test_slot_accuracy_names_credit_the_learner_earned_and_did_not_get():
    """The under-annotation failure A2's floor exists to rescue."""
    from evals.coherence.matrix import slot_accuracy

    gold = {"earned": _gold_with_slots("earned", ("self_name", "partner_name"))}
    observations = [
        Observation(case_id="earned", coherence="on_track", slots_filled=("self_name",)),
    ]

    (report,) = slot_accuracy(observations, gold)

    assert report.missed == {"partner_name": 1}
    assert report.spurious == {}


def test_slot_accuracy_ignores_the_order_slots_were_reported_in():
    from evals.coherence.matrix import slot_accuracy

    gold = {"a": _gold_with_slots("a", ("partner_name", "self_name"))}
    observations = [
        Observation(
            case_id="a", coherence="on_track", slots_filled=("self_name", "partner_name")
        ),
    ]

    assert slot_accuracy(observations, gold)[0].exact == 1


def test_report_scores_slots_against_gold_not_only_coherence():
    from evals.coherence.report import render

    gold = {"gaming": _gold_with_slots("gaming", (), credit_ok=False)}
    observations = [
        Observation(
            case_id="gaming", coherence="on_track", slots_filled=("recommendation",)
        ),
    ]

    text = render(observations, gold, model="m")

    assert "Slot accuracy" in text
    assert "recommendation" in text


def _gold_with_slots(case_id, slots, credit_ok=True):
    return Gold(
        case_id=case_id,
        coherence="on_track",
        credit_ok=credit_ok,
        slots_established=slots,
        rationale="",
    )


# --- A1: the recorded multi-slot misses --------------------------------------
#
# The bug A3 fixed, kept as the guard against it coming back.
#
# **A rate, not a run.** `slots_filled` is a model's output, so a case has a
# success *rate* and not a result. A1 asserted three consecutive exact matches
# against the committed recording, which is one draw asserted three times: at a
# true rate of 0.5 that goes green about one time in eight, and it did — A3
# reported "3/3 on all three cases" off a prompt whose real rate on
# `milk-and-biscuits` was nearer 6/10. A gate you pass by luck is worse than no
# gate, because it is read as evidence.
#
# So the corpus records `DENSE_SAMPLES` draws and the gate is `DENSE_MIN_EXACT`
# of them. Five and four: at a true rate of 0.95 that false-fails 2% of the
# time, and ten samples buys more power to catch a *mediocre* case rather than
# more protection for a good one. Re-recording is the only thing that spends
# money, so the sample count is the weekly job's bill, not every PR's.
#
# The observed rate goes in the failure message on purpose. A case that scrapes
# through at 4/5 must not read the same as one at 5/5.

DENSE_SAMPLES = 5
DENSE_MIN_EXACT = 4

A1_DENSE_CASES = (
    "milk-and-biscuits",
    "computer-work-ni-ne",
    "clip-and-tea",
)


@pytest.mark.parametrize("case_id", A1_DENSE_CASES)
async def test_a_dense_turn_is_credited_for_every_slot_it_established(case_id):
    """One utterance, several slots, credit for every one — most of the time.

    The live sessions in `docs/streams/grading.md` packed a turn and got credit
    for fewer facts than they established. These cases are that record, and
    A3's rewritten `slots_filled` instruction is what moves them.
    """
    from evals import cassette
    from evals.coherence.replay import replay_case

    case = next(c for c in load_cases(CASES_DIR) if c.id == case_id)
    expected = set(load_gold(f"{CASES_DIR}/gold.json")[case_id].slots_established)
    client = cassette.CassetteClient()

    credited = [
        set((await replay_case(case, client=client)).slots_filled)
        for _ in range(DENSE_SAMPLES)
    ]
    exact = sum(1 for slots in credited if slots == expected)

    # A cassette holding fewer draws than the gate asks for would *cycle*, and
    # the repeats would be counted as independent samples. The rate would then
    # be computed from a recording that never measured one.
    (key,) = client.used
    recorded = len(client.store.load(key).samples)
    assert recorded >= DENSE_SAMPLES, (
        f"{case_id}: cassette holds {recorded} samples, gate needs "
        f"{DENSE_SAMPLES}; re-record with --samples {DENSE_SAMPLES}"
    )

    misses = [sorted(slots) for slots in credited if slots != expected]
    assert exact >= DENSE_MIN_EXACT, (
        f"{case_id}: {exact}/{DENSE_SAMPLES} exact, "
        f"need {DENSE_MIN_EXACT}. Expected {sorted(expected)}; "
        f"missed draws credited {misses}"
    )
