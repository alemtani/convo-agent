"""B1: the pure logic under the grader settings sweep.

The sweep itself calls a model, so it is a script and not a test (the same line
`evals/coherence/replay.py` draws). What *is* testable is everything that turns
observations into a recommendation: the accuracy tally, the latency summary, and
the `max_tokens` cap read off measured output.

The cap is the sharp one. `GRADER_MAX_TOKENS` caps thinking plus output
together, and a grade that runs past it comes back `stop_reason: max_tokens`,
raises `GraderError`, and credits the learner nothing
(`workers/grader.py`). So a cap read off a censored measurement is a way to
silently start losing grades — and a measurement taken at a cap that some run
actually hit *is* censored.
"""
import pytest

from evals.grader.score import (
    GraderObservation,
    SweepError,
    percentile,
    recommend_max_tokens,
    score_settings,
)
from evals.grader.settings import DEFAULT_MATRIX, Setting, applied
from evals.coherence.cases import Gold


def gold(case_id, slots=(), coherence="on_track", credit_ok=True):
    return Gold(
        case_id=case_id,
        coherence=coherence,
        credit_ok=credit_ok,
        slots_established=tuple(slots),
    )


def observation(setting_id, case_id, slots=(), **kwargs):
    return GraderObservation(
        setting_id=setting_id,
        case_id=case_id,
        coherence=kwargs.pop("coherence", "on_track"),
        slots_filled=tuple(slots),
        latency_ms=kwargs.pop("latency_ms", 1000),
        output_tokens=kwargs.pop("output_tokens", 100),
        input_tokens=kwargs.pop("input_tokens", 500),
        **kwargs,
    )


# --- Setting ---------------------------------------------------------------


def test_a_setting_names_itself_by_model_and_effort():
    setting = Setting(model="claude-opus-5", effort="medium", max_tokens=4096)
    assert setting.id == "claude-opus-5/medium"


def test_applying_a_setting_moves_the_config_the_grader_reads():
    """`build_request` reads `config.GRADER_*` at call time, so the sweep can
    swap settings without adding an eval-only argument to a production worker."""
    from backend import config

    before = (config.GRADER_MODEL, config.GRADER_EFFORT, config.GRADER_MAX_TOKENS)
    setting = Setting(model="claude-sonnet-5", effort="low", max_tokens=2048)
    with applied(setting):
        assert config.GRADER_MODEL == "claude-sonnet-5"
        assert config.GRADER_EFFORT == "low"
        assert config.GRADER_MAX_TOKENS == 2048
    assert (config.GRADER_MODEL, config.GRADER_EFFORT, config.GRADER_MAX_TOKENS) == before


def test_the_config_is_restored_even_when_the_body_raises():
    from backend import config

    before = config.GRADER_MODEL
    with pytest.raises(ZeroDivisionError):
        with applied(Setting(model="claude-sonnet-5", effort="low", max_tokens=2048)):
            1 / 0
    assert config.GRADER_MODEL == before


def test_the_default_matrix_crosses_both_models_with_both_efforts():
    ids = {setting.id for setting in DEFAULT_MATRIX}
    assert ids == {
        "claude-opus-5/medium",
        "claude-opus-5/low",
        "claude-sonnet-5/medium",
        "claude-sonnet-5/low",
    }


def test_every_matrix_setting_shares_one_max_tokens():
    """The sweep varies model and effort. Varying the budget at the same time
    would make a lost grade unattributable, and the budget is measured *from*
    this run rather than being one of its variables."""
    assert len({setting.max_tokens for setting in DEFAULT_MATRIX}) == 1


# --- Accuracy --------------------------------------------------------------


def test_a_setting_that_matches_gold_every_run_scores_every_run_exact():
    labels = {"a": gold("a", ["name"])}
    observations = [observation("opus/medium", "a", ["name"]) for _ in range(3)]
    (score,) = score_settings(observations, labels)
    assert (score.runs, score.exact, score.spurious_runs, score.missed_runs) == (3, 3, 0, 0)
    assert score.exact_rate == 1.0


def test_credit_gold_did_not_award_counts_spurious_not_missed():
    labels = {"a": gold("a", [])}
    (score,) = score_settings([observation("s", "a", ["recommendation"])], labels)
    assert (score.exact, score.spurious_runs, score.missed_runs) == (0, 1, 0)
    assert score.spurious == {"recommendation": 1}


def test_credit_gold_awarded_and_the_grader_withheld_counts_missed():
    labels = {"a": gold("a", ["self_name", "partner_name"])}
    (score,) = score_settings([observation("s", "a", ["self_name"])], labels)
    assert (score.exact, score.spurious_runs, score.missed_runs) == (0, 0, 1)
    assert score.missed == {"partner_name": 1}


