"""`POST /api/turn` streams the transcript before the reply.

The point of the stream is ordering, not throughput: STT resolves long before
the conversation worker, so the learner's own words should reach the thread
while the reply is still being written. These tests pin the contract that makes
that possible — event order, what is still an HTTP status, and what can only be
reported in-band once the response has committed to 200.

Azure and the worker are mocked throughout; no tokens spent.
"""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from backend import orchestrator
from backend.main import app
from backend.models import PronunciationScore, SyllableScore, TurnAnnotation, Utterance
from backend.speech import pronunciation, stt
from backend.workers import conversation

client = TestClient(app)


def _upload(data=b"FAKEWAV"):
    return {"audio": ("turn.wav", data, "audio/wav")}


def events(resp):
    """Parse an NDJSON body into a list of event dicts."""
    return [json.loads(line) for line in resp.text.splitlines() if line.strip()]


@pytest.fixture(autouse=True)
def stub_worker_and_pa(monkeypatch):
    async def fake_assess(audio_wav, reference_text, language="zh-CN"):
        return PronunciationScore(
            overall=80.0,
            syllables=[SyllableScore(hanzi="你", pinyin="nǐ", accuracy=40.0)],
        )

    async def fake_respond(*, kb_block, sketch, dialogue, user_text, forgiveness_level, client=None):
        return (
            Utterance(zh="你好！你叫什么名字？", pinyin="nǐ hǎo! nǐ jiào shénme míngzi?"),
            TurnAnnotation(coherence="on_track", topic_tags=["greetings"]),
            Utterance(zh="你好", pinyin="nǐ hǎo"),
            object(),
        )

    async def fake_transcribe(audio_wav, language="zh-CN"):
        return "你好"

    monkeypatch.setattr(pronunciation, "assess", fake_assess)
    monkeypatch.setattr(conversation, "respond", fake_respond)
    monkeypatch.setattr(stt, "transcribe", fake_transcribe)


def test_stream_emits_transcript_then_final():
    resp = client.post("/api/turn", files=_upload())

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/x-ndjson")
    first, last = events(resp)
    # `timings` rides every event (see the WS1 Stage 0 section below); the
    # identity that matters here is the stage and its payload.
    assert first["stage"] == "transcript"
    assert first["transcript"] == {"zh": "你好", "pinyin": "nǐ hǎo"}
    assert last["stage"] == "final"
    assert last["reply"]["zh"] == "你好！你叫什么名字？"
    assert last["pronunciation"]["overall"] == 80.0
    assert last["annotation"]["tone_errors"] == [
        {"syllable": "你", "expected": 3, "said": 0, "index": None}
    ]


async def test_transcript_is_yielded_before_the_worker_finishes(monkeypatch):
    """The whole point: the transcript must not wait on the reply.

    Asserting the order of a finished body proves nothing — it would pass just
    as well if both events were buffered until the worker returned. So this
    drives the generator directly and pulls the first event while the worker is
    still blocked. That's the guarantee; `StreamingResponse` and uvicorn carry
    it to the wire without buffering, and `TestClient` can't be used to check
    that because it collects the whole body before returning.
    """
    worker_may_finish = asyncio.Event()

    async def slow_respond(**kwargs):
        await worker_may_finish.wait()
        return (
            Utterance(zh="你好！", pinyin="nǐ hǎo!"),
            TurnAnnotation(coherence="on_track"),
            Utterance(zh="你好", pinyin="nǐ hǎo"),
            object(),
        )

    monkeypatch.setattr(conversation, "respond", slow_respond)

    transcript, kb_block, timer = await orchestrator.prepare_audio_turn(b"FAKEWAV")
    stream = orchestrator.stream_audio_turn(
        b"FAKEWAV", transcript=transcript, kb_block=kb_block, timer=timer
    )

    first = await stream.__anext__()
    assert first.stage == "transcript"
    assert first.transcript.zh == "你好"
    assert not worker_may_finish.is_set(), "the worker was awaited before yielding"

    worker_may_finish.set()
    rest = [event async for event in stream]
    assert [e.stage for e in rest] == ["final"]
    assert rest[0].reply.zh == "你好！"


def test_empty_recognition_streams_transcript_and_a_reprompt(monkeypatch):
    async def fake_transcribe(audio_wav, language="zh-CN"):
        return ""

    async def never(**kwargs):
        raise AssertionError("worker should not run on empty recognition")

    monkeypatch.setattr(stt, "transcribe", fake_transcribe)
    monkeypatch.setattr(conversation, "respond", never)

    resp = client.post("/api/turn", files=_upload())
    assert resp.status_code == 200
    first, last = events(resp)
    assert first["transcript"] == {"zh": "", "pinyin": ""}
    assert last["stage"] == "final"
    assert last["reply"]["zh"]          # a gentle re-prompt
    assert last["pronunciation"] is None
    assert last["annotation"] is None


