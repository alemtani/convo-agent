"""Record the behavioral cases against live, so CI can replay them for free.

    python -m evals.behavior.record --record --samples 3    # live; costs money
    python -m evals.behavior.record                         # replay; proves the keys

Without `--record` this spends nothing and answers one question: does every
case the test asserts on have a recording? That is the check to run after a
prompt change, before the build tells you.

`--refresh` replaces each recording rather than topping it up, which is what
the scheduled job runs (`.github/workflows/rerecord.yml`): the diff against
what is committed is the entire output.

`--used-out` writes the keys this run reached, for `evals.cassette.sweep`. One
store, three runners: this one, `evals.coherence.replay` and
`evals.turn.replay`. None of them reaches the others' keys, so none may prune
alone — the sweep takes the union. A run that skipped this flag and let the
sweep proceed would have its whole corpus deleted as unreachable.
"""
import argparse
import asyncio

from evals import cassette
from evals.behavior.cases import CASES


async def run_all(client, *, samples: int) -> int:
    """Every case, `samples` times. Serial, for the reason `replay.py` is.

    A recording client needs one call per sample: it records until the key
    holds as many as were asked for, so N draws are N calls.
    """
    failures = 0
    for case in CASES:
        for draw in range(1, samples + 1):
            try:
                await case.run(client)
            except Exception as exc:  # noqa: BLE001 — the report is the point
                failures += 1
                print(f"{case.id:<28} draw {draw}/{samples}  FAILED: {exc}")
                continue
            print(f"{case.id:<28} draw {draw}/{samples}  ok")
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    cassette.cli.add_arguments(parser)
    args = parser.parse_args()

    client = cassette.cli.client_from_args(args)
    # A replay walks the samples it has; asking for more than one on a run that
    # is not recording would only re-read the same cassette.
    samples = max(1, args.samples) if args.record else 1
    failures = asyncio.run(run_all(client, samples=samples))
    print(f"\n{client.hits} replayed, {client.recorded} recorded, {failures} failed")
    # This runner always visits the whole corpus — there is no `--case` — so the
    # manifest is always a full sweep of what it owns. Written even on failure:
    # a key that was reached is reached, and reporting fewer would invite the
    # sweep to delete a recording that is still in use.
    cassette.cli.write_used(args, client.used)
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
