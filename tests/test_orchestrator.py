"""Phase 3a orchestrator tests — turn coordination, worker mocked.

The orchestrator is plain Python (no LLM): it loads the topic KB, hands the
worker the session sketch + forgiveness default, and wraps the result. It is the
seam Phase 3b reuses with `text` sourced from STT. Tests stub the worker so no
tokens are spent and assert the wiring (KB loaded, sketch/forgiveness passed,
response shaped).
"""
import pytest

from backend import config, kb, orchestrator
from backend.models import (
    ConversationTurnResponse,
    PronunciationScore,
    SyllableScore,
    TextTurnRequest,
    TurnAnnotation,
    TurnResponse,
    Utterance,
)
from backend.prompts import SKETCH_STUB
from backend.speech import pronunciation, stt
from backend.workers import conversation


async def test_run_text_turn_loads_kb_and_calls_worker(monkeypatch):
    captured = {}

    async def fake_respond(*, kb_block, sketch, dialogue, user_text, forgiveness_level, client=None):
        captured.update(
            kb_block=kb_block, sketch=sketch, dialogue=dialogue,
            user_text=user_text, forgiveness_level=forgiveness_level,
        )
        return (
            Utterance(zh="你好！你叫什么名字？", pinyin="nǐ hǎo! nǐ jiào shénme míngzi?"),
            TurnAnnotation(coherence="on_track", topic_tags=["greetings"]),
            Utterance(zh="我叫小明", pinyin="wǒ jiào xiǎo míng"),
            object(),
        )

    monkeypatch.setattr(conversation, "respond", fake_respond)

    req = TextTurnRequest(
        topic_id="greetings",
        text="我叫小明",
        dialogue=[{"role": "user", "zh": "你好"}],
    )
    resp = await orchestrator.run_text_turn(req)

    assert isinstance(resp, ConversationTurnResponse)
    assert resp.reply.zh == "你好！你叫什么名字？"
    assert resp.annotation.coherence == "on_track"

    # The orchestrator owns the sketch + forgiveness default and loads the real KB.
    assert captured["sketch"] == SKETCH_STUB
    assert captured["forgiveness_level"] == config.FORGIVENESS_LEVEL_DEFAULT
    assert captured["user_text"] == "我叫小明"
    assert captured["kb_block"] == kb.load_kb_block("greetings")


async def test_run_text_turn_transcript_is_the_workers_reading(monkeypatch):
    """The learner types pinyin; the bubble shows the 汉字 the worker read.

    This is the whole point of text mode for a beginner — local romanization can't
    do it (`to_pinyin` only goes hanzi→pinyin), and only the worker has the context
    to resolve `ta` into 他 vs 她.
    """
    monkeypatch.setattr(
        conversation,
        "respond",
        _worker_reply(reading=Utterance(zh="我叫小明", pinyin="wǒ jiào xiǎo míng")),
    )

    resp = await orchestrator.run_text_turn(
        TextTurnRequest(topic_id="greetings", text="wo jiao xiao ming")
    )

    assert resp.transcript == Utterance(zh="我叫小明", pinyin="wǒ jiào xiǎo míng")


async def test_run_text_turn_passes_the_stripped_text_to_the_worker(monkeypatch):
    captured = {}

    async def fake_respond(*, user_text, **kwargs):
        captured["user_text"] = user_text
        return (
            Utterance(zh="你好", pinyin="nǐ hǎo"),
            TurnAnnotation(coherence="on_track"),
            Utterance(zh="你好", pinyin="nǐ hǎo"),
            object(),
        )

    monkeypatch.setattr(conversation, "respond", fake_respond)

    await orchestrator.run_text_turn(
        TextTurnRequest(topic_id="greetings", text="  ni3hao3  ")
    )

    assert captured["user_text"] == "ni3hao3"


async def test_run_text_turn_derives_tone_errors_from_typed_digits(monkeypatch):
    """Text mode's payoff: `said` is the tone the learner actually believed.

    The PA path can only ship `tones.SAID_UNKNOWN` (Azure reports accuracy, not a
    produced tone). Typing states the belief outright, so the misconception is
    nameable — 你 is tone 3 and they wrote tone 2.
    """
    monkeypatch.setattr(
        conversation,
        "respond",
        _worker_reply(reading=Utterance(zh="你好", pinyin="nǐ hǎo")),
    )

    resp = await orchestrator.run_text_turn(
        TextTurnRequest(topic_id="greetings", text="ni2hao3")
    )

    assert [e.model_dump() for e in resp.annotation.tone_errors] == [
        {"syllable": "你", "expected": 3, "said": 2, "index": 0}
    ]