def test_pa_failure_still_streams_transcript_and_reply(monkeypatch):
    async def boom(audio_wav, reference_text, language="zh-CN"):
        raise pronunciation.PaError("azure PA canceled: timeout")

    monkeypatch.setattr(pronunciation, "assess", boom)

    resp = client.post("/api/turn", files=_upload())
    first, last = events(resp)
    assert first["transcript"]["zh"] == "你好"
    assert last["reply"]["zh"] == "你好！你叫什么名字？"
    assert last["pronunciation"] is None
    assert last["annotation"]["tone_errors"] == []


def test_worker_failure_is_an_error_event_not_a_502(monkeypatch):
    """Once the transcript has been sent the status line is spent.

    A 502 would also throw away the transcript the learner can already see, so
    the failure rides in-band and the client keeps their bubble.
    """

    async def boom(**kwargs):
        raise conversation.ConversationError("worker refused the turn")

    monkeypatch.setattr(conversation, "respond", boom)

    resp = client.post("/api/turn", files=_upload())
    assert resp.status_code == 200
    first, last = events(resp)
    assert first["stage"] == "transcript"
    assert last["stage"] == "error"
    assert "refused" in last["detail"]


# --- Settled before the first byte ----------------------------------------


def test_stt_failure_is_still_a_502(monkeypatch):
    async def boom(audio_wav, language="zh-CN"):
        raise stt.SttError("azure canceled: bad key")

    monkeypatch.setattr(stt, "transcribe", boom)

    resp = client.post("/api/turn", files=_upload())
    assert resp.status_code == 502
    assert "bad key" in resp.json()["detail"]


def test_unknown_topic_is_still_a_404():
    resp = client.post("/api/turn", files=_upload(), data={"topic_id": "nope"})
    assert resp.status_code == 404


def test_unknown_topic_fails_before_spending_stt(monkeypatch):
    """KB load moved ahead of STT: a bad topic shouldn't cost an Azure call."""
    called = {"stt": False}

    async def fake_transcribe(audio_wav, language="zh-CN"):
        called["stt"] = True
        return "你好"

    monkeypatch.setattr(stt, "transcribe", fake_transcribe)

    resp = client.post("/api/turn", files=_upload(), data={"topic_id": "nope"})
    assert resp.status_code == 404
    assert called["stt"] is False


def test_malformed_dialogue_is_still_a_422():
    resp = client.post("/api/turn", files=_upload(), data={"dialogue": "not json"})
    assert resp.status_code == 422
    assert "invalid dialogue" in resp.json()["detail"]


def test_missing_audio_field_is_a_422():
    resp = client.post("/api/turn")
    assert resp.status_code == 422


def test_missing_azure_credentials_is_a_502(monkeypatch):
    # Server started before .env had the key: empty creds must fail clean (502),
    # not surface Azure's raw RuntimeError(5) as a 500 — and, now that the body
    # is streamed, not as a half-written 200 either.
    from backend.speech import _recognizer

    # Real STT, not the fixture's stub — the failure being tested comes from the
    # recognizer refusing to build. `monkeypatch` is one instance shared with the
    # autouse fixture, so undo() drops its stubs; the turn never gets past STT.
    monkeypatch.undo()
    monkeypatch.setattr(_recognizer.config, "AZURE_SPEECH_KEY", "")

    resp = client.post("/api/turn", files=_upload())
    assert resp.status_code == 502
    assert "not configured" in resp.json()["detail"]


def test_dialogue_defaults_to_empty(monkeypatch):
    captured = {}

    async def fake_respond(*, kb_block, sketch, dialogue, user_text, forgiveness_level, client=None):
        captured["dialogue"] = dialogue
        return (
            Utterance(zh="你好", pinyin="nǐ hǎo"),
            TurnAnnotation(coherence="on_track"),
            Utterance(zh="你好", pinyin="nǐ hǎo"),
            object(),
        )

    monkeypatch.setattr(conversation, "respond", fake_respond)

    resp = client.post("/api/turn", files=_upload())
    assert resp.status_code == 200
    assert captured["dialogue"] == []


