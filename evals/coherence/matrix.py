"""Observed values versus gold labels. Pure functions over recorded values;
the runners call the models, this module decides what the numbers mean.

Two questions, asked of two workers since A4:

- **`slot_accuracy`** — did the grader credit the facts the learner established?
  Fed by `evals/coherence/replay.py` (the grader alone). The metric V2 is judged on.
- **`confusion`** — does the partner's `coherent` agree with a fair reader? Fed
  by `evals/turn/replay.py`, the only runner that runs a partner.

The gate-search machinery (`CANDIDATE_GATES`, `evaluate_gates`, `recommend`) is
gone: A4 answered the V0/V1 question of which threshold to ship, and a
recommender over a single candidate is not a measurement.
"""
from dataclasses import dataclass
from typing import Dict, Iterable, List, Tuple

from evals.coherence.cases import COHERENCE_TAGS, Gold


@dataclass(frozen=True)
class Observation:
    """One replay of one case through the grader: which slots it credited.

    A case is replayed several times because the answer is a model's output, not
    a function. It carries no `coherence` since A4 — the grader is not asked.
    """

    case_id: str
    slots_filled: Tuple[str, ...] = ()
    # What an owed turn established, when this grade settled a debt. Nothing here
    # scores it (the matrix asks about the turn under test); it is recorded so a
    # grader that merged the two lists is distinguishable from one that dropped
    # the earlier turn.
    slots_filled_previously: Tuple[str, ...] = ()


def confusion(observations, gold: Dict[str, Gold]) -> Dict[Tuple[str, str], int]:
    """`(gold tag, observed tag)` → count, with every cell present.

    Zero-filled so a reader sees the shape of the matrix rather than only the
    cells that happened to fire — an absent cell and an empty one read very
    differently when the question is "does this signal separate anything?".

    Takes anything with `case_id` and `coherence`, which since A4 means
    `evals.turn.replay.TurnObservation`: the tag is the partner's, and the turn
    runner is the only one that runs a partner.
    """
    counts = {(expected, seen): 0 for expected in COHERENCE_TAGS for seen in COHERENCE_TAGS}
    for observation in observations:
        counts[(gold[observation.case_id].coherence, observation.coherence)] += 1
    return counts


@dataclass(frozen=True)
class SlotAccuracy:
    """How one case's credited slots compare with the facts it established.

    Both failure directions are named apart, being different bugs:

    - `spurious` — credited a slot gold says was never established. The false
      positive this track exists to remove.
    - `missed` — did not credit a slot gold says was established. The false
      negative `ACCESSIBILITY.md` A2 exists to remove.

    Counted per run, since one clean run does not make a reliable tracker.
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

    Set comparison, so the order the model lists ids in never counts as a
    disagreement — `termination.advance` treats them as a set too. This is the
    metric V2 is judged on: did the goal-blind grader credit the right facts?
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
    """One `replay.py` invocation: the grades, and which model produced them."""

    model: str
    repeat: int
    observations: List[Observation]


def load_observations(path: str) -> Run:
    """Read what `replay.py` wrote.

    The model id travels with the observations because the judgment is that
    model's opinion. V2 moved the grade to a stronger model, and numbers read
    without knowing which one produced them would be trusted past their
    evidence.
    """
    import json

    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    return Run(
        model=payload["model"],
        repeat=payload.get("repeat", 1),
        observations=[
            Observation(
                case_id=entry["case_id"],
                slots_filled=tuple(entry.get("slots_filled", ())),
                slots_filled_previously=tuple(
                    entry.get("slots_filled_previously", ())
                ),
            )
            for entry in payload["observations"]
        ],
    )
