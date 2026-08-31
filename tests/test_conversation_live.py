"""Contact tests for the conversation worker's cached prefix.

**These stay live on purpose.** A0.6 moved every behavioral test in this file
onto cassettes (`tests/test_worker_behavior.py`), and stopped at the cache
assertions: `cache_read_input_tokens > 0` exists to prove the *real API* served
our prefix from cache, and replaying a recorded `850` proves only that someone
once wrote `850` into a JSON file. Cassetting these would not make them cheap,
it would make them a lie.

Excluded from the default run (`pytest.ini addopts=-m "not live"`); invoke with
`pytest -m live` and a real `ANTHROPIC_API_KEY`. The scheduled live job runs
them weekly (`.github/workflows/live.yml`), which is what stops this file
rotting a second time.
"""
import pytest

from anthropic import AsyncAnthropic

from backend import config, kb
from backend.workers import conversation

pytestmark = pytest.mark.live

# A stand-in for a session's frozen flavour block (`SessionStartResponse.sketch`
# in real use) — these tests are about the KB block caching, not the sketch
# worker itself, so any byte-stable string exercises the same cache mechanics.
SKETCH_STUB = "A short first-meeting exchange."


def _client():
    if not config.ANTHROPIC_API_KEY:
        pytest.skip("ANTHROPIC_API_KEY not configured")
    return AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)


def _kb_block():
    """The block the app actually freezes.

    `load_converser_block`, never `load_kb_block`: V2 blinded the partner to the
    goal and the slots, and a cache measurement taken over a prefix the app no
    longer builds measures nothing about production.
    """
    return kb.load_converser_block("greetings")


async def test_live_cache_read_on_second_turn():
    """Turn 1 writes the cache; turn 2 (same prefix) reads it.

    The cached-prefix invariant, proven end-to-end: if the system prompt + KB +
    sketch are byte-identical across turns, the second call serves them from
    cache. A non-zero `cache_read_input_tokens` is the proof.
    """
    client = _client()
    kb_block = _kb_block()

    _r1, _a1, _rd1, usage1 = await conversation.respond(
        kb_block=kb_block, sketch=SKETCH_STUB, dialogue=[], user_text="你好",
        forgiveness_level=config.FORGIVENESS_LEVEL_DEFAULT, client=client,
    )
    # The KB block must clear the model's minimum cacheable prefix, or nothing
    # ever caches. Turn 1 may *write* the cache (cold) or *read* an entry a
    # prior run left within the TTL (warm) — either proves the prefix is
    # cacheable; only a flat zero means it's too small. (Don't require creation:
    # a warm cache legitimately reports cache_creation == 0.)
    assert (
        usage1.cache_creation_input_tokens > 0
        or usage1.cache_read_input_tokens > 0
    ), "prefix did not cache — too small?"

    _r2, _a2, _rd2, usage2 = await conversation.respond(
        kb_block=kb_block, sketch=SKETCH_STUB,
        dialogue=[
            {"role": "user", "zh": "你好"},
            {"role": "partner", "zh": "你好！你叫什么名字？"},
        ],
        user_text="我叫小明",
        forgiveness_level=config.FORGIVENESS_LEVEL_DEFAULT, client=client,
    )
    assert usage2.cache_read_input_tokens > 0, "cache miss — a silent invalidator?"


async def test_live_each_output_shape_caches_on_its_own_prefix():
    """The output schema rides *inside* the cached span.

    Measured, not assumed: byte-identical system blocks report different
    `cache_creation_input_tokens` per schema, so the spoken and text shapes keep
    separate cache entries. That costs one extra write per session — fine — but
    only if each shape still *reads* on its own second turn. If a shape never
    reads, the output cut has traded ~40 output tokens for a full prefix write
    every turn, which is a much worse deal than it looks.
    """
    client = _client()
    kb_block = _kb_block()

    async def turn(want_reading):
        _reply, _ann, _reading, usage = await conversation.respond(
            kb_block=kb_block, sketch=SKETCH_STUB, dialogue=[], user_text="你好",
            forgiveness_level=config.FORGIVENESS_LEVEL_DEFAULT,
            want_reading=want_reading, client=client,
        )
        return usage

    # Warm both, then read both. Interleaved on purpose: if the two shapes
    # shared one entry, the alternation would show up as a write every turn.
    await turn(True)
    await turn(False)
    warm_text, warm_spoken = await turn(True), await turn(False)

    assert warm_text.cache_read_input_tokens > 0, "text shape stopped caching"
    assert warm_spoken.cache_read_input_tokens > 0, "spoken shape stopped caching"
    assert (
        warm_text.cache_read_input_tokens != warm_spoken.cache_read_input_tokens
    ), "expected distinct prefixes — has the schema left the cached span?"
