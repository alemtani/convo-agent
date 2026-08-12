"""Phase 3a live evals — the project's first token-spending tests.

Excluded from the default run (`pytest.ini addopts=-m "not live"`); invoke with
`pytest -m live` and a real `ANTHROPIC_API_KEY`. These assert structural
invariants and the cache-hit smoke test, never exact model wording (DESIGN.md
risk #3): a silent prefix invalidator would drop `cache_read_input_tokens` to 0.
"""
import pytest

from anthropic import AsyncAnthropic

from backend import config, kb
from backend.models import ConversationResult, Utterance
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


async def test_live_cache_read_on_second_turn():
    """Turn 1 writes the cache; turn 2 (same prefix) reads it.

    The cached-prefix invariant, proven end-to-end: if the system prompt + KB +
    sketch are byte-identical across turns, the second call serves them from
    cache. A non-zero `cache_read_input_tokens` is the proof.
    """
    client = _client()
    kb_block = kb.load_kb_block("greetings")

    _r1, _a1, _rd1, usage1 = await conversation.respond(
        kb_block=kb_block, sketch=SKETCH_STUB, dialogue=[], user_text="你好",
        forgiveness_level=config.FORGIVENESS_LEVEL_DEFAULT, client=client,
    )
    # The KB block must clear Sonnet 4.6's 2048-token minimum cacheable prefix, or
    # nothing ever caches. Turn 1 may *write* the cache (cold) or *read* an entry a
    # prior run left within the 5-min TTL (warm) — either proves the prefix is
    # cacheable; only a flat zero means it's too small. (Don't require creation: a
    # warm cache legitimately reports cache_creation == 0.)
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


async def test_live_reply_is_valid_structured_output():
    """Structural eval: valid schema, a non-empty reply, well-formed annotation.

    Length is intentionally not asserted — brevity is shaped by the prompt, and a
    hard char ceiling on a live reply is brittle. We assert structure only.
    """
    client = _client()
    reply, annotation, _reading, _usage = await conversation.respond(
        kb_block=kb.load_kb_block("greetings"), sketch=SKETCH_STUB, dialogue=[],
        user_text="你好", forgiveness_level=config.FORGIVENESS_LEVEL_DEFAULT,
        client=client,
    )

    assert isinstance(reply, Utterance)
    assert reply.zh and reply.pinyin
    assert annotation.coherence in {"on_track", "drifting", "off_track"}
    assert isinstance(annotation.topic_tags, list)
    # Tone is never the model's to judge, so the field it would go in is not
    # even in the schema any more — the server adds it downstream.
    assert not hasattr(annotation, "tone_errors")


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
    kb_block = kb.load_kb_block("greetings")

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
