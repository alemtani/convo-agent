"""The Anthropic half of the old live audio-turn eval, off cassettes.

`test_turn_live` still hits Azure STT and PA — those stay live. The claim
that the partner produced a real reply does not need a microphone, and
cassetting Azure would not make it cheap, it would make it a different test.
"""
import pytest

from backend import orchestrator
from backend.models import TextTurnRequest
from tests.helpers import cassette_draw_count

pytestmark = pytest.mark.cassette

SKETCH_STUB = "A short first-meeting exchange."


async def test_a_text_turn_produces_a_partner_reply(cassette_client):
    n = cassette_draw_count(cassette_client)
    for _ in range(n):
        resp = await orchestrator.run_text_turn(
            TextTurnRequest(
                topic_id="greetings",
                text="你好",
                sketch=SKETCH_STUB,
            ),
            client=cassette_client,
        )
        assert resp.reply.zh and resp.reply.pinyin
        assert resp.transcript.zh
