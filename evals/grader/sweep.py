"""Grade the labelled corpus at each setting, and record what it cost.

    python -m evals.grader.sweep --repeat 3                 # free, off cassettes
    python -m evals.grader.sweep --record --samples 3       # live; costs money
    python -m evals.grader.sweep --setting claude-sonnet-5/low

**Recording is the only run that produces a latency number.** A replay answers
out of a JSON file, so its wall clock measures this process and not the model.
That is why the recorded observations are committed: accuracy and token usage
replay for free forever, and the latency is the evidence from the run that was
paid for. `--record` writes both; a replay leaves `latency_ms` null and the
report says "not measured" rather than "instant".

It calls `workers.grader.grade` — the same function the turn calls, through the
same `build_request` — with `config.GRADER_*` moved under it by
`settings.applied`. Measuring a hand-built request instead would measure a
prompt the app does not send.

**Turn-1 cases are graded without the partner's opening line.** The corpus
predates the grader's `opening_line` argument and carries no opener
(`evals/coherence/README.md` files that under A1). Two of the seven cases start
at turn 1 and so are judged with slightly less context than the live app gives
them. It is a real limit on the absolute accuracy number and none at all on the
comparison, which is what this sweep is for: every arm sees the identical input.
"""
import argparse
import asyncio
import json
import os
import time
from dataclasses import asdict
from typing import List, Optional, Tuple

from backend import config, kb, orchestrator, termination
from backend.models import SessionState
from backend.workers import grader
from evals import cassette
from evals.coherence.cases import Case, CaseError, load_cases, load_gold, paired
from evals.grader.score import GraderObservation
from evals.grader.settings import DEFAULT_MATRIX, Setting, applied

CASES_DIR = os.path.join("tests", "fixtures", "sessions")
DEFAULT_OUT = os.path.join("evals", "grader", "observations.json")

# The orchestrator's own rule, imported rather than restated — the same reason
# `evals/coherence/replay.py` imports it. A second copy of "which turn is this?"
# is a second thing that can drift, and the grading window is computed from it.
_turn_index = orchestrator._turn_index


