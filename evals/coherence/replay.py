"""Replay the recorded cases through the conversation worker and record the tags.

The live half of V0. It spends tokens, so it is a script rather than a test:
its output is a report, and a report is not an assertion.

Each case is replayed `--repeat` times, because `coherence` is a model's output
and not a function — one run tells you what happened once. Every run is its own
row in `observations.json`, and `matrix.py` reads them all.

Deliberately built on the same seam the text turn uses (`orchestrator.run_text_turn`
calls exactly this): same KB block, same sketch, same pressure hint, same
`turn` derivation. A measurement taken through a different path than the one
that ships would measure something else.

    python -m evals.coherence.replay --repeat 3
    python -m evals.coherence.replay --case nonsequitur-slot-fill --out /tmp/obs.json
"""
import argparse
import asyncio
import json
import os
from dataclasses import asdict
from typing import List

from backend import config, kb, orchestrator, termination
from backend.models import SessionState
from backend.workers import conversation
from evals.coherence.cases import Case, CaseError, load_cases, load_gold, paired
from evals.coherence.matrix import Observation

CASES_DIR = os.path.join("tests", "fixtures", "sessions")
DEFAULT_OUT = os.path.join("evals", "coherence", "observations.json")


# The orchestrator's own rule, imported rather than restated. A second copy of
# "which turn is this?" is a second thing that can drift, and the hint the
# partner sees is computed from it — so a replay that counted differently would
# measure a turn the app never takes.
_turn_index = orchestrator._turn_index


async def replay_case(case: Case, *, client=None) -> Observation:
    """One live turn for one case. Raises `ConversationError` like the real path."""
    scenario = kb.load_scenario(case.topic_id)
    state = SessionState(**case.state) if case.state else SessionState()
    turn = _turn_index(case.dialogue)
    _reply, annotation, _reading, _usage = await conversation.respond(
        kb_block=kb.load_kb_block(case.topic_id),
        sketch=case.sketch,
        dialogue=list(case.dialogue),
        user_text=case.learner_turn,
        forgiveness_level=config.FORGIVENESS_LEVEL_DEFAULT,
        hint=termination.pressure_hint(state, scenario=scenario, turn=turn),
        client=client,
    )
    return Observation(
        case_id=case.id,
        coherence=annotation.coherence,
        slots_filled=tuple(annotation.slots_filled),
    )


async def replay_all(cases: List[Case], *, repeat: int, on_run=None) -> List[Observation]:
    """Every case, `repeat` times, run by run.

    Serial on purpose. This is a measurement of a hot-path call and there is no
    hurry; hammering the API concurrently to save a minute would only add rate
    limiting as a variable the report cannot see.

    A failed call costs the run, not the session. `--repeat 3` over seven cases
    is 21 paid calls, and losing all of them to a timeout on the last one is a
    bill for nothing — so failures are reported and skipped, and `on_run` gets
    every observation as it lands, for a caller that wants to checkpoint.
    """
    observations = []
    for run in range(1, repeat + 1):
        for case in cases:
            try:
                observation = await replay_case(case)
            except conversation.ConversationError as exc:
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
    parser.add_argument("--repeat", type=int, default=3, help="runs per case")
    parser.add_argument("--case", action="append", help="replay only this case id")
    parser.add_argument("--cases-dir", default=CASES_DIR)
    parser.add_argument("--out", default=DEFAULT_OUT)
    args = parser.parse_args()

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
                    "model": config.CONVERSATION_MODEL,
                    "repeat": args.repeat,
                    "observations": [asdict(o) for o in observations],
                },
                handle,
                ensure_ascii=False,
                indent=2,
            )

    # Written after every run, not once at the end: the file is the only record
    # of calls that have already been billed.
    observations = asyncio.run(replay_all(cases, repeat=args.repeat, on_run=write))
    write(observations)
    print(f"\nwrote {len(observations)} observations to {args.out}")


if __name__ == "__main__":
    main()
