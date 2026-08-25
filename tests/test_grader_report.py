"""B1: reading a sweep back off disk, and rendering it.

The report is the deliverable — a table someone decides a config change from —
so the two things it must never do are render a replayed run's wall clock as a
latency, and render a setting's numbers under another setting's name.
"""
import json

import pytest

from evals.coherence.cases import Gold
from evals.grader.report import render
from evals.grader.score import GraderObservation, SweepError, load_observations


def write(tmp_path, payload):
    path = tmp_path / "observations.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def test_a_recorded_sweep_loads_with_its_latencies(tmp_path):
    path = write(
        tmp_path,
        {
            "recorded": True,
            "repeat": 1,
            "max_tokens": 4096,
            "observations": [
                {
                    "setting_id": "claude-opus-5/medium",
                    "case_id": "a",
                    "coherence": "on_track",
                    "slots_filled": ["name"],
                    "latency_ms": 2690.0,
                    "output_tokens": 412,
                    "input_tokens": 900,
                }
            ],
        },
    )
    run = load_observations(path)
    assert run.recorded is True
    assert run.max_tokens == 4096
    assert run.observations[0].latency_ms == 2690.0
    assert run.observations[0].slots_filled == ("name",)


def test_the_budget_the_run_was_taken_at_is_required(tmp_path):
    """`recommend_max_tokens` needs the ceiling to know whether a maximum is a
    measurement or a cut-off. A file that omits it cannot answer that."""
    path = write(tmp_path, {"recorded": True, "repeat": 1, "observations": []})
    with pytest.raises(SweepError):
        load_observations(path)


def test_a_replayed_sweep_loads_with_no_latency_at_all(tmp_path):
    path = write(
        tmp_path,
        {
            "recorded": False,
            "repeat": 1,
            "max_tokens": 4096,
            "observations": [
                {
                    "setting_id": "s",
                    "case_id": "a",
                    "coherence": "on_track",
                    "slots_filled": [],
                    "latency_ms": None,
                    "output_tokens": 100,
                    "input_tokens": 900,
                }
            ],
        },
    )
    assert load_observations(path).observations[0].latency_ms is None


def test_the_report_names_every_setting_it_swept():
    gold = {"a": Gold(case_id="a", coherence="on_track", credit_ok=True,
                      slots_established=("name",))}
    observations = [
        GraderObservation("claude-opus-5/medium", "a", "on_track", ("name",), 2600, 400, 900),
        GraderObservation("claude-sonnet-5/low", "a", "on_track", (), 900, 120, 900),
    ]
    table = render(observations, gold, max_tokens=4096, recorded=True)
    assert "claude-opus-5/medium" in table
    assert "claude-sonnet-5/low" in table


def test_a_replayed_report_says_the_latency_was_not_measured():
    """The one wrong number this report could print is a fast one it did not
    earn. A replay must read as absent, never as zero."""
    gold = {"a": Gold(case_id="a", coherence="on_track", credit_ok=True)}
    table = render(
        [GraderObservation("s", "a", "on_track", (), None, 100, 900)],
        gold,
        max_tokens=4096,
        recorded=False,
    )
    assert "not measured" in table
    assert "0ms" not in table


# --- Failures ---------------------------------------------------------------


def test_a_grade_that_never_landed_is_carried_not_dropped(tmp_path):
    """A timeout is the loudest latency result there is, and a sweep that only
    counts the calls that returned reports the slow setting as the fast one."""
    path = write(
        tmp_path,
        {
            "recorded": True,
            "repeat": 1,
            "max_tokens": 4096,
            "observations": [],
            "failures": [
                {
                    "setting_id": "claude-sonnet-5/medium",
                    "case_id": "elliptical-ni-ne",
                    "error": "grader timed out after 15s",
                }
            ],
        },
    )
    run = load_observations(path)
    assert run.failures == [
        ("claude-sonnet-5/medium", "elliptical-ni-ne", "grader timed out after 15s")
    ]


def test_a_run_with_no_failures_key_reads_as_none_recorded(tmp_path):
    path = write(
        tmp_path,
        {"recorded": True, "repeat": 1, "max_tokens": 4096, "observations": []},
    )
    assert load_observations(path).failures == []


def test_the_report_names_the_setting_that_lost_a_grade():
    gold = {"a": Gold(case_id="a", coherence="on_track", credit_ok=True)}
    table = render(
        [GraderObservation("claude-opus-5/low", "a", "on_track", (), 900, 60, 900)],
        gold,
        max_tokens=4096,
        recorded=True,
        failures=[("claude-sonnet-5/medium", "a", "grader timed out after 15s")],
    )
    assert "claude-sonnet-5/medium" in table
    assert "timed out" in table


def test_a_setting_that_lost_no_grade_is_not_accused_of_one():
    gold = {"a": Gold(case_id="a", coherence="on_track", credit_ok=True)}
    table = render(
        [GraderObservation("claude-opus-5/low", "a", "on_track", (), 900, 60, 900)],
        gold,
        max_tokens=4096,
        recorded=True,
        failures=[],
    )
    assert "timed out" not in table
