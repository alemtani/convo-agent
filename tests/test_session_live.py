"""Contact eval — generated flavour caches across turns, against the real API.

The sketch worker's *shape* is `test_session_eval.py` (cassettes). This file
keeps the one claim cassettes cannot make: that a generated flavour block is
byte-stable enough for the second turn to read the cache (issue #30).
"""
import pytest

from anthropic import AsyncAnthropic

from backend import config, kb
from backend.workers import conversation, sketch
from tests.helpers import require_live_keys

pytestmark = pytest.mark.live


def _client():
    require_live_keys("ANTHROPIC_API_KEY")
    return AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)


async def test_live_generated_flavour_caches_across_turns():
    """The frozen flavour block is byte-identical across every turn of a
    session: generate it once, hand the exact same string to two turns, and
    the second must read the cache (issue #30 acceptance)."""
    client = _client()
    kb_block = kb.load_converser_block("greetings")
    scenario = kb.load_scenario("greetings")

    session = await sketch.generate("greetings", scenario, client=client)

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