def test_pa_is_assessed_against_the_stt_transcript(monkeypatch):
    """Two-pass speech (DESIGN.md Risk 1): PA scores against what STT heard."""
    captured = {}

    async def fake_assess(audio_wav, reference_text, language="zh-CN"):
        captured["reference_text"] = reference_text
        captured["audio"] = audio_wav
        return None

    async def fake_transcribe(audio_wav, language="zh-CN"):
        return "你好老师"

    monkeypatch.setattr(stt, "transcribe", fake_transcribe)
    monkeypatch.setattr(pronunciation, "assess", fake_assess)

    resp = client.post("/api/turn", files=_upload())
    assert resp.status_code == 200
    assert captured["reference_text"] == "你好老师"
    assert captured["audio"] == b"FAKEWAV"


def test_route_threads_dialogue_history_into_the_stream(monkeypatch):
    captured = {}

    async def fake_respond(*, kb_block, sketch, dialogue, user_text, forgiveness_level, client=None):
        captured["dialogue"] = dialogue
        return (
            Utterance(zh="认识你很高兴", pinyin="rènshi nǐ hěn gāoxìng"),
            TurnAnnotation(coherence="on_track"),
            Utterance(zh="你好", pinyin="nǐ hǎo"),
            object(),
        )

    monkeypatch.setattr(conversation, "respond", fake_respond)

    history = json.dumps([{"role": "user", "zh": "你好"}])
    resp = client.post("/api/turn", files=_upload(), data={"dialogue": history})

    assert resp.status_code == 200
    assert [d.model_dump() for d in captured["dialogue"]] == [
        {"role": "user", "zh": "你好"}
    ]


def test_collector_still_returns_one_response():
    """`run_audio_turn` collects the stream, so the two paths can't drift.

    The live smoke test and the orchestrator tests assert the merged result
    rather than the delivery, and shouldn't have to reimplement the merge.
    """
    result = asyncio.run(orchestrator.run_audio_turn(b"FAKEWAV"))
    assert result.transcript.zh == "你好"
    assert result.reply.zh == "你好！你叫什么名字？"
    assert result.pronunciation.overall == 80.0


# --- WS1 Stage 0's timings survive the split (#19) -------------------------
#
# Splitting the coordinator in two is exactly the kind of refactor that quietly
# drops instrumentation: `prepare_audio_turn` owns STT, `stream_audio_turn` owns
# the rest, and a `Timer` created in the wrong half would silently under-report
# the turn by the one stage that sits in front of everything.


def test_every_event_carries_timings_measured_at_emit():
    """Each line reports elapsed-so-far, so arrival is measurable per stage.

    Staged delivery makes *when a line flushed* the number that matters — the
    replay harness reads these to report per-event arrival, not just how long
    each stage ran.
    """
    staged = events(client.post("/api/turn", files=_upload()))

    transcript, final = staged[0], staged[-1]
    assert transcript["timings"]["stt_ms"] is not None
    # The transcript flushes before PA and the worker have run at all.
    assert transcript["timings"]["pa_ms"] is None
    assert transcript["timings"]["claude_ms"] is None

    for stage in ("stt_ms", "pa_ms", "claude_ms", "total_ms"):
        assert final["timings"][stage] is not None, stage
    # `usage` is present in the contract even when the stub worker has none.
    assert "usage" in final


def test_total_includes_stt_despite_the_split():
    """`total_ms` covers the whole turn, not just the post-STT half.

    The regression this guards: a `Timer` constructed inside `stream_audio_turn`
    would start *after* STT, making every staged turn look ~1.3s faster than it
    is — and the p50 target is stated against `total`.
    """
    final = events(client.post("/api/turn", files=_upload()))[-1]
    timings = final["timings"]
    assert timings["total_ms"] >= timings["stt_ms"]


def test_short_circuited_turn_is_still_measured(monkeypatch):
    """Empty recognition cost an STT round trip; dropping it would bias the p50."""

    async def silent(audio_wav, language="zh-CN"):
        return ""

    monkeypatch.setattr(stt, "transcribe", silent)
    final = events(client.post("/api/turn", files=_upload()))[-1]
    assert final["timings"]["stt_ms"] is not None
    assert final["timings"]["total_ms"] is not None


def test_collector_preserves_timings_and_usage():
    """`run_audio_turn` reports what the stream measured — the #19 contract."""
    result = asyncio.run(orchestrator.run_audio_turn(b"FAKEWAV"))
    assert result.timings is not None
    assert result.timings.stt_ms is not None
    assert result.timings.claude_ms is not None
    assert result.timings.total_ms is not None
