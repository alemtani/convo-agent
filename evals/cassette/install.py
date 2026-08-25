"""Seed the workers' module-global client, so a *running server* replays too.

Everything else in this package works because the caller passes `client=`. A
server has no such caller: `backend.main` threads nothing, so each worker falls
back to `_get_client()`, which lazily builds a real `AsyncAnthropic` and holds
it in a module global. `install()` puts a cassette client in that global first,
and the fallback finds it already there.

**There is deliberately no flag inside `backend/`.** `if os.environ["CASSETTES"]`
on the hot path means one misconfigured variable on fly.io serves learners canned
replies, and it drags eval code into the production import path. The
differentiator is instead *which entrypoint you launched*: `evals/server.py`
calls this and then re-exports the same app, so an eval run is
`uvicorn evals.server:app` and the ordinary `uvicorn backend.main:app` cannot be
installed into by any amount of environment.

This is the one piece of the layer that touches the critical path. It adds no
line to `backend/`, but it swaps the object every turn calls out through, at
runtime. Two consequences are load-bearing:

- It reaches **every** worker, not the interesting ones. A worker left out is a
  worker that still spends money, on a run whose whole promise is that it does
  not.
- A worker whose seam moved is an **error**, not a skip. If `_client` is renamed,
  interception silently stops working and the failure arrives as a bill rather
  than a red build, so the rename has to fail here instead.
"""
from typing import Iterable, Optional

from evals.cassette.client import CassetteClient
from evals.cassette.key import CassetteError
from evals.cassette.store import CassetteStore


def worker_modules() -> tuple:
    """Every module holding a `_client` the request path falls back to.

    Imported here rather than at module import so that merely importing the
    eval package does not drag `backend.workers` in behind it.
    """
    from backend.workers import conversation, feedback, grader, sketch

    return (conversation, grader, sketch, feedback)


def install(
    client: Optional[CassetteClient] = None,
    *,
    store: Optional[CassetteStore] = None,
    record: bool = False,
    samples: int = 1,
    refresh: bool = False,
    modules: Optional[Iterable] = None,
) -> CassetteClient:
    """Point every worker's module-global `_client` at a cassette client."""
    if client is None:
        client = CassetteClient(
            store, record=record, samples=samples, refresh=refresh
        )
    targets = tuple(modules) if modules is not None else worker_modules()
    for module in targets:
        if not hasattr(module, "_client"):
            raise CassetteError(
                f"{getattr(module, '__name__', module)} holds no module-global "
                "`_client`, so the cassette layer cannot intercept its calls. "
                "The seam moved: fix this rather than skipping the module — a "
                "worker that escapes interception spends real money."
            )
    for module in targets:
        module._client = client
    return client


def uninstall(*, modules: Optional[Iterable] = None) -> None:
    """Clear the globals again, so the next call builds a real client."""
    for module in tuple(modules) if modules is not None else worker_modules():
        module._client = None
