"""V0: the pure logic under the `coherence` measurement (`docs/VALIDITY.md`).

Two halves, both deterministic and both tested here: loading a recorded case
set with its gold labels held *separately* from the transcripts, and turning a
set of observed tags into a gate recommendation — including the recommendation
that no gate is safe.

The live half (replaying cases through the conversation worker) is a script,
not a test: it costs money and its output is a report, not an assertion.
"""
import json

import pytest

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


def test_the_shipped_corpus_pairs_with_its_gold_labels():
    """Guard the real fixtures, not just tmp_path copies of their shape."""
    cases = load_cases(CASES_DIR)

    assert len(cases) >= 6
    paired(cases, load_gold(f"{CASES_DIR}/gold.json"))


def test_every_case_carries_the_dialogue_shape_the_client_actually_sends():
    """The measurement is worthless if it replays a history no client submits.

    The live client sends `[]` on turn 1 and strict `user`/`partner` pairs after
    it — the opening line lives in the sketch and is never part of `dialogue`.
    A fixture with a leading partner turn builds a different `messages` array
    than production and shifts the pressure hint with it.
    """
    for case in load_cases(CASES_DIR):
        roles = [turn["role"] for turn in case.dialogue]
        assert len(roles) % 2 == 0, f"{case.id}: dangling turn in {roles}"
        assert roles == ["user", "partner"] * (len(roles) // 2), f"{case.id}: {roles}"


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
