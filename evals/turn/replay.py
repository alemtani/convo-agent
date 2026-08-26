"""Replay recorded turns through the *whole* turn, not just the grader.

    python -m evals.turn.replay                 # replays; a miss is an error
    python -m evals.turn.replay --record --samples 3

`evals/coherence/replay.py` calls `grader.grade` directly. That is the right
instrument for measuring the judge — it holds the partner still — but it means
no eval in the repo ever runs the partner. A2 cut three annotation fields and a
third of the system prompt with nothing to replay.

This runner drives `orchestrator.run_text_turn`, which threads **one** client
into `conversation.respond` and `grader.grade` alike, so a single cassette-backed
run covers the reply, the grade computed against that reply, and the state it
advances to.

Two things it reports that the grader-only runner cannot:

- **The grade computed against the converser's own reading** of the learner's
  words, which is what production grades. The grader-only runner feeds it the
  fixture's 汉字 — a stand-in the real path never sees.

- **Coherence**, since A4. It is the partner's own judgment, so this is the
  only runner that can observe one: the grader-only runner holds the partner
  still. `TurnObservation` carries the tag and `main` scores it against
  `gold.json`.

  Note that `slots_filled` here is what the turn *credited*, gate included — a
  turn the partner called incoherent shows an empty list however the grader
  graded it, which is what the learner sees. The grader's own answer, ungated,
  is the other runner's measurement.

- **Over-volunteering** (`withholding.py`): a `request` slot handed over before
  the learner asked is a point they can no longer earn.

The cases are `evals/coherence`'s, unchanged. A case is already the shape of a
turn — topic, sketch, dialogue, the learner's words, the opening line — because
`Case` was written to mirror `/api/turn/text`. `learner_turn` is 汉字 where a
real session sends pinyin; the endpoint accepts both, and the alternative is a
second transcription of every fixture.
"""
import argparse
import asyncio
import json
import os
from dataclasses import asdict, dataclass
from typing import List, Optional, Tuple

from backend import config, kb, orchestrator
from backend.models import SessionState, TextTurnRequest, Utterance
from evals import cassette
from evals.coherence.cases import Case, CaseError, load_cases, load_gold, paired
from evals.coherence.matrix import confusion
from evals.turn import withholding

CASES_DIR = os.path.join("tests", "fixtures", "sessions")
DEFAULT_OUT = os.path.join("evals", "turn", "observations.json")

# Longer than the app's deadline, for the same reason `evals/coherence` is: a
# timeout here is a lost measurement someone paid for, not a learner watching a
# bubble. The gate does not care how long it took.
_TURN_TIMEOUT_S = 90.0


@dataclass
class TurnObservation:
    """One whole turn, as the app would have produced it.

    `coherence` is the tag string rather than the annotation's boolean, so it
    lines up with `gold.json` and can be read straight into `matrix.confusion`.
    """

    case_id: str
    reply_zh: str
    reading_zh: str
    slots_filled: Tuple[str, ...]
    volunteered: Tuple[str, ...]
    said_goodbye: bool
    coherence: str
    status: str


def _request(case: Case) -> TextTurnRequest:
    line = case.opening_line
    return TextTurnRequest(
        topic_id=case.topic_id,
        text=case.learner_turn,
        dialogue=list(case.dialogue),
        state=SessionState(**case.state) if case.state else SessionState(),
        sketch=case.sketch,
        opening_line=Utterance(**line) if line else None,
    )


async def replay_case(case: Case, *, client=None) -> TurnObservation:
    """One turn, plus the withholding judgment on the reply it produced."""
    response = await orchestrator.run_text_turn(_request(case), client=client)
    scenario = kb.load_scenario(case.topic_id)
    # What *this* turn earned: the state comes back with the whole filled set,
    # and the difference from what the case submitted is the credit the turn
    # itself won. Read as a diff rather than by turn number so a settled owed
    # turn (credited late, at an earlier index) still counts as asked-and-
    # answered rather than as a slot the partner gave away.
    was_filled = set((case.state or {}).get("filled_at") or {})
    slots = tuple(sorted(set(response.state.filled_at) - was_filled))
    given_away = await withholding.judge(
        scenario=scenario,
        reply_zh=response.reply.zh,
        candidates=withholding.candidates(
            scenario, filled=sorted(was_filled), credited=slots
        ),
        client=client,
        timeout=_TURN_TIMEOUT_S,
    )
    return TurnObservation(
        case_id=case.id,
        reply_zh=response.reply.zh,
        reading_zh=response.transcript.zh,
        slots_filled=slots,
        volunteered=given_away,
        said_goodbye=response.annotation.learner_said_goodbye,
        coherence="coherent" if response.annotation.coherent else "incoherent",
        status=response.state.status,
    )


async def replay_all(
    cases: List[Case], *, repeat: int, on_run=None, client=None
) -> List[TurnObservation]:
    """Every case, `repeat` times. Serial, and a failure costs one run."""
    observations = []
    for run in range(1, repeat + 1):
        for case in cases:
            try:
                observation = await replay_case(case, client=client)
            except (
                orchestrator.conversation.ConversationError,
                withholding.WithholdingError,
            ) as exc:
                print(f"run {run}/{repeat}  {case.id:<24} FAILED: {exc}")
                continue
            observations.append(observation)
            gave = list(observation.volunteered)
            print(
                f"run {run}/{repeat}  {case.id:<24} "
                f"{observation.coherence:<10} slots={list(observation.slots_filled)}"
                + (f"  VOLUNTEERED={gave}" if gave else "")
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
    # Same rule as the grader runner: a manifest written from a `--case` run
    # would tell the sweep every other recording in the shared store is stale.
    if getattr(args, "used_out", None) and args.case:
        raise SystemExit("--used-out needs the whole corpus; drop --case")

    cases = load_cases(args.cases_dir)
    # Pair before spending anything: an unlabelled case is a hole in the
    # coherence matrix, and finding that out after the calls is too late.
    gold = load_gold(os.path.join(args.cases_dir, "gold.json"))
    paired(cases, gold)
    if args.case:
        wanted = set(args.case)
        unknown = sorted(wanted - {case.id for case in cases})
        if unknown:
            raise CaseError(f"no such case: {', '.join(unknown)}")
        cases = [case for case in cases if case.id in wanted]

    def write(observations):
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "conversation_model": config.CONVERSATION_MODEL,
                    "grader_model": config.GRADER_MODEL,
                    "judge_model": withholding.JUDGE_MODEL,
                    "repeat": args.repeat,
                    "observations": [asdict(o) for o in observations],
                },
                handle,
                ensure_ascii=False,
                indent=2,
            )

    client = cassette.cli.client_from_args(args)
    observations = asyncio.run(
        replay_all(cases, repeat=args.repeat, on_run=write, client=client)
    )
    write(observations)
    print(f"\nwrote {len(observations)} observations to {args.out}")

    # The partner's tag against a fair reader's, as a 2×2. Printed rather than
    # asserted: this runner reports, and the gate it feeds is asserted in
    # `tests/test_orchestrator.py` where it is a pure function.
    counts = confusion(observations, gold)
    print("\ncoherence — gold \\ observed")
    for expected in ("coherent", "incoherent"):
        row = "  ".join(
            f"{seen}={counts[(expected, seen)]}"
            for seen in ("coherent", "incoherent")
        )
        print(f"  {expected:<11} {row}")

    cassette.cli.write_used(args, client.used)


if __name__ == "__main__":
    main()
