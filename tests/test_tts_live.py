"""M4 live eval — real Azure synthesis.

Excluded from the default run (`pytest.ini addopts=-m "not live"`); invoke with
`pytest -m live` and real Azure keys. Structural asserts only: that bytes come
back, that they are the MP3 we asked for, and that the rate knob actually
changes the audio.

That last one is the reason this file exists. Everything about the SSML is
asserted for free in `test_tts.py`, but "Azure accepted the attribute" and
"Azure slowed the voice down" are different claims, and only a real synthesis
can tell them apart. Constant-bitrate MP3 makes it measurable without a decoder:
at a fixed 48 kbit/s, bytes are proportional to duration, so a rate of -10%
should return about 1/0.9 as many bytes as an unmodified one.
"""
import pytest

from backend.speech import tts
from tests.helpers import require_live_keys

pytestmark = pytest.mark.live

LINE = "你好！很高兴认识你。"


def _require_azure():
    require_live_keys("AZURE_SPEECH_KEY")


async def test_live_synthesis_returns_playable_mp3():
    _require_azure()
    tts.clear_cache()

    audio = await tts.synthesize(LINE)

    assert len(audio) > 2000, "too small to be a spoken sentence"
    # An ID3 tag, or a bare MPEG frame sync (11 set bits). Either is what a
    # browser's decodeAudioData expects to find; neither is what an error page
    # or a raw-PCM misconfiguration looks like.
    assert audio[:3] == b"ID3" or (audio[0] == 0xFF and audio[1] & 0xE0 == 0xE0)


async def test_live_rate_actually_slows_the_voice(monkeypatch):
    """The knob is for a band-1–2 learner; a knob that does nothing is worse
    than none, because it ends the conversation about pace."""
    _require_azure()

    tts.clear_cache()
    monkeypatch.setattr(tts.config, "TTS_RATE_PCT", 0)
    normal = await tts.synthesize(LINE)

    tts.clear_cache()
    monkeypatch.setattr(tts.config, "TTS_RATE_PCT", -10)
    slowed = await tts.synthesize(LINE)

    ratio = len(slowed) / len(normal)
    # Expected ~1.11. The window is wide because leading/trailing silence and
    # the ID3 tag are fixed overhead that dilutes the ratio on a short line —
    # the assertion is "meaningfully longer", not a duration measurement.
    assert 1.03 < ratio < 1.30, f"rate had no clear effect (ratio {ratio:.2f})"
