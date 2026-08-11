"""Azure TTS boundary — contract tests with the SDK fully mocked (M4).

Two things are worth pinning here, and neither needs Azure.

The **SSML we build** is the whole configuration surface: voice, rate, language,
and the escaping that decides whether a reply containing `&` synthesizes or
fails. It is a request, so we assert it as one — parsed as XML, not compared as
a string, so reformatting the template doesn't break the suite.

The **cache** is the acceptance criterion ("replay costs no second synthesis
call") expressed on the server. Client-side replay never reaches this module;
this layer is what makes a page reload, or a partner repeating 你好, free. Its
tests count SDK invocations, because that count *is* the money.

We never hit Azure and never assert what the audio sounds like — the live check
in `test_tts_live.py` does the part only a real voice can answer.
"""
import types
from xml.etree import ElementTree

import pytest

from backend.speech import _azure, tts

SSML_NS = {"s": "http://www.w3.org/2001/10/synthesis"}


def _make_fake_speechsdk(result, recorder):
    """Build a stand-in for the `speechsdk` module wired to `result`."""

    class FakeSpeechConfig:
        def __init__(self, subscription, region):
            self.subscription = subscription
            self.region = region
            self.output_format = None

        def set_speech_synthesis_output_format(self, fmt):
            self.output_format = fmt

    class FakeSynthesizer:
        def __init__(self, speech_config, audio_config):
            recorder["speech_config"] = speech_config
            recorder["audio_config"] = audio_config

        def speak_ssml_async(self, ssml):
            recorder["ssml"] = ssml
            recorder["calls"] = recorder.get("calls", 0) + 1
            return types.SimpleNamespace(get=lambda: result)

    return types.SimpleNamespace(
        SpeechConfig=FakeSpeechConfig,
        SpeechSynthesizer=FakeSynthesizer,
        ResultReason=types.SimpleNamespace(
            SynthesizingAudioCompleted="SynthesizingAudioCompleted",
            Canceled="Canceled",
        ),
        SpeechSynthesisOutputFormat=types.SimpleNamespace(
            Audio24Khz48KBitRateMonoMp3="Audio24Khz48KBitRateMonoMp3",
        ),
        CancellationDetails=lambda r: types.SimpleNamespace(
            reason="Error", error_details=getattr(r, "error_details", "")
        ),
    )


def _result(reason, audio_data=b"", error_details=""):
    return types.SimpleNamespace(
        reason=reason, audio_data=audio_data, error_details=error_details
    )


@pytest.fixture
def patched(monkeypatch):
    """Install fake credentials and an empty cache; wire a fake SDK + result.

    The cache is module state, so it is cleared per test rather than per call —
    a test that inherited another's entries would assert on a hit it never made.
    """
    monkeypatch.setattr(_azure.config, "AZURE_SPEECH_KEY", "test-key")
    monkeypatch.setattr(_azure.config, "AZURE_SPEECH_REGION", "test-region")
    tts.clear_cache()

    def install(result):
        recorder = {}
        fake = _make_fake_speechsdk(result, recorder)
        monkeypatch.setattr(tts, "speechsdk", fake)
        monkeypatch.setattr(_azure, "speechsdk", fake)
        return recorder

    return install


def _ssml_of(recorder):
    return ElementTree.fromstring(recorder["ssml"])


# --- what we send -----------------------------------------------------------

async def test_completed_synthesis_returns_audio_bytes(patched):
    patched(_result("SynthesizingAudioCompleted", audio_data=b"ID3-mp3-bytes"))

    assert await tts.synthesize("你好") == b"ID3-mp3-bytes"


async def test_ssml_carries_the_voice_language_and_text(patched):
    recorder = patched(_result("SynthesizingAudioCompleted", audio_data=b"x"))

    await tts.synthesize("你好")

    root = _ssml_of(recorder)
    assert root.get("{http://www.w3.org/XML/1998/namespace}lang") == "zh-CN"
    voice = root.find("s:voice", SSML_NS)
    assert voice.get("name") == "zh-CN-XiaoxiaoNeural"
    assert "".join(voice.itertext()).strip() == "你好"


async def test_ssml_slows_the_default_neural_pace(patched):
    """The reason this endpoint builds SSML at all, rather than calling speak_text."""
    recorder = patched(_result("SynthesizingAudioCompleted", audio_data=b"x"))

    await tts.synthesize("你好")

    prosody = _ssml_of(recorder).find("s:voice/s:prosody", SSML_NS)
    assert prosody.get("rate") == "-10%"


async def test_a_positive_rate_keeps_its_sign(patched, monkeypatch):
    """Azure reads the percentage as signed; a bare `10%` means *faster*."""
    monkeypatch.setattr(tts.config, "TTS_RATE_PCT", 10)
    recorder = patched(_result("SynthesizingAudioCompleted", audio_data=b"x"))

    await tts.synthesize("你好")

    assert _ssml_of(recorder).find("s:voice/s:prosody", SSML_NS).get("rate") == "+10%"


@pytest.mark.parametrize("text", ["茶 & 咖啡", "他说<好>", 'she said "hi"', "a'b"])
async def test_markup_in_the_reply_is_escaped(patched, text):
    """The partner writes the text, so anything it can emit must survive.

    Unescaped, a single `&` makes Azure reject the whole request — and it would
    do it on a *real reply mid-session*, which is the worst place to find out.
    """
    recorder = patched(_result("SynthesizingAudioCompleted", audio_data=b"x"))

    await tts.synthesize(text)

    root = _ssml_of(recorder)          # parses at all == escaping worked
    assert "".join(root.itertext()).strip() == text


