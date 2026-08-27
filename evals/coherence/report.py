"""Render the grader's measurement as markdown: is it crediting the right facts?

Pure formatting over `matrix.py`'s numbers.

It used to lead with a gate recommendation, because V0's question was whether
`coherence` could carry one at all. A4 answers that and ships the gate, and the
tag is the partner's now — so this report is what is left of the question and
what always mattered most: slot accuracy, per case, with both failure
directions named apart.
"""
from typing import Dict, Iterable, List

from evals.coherence.cases import Gold
from evals.coherence.matrix import Observation, slot_accuracy


def _slot_counts(counts) -> str:
    """`slot ×n` per id, or an em dash. A count matters: 3/3 is not 1/3."""
    return ", ".join(f"`{slot}` ×{n}" for slot, n in sorted(counts.items())) or "—"


def render(
    observations: Iterable[Observation], gold: Dict[str, Gold], *, model: str
) -> str:
    observations = list(observations)
    lines: List[str] = [
        "# The grade against gold labels",
        "",
        f"Model: `{model}`. {len(observations)} runs over "
        f"{len({o.case_id for o in observations})} cases.",
    ]
    lines += [
        "",
        "## Slot accuracy",
        "",
        "Does the tracker credit the facts the learner actually established? "
        "**Spurious** is credit they did not earn — the failure this track "
        "exists to remove. **Missed** is credit they earned and did not get — "
        "the failure A2's floor exists to rescue. A3's numbers are the standing "
        "baseline: 0 missed over 55 runs.",
        "",
        "| case | exact | spurious | missed |",
        "|---|---|---|---|",
    ]
    totals = {"runs": 0, "exact": 0, "spurious": 0, "missed": 0}
    for accuracy in slot_accuracy(observations, gold):
        totals["runs"] += accuracy.runs
        totals["exact"] += accuracy.exact
        totals["spurious"] += sum(accuracy.spurious.values())
        totals["missed"] += sum(accuracy.missed.values())
        lines.append(
            f"| {accuracy.case_id} | {accuracy.exact}/{accuracy.runs} | "
            f"{_slot_counts(accuracy.spurious)} | {_slot_counts(accuracy.missed)} |"
        )
    lines.append(
        f"| **total** | **{totals['exact']}/{totals['runs']}** | "
        f"**{totals['spurious']}** | **{totals['missed']}** |"
    )

    lines += [
        "", "## Per-case runs", "",
        "| case | gold slots | credited | owed turns |", "|---|---|---|---|",
    ]
    for observation in observations:
        expected = gold[observation.case_id].slots_established
        mark = "" if set(expected) == set(observation.slots_filled) else " ⚠️"
        lines.append(
            f"| {observation.case_id} | {', '.join(expected) or '—'} | "
            f"{', '.join(observation.slots_filled) or '—'}{mark} | "
            f"{', '.join(observation.slots_filled_previously) or '—'} |"
        )
    return "\n".join(lines) + "\n"
