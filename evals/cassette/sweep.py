"""Delete the recordings no runner reached.

**Why this is not a flag on a runner.** One cassette store is shared by
`evals.coherence.replay` (the grader alone) and `evals.turn.replay` (the whole
turn). Neither reaches the other's keys, so neither can decide on its own what
is stale — a runner that pruned what *it* did not touch would delete the other
runner's entire corpus.

So each runner writes down what it reached (`--used-out`), and the sweep takes
the union. A key nothing reached is one no prompt in the tree can produce any
more: the request is rebuilt from the code on every run, and a prompt edit
changes the hash, so the old file is unreachable and its name is a hash nobody
can read.

    python -m evals.coherence.replay --record --refresh --samples 5 \\
        --used-out /tmp/coherence.json
    python -m evals.turn.replay --record --refresh --samples 5 \\
        --used-out /tmp/turn.json
    python -m evals.cassette.sweep --used /tmp/coherence.json /tmp/turn.json

Only the scheduled re-record job runs this. It is the one context where every
runner sweeps the whole corpus in the same pass.
"""
import argparse
import json
import os
from typing import List

from evals.cassette.key import CassetteError
from evals.cassette.store import CassetteStore


def load_used(path: str) -> List[str]:
    """The keys one runner reported reaching.

    A manifest that is not there is an error rather than an empty set. A runner
    that crashed writes nothing, and treating that as "reached no keys" would
    delete everything it was responsible for — the expensive mistake, made
    silently, which is exactly what this indirection exists to prevent.
    """
    if not os.path.exists(path):
        raise CassetteError(
            f"missing manifest {path}: a runner did not report which keys it "
            "reached, so the sweep cannot tell stale from unswept"
        )
    with open(path, encoding="utf-8") as handle:
        return list(json.load(handle)["used"])


def run(*, store: CassetteStore, manifests: List[str]) -> List[str]:
    """Prune the store down to the union of every manifest. Returns what went."""
    keep = set()
    for path in manifests:
        keep.update(load_used(path))
    return store.prune(keep=keep)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--used",
        nargs="+",
        required=True,
        help="every runner's --used-out manifest. All of them, or the sweep "
        "deletes what the missing runner was responsible for.",
    )
    parser.add_argument("--cassettes", default=str(CassetteStore.default_root()))
    args = parser.parse_args()

    removed = run(store=CassetteStore(args.cassettes), manifests=args.used)
    for key in removed:
        print(f"pruned {key[:12]}… — no prompt in this tree produces it")
    print(f"pruned {len(removed)} unreachable cassette(s)")


if __name__ == "__main__":
    main()