async def test_mp3_output_format_is_requested(patched):
    """Raw PCM is ~8x the bytes for the same reply; this is a phone."""
    recorder = patched(_result("SynthesizingAudioCompleted", audio_data=b"x"))

    await tts.synthesize("你好")

    assert recorder["speech_config"].output_format == "Audio24Khz48KBitRateMonoMp3"
    # Synthesis goes to memory, not to the machine's speakers — on a server there
    # are none, and the bytes are what we owe the client.
    assert recorder["audio_config"] is None


# --- what we do when it fails -----------------------------------------------

async def test_canceled_raises_tts_error(patched):
    patched(_result("Canceled", error_details="bad key"))

    with pytest.raises(tts.TtsError, match="bad key"):
        await tts.synthesize("你好")


async def test_completed_but_empty_audio_raises(patched):
    """Silence is the one failure the learner cannot tell from a broken speaker.

    Returning 0 bytes would autoplay nothing and reveal nothing; a raised error
    reaches the client, which falls back to showing the text.
    """
    patched(_result("SynthesizingAudioCompleted", audio_data=b""))

    with pytest.raises(tts.TtsError, match="no audio"):
        await tts.synthesize("你好")


async def test_missing_credentials_raise_speech_config_error(patched, monkeypatch):
    patched(_result("SynthesizingAudioCompleted", audio_data=b"x"))
    monkeypatch.setattr(_azure.config, "AZURE_SPEECH_KEY", "")

    with pytest.raises(_azure.SpeechConfigError):
        await tts.synthesize("你好")


async def test_a_stalled_synthesizer_times_out(monkeypatch):
    """TTS is off the turn's critical path, but it still holds a request open.

    Unbounded, a wedged synthesis parks a connection and leaves the bubble with
    neither audio nor text. The deadline turns it into the 502 the route maps to
    a text reveal.
    """
    import time

    def never_returns(*args, **kwargs):
        time.sleep(30)
        raise AssertionError("the timeout should have fired long before this")

    tts.clear_cache()
    monkeypatch.setattr(tts, "_synthesize_sync", never_returns)
    monkeypatch.setattr(tts.config, "TTS_TIMEOUT_S", 0.05)

    with pytest.raises(tts.TtsError, match="timed out"):
        await tts.synthesize("你好")


async def test_a_failed_synthesis_is_not_cached(patched):
    """Otherwise one transient Azure error makes a line permanently silent."""
    recorder = patched(_result("Canceled", error_details="transient"))

    with pytest.raises(tts.TtsError):
        await tts.synthesize("你好")
    with pytest.raises(tts.TtsError):
        await tts.synthesize("你好")

    assert recorder["calls"] == 2


# --- the cache --------------------------------------------------------------

async def test_the_same_line_is_synthesized_once(patched):
    """The acceptance criterion, server side: replay must not re-spend Azure."""
    recorder = patched(_result("SynthesizingAudioCompleted", audio_data=b"mp3"))

    first = await tts.synthesize("你好")
    second = await tts.synthesize("你好")

    assert first == second == b"mp3"
    assert recorder["calls"] == 1


async def test_a_different_line_is_a_miss(patched):
    recorder = patched(_result("SynthesizingAudioCompleted", audio_data=b"mp3"))

    await tts.synthesize("你好")
    await tts.synthesize("再见")

    assert recorder["calls"] == 2


@pytest.mark.parametrize("setting,value", [("TTS_VOICE", "zh-CN-YunxiNeural"),
                                           ("TTS_RATE_PCT", -25)])
async def test_changing_a_voice_setting_is_a_miss(patched, monkeypatch, setting, value):
    """The key covers everything that shapes the audio, not just the text.

    Keyed on text alone, a rate change would keep serving the old pace forever —
    a tuning knob that silently does nothing is worse than no knob.
    """
    recorder = patched(_result("SynthesizingAudioCompleted", audio_data=b"mp3"))

    await tts.synthesize("你好")
    monkeypatch.setattr(tts.config, setting, value)
    await tts.synthesize("你好")

    assert recorder["calls"] == 2


async def test_the_cache_is_bounded(patched, monkeypatch):
    """A per-line cache with no ceiling is a slow leak on a long-lived process."""
    recorder = patched(_result("SynthesizingAudioCompleted", audio_data=b"mp3"))
    monkeypatch.setattr(tts.config, "TTS_CACHE_MAX_ENTRIES", 2)

    await tts.synthesize("一")
    await tts.synthesize("二")
    await tts.synthesize("三")      # evicts 一
    await tts.synthesize("一")      # ...so this is a miss

    assert recorder["calls"] == 4


async def test_a_hit_keeps_the_entry_alive(patched, monkeypatch):
    """LRU, not FIFO: the line being replayed is the one worth keeping."""
    recorder = patched(_result("SynthesizingAudioCompleted", audio_data=b"mp3"))
    monkeypatch.setattr(tts.config, "TTS_CACHE_MAX_ENTRIES", 2)

    await tts.synthesize("一")
    await tts.synthesize("二")
    await tts.synthesize("一")      # hit — 二 is now the least recent
    await tts.synthesize("三")      # evicts 二, not 一
    await tts.synthesize("一")

    assert recorder["calls"] == 3
