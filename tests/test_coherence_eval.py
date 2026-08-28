"""V0/A1: the logic under the grader's measurement (`docs/VALIDITY.md`).

Two halves, both deterministic and both tested here: loading a recorded case
set with its gold labels held *separately* from the transcripts, and turning a
set of observed grades into per-case slot accuracy.

The gate-recommendation half is gone with A4, which ships the gate instead of
searching for one. What is left of `coherence` here is the label schema — two
tags, not three — and the 2×2 the *turn* runner fills, since the tag is the
partner's judgment now and only that runner runs a partner.

Replay itself runs off cassettes (`evals/cassettes/`). A1's dense-turn cases
assert slot credit against those recordings, and all three pass since A3
rewrote the grader's `slots_filled` instruction.
"""
import json
from types import SimpleNamespace

import pytest

from backend import termination
from evals.coherence.cases import (
    COHERENCE_TAGS,
    CaseError,
    Gold,
    load_cases,
    load_gold,
    paired,
)
from evals.coherence.matrix import Observation, confusion


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
    _write_gold(tmp_path, {"only-case": {"coherence": "incoherent", "credit_ok": False}})

    assert [c.id for c in load_cases(str(tmp_path))] == ["only-case"]


def test_load_cases_ignores_every_label_file_not_just_the_default_one(tmp_path):
    """A second opinion is a label set, not a case. It must not become one."""
    _write_case(tmp_path, "only-case")
    (tmp_path / "gold.second-opinion.json").write_text(
        json.dumps({"only-case": {"coherence": "coherent", "credit_ok": True}}),
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
                "coherence": "incoherent",
                "credit_ok": False,
                "slots_established": ["recommendation"],
                "rationale": "answered nothing that was asked",
            },
            "clean": {"coherence": "coherent", "credit_ok": True},
        },
    )

    gold = load_gold(str(tmp_path / "gold.json"))

    assert gold["nonsequitur"] == Gold(
        case_id="nonsequitur",
        coherence="incoherent",
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
    _write_gold(tmp_path, {"bad": {"coherence": "coherent"}})

    with pytest.raises(CaseError, match="credit_ok"):
        load_gold(str(tmp_path / "gold.json"))


def test_paired_rejects_a_case_with_no_label(tmp_path):
    _write_case(tmp_path, "unlabelled")
    _write_gold(tmp_path, {"other": {"coherence": "coherent", "credit_ok": True}})

    with pytest.raises(CaseError, match="unlabelled"):
        paired(load_cases(str(tmp_path)), load_gold(str(tmp_path / "gold.json")))


def test_paired_rejects_a_label_with_no_case(tmp_path):
    _write_case(tmp_path, "present")
    _write_gold(
        tmp_path,
        {
            "present": {"coherence": "coherent", "credit_ok": True},
            "ghost": {"coherence": "coherent", "credit_ok": True},
        },
    )

    with pytest.raises(CaseError, match="ghost"):
        paired(load_cases(str(tmp_path)), load_gold(str(tmp_path / "gold.json")))


# --- the matrix --------------------------------------------------------------


def _gold(case_id, coherence, credit_ok, slots_established=()):
    return Gold(
        case_id=case_id,
        coherence=coherence,
        credit_ok=credit_ok,
        slots_established=tuple(slots_established),
        rationale="",
    )


def _tagged(case_id, coherence):
    """The shape `confusion` reads: a case id and a tag.

    A stand-in for `evals.turn.replay.TurnObservation`, which is what produces
    the tag since A4. Building one here would drag the whole turn — a reply, a
    reading, a withholding judgment — into a test about counting.
    """
    return SimpleNamespace(case_id=case_id, coherence=coherence)


def test_confusion_counts_gold_against_observed():
    gold = {
        "a": _gold("a", "coherent", True),
        "b": _gold("b", "incoherent", False),
    }
    observations = [
        _tagged("a", "coherent"),
        _tagged("a", "incoherent"),
        _tagged("b", "incoherent"),
    ]

    counts = confusion(observations, gold)

    assert counts[("coherent", "coherent")] == 1
    assert counts[("coherent", "incoherent")] == 1
    assert counts[("incoherent", "incoherent")] == 1
    # Zero-filled: an absent cell and an empty one read very differently.
    assert counts[("incoherent", "coherent")] == 0


def test_the_tag_set_is_binary():
    """Three tags collapsed to two in A4 (`docs/streams/grading.md`).

    The tags were built to measure. A gate has one consequence, so `drifting`
    and `off_track` are the same answer to it — and a legitimate topic change
    is caught along with the gaming, which is the cost that collapse was
    accepted at.
    """
    assert COHERENCE_TAGS == ("coherent", "incoherent")


def test_load_observations_reads_what_replay_wrote(tmp_path):
    from evals.coherence.matrix import load_observations

    path = tmp_path / "observations.json"
    path.write_text(
        json.dumps(
            {
                "model": "claude-opus-5",
                "repeat": 2,
                "observations": [
                    {
                        "case_id": "a",
                        "slots_filled": ["order"],
                        "slots_filled_previously": ["drinks"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    run = load_observations(str(path))

    # The model id travels with the numbers: the judgment is that model's.
    assert run.model == "claude-opus-5"
    assert run.repeat == 2
    assert run.observations == [
        Observation(
            case_id="a",
            slots_filled=("order",),
            slots_filled_previously=("drinks",),
        )
    ]


def test_the_grader_runner_records_no_coherence():
    """A4's shape. The grader is not asked, so an `Observation` has nowhere to
    put a tag — a field here could only ever hold an invention."""
    assert not hasattr(Observation(case_id="a"), "coherence")


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


def test_replay_refuses_a_used_manifest_from_a_subset_run():
    """`--case` reaches a handful of keys. A manifest from one would tell the
    sweep every other recording is stale.
    """
    from evals.coherence import replay

    with pytest.raises(SystemExit, match="--case"):
        replay._check_manifest_is_a_full_sweep(
            used_out="/tmp/used.json", cases=["milk-and-biscuits"]
        )


def test_replay_allows_a_used_manifest_from_a_whole_run():
    from evals.coherence import replay

    assert (
        replay._check_manifest_is_a_full_sweep(used_out="/tmp/used.json", cases=None)
        is None
    )


def test_replay_does_not_call_the_retired_pressure_hint():
    """The name that raised before this runner reached the network."""
    import inspect

    from evals.coherence import replay

    source = inspect.getsource(replay)
    assert "pressure_hint" not in source
    assert "grader_worker.grade" in source


async def test_replay_reads_the_credited_slots_from_the_grader(monkeypatch):
    """V2 moved the grade off the converser, and A4 moved coherence back to it.
    What this runner measures is what is left on `GraderResult`: which slots the
    turn filled, read from the grader and from nothing else.
    """
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from backend.models import GraderResult
    from evals.coherence.cases import Case
    from evals.coherence.replay import replay_case

    recorded = GraderResult(slots_filled=["recommendation", "drinks"])
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

    assert Observation(case_id="a").slots_filled_previously == ()


async def test_replay_reads_both_slot_lists_off_the_grader(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from backend.models import GraderResult
    from evals.coherence.cases import Case
    from evals.coherence.replay import replay_case

    recorded = GraderResult(
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
    _write_gold(tmp_path, {"bad": {"coherence": "coherent", "credit_ok": "false"}})

    with pytest.raises(CaseError, match="credit_ok"):
        load_gold(str(tmp_path / "gold.json"))


# --- slot accuracy: the metric V2 is judged on -------------------------------


def test_slot_accuracy_counts_exact_agreement_per_case():
    from evals.coherence.matrix import slot_accuracy

    gold = {"a": _gold_with_slots("a", ("self_name",))}
    observations = [
        Observation(case_id="a", slots_filled=("self_name",)),
        Observation(case_id="a", slots_filled=()),
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
        Observation(case_id="gaming", slots_filled=("recommendation",)),
        Observation(case_id="gaming", slots_filled=("recommendation",)),
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
        Observation(case_id="earned", slots_filled=("self_name",)),
    ]

    (report,) = slot_accuracy(observations, gold)

    assert report.missed == {"partner_name": 1}
    assert report.spurious == {}


def test_slot_accuracy_ignores_the_order_slots_were_reported_in():
    from evals.coherence.matrix import slot_accuracy

    gold = {"a": _gold_with_slots("a", ("partner_name", "self_name"))}
    observations = [
        Observation(case_id="a", slots_filled=("self_name", "partner_name")),
    ]

    assert slot_accuracy(observations, gold)[0].exact == 1


def test_report_scores_slots_against_gold():
    from evals.coherence.report import render

    gold = {"gaming": _gold_with_slots("gaming", (), credit_ok=False)}
    observations = [
        Observation(case_id="gaming", slots_filled=("recommendation",)),
    ]

    text = render(observations, gold, model="m")

    assert "Slot accuracy" in text
    assert "recommendation" in text


def _gold_with_slots(case_id, slots, credit_ok=True):
    return Gold(
        case_id=case_id,
        coherence="coherent",
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
# **Five draws was still too few, and A8 found out the expensive way.** The
# gate ran at `DENSE_SAMPLES = 5, DENSE_MIN_EXACT = 4` and went red on
# `computer-work-ni-ne` the first time A8 re-recorded — 3/5, read as a
# regression the prompt cut had caused. Measured at twenty draws it is 16/20 on
# the new prompt and 17/20 on the old: no regression, and a gate that
# false-fails about a quarter of the time at the rate this case actually has.
# A5's "0 missed of 55" and A3's "3/3" were the same coin landing the other way.
#
# So the depth moved to twenty and the floor to fourteen. At a true rate of 0.85
# that false-fails about 2% of the time and still catches a case that has slipped
# to 0.6. The cost is the weekly job's, not every PR's — replay spends nothing.
#
# The observed rate goes in the failure message on purpose. A case that scrapes
# through must not read the same as one at 20/20.

DENSE_SAMPLES = 20
DENSE_MIN_EXACT = 14

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
