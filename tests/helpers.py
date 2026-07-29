"""Shared test helpers.

`collect_audio_turn` drives the staged spoken turn to completion. It lives here,
in the test tree, rather than in `backend/`: the orchestrator used to ship a
`run_audio_turn` that collected the stream into a single `TurnResponse`, but once
`POST /api/turn` became NDJSON nothing in production called it — the route
streams and the frontend dispatches on `stage`. Keeping a second, collected
contract alive in a request-path module meant its merge semantics constrained the
streaming one for the benefit of tests alone.
"""
from backend import orchestrator


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