async def test_run_text_turn_has_no_tone_errors_without_tone_digits(monkeypatch):
    # Tone digits are optional; typing toneless pinyin is a normal turn, not an
    # error-laden one. The orchestrator must not invent tone errors from nothing.
    monkeypatch.setattr(
        conversation,
        "respond",
        _worker_reply(reading=Utterance(zh="你好", pinyin="nǐ hǎo")),
    )

    resp = await orchestrator.run_text_turn(
        TextTurnRequest(topic_id="greetings", text="nihao")
    )

    assert resp.annotation.tone_errors == []


async def test_run_text_turn_propagates_unknown_topic(monkeypatch):
    async def fake_respond(**kwargs):
        raise AssertionError("worker should not be called for an unknown topic")

    monkeypatch.setattr(conversation, "respond", fake_respond)

    with pytest.raises(kb.KbError):
        await orchestrator.run_text_turn(TextTurnRequest(topic_id="nope", text="你好"))


# --- Phase 3b: the audio turn (STT -> PA || worker -> merged tone errors) ----


def _worker_reply(annotation=None, reading=None):
    async def fake_respond(*, kb_block, sketch, dialogue, user_text, forgiveness_level, client=None):
        return (
            Utterance(zh="你好！你叫什么名字？", pinyin="nǐ hǎo! nǐ jiào shénme míngzi?"),
            annotation or TurnAnnotation(coherence="on_track", topic_tags=["greetings"]),
            reading or Utterance(zh="你好", pinyin="nǐ hǎo"),
            object(),
        )
    return fake_respond


def _pa_score(*syllables):
    return PronunciationScore(overall=70.0, syllables=list(syllables))


async def test_run_audio_turn_two_pass_and_merges_tone_errors(monkeypatch):
    captured = {}

    async def fake_transcribe(audio_wav, language="zh-CN"):
        captured["stt_audio"] = audio_wav
        return "你好"

    async def fake_assess(audio_wav, reference_text, language="zh-CN"):
        captured["pa_audio"] = audio_wav
        captured["pa_reference"] = reference_text
        return _pa_score(
            SyllableScore(hanzi="你", pinyin="nǐ", accuracy=40.0),  # below threshold
            SyllableScore(hanzi="好", pinyin="hǎo", accuracy=95.0),
        )

    async def fake_respond(*, user_text, **kwargs):
        captured["worker_user_text"] = user_text
        return (
            Utterance(zh="你好！你叫什么名字？", pinyin="nǐ hǎo! nǐ jiào shénme míngzi?"),
            TurnAnnotation(coherence="on_track", topic_tags=["greetings"]),
            Utterance(zh="你好", pinyin="nǐ hǎo"),
            object(),
        )

    monkeypatch.setattr(stt, "transcribe", fake_transcribe)
    monkeypatch.setattr(pronunciation, "assess", fake_assess)
    monkeypatch.setattr(conversation, "respond", fake_respond)

    resp = await orchestrator.run_audio_turn(b"FAKEWAV", topic_id="greetings")

    assert isinstance(resp, TurnResponse)
    # Two-pass: PA is assessed against the STT transcript, on the same audio.
    assert captured["pa_reference"] == "你好"
    assert captured["pa_audio"] == b"FAKEWAV"
    # The worker is driven by the STT transcript.
    assert captured["worker_user_text"] == "你好"
    # Transcript echoes STT + derived pinyin; reply is the worker's.
    assert resp.transcript == Utterance(zh="你好", pinyin="nǐ hǎo")
    assert resp.reply.zh == "你好！你叫什么名字？"
    assert resp.pronunciation.overall == 70.0
    # tone_errors are merged in deterministically from PA (only 你 was below 60).
    assert [e.model_dump() for e in resp.annotation.tone_errors] == [
        {"syllable": "你", "expected": 3, "said": 0, "index": None}
    ]


