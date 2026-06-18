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
from backend.prompts import SKETCH_STUB
from backend.workers import conversation

pytestmark = pytest.mark.live


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

    _r1, _a1, usage1 = await conversation.respond(
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

    _r2, _a2, usage2 = await conversation.respond(
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
    reply, annotation, _usage = await conversation.respond(
        kb_block=kb.load_kb_block("greetings"), sketch=SKETCH_STUB, dialogue=[],
        user_text="你好", forgiveness_level=config.FORGIVENESS_LEVEL_DEFAULT,
        client=client,
    )

    assert isinstance(reply, Utterance)
    assert reply.zh and reply.pinyin
    assert annotation.coherence in {"on_track", "drifting", "off_track"}
    assert isinstance(annotation.topic_tags, list)
    # Text-only turn carries no audio, so there is nothing to score for tone.
    assert annotation.tone_errors == []
