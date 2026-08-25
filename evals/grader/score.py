"""Observations in, a recommendation out. Pure functions; nothing here calls a model.

Three questions, and they are asked together on purpose:

- **Is it right?** `slots_filled` against `gold.slots_established`, the metric
  `evals/coherence/matrix.slot_accuracy` establishes as the one V2 is judged on.
  Named in both directions, because they are different bugs: `spurious` tells a
  learner they did something they did not, `missed` denies them something they
  did.
- **Is it fast?** Wall clock per grade, summarised per setting.
- **What does it actually use?** `output_tokens` is thinking plus output, which
  is precisely what `GRADER_MAX_TOKENS` caps. The cap is read off this rather
  than guessed.

The reason they share a module is the rule in the stream doc: a latency number
without an accuracy number beside it is not a result, it is half of one.
"""
from dataclasses import dataclass, field
from math import ceil
from typing import Dict, Iterable, List, Optional, Tuple

from evals.coherence.cases import Gold


class SweepError(Exception):
    """A measurement that cannot be read: unlabelled, unmeasured, or censored."""


# The cap is rounded up to a multiple of this. A budget of 1173 says a precision
# the corpus does not have, and a round number is what someone will actually
# recognise in `config.py` a year from now.
CAP_GRANULARITY = 256

# How far above the largest grade anyone observed the cap sits. Over the cap is
# `stop_reason: max_tokens` → `GraderError` → an uncredited turn, and the corpus
# is seven authored cases rather than a distribution of real sessions, so the
# factor is doubling rather than trimming.
CAP_FACTOR = 2.0


@dataclass(frozen=True)
class GraderObservation:
    """One grade of one case at one setting.

    `latency_ms` is `None` for a replayed run and a number for a recorded one.
    A cassette returns in microseconds, so reporting a replay's wall clock as
    the grader's latency would claim the branch is free — the exact claim this
    step exists to test. The token counts survive replay intact, because the
    cassette stores the usage the live call reported.
    """

    setting_id: str
    case_id: str
    coherence: str
    slots_filled: Tuple[str, ...] = ()
    latency_ms: Optional[float] = None
    output_tokens: Optional[int] = None
    input_tokens: Optional[int] = None


@dataclass(frozen=True)
class SettingScore:
    """Every run at one setting, scored and summarised."""

    setting_id: str
    runs: int
    exact: int
    spurious_runs: int
    missed_runs: int
    spurious: Dict[str, int] = field(default_factory=dict)
    missed: Dict[str, int] = field(default_factory=dict)
    latencies_ms: Tuple[float, ...] = ()
    output_tokens: Tuple[int, ...] = ()

    @property
    def exact_rate(self) -> float:
        return self.exact / self.runs if self.runs else 0.0

    @property
    def p50_latency_ms(self) -> Optional[float]:
        return percentile(self.latencies_ms, 50)

    @property
    def p95_latency_ms(self) -> Optional[float]:
        return percentile(self.latencies_ms, 95)

    @property
    def max_latency_ms(self) -> Optional[float]:
        return max(self.latencies_ms) if self.latencies_ms else None

    @property
    def max_output_tokens(self) -> Optional[int]:
        return max(self.output_tokens) if self.output_tokens else None


def percentile(values: Iterable[float], q: float) -> Optional[float]:
    """Linear-interpolated percentile, or `None` for no samples.

    `None` rather than `0.0`: zero milliseconds is the fastest grade imaginable
    and "not measured" is not a speed. A summary that rendered the two the same
    would report a replayed sweep as an instant grader.
    """
    ordered = sorted(values)
    if not ordered:
        return None
    if len(ordered) == 1:
        return float(ordered[0])
    position = (q / 100) * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return float(ordered[low] + (ordered[high] - ordered[low]) * (position - low))


