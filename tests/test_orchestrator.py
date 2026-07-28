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


async def test_run_text_turn_echoes_transcript_with_derived_pinyin(monkeypatch):
    # WS3: text mode renders through the same bubble path as the spoken loop, so
    # the response carries the learner's own turn with server-derived pinyin.
    monkeypatch.setattr(conversation, "respond", _worker_reply())

    resp = await orchestrator.run_text_turn(
        TextTurnRequest(topic_id="greetings", text="我叫小明")
    )

    assert resp.transcript == Utterance(zh="我叫小明", pinyin="wǒ jiào xiǎo míng")


async def test_run_text_turn_echoes_the_stripped_text(monkeypatch):
    captured = {}

    async def fake_respond(*, user_text, **kwargs):
        captured["user_text"] = user_text
        return (
            Utterance(zh="你好", pinyin="nǐ hǎo"),
            TurnAnnotation(coherence="on_track"),
            object(),
        )

    monkeypatch.setattr(conversation, "respond", fake_respond)

    resp = await orchestrator.run_text_turn(
        TextTurnRequest(topic_id="greetings", text="  我叫小明  ")
    )

    # One normalization, applied at the model boundary: the worker and the echo
    # both see the stripped text, so the bubble matches what the partner replied to.
    assert captured["user_text"] == "我叫小明"
    assert resp.transcript.zh == "我叫小明"


async def test_run_text_turn_carries_no_tone_errors(monkeypatch):
    # Inherent scope limit: no audio means no PA, so a text turn never reports
    # tone errors. Asserted so a future change has to be deliberate.
    monkeypatch.setattr(conversation, "respond", _worker_reply())

    resp = await orchestrator.run_text_turn(
        TextTurnRequest(topic_id="greetings", text="我叫小明")
    )

    assert resp.annotation.tone_errors == []


async def test_run_text_turn_propagates_unknown_topic(monkeypatch):
    async def fake_respond(**kwargs):
        raise AssertionError("worker should not be called for an unknown topic")

    monkeypatch.setattr(conversation, "respond", fake_respond)

    with pytest.raises(kb.KbError):
        await orchestrator.run_text_turn(TextTurnRequest(topic_id="nope", text="你好"))


# --- Phase 3b: the audio turn (STT -> PA || worker -> merged tone errors) ----


def _worker_reply(annotation=None):
    async def fake_respond(*, kb_block, sketch, dialogue, user_text, forgiveness_level, client=None):
        return (
            Utterance(zh="你好！你叫什么名字？", pinyin="nǐ hǎo! nǐ jiào shénme míngzi?"),
            annotation or TurnAnnotation(coherence="on_track", topic_tags=["greetings"]),
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
        {"syllable": "你", "expected": 3, "said": 0}
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