async def grade_case(
    case: Case, setting: Setting, *, client=None, timed: bool
) -> GraderObservation:
    """One grade of one case at one setting. Raises `GraderError` like the real path."""
    scenario = kb.load_scenario(case.topic_id)
    if scenario is None:
        raise CaseError(
            f"{case.id}: topic {case.topic_id!r} carries no scenario, so there "
            "is nothing for a grader to judge"
        )
    state = SessionState(**case.state) if case.state else SessionState()
    turn = _turn_index(case.dialogue)

    started = time.perf_counter()
    result, usage = await grader.grade(
        scenario=scenario,
        dialogue=list(case.dialogue),
        user_text=case.learner_turn,
        window=termination.grading_window(state, turn=turn),
        client=client,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000

    return GraderObservation(
        setting_id=setting.id,
        case_id=case.id,
        coherence=result.coherence,
        slots_filled=tuple(result.slots_filled),
        latency_ms=elapsed_ms if timed else None,
        output_tokens=getattr(usage, "output_tokens", None),
        input_tokens=getattr(usage, "input_tokens", None),
    )


async def sweep(
    cases: List[Case],
    settings: List[Setting],
    *,
    repeat: int,
    client=None,
    timed: bool,
    on_run=None,
) -> List[GraderObservation]:
    """Every setting, every case, `repeat` times.

    Serial, and for a stronger reason than the coherence replay's: this run
    produces a latency number, and concurrent calls would measure the queue
    rather than the grader.

    A failed grade costs the run and not the sweep. Four settings over seven
    cases three times is 84 paid calls, and losing them to one timeout on the
    last is a bill for nothing — so failures are collected and the sweep goes
    on, and `on_run` gets both lists as they land, for a caller that checkpoints.

    **Collected, never dropped.** A grade that times out is the single loudest
    latency result this sweep can produce: past `GRADER_TIMEOUT_S` the learner
    does not get a slow grade, they get no grade and no credit. Scoring only the
    calls that returned would report the setting that lost a turn as the fast
    one.
    """
    observations: List[GraderObservation] = []
    failures: List[Tuple[str, str, str]] = []
    for setting in settings:
        with applied(setting):
            for run in range(1, repeat + 1):
                for case in cases:
                    try:
                        observation = await grade_case(
                            case, setting, client=client, timed=timed
                        )
                    except grader.GraderError as exc:
                        print(f"{setting.id:<24} run {run}  {case.id:<24} FAILED: {exc}")
                        failures.append((setting.id, case.id, str(exc)))
                        if on_run is not None:
                            on_run(observations, failures)
                        continue
                    observations.append(observation)
                    latency = (
                        f"{observation.latency_ms:6.0f}ms"
                        if observation.latency_ms is not None
                        else "  replay"
                    )
                    print(
                        f"{setting.id:<24} run {run}  {case.id:<24} "
                        f"{latency}  out={observation.output_tokens}  "
                        f"slots={list(observation.slots_filled)}"
                    )
                    if on_run is not None:
                        on_run(observations, failures)
    return observations, failures


def _resolve_settings(names: Optional[List[str]]) -> List[Setting]:
    """The matrix, or the named subset of it.

    A typo would otherwise silently sweep nothing and overwrite the observations
    with zero rows — a quiet way to lose a measurement someone has already paid
    for.
    """
    if not names:
        return list(DEFAULT_MATRIX)
    by_id = {setting.id: setting for setting in DEFAULT_MATRIX}
    unknown = sorted(set(names) - set(by_id))
    if unknown:
        raise SystemExit(
            f"no such setting: {', '.join(unknown)}\n"
            f"known: {', '.join(sorted(by_id))}"
        )
    return [by_id[name] for name in names]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    cassette.cli.add_arguments(parser)
    parser.add_argument("--repeat", type=int, default=3, help="runs per case per setting")
    parser.add_argument("--case", action="append", help="sweep only this case id")
    parser.add_argument(
        "--setting", action="append", help="sweep only this setting id (model/effort)"
    )
    parser.add_argument("--cases-dir", default=CASES_DIR)
    parser.add_argument("--out", default=DEFAULT_OUT)
    args = parser.parse_args()

    cases = load_cases(args.cases_dir)
    # Pair even when sweeping a subset: an unlabelled case is a hole in the
    # matrix, and finding that out after spending the tokens is too late.
    paired(cases, load_gold(os.path.join(args.cases_dir, "gold.json")))
    if args.case:
        wanted = set(args.case)
        unknown = sorted(wanted - {case.id for case in cases})
        if unknown:
            raise CaseError(f"no such case: {', '.join(unknown)}")
        cases = [case for case in cases if case.id in wanted]
    settings = _resolve_settings(args.setting)

    def write(observations, failures):
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "recorded": bool(args.record),
                    "repeat": args.repeat,
                    "max_tokens": settings[0].max_tokens,
                    "settings": [asdict(setting) for setting in settings],
                    "observations": [asdict(o) for o in observations],
                    "failures": [
                        {"setting_id": s, "case_id": c, "error": e}
                        for s, c, e in failures
                    ],
                },
                handle,
                ensure_ascii=False,
                indent=2,
            )

    client = cassette.cli.client_from_args(args)
    # Written after every run, not once at the end: the file is the only record
    # of calls that have already been billed.
    observations, failures = asyncio.run(
        sweep(
            cases,
            settings,
            repeat=args.repeat,
            client=client,
            timed=bool(args.record),
            on_run=write,
        )
    )
    write(observations, failures)
    print(
        f"\nwrote {len(observations)} observations and {len(failures)} "
        f"failures to {args.out}"
    )


if __name__ == "__main__":
    main()