async def test_run_audio_turn_degrades_when_pa_fails(monkeypatch):
    async def fake_transcribe(audio_wav, language="zh-CN"):
        return "你好"

    async def fake_assess(audio_wav, reference_text, language="zh-CN"):
        raise pronunciation.PaError("boom")

    monkeypatch.setattr(stt, "transcribe", fake_transcribe)
    monkeypatch.setattr(pronunciation, "assess", fake_assess)
    monkeypatch.setattr(conversation, "respond", _worker_reply())

    resp = await orchestrator.run_audio_turn(b"FAKEWAV", topic_id="greetings")

    # PA failure degrades to scores-off; the reply still lands and no tone errors.
    assert resp.pronunciation is None
    assert resp.reply.zh == "你好！你叫什么名字？"
    assert resp.annotation.tone_errors == []


async def test_run_audio_turn_short_circuits_on_empty_recognition(monkeypatch):
    async def fake_transcribe(audio_wav, language="zh-CN"):
        return ""

    async def fake_assess(*args, **kwargs):
        raise AssertionError("PA should not run when nothing was recognized")

    async def fake_respond(**kwargs):
        raise AssertionError("worker should not run when nothing was recognized")

    monkeypatch.setattr(stt, "transcribe", fake_transcribe)
    monkeypatch.setattr(pronunciation, "assess", fake_assess)
    monkeypatch.setattr(conversation, "respond", fake_respond)

    resp = await orchestrator.run_audio_turn(b"FAKEWAV", topic_id="greetings")

    assert resp.transcript.zh == ""
    assert resp.pronunciation is None
    assert resp.annotation is None
    assert resp.reply.zh  # a gentle re-prompt, not empty


async def test_run_audio_turn_propagates_unknown_topic(monkeypatch):
    async def fake_transcribe(audio_wav, language="zh-CN"):
        return "你好"

    monkeypatch.setattr(stt, "transcribe", fake_transcribe)

    with pytest.raises(kb.KbError):
        await orchestrator.run_audio_turn(b"FAKEWAV", topic_id="nope")


# --- WS1 Stage 0: per-stage timings + Anthropic usage ----------------------
#
# The critical path is `STT + max(PA, Claude)`, so every stage is timed
# separately — including the two that overlap — and the token usage the worker
# already returned is surfaced instead of discarded. Timings are wall-clock, so
# the assertions are about *which* stages are reported and how they relate, never
# about a specific millisecond count.


class _FakeUsage:
    input_tokens = 120
    output_tokens = 90
    cache_read_input_tokens = 4200
    cache_creation_input_tokens = 0


async def test_run_audio_turn_reports_a_timing_for_every_stage(monkeypatch):
    import asyncio

    async def fake_transcribe(audio_wav, language="zh-CN"):
        await asyncio.sleep(0.02)
        return "你好"

    async def fake_assess(audio_wav, reference_text, language="zh-CN"):
        await asyncio.sleep(0.01)
        return _pa_score(SyllableScore(hanzi="你", pinyin="nǐ", accuracy=95.0))

    async def fake_respond(**kwargs):
        await asyncio.sleep(0.05)
        return (
            Utterance(zh="你好", pinyin="nǐ hǎo"),
            TurnAnnotation(coherence="on_track"),
            Utterance(zh="你好", pinyin="nǐ hǎo"),
            _FakeUsage(),
        )

    monkeypatch.setattr(stt, "transcribe", fake_transcribe)
    monkeypatch.setattr(pronunciation, "assess", fake_assess)
    monkeypatch.setattr(conversation, "respond", fake_respond)

    resp = await orchestrator.run_audio_turn(b"FAKEWAV", topic_id="greetings")

    t = resp.timings
    assert t is not None
    assert t.stt_ms > 0 and t.pa_ms > 0 and t.claude_ms > 0
    # PA and Claude are timed as separate branches of the same gather — the
    # number Stage 2 turns on. The slow branch must not be charged to the fast one.
    assert t.pa_ms < t.claude_ms
    # Total spans the whole turn, so it is at least the serial critical path
    # (STT then the slower of the two concurrent branches).
    assert t.total_ms >= t.stt_ms + t.claude_ms
    # ...but the concurrent branches are not summed into it.
    assert t.total_ms < t.stt_ms + t.claude_ms + t.pa_ms + 1000


