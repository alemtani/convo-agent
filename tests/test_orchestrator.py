"""Phase 3a orchestrator tests — the text turn, worker mocked.

The orchestrator is plain Python (no LLM): it loads the topic KB, hands the
worker the session sketch + forgiveness default, and wraps the result. Tests stub
the worker so no tokens are spent and assert the wiring (KB loaded,
sketch/forgiveness passed, response shaped).

The *spoken* turn lives in `tests/test_turn_stream.py`, not here. It has no
collected form to assert against any more — `POST /api/turn` streams, so the
staged events are the contract and there is no second code path that could
disagree with them.
"""
import pytest

from backend import config, kb, orchestrator
from backend.models import (
    ConversationTurnResponse,
    TextTurnRequest,
    TurnAnnotation,
    Utterance,
)
from backend.prompts import SKETCH_STUB
from backend.workers import conversation


async def test_run_text_turn_loads_kb_and_calls_worker(monkeypatch):
    captured = {}

    async def fake_respond(*, kb_block, sketch, dialogue, user_text, forgiveness_level,
                           want_reading=True, client=None):
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


# --- WS1 Stage 0: per-stage timings + Anthropic usage ----------------------
#
# Text mode has only one timed stage, so what these pin is the relationship
# between `claude_ms` and `total_ms` — the gap is the server's own overhead, and
# it's the comparison that says whether a slow spoken turn is Claude or the
# speech stages. Timings are wall-clock, so the assertions are about *which*
# stages are reported and how they relate, never about a millisecond count.
# The concurrent PA-vs-Claude timings are asserted on the stream.


class _FakeUsage:
    input_tokens = 120
    output_tokens = 90
    cache_read_input_tokens = 4200
    cache_creation_input_tokens = 0


def _worker_reply(annotation=None, reading=None):
    async def fake_respond(*, kb_block, sketch, dialogue, user_text, forgiveness_level,
                           want_reading=True, client=None):
        return (
            Utterance(zh="你好！你叫什么名字？", pinyin="nǐ hǎo! nǐ jiào shénme míngzi?"),
            annotation or TurnAnnotation(coherence="on_track", topic_tags=["greetings"]),
            reading or Utterance(zh="你好", pinyin="nǐ hǎo"),
            object(),
        )
    return fake_respond


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
