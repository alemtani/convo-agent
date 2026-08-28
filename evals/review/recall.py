"""What the session review recovered, against what it owed. Pure functions.

The runner calls the model; this module decides what the numbers mean — the
same split `evals/coherence/matrix.py` keeps.

**The metric is a rate, per slot, with its sample count.** A6 measured earlier-
turn recall by hand at four waves of five draws and could not tell a fix from a
lucky wave. A recovery is a model's output, so a case has a success *rate*, and
a number reported without the count behind it is not a measurement. Every
number this module produces carries `runs`.

Two directions, named apart because they are different bugs:

- **missed** — a slot an earlier turn established, that the review did not
  report. Credit the learner earned and did not get; the failure A6.5 exists to
  size and A8 exists to move.
- **spurious** — a slot no turn established, reported anyway. The review is
  add-only, so a spurious recovery is credit that cannot be taken back. It is
  the more expensive failure and it must stay at zero.
"""
from dataclasses import dataclass
from typing import Dict, Iterable, List, Tuple

from evals.review.cases import ReviewGold


@dataclass(frozen=True)
class ReviewObservation:
    """One replay of one session review: which slots the pass added.

    `recovered` is the diff `review_session` produced — the ids that entered
    `filled_at` and were not in the state the client submitted. That is the
    learner-visible outcome of the pass, and it is deliberately *not* the raw
    `slots_filled_previously` list: the union of both lists is what the shipped
    code credits, so scoring one half would measure something nobody ships.
    """

    case_id: str
    recovered: Tuple[str, ...] = ()


@dataclass(frozen=True)
class Recall:
    """One case's review, scored over `runs` draws.

    `per_slot` is the rate each owed slot came back at, which is the number A6
    could not produce: 你叫什么名字？ two turns back and 我叫小明 on the same
    turn are both "recoverable" and they do not behave alike.
    """

    case_id: str
    runs: int
    expected: Tuple[str, ...]
    per_slot: Dict[str, int]
    spurious: Dict[str, int]
    complete: int

    @property
    def recovered(self) -> int:
        """Owed-slot recoveries across every draw. The numerator of recall."""
        return sum(self.per_slot.values())

    @property
    def owed(self) -> int:
        """Owed-slot chances across every draw. The denominator of recall."""
        return len(self.expected) * self.runs

    @property
    def rate(self) -> float:
        return self.recovered / self.owed if self.owed else 0.0

    @property
    def complete_rate(self) -> float:
        """Draws that recovered *everything* owed. Harsher than `rate`, and the
        one the learner feels: a card is right or it is not."""
        return self.complete / self.runs if self.runs else 0.0


def recall(
    observations: Iterable[ReviewObservation], gold: Dict[str, ReviewGold]
) -> List[Recall]:
    """Score recovered slots against the labels, per case.

    Set comparison, so the order the model lists ids in never counts as a
    disagreement — `filled_at` is a mapping and `termination` reads it as a set.
    """
    per_case: Dict[str, List[ReviewObservation]] = {}
    for observation in observations:
        per_case.setdefault(observation.case_id, []).append(observation)

    reports = []
    for case_id in sorted(per_case):
        expected = tuple(gold[case_id].recoverable)
        never = set(gold[case_id].never_established)
        found: Dict[str, int] = {}
        spurious: Dict[str, int] = {}
        complete = 0
        for observation in per_case[case_id]:
            seen = set(observation.recovered)
            if set(expected) <= seen:
                complete += 1
            for slot_id in sorted(seen & set(expected)):
                found[slot_id] = found.get(slot_id, 0) + 1
            # Only a slot gold says *no* turn established is spurious. A slot
            # outside both lists is one the labeller left unaccounted for, and
            # `tests/test_review_eval.py` fails the corpus for it rather than
            # letting this silently call it a false positive.
            for slot_id in sorted(seen & never):
                spurious[slot_id] = spurious.get(slot_id, 0) + 1
        reports.append(
            Recall(
                case_id=case_id,
                runs=len(per_case[case_id]),
                expected=expected,
                per_slot=found,
                spurious=spurious,
                complete=complete,
            )
        )
    return reports


@dataclass(frozen=True)
class Run:
    """One `replay.py` invocation: the recoveries, and which model produced them."""

    model: str
    repeat: int
    observations: List[ReviewObservation]


def load_observations(path: str) -> Run:
    """Read what `replay.py` wrote.

    The model id travels with the numbers because the judgment is that model's
    — the same reason the grader's runner records it.
    """
    import json

    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    return Run(
        model=payload["model"],
        repeat=payload.get("repeat", 1),
        observations=[
            ReviewObservation(
                case_id=entry["case_id"],
                recovered=tuple(entry.get("recovered", ())),
            )
            for entry in payload["observations"]
        ],
    )