async def test_run_audio_turn_surfaces_the_anthropic_usage_block(monkeypatch):
    """`cache_read_input_tokens > 0` is the whole prompt-caching claim; before
    this the orchestrator dropped it on the floor."""

    async def fake_transcribe(audio_wav, language="zh-CN"):
        return "你好"

    async def fake_assess(audio_wav, reference_text, language="zh-CN"):
        return None

    async def fake_respond(**kwargs):
        return (
            Utterance(zh="你好", pinyin="nǐ hǎo"),
            TurnAnnotation(coherence="on_track"),
            Utterance(zh="你好", pinyin="nǐ hǎo"),
            _FakeUsage(),
        )

    monkeypatch.setattr(stt, "transcribe", fake_transcribe)
    monkeypatch.setattr(pronunciation, "assess", fake_assess)
    monkeypatch.setattr(conversation, "respond", fake_respond)

    resp = await orchestrator.run_audio_turn(b"FAKEWAV", topic_id="greetings")

    assert resp.usage.cache_read_input_tokens == 4200
    assert resp.usage.input_tokens == 120
    assert resp.usage.output_tokens == 90


async def test_run_audio_turn_times_pa_even_when_it_fails(monkeypatch):
    """A PA call that burned two seconds and then failed is the most interesting
    timing there is; degrading to scores-off must not also lose the number."""
    import asyncio

    async def fake_transcribe(audio_wav, language="zh-CN"):
        return "你好"

    async def fake_assess(audio_wav, reference_text, language="zh-CN"):
        await asyncio.sleep(0.01)
        raise pronunciation.PaError("boom")

    monkeypatch.setattr(stt, "transcribe", fake_transcribe)
    monkeypatch.setattr(pronunciation, "assess", fake_assess)
    monkeypatch.setattr(conversation, "respond", _worker_reply())

    resp = await orchestrator.run_audio_turn(b"FAKEWAV", topic_id="greetings")

    assert resp.pronunciation is None
    assert resp.timings.pa_ms > 0


async def test_run_audio_turn_times_the_short_circuited_turn(monkeypatch):
    """Nothing recognized still cost an STT round trip — and a turn that returns
    early must not silently drop out of the measurements."""

    async def fake_transcribe(audio_wav, language="zh-CN"):
        return ""

    monkeypatch.setattr(stt, "transcribe", fake_transcribe)

    resp = await orchestrator.run_audio_turn(b"FAKEWAV", topic_id="greetings")

    assert resp.timings.stt_ms is not None
    assert resp.timings.total_ms is not None
    assert resp.timings.claude_ms is None   # the worker never ran
    assert resp.timings.pa_ms is None


async def test_run_text_turn_reports_claude_and_total_only(monkeypatch):
    """Text mode has no STT and no PA, so the difference between `claude_ms` and
    `total_ms` is the server's own overhead — the comparison that says whether a
    slow spoken turn is Claude or the speech stages."""

    async def fake_respond(**kwargs):
        return (
            Utterance(zh="你好", pinyin="nǐ hǎo"),
            TurnAnnotation(coherence="on_track"),
            Utterance(zh="你好", pinyin="nǐ hǎo"),
            _FakeUsage(),
        )

    monkeypatch.setattr(conversation, "respond", fake_respond)

    resp = await orchestrator.run_text_turn(
        TextTurnRequest(topic_id="greetings", text="你好")
    )

    assert resp.timings.claude_ms is not None
    assert resp.timings.total_ms >= resp.timings.claude_ms
    assert resp.timings.stt_ms is None and resp.timings.pa_ms is None
    assert resp.usage.cache_read_input_tokens == 4200


async def test_turn_usage_is_none_when_the_worker_returns_no_usage(monkeypatch):
    # The stub workers elsewhere in this suite hand back a bare `object()`; a
    # response with no readable usage must still be a valid turn.
    monkeypatch.setattr(conversation, "respond", _worker_reply())

    resp = await orchestrator.run_text_turn(
        TextTurnRequest(topic_id="greetings", text="你好")
    )

    assert resp.usage.input_tokens is None
