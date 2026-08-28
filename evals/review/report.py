"""Render the session review's measurement as markdown: what came back, and how
often. Pure formatting over `recall.py`'s numbers.
"""
from typing import Dict, Iterable, List

from evals.review.cases import ReviewGold
from evals.review.recall import ReviewObservation, recall


def _counts(counts, runs) -> str:
    """`slot n/runs` per id, or an em dash. The count is the whole point."""
    return ", ".join(f"`{s}` {n}/{runs}" for s, n in sorted(counts.items())) or "—"


def render(
    observations: Iterable[ReviewObservation],
    gold: Dict[str, ReviewGold],
    *,
    model: str,
) -> str:
    observations = list(observations)
    reports = recall(observations, gold)
    lines: List[str] = [
        "# Earlier-turn recall, measured",
        "",
        f"Model: `{model}`. {len(observations)} draws over "
        f"{len({o.case_id for o in observations})} sessions.",
        "",
        "Each session is submitted with the state the *live* grades produced — "
        "under-credited on purpose — and re-read by `feedback.review_session`. "
        "**Recovered** is credit an earlier turn earned and the review gave "
        "back. **Spurious** is credit no turn in the session earned; the pass "
        "is add-only, so a spurious recovery cannot be taken back.",
        "",
        "| session | owed | recovered | rate | every slot | spurious |",
        "|---|---|---|---|---|---|",
    ]
    owed = found = complete = runs = spurious = 0
    for report in reports:
        owed += report.owed
        found += report.recovered
        complete += report.complete
        runs += report.runs
        spurious += sum(report.spurious.values())
        lines.append(
            f"| {report.case_id} | {', '.join(report.expected) or '—'} | "
            f"{_counts(report.per_slot, report.runs)} | "
            # A session with nothing owed has no rate. Printing 0% for one
            # reads as a total failure of the case that exists to prove the
            # pass invents nothing.
            f"{f'{report.rate:.0%}' if report.owed else '—'} | "
            f"{report.complete}/{report.runs} | "
            f"{_counts(report.spurious, report.runs)} |"
        )
    rate = found / owed if owed else 0.0
    lines.append(
        f"| **total** | **{owed}** | **{found}** | **{rate:.0%}** | "
        f"**{complete}/{runs}** | **{spurious}** |"
    )

    lines += [
        "",
        "## Per-slot rates",
        "",
        "The number A6 could not produce. Two slots both labelled "
        "*recoverable* can behave nothing alike, and only a per-slot rate with "
        "its sample count shows which one the loss is in.",
        "",
        "| session | slot | recovered |",
        "|---|---|---|",
    ]
    for report in reports:
        for slot_id in report.expected:
            got = report.per_slot.get(slot_id, 0)
            mark = "" if got == report.runs else " ⚠️"
            lines.append(
                f"| {report.case_id} | {slot_id} | {got}/{report.runs}{mark} |"
            )
    return "\n".join(lines) + "\n"
