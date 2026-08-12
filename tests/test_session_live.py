"""M2-B live eval — the session-start flavour, end to end.

Excluded from the default run (`pytest.ini addopts=-m "not live"`); invoke with
`pytest -m live` and a real `ANTHROPIC_API_KEY`. Proves the acceptance bar in
`docs/SCENARIOS.md` / issue #30: a real sketch-worker call produces an opening
line + flavour, and that flavour caches across turns exactly like the old
hardcoded `SKETCH_STUB` did — the swap changed *who generates the bytes*, not
the caching contract.
"""
import pytest

from anthropic import AsyncAnthropic

from backend import config, kb
from backend.models import SketchResult, Utterance
from backend.workers import conversation, sketch

pytestmark = pytest.mark.live


def _client():
    if not config.ANTHROPIC_API_KEY:
        pytest.skip("ANTHROPIC_API_KEY not configured")
    return AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)


async def test_live_sketch_worker_produces_a_valid_result():
    client = _client()
    result = await sketch.generate("greetings", client=client)

    assert isinstance(result, SketchResult)
    assert result.opening_line.zh and result.opening_line.pinyin
    assert result.sketch.strip()


async def test_live_generated_flavour_caches_across_turns():
    """The frozen flavour block is byte-identical across every turn of a
    session: generate it once, hand the exact same string to two turns, and
    the second must read the cache (issue #30 acceptance)."""
    client = _client()
    kb_block = kb.load_kb_block("greetings")

    session = await sketch.generate("greetings", client=client)

    _r1, _a1, _rd1, usage1 = await conversation.respond(
        kb_block=kb_block, sketch=session.sketch, dialogue=[], user_text="你好",
        forgiveness_level=config.FORGIVENESS_LEVEL_DEFAULT, client=client,
    )
    assert (
        usage1.cache_creation_input_tokens > 0 or usage1.cache_read_input_tokens > 0
    ), "prefix did not cache — too small?"

    _r2, _a2, _rd2, usage2 = await conversation.respond(
        kb_block=kb_block, sketch=session.sketch,
        dialogue=[
            {"role": "user", "zh": "你好"},
            {"role": "partner", "zh": _r1.zh},
        ],
        user_text="我叫小明",
        forgiveness_level=config.FORGIVENESS_LEVEL_DEFAULT, client=client,
    )
    assert usage2.cache_read_input_tokens > 0, "cache miss — generated flavour isn't byte-stable?"