def score_settings(
    observations: Iterable[GraderObservation], gold: Dict[str, Gold]
) -> List[SettingScore]:
    """Score every setting against the labels, in id order.

    Set comparison, so the order the grader happened to list ids in is never a
    disagreement — `termination.advance` treats them as a set too.

    An observation with no label raises rather than scoring zero. A hole in a
    matrix that reads as a miss is worse than a missing row: it makes one
    setting look worse than another for a reason that has nothing to do with the
    setting.
    """
    grouped: Dict[str, List[GraderObservation]] = {}
    for observation in observations:
        if observation.case_id not in gold:
            raise SweepError(
                f"{observation.case_id}: observed at setting "
                f"{observation.setting_id!r} with no gold label — a hole in the "
                "matrix, not a failed run"
            )
        grouped.setdefault(observation.setting_id, []).append(observation)

    scores = []
    for setting_id in sorted(grouped):
        runs = grouped[setting_id]
        spurious: Dict[str, int] = {}
        missed: Dict[str, int] = {}
        exact = spurious_runs = missed_runs = 0
        for observation in runs:
            expected = set(gold[observation.case_id].slots_established)
            seen = set(observation.slots_filled)
            if seen == expected:
                exact += 1
            over = sorted(seen - expected)
            under = sorted(expected - seen)
            spurious_runs += bool(over)
            missed_runs += bool(under)
            for slot_id in over:
                spurious[slot_id] = spurious.get(slot_id, 0) + 1
            for slot_id in under:
                missed[slot_id] = missed.get(slot_id, 0) + 1
        scores.append(
            SettingScore(
                setting_id=setting_id,
                runs=len(runs),
                exact=exact,
                spurious_runs=spurious_runs,
                missed_runs=missed_runs,
                spurious=spurious,
                missed=missed,
                latencies_ms=tuple(
                    o.latency_ms for o in runs if o.latency_ms is not None
                ),
                output_tokens=tuple(
                    o.output_tokens for o in runs if o.output_tokens is not None
                ),
            )
        )
    return scores


def recommend_max_tokens(
    observations: Iterable[GraderObservation],
    *,
    ceiling: int,
    factor: float = CAP_FACTOR,
    granularity: int = CAP_GRANULARITY,
) -> int:
    """The `GRADER_MAX_TOKENS` the corpus supports: observed maximum, times headroom.

    `ceiling` is the budget the observations were *taken* at, and it bounds the
    answer in both directions:

    - **Never above it.** Nothing in the corpus ran past the ceiling, so the
      corpus says nothing about what happens there. Recommending past it would
      be extrapolation wearing a measurement's clothes.
    - **Never off a run that reached it.** A grade that hit the cap came back
      `stop_reason: max_tokens`, which means `output_tokens` is the cap rather
      than what the grade wanted. Sizing a smaller budget from a censored
      maximum makes the next cut-off likelier, not less.

    Refuses an empty or unmeasured set for the same reason it refuses a censored
    one: a cap is a promise about the hardest grade, and a promise made on no
    evidence is how the grader starts failing on exactly the turns that matter.
    """
    counts = []
    for observation in observations:
        if observation.output_tokens is None:
            raise SweepError(
                f"{observation.case_id} at {observation.setting_id!r} has no "
                "output_tokens — a cap cannot be read off a run nobody measured"
            )
        if observation.output_tokens >= ceiling:
            raise SweepError(
                f"{observation.case_id} at {observation.setting_id!r} used "
                f"{observation.output_tokens} of a {ceiling} budget — the grade "
                "was cut off, so this maximum is the cap and not a measurement"
            )
        counts.append(observation.output_tokens)
    if not counts:
        raise SweepError("no observations — nothing to read a cap off")
    wanted = ceil(max(counts) * factor / granularity) * granularity
    return min(wanted, ceiling)


@dataclass(frozen=True)
class SweepRun:
    """One `sweep.py` invocation, read back off disk.

    `failures` is `(setting_id, case_id, error)` for every grade that never
    landed. It is carried rather than dropped because a timeout is the loudest
    latency result a sweep can produce, and a table built only from the calls
    that returned reports the setting that lost a grade as the one that was
    fast.

    `recorded` and `max_tokens` travel with the observations because neither can
    be recovered from them. A replayed run's latencies are null and a recorded
    one's are real, and the budget the run was taken at is what tells
    `recommend_max_tokens` whether its maximum is a measurement or a cut-off.
    """

    recorded: bool
    repeat: int
    max_tokens: int
    observations: List[GraderObservation]
    failures: List[Tuple[str, str, str]] = field(default_factory=list)


def load_observations(path: str) -> SweepRun:
    """Read what `sweep.py` wrote."""
    import json

    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    if "max_tokens" not in payload:
        raise SweepError(
            f"{path}: no max_tokens — without the budget the run was taken at, "
            "a maximum cannot be told from a cut-off"
        )
    return SweepRun(
        recorded=bool(payload.get("recorded", False)),
        repeat=int(payload.get("repeat", 1)),
        max_tokens=int(payload["max_tokens"]),
        observations=[
            GraderObservation(
                setting_id=entry["setting_id"],
                case_id=entry["case_id"],
                coherence=entry["coherence"],
                slots_filled=tuple(entry.get("slots_filled", ())),
                latency_ms=entry.get("latency_ms"),
                output_tokens=entry.get("output_tokens"),
                input_tokens=entry.get("input_tokens"),
            )
            for entry in payload["observations"]
        ],
        failures=[
            (entry["setting_id"], entry["case_id"], entry["error"])
            for entry in payload.get("failures", [])
        ],
    )
