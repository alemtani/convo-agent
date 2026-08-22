"""Per-stage wall-clock timing — WS1 Stage 0's measuring instrument.

A spoken turn is `STT + max(PA, Claude)`. Which of PA or Claude dominates decides
whether moving PA off the turn buys wall-clock time at all, so every stage is
timed separately, including the two that overlap under `asyncio.gather`.

Two pieces, both pure logic:

- `Timer` — a stopwatch whose `stage()` blocks record their own elapsed time.
  Concurrency-safe for our purposes because each `with` block closes over its own
  start instant rather than a shared "current stage" cursor, so overlapping
  branches each report their own duration.
- `percentile` / `summarize` — the replay harness's aggregation, kept here (not in
  the script) so it is covered by the normal test run.

Timings are reported in **milliseconds**, rounded to a tenth: sub-millisecond
precision is noise next to a multi-second turn, and round numbers read better in
a log line.
"""
import math
import time
from contextlib import contextmanager
from typing import Callable, Dict, Iterable, List, Optional, Sequence

# Stage names, in critical-path order. The harness prints stages in this order
# and skips any a given run didn't record (PA is absent when it degrades off).
# `grader` sits beside `claude` rather than after it: they are concurrent
# branches of the same turn on the spoken path, and the critical path is
# `stt + max(pa, claude, grader)`. Printing them side by side is what says which
# branch is the slow one — the question the whole fan-out is built around.
STAGE_ORDER = ("stt", "pa", "claude", "grader", "total")

# Staged-event names, in the order a turn *usually* emits them. Only `transcript`
# and the terminal event have fixed positions — `score` and `reply` race — so
# this is a reading order for the report, never an assertion about the wire.
EVENT_ORDER = ("transcript", "score", "reply", "state", "done", "error")


class Timer:
    """Wall-clock stopwatch for one turn.

    `total_ms` runs from construction, so work outside any `stage()` (KB load,
    response shaping, request parsing) still lands in the total — the total is
    the number the p50 target is stated against, and it should never be merely
    the sum of the parts.

    `clock` is injectable so tests can make durations exact.
    """

    def __init__(self, clock: Callable[[], float] = time.perf_counter):
        self._clock = clock
        self._start = clock()
        self.stages: Dict[str, float] = {}

    @contextmanager
    def stage(self, name: str):
        """Record the elapsed time of the block as stage ``name``.

        The duration is recorded even when the body raises: a stage that failed
        still spent wall-clock time, and dropping it would make a slow-then-
        failing Azure call look free.
        """
        start = self._clock()
        try:
            yield
        finally:
            self.stages[name] = _ms(self._clock() - start)

    @property
    def total_ms(self) -> float:
        """Milliseconds since this timer was constructed."""
        return _ms(self._clock() - self._start)

    def as_dict(self) -> Dict[str, float]:
        """All recorded stages plus `total` — the shape the wire model takes."""
        return {**self.stages, "total": self.total_ms}


def _ms(seconds: float) -> float:
    return round(seconds * 1000, 1)


def percentile(values: Sequence[float], p: float) -> Optional[float]:
    """Nearest-rank percentile of ``values`` (``p`` in 0–100), or None if empty.

    Nearest-rank rather than interpolating: with ~10 replay samples an
    interpolated p95 invents a number between two real ones, and "the second
    slowest turn we actually saw" is the honest claim at this sample size.
    """
    ordered = sorted(values)
    if not ordered:
        return None
    rank = math.ceil(p / 100 * len(ordered))
    index = min(max(rank - 1, 0), len(ordered) - 1)
    return ordered[index]


def summarize(samples: Iterable[Dict[str, float]]) -> Dict[str, Dict[str, float]]:
    """Aggregate per-run stage timings into ``{stage: {n, p50, p95}}``.

    A stage missing from a run is simply not counted for that run — never
    imputed as zero. PA drops off a degraded turn, and a zero there would
    flatter the p50 of exactly the stage Stage 2 is deciding about; `n` makes
    the shortfall visible instead.
    """
    by_stage: Dict[str, List[float]] = {}
    for sample in samples:
        for name, value in sample.items():
            if value is None:
                continue
            by_stage.setdefault(name, []).append(value)

    return {
        name: {
            "n": len(values),
            "p50": percentile(values, 50),
            "p95": percentile(values, 95),
        }
        for name, values in by_stage.items()
    }


def stage_sample(timings: Optional[Dict[str, Optional[float]]]) -> Dict[str, float]:
    """One turn's stage durations, from the wire `TurnTimings` to `{stage: ms}`.

    `TurnTimings` names its fields `{stage}_ms` and leaves a stage that didn't
    run (or hadn't finished) as `None`. Those are dropped rather than zeroed —
    `summarize` counts what it is given, and a zero PA would flatter the p50 of
    exactly the stage this work is deciding about.
    """
    timings = timings or {}
    return {
        name: timings[f"{name}_ms"]
        for name in STAGE_ORDER
        if timings.get(f"{name}_ms") is not None
    }


def arrival_sample(events: Iterable[dict]) -> Dict[str, float]:
    """One turn's per-event arrival times, as `{stage: elapsed_ms}`.

    The other half of what a staged turn costs. `stage_sample` says how long
    each stage *ran*; this says how old the turn was when each line *flushed* —
    which is the thing staging exists to change, and which no combination of the
    durations reconstructs.
    """
    return {
        event["stage"]: event["elapsed_ms"]
        for event in events
        if event.get("stage") and event.get("elapsed_ms") is not None
    }


def format_summary(
    summary: Dict[str, Dict[str, float]],
    order: Sequence[str] = STAGE_ORDER,
) -> str:
    """Render `summarize` output as a fixed-width table, in `order`.

    `order` is a parameter because the same aggregation serves two tables: stage
    durations run along the critical path, arrivals along the turn's events.
    Rows outside it fall through to an alphabetical tail rather than vanishing.

    Each row carries `n/runs` so a stage that only ran on some turns can't be
    quoted as though its p50 rested on the full sample.
    """
    if not summary:
        return "no samples"

    runs = max(stat["n"] for stat in summary.values())
    names = [n for n in order if n in summary]
    names += sorted(n for n in summary if n not in order)

    lines = [f"{'stage':<10}{'p50':>10}{'p95':>10}{'runs':>10}"]
    for name in names:
        stat = summary[name]
        coverage = "{}/{}".format(stat["n"], runs)
        lines.append(
            f"{name:<10}{stat['p50'] / 1000:>9.2f}s{stat['p95'] / 1000:>9.2f}s"
            f"{coverage:>10}"
        )
    return "\n".join(lines)
