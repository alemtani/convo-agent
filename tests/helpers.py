"""Shared test helpers.

`collect_audio_turn` drives the staged spoken turn to completion. It lives here,
in the test tree, rather than in `backend/`: the orchestrator used to ship a
`run_audio_turn` that collected the stream into a single `TurnResponse`, but once
`POST /api/turn` became NDJSON nothing in production called it — the route
streams and the frontend dispatches on `stage`. Keeping a second, collected
contract alive in a request-path module meant its merge semantics constrained the
streaming one for the benefit of tests alone.
"""
import os

import pytest

from backend import config, orchestrator


def require_live_keys(*names: str) -> None:
    """Skip locally, fail in CI, when a contact test is missing credentials.

    Skip-as-pass is how the live suite rotted: an excluded run with no keys
    looks green. On GitHub Actions a missing secret is a configuration error.
    """
    missing = [name for name in names if not getattr(config, name)]
    if not missing:
        return
    msg = "not configured: " + ", ".join(missing)
    if os.environ.get("GITHUB_ACTIONS"):
        pytest.fail(msg + " (a skipped live test is how this suite rotted)")
    pytest.skip(msg)


def cassette_draw_count(client) -> int:
    """How many times to call so a recording fills `--samples` and a replay sees them."""
    return max(1, getattr(client, "samples", 1))


async def collect_audio_turn(
    audio=b"FAKEWAV", *, topic_id="greetings", dialogue=None, client=None
):
    """Run a spoken turn to its terminal event; return `{stage: event}`.

    Keyed by stage because a turn emits each at most once, and because that is
    how a client reads the stream — order is a property of two racing branches,
    not something a collected view should pretend to fix.

    Propagates `kb.KbError` / `stt.SttError` from the prepare half. A worker
    failure is *not* raised: it is a `TurnErrorEvent` in the returned mapping,
    which is the actual contract.
    """
    transcript, kb_block, timer = await orchestrator.prepare_audio_turn(
        audio, topic_id=topic_id
    )
    events = [
        event
        async for event in orchestrator.stream_audio_turn(
            audio,
            transcript=transcript,
            kb_block=kb_block,
            timer=timer,
            dialogue=dialogue,
            client=client,
        )
    ]
    return {event.stage: event for event in events}


def grade_stub(**fields):
    """An async stand-in for `workers.grader.grade`.

    Defaults to a grade that credits nothing, which is what most tests want: the
    fan-out, the events and the routes are what they are about, and the grader
    has its own contract tests. For a failure, use `failing_grade_stub`.
    """
    from backend.models import GraderResult

    result = GraderResult(coherence=fields.pop("coherence", "on_track"), **fields)

    async def _grade(**_kwargs):
        # `(grade, usage)`, matching the real worker: the turn reports what the
        # judgment cost separately, because it runs on a different model.
        return result, None

    return _grade


def failing_grade_stub(exc=None):
    """An async stand-in that fails the way a real grader outage does."""
    from backend.workers.grader import GraderError

    async def _grade(**_kwargs):
        raise exc or GraderError("grader unavailable")

    return _grade
