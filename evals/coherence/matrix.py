"""Observed tags versus gold labels, and what gate — if any — that supports.

Pure functions over recorded values. Nothing here calls a model; `replay.py`
produces the observations and this module decides what they mean.

**A gate is a set of tags allowed to earn credit.** `coherence` is ordinal, so
only three gates are worth considering: strict (`on_track` only), loose
(`on_track` or `drifting`), and open (everything — no gate at all).

A gate is scored on two counts that pull against each other:

- **gaming blocked** — runs where the tracker credited a slot gold says it should
  not have, that the gate stops. This is the whole reason V1 exists. A run that
  credited nothing is not counted: a gate cannot win by stopping credit that was
  never granted, and counting those would make an idle gate look useful.
- **rescues suppressed** — turns gold says *did* earn their credit, that the gate
  stops anyway. This is the false negative `ACCESSIBILITY.md` A2 exists to
  remove, and V1 re-introducing it would be a straight trade of one bug for a
  worse one.

So safety is not a ratio. **One suppressed rescue makes a gate unsafe**, however
much gaming it catches, because the tags are stochastic and the learner on the
wrong side of that run cannot tell it happened.
"""
from dataclasses import dataclass
from typing import Dict, FrozenSet, Iterable, List, Optional, Tuple

from evals.coherence.cases import COHERENCE_TAGS, CaseError, Gold

# Strictest first. `recommend` walks this order and takes the first safe one, so
# the ordering is the preference: block as much as can be blocked for free.
CANDIDATE_GATES: Tuple[FrozenSet[str], ...] = (
    frozenset({"on_track"}),
    frozenset({"on_track", "drifting"}),
    frozenset(COHERENCE_TAGS),
)


@dataclass(frozen=True)
class Observation:
    """One replay of one case: what the worker actually tagged it.

    A case is replayed several times because the tag is a model's output and not
    a function. Every run is its own row here.
    """

    case_id: str
    coherence: str
    slots_filled: Tuple[str, ...] = ()


@dataclass(frozen=True)
class GateReport:
    """How one candidate gate scores against the whole observation set."""

    allow: FrozenSet[str]
    gaming_blocked: int
    gaming_total: int
    rescues_suppressed: int
    rescues_total: int

    @property
    def safe(self) -> bool:
        """No turn that earned its credit was ever denied it."""
        return self.rescues_suppressed == 0

    @property
    def useful(self) -> bool:
        """The gate stops at least one turn that should not have been credited."""
        return self.gaming_blocked > 0


def confusion(
    observations: Iterable[Observation], gold: Dict[str, Gold]
) -> Dict[Tuple[str, str], int]:
    """`(gold tag, observed tag)` → count, with every cell present.

    Zero-filled so a reader sees the shape of the matrix rather than only the
    cells that happened to fire — an absent cell and an empty one read very
    differently when the question is "does this signal separate anything?".
    """
    counts = {(expected, seen): 0 for expected in COHERENCE_TAGS for seen in COHERENCE_TAGS}
    for observation in observations:
        counts[(gold[observation.case_id].coherence, observation.coherence)] += 1
    return counts


def evaluate_gates(
    observations: Iterable[Observation], gold: Dict[str, Gold]
) -> List[GateReport]:
    """Score every candidate gate against the observations, strictest first."""
    observations = list(observations)
    reports = []
    for allow in CANDIDATE_GATES:
        blocked = suppressed = gaming = rescues = 0
        for observation in observations:
            credit_ok = gold[observation.case_id].credit_ok
            stops = observation.coherence not in allow
            if credit_ok:
                # A rescue counts however the credit would have arrived. The
                # gate sits under the tracker *and* under A2's floor, so a
                # suppressed turn is suppressed either way.
                rescues += 1
                suppressed += stops
            elif observation.slots_filled:
                gaming += 1
                blocked += stops
        reports.append(
            GateReport(
                allow=allow,
                gaming_blocked=blocked,
                gaming_total=gaming,
                rescues_suppressed=suppressed,
                rescues_total=rescues,
            )
        )
    return reports


