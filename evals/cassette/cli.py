"""The flags every eval runner shares, in one place.

Two runners already want them (`evals.coherence.replay` and the scheduled
re-record job), and a second copy of "does `--record` mean top up or start
over?" is a second answer to that question.

    parser = argparse.ArgumentParser()
    cassette.cli.add_arguments(parser)
    client = cassette.cli.client_from_args(parser.parse_args())
"""
import argparse

from evals.cassette.client import CassetteClient
from evals.cassette.store import CassetteStore


def add_arguments(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Add `--record`, `--samples`, `--refresh`, `--cassettes` to a parser."""
    group = parser.add_argument_group("cassettes")
    group.add_argument(
        "--record",
        action="store_true",
        help="make real API calls for keys that are missing samples, and "
        "record them. Costs money. Without it, a miss is an error.",
    )
    group.add_argument(
        "--samples",
        type=int,
        default=1,
        help="samples to hold per key when recording. More than one draw is "
        "what lets an eval assert against a distribution (default: 1).",
    )
    group.add_argument(
        "--refresh",
        action="store_true",
        help="with --record, replace each cassette instead of topping it up. "
        "What the scheduled re-record job runs, so the diff is the news.",
    )
    group.add_argument(
        "--cassettes",
        default=str(CassetteStore.default_root()),
        help="cassette directory (default: evals/cassettes)",
    )
    return parser


def client_from_args(args) -> CassetteClient:
    """The client those flags describe."""
    record = getattr(args, "record", False)
    refresh = getattr(args, "refresh", False)
    if refresh and not record:
        raise SystemExit("--refresh does nothing without --record")
    return CassetteClient(
        CassetteStore(getattr(args, "cassettes", None) or CassetteStore.default_root()),
        record=record,
        samples=getattr(args, "samples", 1),
        refresh=refresh,
    )
