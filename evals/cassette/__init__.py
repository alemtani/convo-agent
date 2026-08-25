"""Record/replay for Anthropic calls, so the eval gate costs nothing to run.

An eval suite that spends money on every run is a suite nobody runs on every
PR, and a gate nobody runs is not a gate. This layer records what the model said
once, commits it, and replays it for free thereafter.

    from evals import cassette

    client = cassette.CassetteClient()             # replay; a miss raises
    client = cassette.CassetteClient(record=True, samples=3)   # spends money

Hand-rolled rather than `pytest-recording`/VCR.py on purpose: those key on HTTP
method, URI and body, and the question an eval asks is about the request we
assemble — model, system blocks, output schema, messages. See `key.py`.

Cassettes live in `evals/cassettes/` and are committed. Change a prompt and the
keys change, so only the affected cases go stale; `--record` re-records exactly
those. Freshness against the live model is a scheduled job's problem
(`.github/workflows/rerecord.yml`), never a per-PR probabilistic call — CI stays
deterministic.
"""
from evals.cassette import cli
from evals.cassette.client import (
    USAGE_FIELDS,
    CassetteClient,
    CassetteMiss,
)
from evals.cassette.key import (
    NOT_IN_KEY,
    CassetteError,
    canonical_request,
    request_key,
)
from evals.cassette.store import Cassette, CassetteStore

__all__ = [
    "cli",
    "Cassette",
    "CassetteClient",
    "CassetteError",
    "CassetteMiss",
    "CassetteStore",
    "NOT_IN_KEY",
    "USAGE_FIELDS",
    "canonical_request",
    "request_key",
]