def recommend(reports: Iterable[GateReport]) -> Optional[GateReport]:
    """The strictest gate that is both safe and useful, or `None`.

    `None` is a real answer and the one V0 is most prepared to give: either no
    gate separates gaming from earned credit, or the only safe gate never fires,
    and shipping a gate that never fires is risk bought with no benefit.
    """
    for report in reports:
        if report.safe and report.useful:
            return report
    return None


@dataclass(frozen=True)
class SlotAccuracy:
    """How one case's credited slots compare with the facts it actually established.

    The gate analysis above asks whether `coherence` can *police* the tracker.
    This asks the blunter question underneath it: **is the tracker right?** Both
    failure directions are named separately, because they are different bugs
    with different victims:

    - `spurious` — credited a slot gold says was never established. The false
      positive this track exists to remove; the learner is told they did
      something they did not do.
    - `missed` — did not credit a slot gold says was established. The false
      negative `ACCESSIBILITY.md` A2 exists to remove.

    Counted per run rather than per case, since the annotation is a model's
    output and one clean run does not make a reliable tracker.
    """

    case_id: str
    runs: int
    exact: int
    spurious: Dict[str, int]
    missed: Dict[str, int]

    @property
    def exact_rate(self) -> float:
        return self.exact / self.runs if self.runs else 0.0


def slot_accuracy(
    observations: Iterable[Observation], gold: Dict[str, Gold]
) -> List["SlotAccuracy"]:
    """Score `slots_filled` against `gold.slots_established`, per case.

    Set comparison, so the order the model happened to list ids in never counts
    as a disagreement — `termination.advance` treats them as a set too.

    This is the metric **V2 is judged on**. The gate analysis dies with V1; this
    survives it, because "did the grader credit the right facts?" is the question
    a goal-blind grader exists to answer better. The numbers recorded here on the
    current partner are the baseline it has to beat.
    """
    per_case: Dict[str, List[Observation]] = {}
    for observation in observations:
        per_case.setdefault(observation.case_id, []).append(observation)

    reports = []
    for case_id in sorted(per_case):
        expected = set(gold[case_id].slots_established)
        spurious: Dict[str, int] = {}
        missed: Dict[str, int] = {}
        exact = 0
        for observation in per_case[case_id]:
            seen = set(observation.slots_filled)
            if seen == expected:
                exact += 1
            for slot_id in sorted(seen - expected):
                spurious[slot_id] = spurious.get(slot_id, 0) + 1
            for slot_id in sorted(expected - seen):
                missed[slot_id] = missed.get(slot_id, 0) + 1
        reports.append(
            SlotAccuracy(
                case_id=case_id,
                runs=len(per_case[case_id]),
                exact=exact,
                spurious=spurious,
                missed=missed,
            )
        )
    return reports


@dataclass(frozen=True)
class Run:
    """One `replay.py` invocation: the tags, and which model produced them."""

    model: str
    repeat: int
    observations: List[Observation]


def load_observations(path: str) -> Run:
    """Read what `replay.py` wrote.

    The model id travels with the observations because the tag is that model's
    opinion. V2 moves `coherence` to a stronger grader, and a matrix read
    without knowing which model produced it would be trusted past its evidence.
    """
    import json

    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    for entry in payload["observations"]:
        # The open gate is the whole tag set, so an unknown tag falls outside
        # *every* gate — including the one defined never to stop anything. A
        # typo here would make "no gate at all" look useful, or unsafe.
        if entry["coherence"] not in COHERENCE_TAGS:
            raise CaseError(
                f"{entry['case_id']}: observed coherence {entry['coherence']!r} is "
                f"not one of {COHERENCE_TAGS}"
            )
    return Run(
        model=payload["model"],
        repeat=payload.get("repeat", 1),
        observations=[
            Observation(
                case_id=entry["case_id"],
                coherence=entry["coherence"],
                slots_filled=tuple(entry.get("slots_filled", ())),
            )
            for entry in payload["observations"]
        ],
    )
