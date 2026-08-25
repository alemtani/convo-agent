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


def add_pytest_options(parser) -> None:
    """Register the same flags on a pytest parser.

    Pytest's default `--samples` is 3, not the CLI's 1: the pytest evals are
    the merge gate, and a single recording is a lucky draw. The CLI stays at 1
    because `replay.py --repeat` is what walks the distribution there.
    """
    group = parser.getgroup("cassettes")
    group.addoption(
        "--record",
        action="store_true",
        default=False,
        help="make real API calls for keys that are missing samples, and "
        "record them. Costs money. Without it, a miss is an error.",
    )
    group.addoption(
        "--samples",
        action="store",
        type=int,
        default=3,
        help="samples to hold per key when recording (default: 3).",
    )
    group.addoption(
        "--refresh",
        action="store_true",
        default=False,
        help="with --record, replace each cassette instead of topping it up.",
    )
    group.addoption(
        "--cassettes",
        action="store",
        default=str(CassetteStore.default_root()),
        help="cassette directory (default: evals/cassettes)",
    )


def client_from_pytest_config(config) -> CassetteClient:
    """The client the pytest flags describe. Same object the CLI builds."""
    class _Args:
        record = config.getoption("record")
        samples = config.getoption("samples")
        refresh = config.getoption("refresh")
        cassettes = config.getoption("cassettes")

    return client_from_args(_Args)
