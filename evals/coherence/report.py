"""Render the measurement as markdown: the matrix, the gates, the recommendation.

Pure formatting over `matrix.py`'s numbers. It states the recommendation in
words rather than leaving a reader to infer it from a table — including the
recommendation V0 is most prepared to make, that no gate is safe to ship.
"""
from typing import Dict, Iterable, List

from evals.coherence.cases import COHERENCE_TAGS, Gold
from evals.coherence.matrix import Observation, confusion, evaluate_gates, recommend


def _gate_name(allow) -> str:
    return " | ".join(tag for tag in COHERENCE_TAGS if tag in allow)


def render(
    observations: Iterable[Observation], gold: Dict[str, Gold], *, model: str
) -> str:
    observations = list(observations)
    reports = evaluate_gates(observations, gold)
    best = recommend(reports)
    lines: List[str] = [
        "# `coherence` against gold labels",
        "",
        f"Model: `{model}`. {len(observations)} runs over "
        f"{len({o.case_id for o in observations})} cases.",
        "",
        "## Tag vs gold",
        "",
        "| gold \\ observed | " + " | ".join(COHERENCE_TAGS) + " |",
        "|---" * (len(COHERENCE_TAGS) + 1) + "|",
    ]
    counts = confusion(observations, gold)
    for expected in COHERENCE_TAGS:
        row = " | ".join(str(counts[(expected, seen)]) for seen in COHERENCE_TAGS)
        lines.append(f"| **{expected}** | {row} |")

    lines += [
        "",
        "## Candidate gates",
        "",
        "| gate | gaming blocked | rescues suppressed | safe | useful |",
        "|---|---|---|---|---|",
    ]
    for report in reports:
        lines.append(
            f"| `{_gate_name(report.allow)}` | "
            f"{report.gaming_blocked}/{report.gaming_total} | "
            f"{report.rescues_suppressed}/{report.rescues_total} | "
            f"{'yes' if report.safe else 'no'} | "
            f"{'yes' if report.useful else 'no'} |"
        )

    lines += ["", "## Recommendation", ""]
    if best is None:
        # Two different failures wear the same verdict, and they say different
        # things about the signal. A gate that never fires means `coherence`
        # did not notice the gaming at all; a gate that misjudges means it
        # noticed something, but not reliably enough to act on.
        if all(report.safe for report in reports):
            lines.append(
                "**No safe gate: every candidate never fires.** `coherence` tagged "
                "the wrongly credited runs the same way it tagged the earned ones, "
                "so no threshold over it separates them. A gate that never fires is "
                "risk bought with no benefit. V1 does not ship one on this evidence; "
                "the fix stays with V2."
            )
        else:
            lines.append(
                "**No safe gate.** Every gate that blocks any wrongly credited run "
                "also suppresses a turn the learner earned — the false negative "
                "`ACCESSIBILITY.md` A2 exists to remove. V1 does not ship a gate on "
                "this evidence; the fix stays with V2."
            )
    else:
        lines.append(
            f"**Recommended gate: `{_gate_name(best.allow)}`.** It blocks "
            f"{best.gaming_blocked}/{best.gaming_total} wrongly credited runs and "
            f"suppresses none of {best.rescues_total} earned ones."
        )

    lines += ["", "## Per-case runs", "", "| case | gold | observed | slots filled |", "|---|---|---|---|"]
    for observation in observations:
        expected = gold[observation.case_id].coherence
        mark = "" if expected == observation.coherence else " ⚠️"
        lines.append(
            f"| {observation.case_id} | {expected} | {observation.coherence}{mark} | "
            f"{', '.join(observation.slots_filled) or '—'} |"
        )
    return "\n".join(lines) + "\n"
