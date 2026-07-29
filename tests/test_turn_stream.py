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
    staged = events(resp)
    # `timings` rides every event (see the WS1 Stage 0 section below); the
    # identity that matters here is the stage sequence and each payload.
    assert [e["stage"] for e in staged] == ["transcript", "score", "final"]
    first, last = staged[0], staged[-1]
    assert first["transcript"] == {"zh": "你好", "pinyin": "nǐ hǎo"}
    assert last["stage"] == "final"
    assert last["reply"]["zh"] == "你好！你叫什么名字？"
    # Scores ride the `score` event, not `final` — see the section on C below.
    assert staged[1]["pronunciation"]["overall"] == 80.0
    # Tone errors ride `score` too: both they and `pronunciation` come from the
    # same PA result, and gating them on the worker would defeat the split.
    assert staged[1]["tone_errors"] == [
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

    first = await _next(stream)
    assert first.stage == "transcript"
    assert first.transcript.zh == "你好"
    assert not worker_may_finish.is_set(), "the worker was awaited before yielding"

    worker_may_finish.set()
    rest = [event async for event in stream]
    assert [e.stage for e in rest] == ["score", "final"]
    assert rest[-1].reply.zh == "你好！"


def test_empty_recognition_streams_transcript_and_a_reprompt(monkeypatch):
    async def fake_transcribe(audio_wav, language="zh-CN"):
        return ""

    async def never(**kwargs):
        raise AssertionError("worker should not run on empty recognition")

    monkeypatch.setattr(stt, "transcribe", fake_transcribe)
    monkeypatch.setattr(conversation, "respond", never)

    resp = client.post("/api/turn", files=_upload())
    assert resp.status_code == 200
    staged = events(resp)
    first, last = staged[0], staged[-1]
    assert first["transcript"] == {"zh": "", "pinyin": ""}
    assert last["stage"] == "final"
    assert last["reply"]["zh"]          # a gentle re-prompt
    # Nothing was recognized, so PA never ran: no `score` event at all, and the
    # reply carries no scores to omit.
    assert [e["stage"] for e in staged] == ["transcript", "final"]
    assert "pronunciation" not in last
    assert last["annotation"] is None


def test_pa_failure_still_streams_transcript_and_reply(monkeypatch):
    async def boom(audio_wav, reference_text, language="zh-CN"):
        raise pronunciation.PaError("azure PA canceled: timeout")

    monkeypatch.setattr(pronunciation, "assess", boom)

    staged = events(resp := client.post("/api/turn", files=_upload()))
    first, score, last = staged
    assert first["transcript"]["zh"] == "你好"
    assert last["reply"]["zh"] == "你好！你叫什么名字？"
    # The turn is still fully delivered; only the scores are missing, and the
    # `score` event says so rather than going silent.
    assert score["pronunciation"] is None
    assert score["tone_errors"] == []


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
    staged = events(resp)
    first, last = staged[0], staged[-1]
    assert first["stage"] == "transcript"
    assert last["stage"] == "error"
    assert "refused" in last["detail"]
    # The scores are independent of the worker, so a refused turn still gets
    # them — the learner's pronunciation was assessed either way.
    assert [e["stage"] for e in staged] == ["transcript", "score", "error"]


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


# A stream that re-couples its branches doesn't fail an ordering assertion — it
# blocks forever on the branch the test is deliberately holding open. Every
# staged read goes through this so that regression surfaces as a failure with a
# name attached, not a hung CI job.
async def _next(stream, *, timeout=2.0):
    return await asyncio.wait_for(stream.__anext__(), timeout=timeout)


# --- C: the score event is not gated on the worker ------------------------
#
# Stage 0 measured PA at 1.20s against Claude's 3.56s, so the scores exist ~2.4s
# before the reply does. Emitting them together throws that away — it is the
# same "hold it until everything's done" mistake the transcript event exists to
# fix, one layer down.


@pytest.mark.asyncio
async def test_score_is_yielded_before_the_worker_finishes(monkeypatch):
    """PA resolving must flush `score` while Claude is still pending.

    This is the test that stops a future refactor from quietly re-`gather`ing the
    two branches: with a single gather the scores cannot reach the wire until the
    slower branch returns, and every assertion here would still pass on ordering
    alone. The blocked worker is what makes the timing observable.
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
    ).__aiter__()

    assert (await _next(stream)).stage == "transcript"

    score = await _next(stream)
    assert score.stage == "score", "scores waited on the worker"
    assert score.pronunciation.overall == 80.0
    assert not worker_may_finish.is_set()

    worker_may_finish.set()
    assert (await _next(stream)).stage == "final"


def test_score_event_carries_the_tone_errors():
    """Tone errors ride `score`, not `final` — both are derived from PA.

    Keeping them on `final` would re-gate the underlines on the worker even
    though the data that produces them landed seconds earlier.
    """
    staged = events(client.post("/api/turn", files=_upload()))
    score = next(e for e in staged if e["stage"] == "score")

    assert score["pronunciation"]["overall"] == 80.0
    # The stub syllable scores 40.0, under the 60.0 threshold.
    assert [t["syllable"] for t in score["tone_errors"]] == ["你"]


def test_pa_failure_still_emits_a_score_event(monkeypatch):
    """A degraded turn says so explicitly instead of silently omitting scores.

    `_assess_or_degrade` swallowing a PA failure is invisible when the absence of
    scores and the absence of an *event* look identical to the client. A `score`
    event with `pronunciation: null` is the difference between "not scored" and
    "still scoring".
    """

    async def boom(audio_wav, reference_text, language="zh-CN"):
        raise pronunciation.PaError("azure said no")

    monkeypatch.setattr(pronunciation, "assess", boom)
    staged = events(client.post("/api/turn", files=_upload()))

    score = next(e for e in staged if e["stage"] == "score")
    assert score["pronunciation"] is None
    assert score["tone_errors"] == []
    # The turn still completes — a scoring failure is not a turn failure.
    assert staged[-1]["stage"] == "final"


async def test_final_is_not_delayed_by_a_slow_pa(monkeypatch):
    """The reply must not wait on scoring, even when PA is the slower branch.

    The inverse of the gating bug: deriving `final`'s annotation from the PA
    result would make the reply wait on scores whenever PA runs long, which is
    precisely the coupling this split removes.
    """
    pa_may_finish = asyncio.Event()

    async def slow_assess(audio_wav, reference_text, language="zh-CN"):
        await pa_may_finish.wait()
        return PronunciationScore(overall=90.0, syllables=[])

    monkeypatch.setattr(pronunciation, "assess", slow_assess)

    transcript, kb_block, timer = await orchestrator.prepare_audio_turn(b"FAKEWAV")
    stream = orchestrator.stream_audio_turn(
        b"FAKEWAV", transcript=transcript, kb_block=kb_block, timer=timer
    ).__aiter__()

    assert (await _next(stream)).stage == "transcript"

    final = await _next(stream)
    assert final.stage == "final", "the reply waited on pronunciation scoring"
    assert not pa_may_finish.is_set()

    pa_may_finish.set()
    assert (await _next(stream)).stage == "score"


def test_collector_still_merges_tone_errors_into_the_annotation():
    """`TurnResponse` is unchanged by the split — the collector does the merge.

    The wire contract gained an event; the collected shape callers already have
    must not move, or the split becomes a breaking change for the live smoke
    test and every orchestrator test.
    """
    result = asyncio.run(orchestrator.run_audio_turn(b"FAKEWAV"))
    assert result.pronunciation.overall == 80.0
    assert [t.syllable for t in result.annotation.tone_errors] == ["你"]


async def test_worker_failure_cancels_the_still_running_pa(monkeypatch):
    """The losing branch must not outlive the request.

    PA and the worker are separate tasks now, so a failure in one leaves the
    other running. Without an explicit cancel it keeps a live Azure call (and
    the request's audio buffer) alive after the response has finished — an
    orphaned task per failed turn, which is a leak under any real traffic.
    """
    pa_started = asyncio.Event()
    pa_cancelled = asyncio.Event()

    async def never_finishing_assess(audio_wav, reference_text, language="zh-CN"):
        pa_started.set()
        try:
            await asyncio.Event().wait()   # never resolves
        except asyncio.CancelledError:
            pa_cancelled.set()
            raise

    async def boom(**kwargs):
        # Let PA get as far as its await before the worker fails, so there is a
        # genuinely in-flight task to clean up.
        await pa_started.wait()
        raise conversation.ConversationError("worker refused the turn")

    monkeypatch.setattr(pronunciation, "assess", never_finishing_assess)
    monkeypatch.setattr(conversation, "respond", boom)

    transcript, kb_block, timer = await orchestrator.prepare_audio_turn(b"FAKEWAV")
    staged = [
        event
        async for event in orchestrator.stream_audio_turn(
            b"FAKEWAV", transcript=transcript, kb_block=kb_block, timer=timer
        )
    ]

    assert [e.stage for e in staged] == ["transcript", "error"]
    # Let the cancellation the generator requested actually be delivered.
    await asyncio.sleep(0)
    assert pa_cancelled.is_set(), "PA was left running after the turn failed"


def test_collector_reraises_the_in_band_error():
    """`run_audio_turn` keeps the exception contract its callers already have.

    The stream reports a mid-turn worker failure in-band because its status line
    is spent; the collected path has no such constraint, so it re-raises and the
    route maps it to a 502 exactly as before the split.
    """

    async def boom(**kwargs):
        raise conversation.ConversationError("worker refused the turn")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(conversation, "respond", boom)
        with pytest.raises(conversation.ConversationError, match="refused"):
            asyncio.run(orchestrator.run_audio_turn(b"FAKEWAV"))
