"""Phase 3b live eval — the full spoken loop against real Azure + Claude.

Excluded from the default run (`pytest.ini addopts=-m "not live"`); invoke with
`pytest -m live`, real Azure/Anthropic keys, and a recorded greeting WAV at
`tests/fixtures/greeting.wav` (16 kHz mono — capture via the app's recorder).
Structural asserts only, never exact wording (DESIGN.md eval policy): a non-empty
transcript + partner reply, and `expected` tones that match pypinyin on the
recognized text. Skips cleanly when keys or the fixture are absent.
"""
import os

import pytest

from anthropic import AsyncAnthropic

from backend import config
from backend.pinyin import tone_numbers
from tests.helpers import collect_audio_turn

pytestmark = pytest.mark.live

_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "greeting.wav")


def _client():
    missing = [
        name
        for name, value in (
            ("ANTHROPIC_API_KEY", config.ANTHROPIC_API_KEY),
            ("AZURE_SPEECH_KEY", config.AZURE_SPEECH_KEY),
        )
        if not value
    ]
    if missing:
        pytest.skip(f"not configured: {', '.join(missing)}")
    if not os.path.exists(_FIXTURE):
        pytest.skip(f"recorded greeting WAV missing: {_FIXTURE}")
    return AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)


async def test_live_audio_turn_transcribes_replies_and_scores_tones():
    client = _client()
    with open(_FIXTURE, "rb") as f:
        audio = f.read()

    seen = await collect_audio_turn(audio, topic_id="greetings", client=client)

    # The turn ran to completion rather than failing in-band.
    assert "error" not in seen, seen.get("error")
    assert set(seen) >= {"transcript", "score", "reply", "done"}

    # Real STT produced something, and the worker produced a real partner reply.
    assert seen["transcript"].transcript.zh, "STT recognized nothing from the WAV"
    assert seen["reply"].reply.zh and seen["reply"].reply.pinyin

    # PA may or may not flag tones, but every expected tone we surface must be the
    # genuine target for that hanzi (derived locally, not invented by the model).
    for err in seen["score"].tone_errors:
        assert err.expected in tone_numbers(err.syllable)
        assert 1 <= err.expected <= 5

    # The live check the whole caching design rests on.
    assert seen["done"].timings.total_ms > 0
