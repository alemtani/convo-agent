"""Delete the recordings no runner reached.

**Why this is not a flag on a runner.** One cassette store is shared by five
runners — the grader (`evals.coherence.replay`), the turn and the probes
(`evals.turn.replay` over two corpora), the whole session
(`evals.review.replay`) and the behavioral cases (`evals.behavior.record`).
None reaches another's keys, so none can decide on its own what is stale: a
runner that pruned what *it* did not touch would delete the other four corpora.

So each runner writes down what it reached (`--used-out`), and the sweep takes
the union. A key nothing reached is one no prompt in the tree can produce any
more: the request is rebuilt from the code on every run, and a prompt edit
changes the hash, so the old file is unreachable and its name is a hash nobody
can read.

**Sweep only from a pass where every runner ran.** A manifest short of the full
set is the expensive mistake this indirection exists to prevent — the missing
runner's whole corpus reads as unreachable and goes. The full recipe, with the
depth each corpus needs, is in `evals/cassettes/README.md`:

    python -m evals.coherence.replay --record --refresh --samples 5 --repeat 5 \\
        --used-out /tmp/coherence.json
    python -m evals.turn.replay --record --refresh --samples 5 --repeat 5 \\
        --used-out /tmp/turn.json
    python -m evals.turn.replay --record --refresh --samples 5 --repeat 5 \\
        --cases-dir evals/turn/cases --out evals/turn/observations.probes.json \\
        --used-out /tmp/turn-probes.json
    python -m evals.review.replay --record --refresh --samples 20 --repeat 20 \\
        --used-out /tmp/review.json
    python -m evals.behavior.record --record --refresh --samples 5 \\
        --used-out /tmp/behavior.json
    python -m evals.cassette.sweep --used /tmp/coherence.json /tmp/turn.json \\
        /tmp/turn-probes.json /tmp/review.json /tmp/behavior.json

Nothing runs this on a schedule. It is a manual step, and the manifests are what
make it safe: run it only from a pass where **every** runner ran with
`--used-out`, never from one runner's. `evals/cassettes/README.md` has the full
recipe.
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
