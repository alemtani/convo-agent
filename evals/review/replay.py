"""Replay finished sessions through the end-of-session review, and record what
credit came back.

Runs off **cassettes** by default (`evals/cassettes/`): a key with no recording
is an error, not a live call. `--record` is the only thing that spends money,
and what it buys is committed.

    python -m evals.review.replay --repeat 5                # free, off cassettes
    python -m evals.review.replay --record --samples 20     # live; costs money
    python -m evals.review.replay --case greetings-name-question-two-back

**It drives `feedback.review_session`, not the grader.** The review is a call
the grader makes on the review's terms — a whole-session window, its own note,
`review=True` — and a runner that assembled that call itself would measure a
request nobody ships. What it reads back is the state the pass returns, so the
number is the credit a learner would actually have seen on their card.

Each case is replayed `--repeat` times, because a recovery is a model's output
and not a function. Every run is its own row in `observations.json`.
"""
import argparse
import asyncio
import json
import os
from dataclasses import asdict
from typing import List

from backend import config, kb
from backend.models import DialogueTurn, SessionState, Utterance, VerdictRequest
from backend.workers import feedback
from evals import cassette
from evals.review.cases import (
    ReviewCase,
    ReviewCaseError,
    load_cases,
    load_gold,
    paired,
)
from evals.review.recall import ReviewObservation

CASES_DIR = os.path.join("evals", "review", "cases")
DEFAULT_OUT = os.path.join("evals", "review", "observations.json")

# The shipped deadline is 8s, so a learner is not left watching a pending card
# while a whole-session re-read runs. A cassette that times out is a hole in
# the gate, so recording waits longer than the app does — the same trade
# `evals/coherence/replay.py` makes for the live grade.
_REVIEW_TIMEOUT_S = 120.0


def _check_manifest_is_a_full_sweep(*, used_out, cases) -> None:
    """Refuse `--used-out` on a run that only visits some of the corpus.

    The manifest is what `evals.cassette.sweep` keeps, so one written from a
    `--case` run would tell the sweep every other recording is stale.
    """
    if used_out and cases:
        raise SystemExit("--used-out needs the whole corpus; drop --case")


def request_for(case: ReviewCase) -> VerdictRequest:
    """The `/api/verdict` body this case is, in the shape the route receives."""
    return VerdictRequest(
        topic_id=case.topic_id,
        dialogue=[DialogueTurn(role=t["role"], zh=t["zh"]) for t in case.dialogue],
        state=SessionState(**case.state) if case.state else SessionState(),
        opening_line=(
            Utterance(
                zh=case.opening_line["zh"],
                pinyin=case.opening_line.get("pinyin", ""),
            )
            if case.opening_line
            else None
        ),
    )


async def replay_case(case: ReviewCase, *, client=None) -> ReviewObservation:
    """One session review for one case; report the credit it added.

    `review_session` swallows a `GraderError` and returns the submitted state —
    that is the shipped degradation, and here it reads as a draw that recovered
    nothing. Which is the truth about that draw: the learner got no credit.
    """
    request = request_for(case)
    before = request.state.filled
    state = await feedback.review_session(
        request, scenario=kb.load_scenario(case.topic_id), grader_client=client
    )
    return ReviewObservation(
        case_id=case.id, recovered=tuple(sorted(state.filled - before))
    )


async def replay_all(
    cases: List[ReviewCase], *, repeat: int, on_run=None, client=None
) -> List[ReviewObservation]:
    """Every case, `repeat` times, run by run.

    Serial on purpose, like the grader's runner: this measures a call, and
    hammering the API concurrently to save a minute would only add rate
    limiting as a variable the report cannot see.
    """
    observations = []
    for run in range(1, repeat + 1):
        for case in cases:
            observation = await replay_case(case, client=client)
            observations.append(observation)
            print(
                f"run {run}/{repeat}  {case.id:<38} "
                f"recovered={list(observation.recovered)}"
            )
            if on_run is not None:
                on_run(observations)
    return observations


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    cassette.cli.add_arguments(parser)
    parser.add_argument("--repeat", type=int, default=5, help="runs per case")
    parser.add_argument("--case", action="append", help="replay only this case id")
    parser.add_argument("--cases-dir", default=CASES_DIR)
    parser.add_argument("--out", default=DEFAULT_OUT)
    args = parser.parse_args()
    _check_manifest_is_a_full_sweep(
        used_out=getattr(args, "used_out", None), cases=args.case
    )

    cases = load_cases(args.cases_dir)
    # Pair even for a subset run: an unlabelled case is a hole in the numbers,
    # and finding that out after spending the tokens is too late.
    paired(cases, load_gold(os.path.join(args.cases_dir, "gold.json")))
    if args.case:
        wanted = set(args.case)
        unknown = sorted(wanted - {case.id for case in cases})
        if unknown:
            raise ReviewCaseError(f"no such case: {', '.join(unknown)}")
        cases = [case for case in cases if case.id in wanted]

    # The app's deadline is a promise to a learner watching a spinner; this is
    # a recording nobody is waiting on. Set on the module the worker reads,
    # because the review's timeout is not a parameter it takes — and it should
    # not become one for an eval's convenience.
    config.VERDICT_REVIEW_TIMEOUT_S = _REVIEW_TIMEOUT_S

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

    cassette.cli.write_used(args, client.used)


if __name__ == "__main__":
    main()