def test_one_run_can_be_both_spurious_and_missed():
    labels = {"a": gold("a", ["wanted"])}
    (score,) = score_settings([observation("s", "a", ["other"])], labels)
    assert (score.spurious_runs, score.missed_runs) == (1, 1)


def test_slot_order_is_never_a_disagreement():
    labels = {"a": gold("a", ["x", "y"])}
    (score,) = score_settings([observation("s", "a", ["y", "x"])], labels)
    assert score.exact == 1


def test_each_setting_is_scored_apart_from_the_others():
    labels = {"a": gold("a", ["name"])}
    scores = score_settings(
        [
            observation("opus/medium", "a", ["name"]),
            observation("sonnet/low", "a", []),
        ],
        labels,
    )
    by_id = {score.setting_id: score for score in scores}
    assert by_id["opus/medium"].exact == 1
    assert by_id["sonnet/low"].exact == 0


def test_settings_come_back_in_a_stable_order():
    labels = {"a": gold("a")}
    observations = [observation(name, "a") for name in ("b", "a", "c")]
    assert [s.setting_id for s in score_settings(observations, labels)] == ["a", "b", "c"]


def test_an_observation_with_no_gold_label_is_an_error_not_a_zero():
    """A hole in the matrix that scores as a pass is the one failure a
    measurement must never have."""
    with pytest.raises(SweepError):
        score_settings([observation("s", "unlabelled")], {"a": gold("a")})


# --- Latency ---------------------------------------------------------------


def test_percentile_interpolates_between_samples():
    assert percentile([10, 20], 50) == 15.0


def test_percentile_of_one_sample_is_that_sample():
    assert percentile([42], 95) == 42.0


def test_percentile_of_nothing_is_none_rather_than_zero():
    """Zero milliseconds is the fastest possible grade. `None` is 'not measured',
    and the two must not render the same."""
    assert percentile([], 50) is None


def test_a_setting_summarises_the_latency_of_its_own_runs():
    labels = {"a": gold("a")}
    observations = [
        observation("s", "a", latency_ms=1000),
        observation("s", "a", latency_ms=2000),
        observation("s", "a", latency_ms=3000),
    ]
    (score,) = score_settings(observations, labels)
    assert score.p50_latency_ms == 2000
    assert score.max_latency_ms == 3000


def test_latency_is_not_summarised_for_a_replayed_run():
    """A cassette replay returns in microseconds. Reporting that as the grader's
    latency would say the branch costs nothing, which is the exact claim this
    step exists to check."""
    labels = {"a": gold("a")}
    (score,) = score_settings([observation("s", "a", latency_ms=None)], labels)
    assert score.p50_latency_ms is None
    assert score.runs == 1


# --- The max_tokens cap ----------------------------------------------------


def test_the_cap_clears_the_largest_grade_anyone_observed():
    observations = [
        observation("s", "a", output_tokens=300),
        observation("s", "a", output_tokens=1100),
    ]
    assert recommend_max_tokens(observations, ceiling=4096) > 1100


def test_the_cap_is_a_multiple_of_the_observed_maximum_not_a_hair_over_it():
    """Headroom is the whole point: over the cap is a `GraderError` and a turn
    the learner earns nothing for, so the cap is sized against grades harder
    than any in the corpus."""
    assert recommend_max_tokens(
        [observation("s", "a", output_tokens=500)], ceiling=4096, factor=2.0
    ) == 1024


def test_the_cap_rounds_up_to_a_round_number():
    assert recommend_max_tokens(
        [observation("s", "a", output_tokens=530)], ceiling=4096, factor=2.0
    ) == 1280


def test_the_cap_never_exceeds_the_budget_it_was_measured_under():
    """Every observation was drawn at the current ceiling, so the corpus is
    silent about anything above it. Recommending past it would be extrapolation
    dressed as measurement."""
    assert recommend_max_tokens(
        [observation("s", "a", output_tokens=3000)], ceiling=4096, factor=2.0
    ) == 4096


def test_a_run_that_hit_the_ceiling_censors_the_measurement():
    """`stop_reason: max_tokens` means the grade was cut off, so the observed
    output_tokens is the cap rather than what the grade wanted. Cutting the
    budget on that number would make the next one worse."""
    with pytest.raises(SweepError):
        recommend_max_tokens(
            [observation("s", "a", output_tokens=4096)], ceiling=4096
        )


def test_a_missing_token_count_is_an_error_not_a_zero():
    with pytest.raises(SweepError):
        recommend_max_tokens([observation("s", "a", output_tokens=None)], ceiling=4096)


def test_no_observations_recommend_nothing():
    with pytest.raises(SweepError):
        recommend_max_tokens([], ceiling=4096)
