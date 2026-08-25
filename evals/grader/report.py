"""The sweep as markdown: accuracy and latency in one table, per setting.

One table, deliberately. The stream doc's rule is that a change to the grader
needs an accuracy number beside its latency number, and two tables on two pages
is how the second one gets read without the first.
"""
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from evals.coherence.cases import Gold
from evals.grader.score import (
    GraderObservation,
    SweepError,
    recommend_max_tokens,
    score_settings,
)

NOT_MEASURED = "not measured"


def _ms(value: Optional[float]) -> str:
    return NOT_MEASURED if value is None else f"{value / 1000:.2f}s"


def render(
    observations: Iterable[GraderObservation],
    gold: Dict[str, Gold],
    *,
    max_tokens: int,
    recorded: bool,
    failures: Sequence[Tuple[str, str, str]] = (),
) -> str:
    """The whole measurement, in the order a reader needs it.

    `failures` are grades that never landed. They get their own section rather
    than a column, because a lost grade is not a worse score — it is an
    uncredited turn, which is a different and worse thing.
    """
    observations = list(observations)
    failures = list(failures)
    scores = score_settings(observations, gold)

    lines = ["# B1 — the grader's settings, measured", ""]
    if not recorded:
        lines += [
            f"**Latency is `{NOT_MEASURED}`.** These observations were replayed "
            "off cassettes, so the wall clock belongs to this process. Accuracy "
            "and token usage are the live call's own and replay intact; the "
            "latency column is only ever filled by a `--record` run.",
            "",
        ]

    lines += [
        "| setting | runs | exact | spurious | missed | p50 | p95 | max out |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for score in scores:
        lines.append(
            f"| `{score.setting_id}` | {score.runs} | "
            f"{score.exact}/{score.runs} ({score.exact_rate:.0%}) | "
            f"{score.spurious_runs} | {score.missed_runs} | "
            f"{_ms(score.p50_latency_ms)} | {_ms(score.p95_latency_ms)} | "
            f"{score.max_output_tokens if score.max_output_tokens is not None else '—'} |"
        )

    lines += ["", "## Where each setting goes wrong", ""]
    for score in scores:
        detail = []
        if score.spurious:
            detail.append(
                "spurious "
                + ", ".join(f"`{slot}`×{n}" for slot, n in sorted(score.spurious.items()))
            )
        if score.missed:
            detail.append(
                "missed "
                + ", ".join(f"`{slot}`×{n}" for slot, n in sorted(score.missed.items()))
            )
        lines.append(f"- `{score.setting_id}` — {'; '.join(detail) or 'nothing'}")

    if failures:
        lost: Dict[str, List[Tuple[str, str]]] = {}
        for setting_id, case_id, error in failures:
            lost.setdefault(setting_id, []).append((case_id, error))
        lines += [
            "",
            "## Grades that never landed",
            "",
            "Not a lower score — an **uncredited turn**. The learner is told they "
            "established nothing, and the session state does not advance.",
            "",
        ]
        for setting_id in sorted(lost):
            for case_id, error in sorted(lost[setting_id]):
                lines.append(f"- `{setting_id}` — `{case_id}`: {error}")

    lines += ["", "## The `max_tokens` cap", ""]
    try:
        cap = recommend_max_tokens(observations, ceiling=max_tokens)
    except SweepError as exc:
        lines.append(f"No recommendation: {exc}")
    else:
        largest = max(
            o.output_tokens for o in observations if o.output_tokens is not None
        )
        lines.append(
            f"The largest grade in the sweep used **{largest}** of a "
            f"{max_tokens} budget. `GRADER_MAX_TOKENS` = **{cap}** clears it "
            "with a doubling of headroom, and over the cap is an uncredited "
            "turn rather than a truncated one."
        )
    return "\n".join(lines) + "\n"
