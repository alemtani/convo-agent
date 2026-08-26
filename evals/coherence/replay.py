"""Replay the recorded cases through the grader and record the tags.

Runs off **cassettes** by default (`evals/cassette/`): a key with no recording
is an error, not a live call. `--record` is the only thing that spends money,
and what it buys is committed. Its output is still a report rather than an
assertion — but a report that costs nothing to reproduce.

Each case is replayed `--repeat` times, because `coherence` and `slots_filled`
are a model's output and not a function — one run tells you what happened once.
Every run is its own row in `observations.json`, and `matrix.py` reads them all.

V2 moved both fields onto `GraderResult`. This runner calls the grader, not the
converser: a measurement taken through a different path than the one that
ships would measure something else. Turn 1 still needs the opening line the
client resubmits, because that line is never in `dialogue`.

    python -m evals.coherence.replay --repeat 3               # free, off cassettes
    python -m evals.coherence.replay --record --samples 3     # live; costs money
    python -m evals.coherence.replay --case nonsequitur-slot-fill --out /tmp/obs.json
"""
import argparse
import asyncio
import json
import os
from dataclasses import asdict
from typing import List, Optional

from backend import config, kb, orchestrator, termination
from backend.models import SessionState
from backend.workers import grader as grader_worker
from evals import cassette
from evals.coherence.cases import Case, CaseError, load_cases, load_gold, paired
from evals.coherence.matrix import Observation

CASES_DIR = os.path.join("tests", "fixtures", "sessions")
DEFAULT_OUT = os.path.join("evals", "coherence", "observations.json")

# Eval recording is not the learner-facing turn. The live bound is 15s so a
# phone is not left staring at a dead mic; a cassette that times out is a hole
# in the gate, so this runner waits longer than the app does.
_GRADE_TIMEOUT_S = 60.0


# The orchestrator's own rule, imported rather than restated. A second copy of
# "which turn is this?" is a second thing that can drift, and the window the
# grader sees is computed from it — so a replay that counted differently would
# measure a turn the app never takes.
_turn_index = orchestrator._turn_index


def _check_manifest_is_a_full_sweep(*, used_out, cases) -> None:
    """Refuse `--used-out` on a run that only visits some of the corpus.

    The manifest is what `evals.cassette.sweep` keeps. A `--case` run reaches a
    handful, so a manifest written from one would tell the sweep to delete
    every other recording in the store — the expensive mistake, made silently.
    """
    if used_out and cases:
        raise SystemExit("--used-out needs the whole corpus; drop --case")


def _opening_zh(case: Case) -> Optional[str]:
    """The 汉字 the grader is prefixed with on turn 1, or `None`."""
    line = case.opening_line
    if not line:
        return None
    zh = (line.get("zh") or "").strip()
    return zh or None


async def replay_case(case: Case, *, client=None) -> Observation:
    """One grade for one case. Raises `GraderError` like the real path."""
    scenario = kb.load_scenario(case.topic_id)
    state = SessionState(**case.state) if case.state else SessionState()
    turn = _turn_index(case.dialogue)
    grade, _usage = await grader_worker.grade(
        scenario=scenario,
        dialogue=list(case.dialogue),
        user_text=case.learner_turn,
        opening_line=_opening_zh(case),
        window=termination.grading_window(state, turn=turn),
        timeout=_GRADE_TIMEOUT_S,
        client=client,
    )
    return Observation(
        case_id=case.id,
        coherence=grade.coherence,
        slots_filled=tuple(grade.slots_filled),
        slots_filled_previously=tuple(grade.slots_filled_previously),
    )


async def replay_all(
    cases: List[Case], *, repeat: int, on_run=None, client=None
) -> List[Observation]:
    """Every case, `repeat` times, run by run.

    Serial on purpose. This is a measurement of a hot-path call and there is no
    hurry; hammering the API concurrently to save a minute would only add rate
    limiting as a variable the report cannot see.

    A failed call costs the run, not the session. `--repeat 3` over seven cases
    is 21 paid calls, and losing all of them to a timeout on the last one is a
    bill for nothing — so failures are reported and skipped, and `on_run` gets
    every observation as it lands, for a caller that wants to checkpoint.

    A cassette miss is not a failed call. It is a stale key, and it stops the
    run rather than being counted as one lost observation.
    """
    observations = []
    for run in range(1, repeat + 1):
        for case in cases:
            try:
                observation = await replay_case(case, client=client)
            except grader_worker.GraderError as exc:
                print(f"run {run}/{repeat}  {case.id:<24} FAILED: {exc}")
                continue
            observations.append(observation)
            print(
                f"run {run}/{repeat}  {case.id:<24} "
                f"{observation.coherence:<10} slots={list(observation.slots_filled)}"
            )
            if on_run is not None:
                on_run(observations)
    return observations


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    cassette.cli.add_arguments(parser)
    parser.add_argument("--repeat", type=int, default=3, help="runs per case")
    parser.add_argument("--case", action="append", help="replay only this case id")
    parser.add_argument("--cases-dir", default=CASES_DIR)
    parser.add_argument("--out", default=DEFAULT_OUT)
    args = parser.parse_args()
    _check_manifest_is_a_full_sweep(
        used_out=getattr(args, "used_out", None), cases=args.case
    )

    cases = load_cases(args.cases_dir)
    # Pair even when replaying a subset: an unlabelled case is a hole in the
    # matrix, and finding that out after spending the tokens is too late.
    paired(cases, load_gold(os.path.join(args.cases_dir, "gold.json")))
    if args.case:
        wanted = set(args.case)
        # A typo would otherwise filter to an empty list, spend nothing, and
        # overwrite the observations with zero rows — a silent way to lose a
        # measurement someone already paid for.
        unknown = sorted(wanted - {case.id for case in cases})
        if unknown:
            raise CaseError(f"no such case: {', '.join(unknown)}")
        cases = [case for case in cases if case.id in wanted]

    def write(observations):
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "model": config.GRADER_MODEL,
                    "repeat": args.repeat,
                    "observations": [asdict(o) for o in observations],
                },
                handle,
                ensure_ascii=False,
                indent=2,
            )

    # Written after every run, not once at the end: the file is the only record
    # of calls that have already been billed.
    client = cassette.cli.client_from_args(args)
    observations = asyncio.run(
        replay_all(cases, repeat=args.repeat, on_run=write, client=client)
    )
    write(observations)
    print(f"\nwrote {len(observations)} observations to {args.out}")

    # After the write, never before: the observations are already paid for.
    cassette.cli.write_used(args, client.used)


if __name__ == "__main__":
    main()
