"""WS1 Stage 0 — the measuring instrument itself.

`timing` is pure logic (a stopwatch over an injectable clock plus nearest-rank
percentiles), so it gets real red-green TDD: a fake clock makes every duration
exact, and the harness's aggregation is asserted on hand-computed numbers rather
than on whatever a real run happens to produce.
"""
import pytest

from backend import timing


class FakeClock:
    """A monotonic clock we advance by hand, in seconds."""

    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


# --- Timer ----------------------------------------------------------------


def test_stage_records_elapsed_milliseconds():
    clock = FakeClock()
    timer = timing.Timer(clock=clock)

    with timer.stage("stt"):
        clock.advance(1.25)

    assert timer.stages == {"stt": 1250.0}


def test_stages_are_recorded_independently():
    clock = FakeClock()
    timer = timing.Timer(clock=clock)

    with timer.stage("stt"):
        clock.advance(1.0)
    with timer.stage("claude"):
        clock.advance(3.5)

    assert timer.stages == {"stt": 1000.0, "claude": 3500.0}


def test_total_ms_spans_the_whole_turn_not_just_the_stages():
    """Total is measured from Timer construction, so any un-instrumented work
    between stages (KB load, response shaping) still shows up in the number we
    compare against the p50 target."""
    clock = FakeClock()
    timer = timing.Timer(clock=clock)

    clock.advance(0.1)          # untimed work before the first stage
    with timer.stage("stt"):
        clock.advance(1.0)
    clock.advance(0.4)          # untimed work after the last stage

    assert timer.total_ms == 1500.0


def test_stage_records_the_duration_even_when_the_body_raises():
    """A degraded PA still cost wall-clock time. If the failure path dropped its
    timing we would misread a slow-then-failing Azure call as a free stage."""
    clock = FakeClock()
    timer = timing.Timer(clock=clock)

    with pytest.raises(ValueError):
        with timer.stage("pa"):
            clock.advance(2.0)
            raise ValueError("azure said no")

    assert timer.stages == {"pa": 2000.0}


def test_durations_are_rounded_to_a_tenth_of_a_millisecond():
    clock = FakeClock()
    timer = timing.Timer(clock=clock)

    with timer.stage("stt"):
        clock.advance(0.0123456)

    assert timer.stages["stt"] == 12.3


async def test_timer_measures_concurrent_stages_separately():
    """PA and Claude overlap under `asyncio.gather`; each branch must report its
    own duration, not the duration of the pair. This is the number Stage 2 turns
    on — whether PA is genuinely hidden behind Claude."""
    import asyncio

    timer = timing.Timer()

    async def branch(name, seconds):
        with timer.stage(name):
            await asyncio.sleep(seconds)

    await asyncio.gather(branch("pa", 0.05), branch("claude", 0.15))

    assert timer.stages["pa"] == pytest.approx(50, abs=40)
    assert timer.stages["claude"] == pytest.approx(150, abs=40)
    # The point of the assertion: the fast branch is not charged for the slow one.
    assert timer.stages["pa"] < timer.stages["claude"]


# --- percentiles / summarize ----------------------------------------------


def test_percentile_uses_nearest_rank():
    values = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert timing.percentile(values, 50) == 5      # ceil(0.50 * 10) = 5th value
    assert timing.percentile(values, 95) == 10     # ceil(0.95 * 10) = 10th value


def test_percentile_of_a_single_sample_is_that_sample():
    assert timing.percentile([42.0], 50) == 42.0
    assert timing.percentile([42.0], 95) == 42.0


def test_percentile_of_nothing_is_none():
    assert timing.percentile([], 50) is None


def test_percentile_does_not_mutate_the_caller_list():
    values = [3, 1, 2]
    timing.percentile(values, 50)
    assert values == [3, 1, 2]


def test_summarize_reports_n_p50_p95_per_stage():
    samples = [
        {"stt": 1000, "claude": 3000, "total": 4000},
        {"stt": 1200, "claude": 3400, "total": 4600},
        {"stt": 1100, "claude": 3200, "total": 4300},
    ]

    summary = timing.summarize(samples)

    assert summary["stt"] == {"n": 3, "p50": 1100, "p95": 1200}
    assert summary["claude"] == {"n": 3, "p50": 3200, "p95": 3400}
    assert summary["total"] == {"n": 3, "p50": 4300, "p95": 4600}


def test_summarize_counts_only_the_runs_a_stage_actually_ran():
    """PA is absent from a turn where it failed or was skipped. Treating a missing
    stage as zero would flatter the p50 of the exact stage we are deciding
    about."""
    samples = [
        {"stt": 1000, "pa": 900, "total": 4000},
        {"stt": 1000, "total": 3800},              # PA degraded off this turn
        {"stt": 1000, "pa": 1100, "total": 4200},
    ]

    summary = timing.summarize(samples)

    assert summary["pa"]["n"] == 2
    assert summary["pa"]["p50"] == 900
    assert summary["stt"]["n"] == 3


def test_summarize_of_no_samples_is_empty():
    assert timing.summarize([]) == {}


# --- report formatting ----------------------------------------------------


def test_format_summary_orders_stages_along_the_critical_path():
    report = timing.format_summary(
        {
            "total": {"n": 3, "p50": 4300.0, "p95": 4600.0},
            "claude": {"n": 3, "p50": 3200.0, "p95": 3400.0},
            "stt": {"n": 3, "p50": 1100.0, "p95": 1200.0},
        }
    )
    stages = [line.split()[0] for line in report.splitlines()[1:]]
    assert stages == ["stt", "claude", "total"]


def test_format_summary_flags_a_stage_that_did_not_run_every_time():
    """A p50 computed over 6 of 10 runs is a different claim from one over 10;
    the report has to say so or the number gets quoted as if it were solid."""
    report = timing.format_summary(
        {
            "stt": {"n": 10, "p50": 1000.0, "p95": 1200.0},
            "pa": {"n": 6, "p50": 900.0, "p95": 1100.0},
            "total": {"n": 10, "p50": 4000.0, "p95": 4400.0},
        }
    )
    pa_line = next(l for l in report.splitlines() if l.startswith("pa"))
    assert "6/10" in pa_line


def test_format_summary_of_nothing_says_so():
    assert "no samples" in timing.format_summary({}).lower()
