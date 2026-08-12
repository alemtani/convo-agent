"""Azure Text-to-Speech boundary (M4).

The third module that imports the Azure Speech SDK, and the only one that
*produces* audio rather than consuming it. It takes a partner reply and returns
MP3 bytes, slowed for a band-1–2 learner.

Two decisions shape it.

**SSML, not `speak_text`.** The default neural pace is native-speed, which is
useless to a beginner: they hear a wave, not words. A `<prosody rate>` of -10%
is the whole reason a request is assembled by hand, and it means the reply text
is now *markup*, so it has to be escaped — the partner writes it, and one `&` in
an unescaped body fails the request mid-session.

**A cache in front of it.** Synthesis is billed per character and the same line
is spoken more than once — replayed by the learner, re-fetched after a reload,
or simply repeated by the partner (你好 opens most sessions). The cache keys on
everything that shapes the audio, so a voice or rate change is a miss rather
than a stale hit. Client-side replay never reaches this module at all; this
layer catches the requests that survive it.
"""
import asyncio
import hashlib
from collections import OrderedDict
from xml.sax.saxutils import escape

import azure.cognitiveservices.speech as speechsdk

from backend import config
from backend.speech._azure import cancellation_message, speech_config


class TtsError(RuntimeError):
    """Azure synthesis failed (canceled, timed out, or returned no audio)."""


# Text -> audio bytes, most-recently-used last. Bounded: a per-line cache with
# no ceiling is a slow leak on a process that stays up for weeks.
_CACHE: "OrderedDict[str, bytes]" = OrderedDict()


def clear_cache() -> None:
    """Drop every cached line. For tests, and for nothing else in the app."""
    _CACHE.clear()


def _cache_key(text: str, voice: str, rate_pct: int) -> str:
    """Key on everything that changes the audio, not just the text.

    Keyed on text alone, a rate change would keep serving the old pace forever —
    a tuning knob that silently does nothing. Hashed because the text is
    arbitrary learner-visible content and this key ends up in memory alongside
    every other line of the session.
    """
    return hashlib.sha256(
        f"{voice}|{rate_pct}|{text}".encode("utf-8")
    ).hexdigest()


def build_ssml(text: str, voice: str, rate_pct: int, language: str = "zh-CN") -> str:
    """Assemble the synthesis request for one line.

    `escape` is load-bearing, not defensive: the partner composes this text, so
    an `&` or a `<` is an ordinary thing for it to emit and an unescaped one
    makes Azure reject the entire request.

    The rate keeps its sign (`-10%`, `+10%`) because Azure reads it as a signed
    delta — a bare `10%` means *faster*, the opposite of the intent here.
    """
    return (
        '<speak version="1.0" '
        'xmlns="http://www.w3.org/2001/10/synthesis" '
        f'xml:lang="{language}">'
        f'<voice name="{voice}">'
        f'<prosody rate="{rate_pct:+d}%">{escape(text)}</prosody>'
        "</voice></speak>"
    )


def _synthesize_sync(text: str, voice: str, rate_pct: int, language: str) -> bytes:
    """Blocking synthesis: one pass, returning the encoded audio.

    `audio_config=None` sends the result to memory rather than to a speaker —
    a server has none, and the bytes are what we owe the client.
    """
    cfg = speech_config()
    cfg.set_speech_synthesis_output_format(
        # Constant-bitrate MP3 at the voice's native 24 kHz. About an eighth the
        # bytes of raw PCM for the same line, which is the difference that
        # matters on a phone; `decodeAudioData` handles the rest client-side.
        speechsdk.SpeechSynthesisOutputFormat.Audio24Khz48KBitRateMonoMp3
    )
    synthesizer = speechsdk.SpeechSynthesizer(speech_config=cfg, audio_config=None)
    result = synthesizer.speak_ssml_async(
        build_ssml(text, voice, rate_pct, language)
    ).get()

    if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
        audio = bytes(result.audio_data)
        if not audio:
            # Silence is the one failure a learner cannot tell from a broken
            # speaker. Raising means the client reveals the text instead.
            raise TtsError("Azure TTS returned no audio for a completed synthesis")
        return audio

    raise TtsError(f"Azure TTS canceled {cancellation_message(result)}")


async def synthesize(text: str, language: str = "zh-CN") -> bytes:
    """Speak ``text`` as slowed Mandarin and return MP3 bytes.

    Settings are read at call time, not at import, so `TTS_VOICE` and
    `TTS_RATE_PCT` behave like the other dials in `config` — and so the cache key
    reflects the values actually used for this synthesis.

    Bounded by `TTS_TIMEOUT_S`. TTS is off the turn's critical path, but it still
    holds a request open: unbounded, a wedged synthesis parks a connection and
    leaves a bubble with neither audio nor text.

    Same caveat as `stt.transcribe`: `wait_for` cancels the await, not the
    thread. A wedged SDK call keeps its thread-pool slot until Azure returns.

    Only successes are cached. Caching a failure would make one transient Azure
    error a permanent silence for that line.
    """
    voice, rate_pct = config.TTS_VOICE, config.TTS_RATE_PCT
    key = _cache_key(text, voice, rate_pct)
    if key in _CACHE:
        _CACHE.move_to_end(key)     # LRU: a replayed line is one worth keeping
        return _CACHE[key]

    try:
        audio = await asyncio.wait_for(
            asyncio.to_thread(_synthesize_sync, text, voice, rate_pct, language),
            timeout=config.TTS_TIMEOUT_S,
        )
    except asyncio.TimeoutError as exc:
        raise TtsError(
            f"Azure TTS timed out after {config.TTS_TIMEOUT_S:g}s"
        ) from exc

    _CACHE[key] = audio
    while len(_CACHE) > config.TTS_CACHE_MAX_ENTRIES:
        _CACHE.popitem(last=False)
    return audio
