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
from tests.helpers import collect_audio_turn

client = TestClient(app)


def _upload(data=b"FAKEWAV"):
    return {"audio": ("turn.wav", data, "audio/wav")}


def events(resp):
    """Parse an NDJSON body into a list of event dicts."""
    return [json.loads(line) for line in resp.text.splitlines() if line.strip()]


# Captured before the autouse fixture replaces it, so a test can put the real
# implementation back and exercise the code path *inside* `assess` — the timeout
# wrapper — rather than only the stub that stands in for it.
_REAL_ASSESS = pronunciation.assess


class _FakeUsage:
    input_tokens = 120
    output_tokens = 90
    cache_read_input_tokens = 4200
    cache_creation_input_tokens = 0


@pytest.fixture(autouse=True)
def stub_worker_and_pa(monkeypatch):
    async def fake_assess(audio_wav, reference_text, language="zh-CN"):
        return PronunciationScore(
            overall=80.0,
            syllables=[SyllableScore(hanzi="你", pinyin="nǐ", accuracy=40.0)],
        )

    async def fake_respond(*, kb_block, sketch, dialogue, user_text, forgiveness_level,
                           want_reading=True, client=None):
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


def by_stage(resp):
    """Parse a body into `{stage: event}` — the way a client reads it.

    Deliberately not a sequence. `score` and `reply` are two concurrent branches
    emitted in whichever order they resolve, so asserting their *positions*
    pins an accident of how fast the stubs happen to be rather than anything the
    server guarantees. The order that is real is asserted separately, by holding
    one branch open (see section C).
    """
    return {e["stage"]: e for e in events(resp)}


def test_stream_emits_every_stage_of_a_turn():
    resp = client.post("/api/turn", files=_upload())

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/x-ndjson")
    staged = events(resp)

    # What the contract actually fixes: the transcript is first, `done` is last,
    # and `score`/`reply` are both present somewhere between.
    assert staged[0]["stage"] == "transcript"
    assert staged[-1]["stage"] == "done"
    assert {e["stage"] for e in staged} == {"transcript", "score", "reply", "done"}

    seen = by_stage(resp)
    assert seen["transcript"]["transcript"] == {"zh": "你好", "pinyin": "nǐ hǎo"}
    assert seen["reply"]["reply"]["zh"] == "你好！你叫什么名字？"
    # Scores ride the `score` event, not `reply` — see the section on C below.
    assert seen["score"]["pronunciation"]["overall"] == 80.0
    # Tone errors ride `score` too: both they and `pronunciation` come from the
    # same PA result, and gating them on the worker would defeat the split.
    assert seen["score"]["tone_errors"] == [
        {"syllable": "你", "expected": 3, "said": 0, "index": None}
    ]
    # `reply` carries none of it: it is the reply, not the turn's accounting.
    assert "pronunciation" not in seen["reply"]


def test_the_turn_always_ends_in_exactly_one_terminal_event():
    """`done` is the completion signal; the stream closing is not.

    Without an explicit terminator a client cannot tell a finished turn from a
    dropped connection, a truncated proxy response, or a crashed worker — every
    one of those also just stops producing lines.
    """
    staged = events(client.post("/api/turn", files=_upload()))
    terminal = [e for e in staged if e["stage"] in ("done", "error")]
    assert [e["stage"] for e in terminal] == ["done"]
    assert staged[-1] is terminal[0], "`done` must be the last line, not merely present"


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
    assert [e.stage for e in rest] == ["score", "reply", "done"]
    assert rest[1].reply.zh == "你好！"


def test_empty_recognition_streams_transcript_and_a_reprompt(monkeypatch):
    """Silence costs an STT round trip and nothing else.

    Both branches are asserted *not called*, not merely absent from the output:
    a refactor that starts them before checking `transcript.zh` still produces
    the same event sequence while burning an Azure PA call and a Claude turn on
    an empty recording.
    """
    called = []

    async def fake_transcribe(audio_wav, language="zh-CN"):
        return ""

    async def never_respond(**kwargs):
        called.append("claude")
        raise AssertionError("worker should not run on empty recognition")

    async def never_assess(audio_wav, reference_text, language="zh-CN"):
        called.append("pa")
        raise AssertionError("PA should not run when nothing was recognized")

    monkeypatch.setattr(stt, "transcribe", fake_transcribe)
    monkeypatch.setattr(conversation, "respond", never_respond)
    monkeypatch.setattr(pronunciation, "assess", never_assess)

    resp = client.post("/api/turn", files=_upload())
    assert resp.status_code == 200
    staged = events(resp)
    assert [e["stage"] for e in staged] == ["transcript", "reply", "done"]
    assert called == []

    reply = staged[1]
    assert staged[0]["transcript"] == {"zh": "", "pinyin": ""}
    assert reply["reply"]["zh"]          # a gentle re-prompt
    assert reply["annotation"] is None
    assert "pronunciation" not in reply


def test_pa_failure_still_streams_transcript_and_reply(monkeypatch):
    async def boom(audio_wav, reference_text, language="zh-CN"):
        raise pronunciation.PaError("azure PA canceled: timeout")

    monkeypatch.setattr(pronunciation, "assess", boom)

    seen = by_stage(client.post("/api/turn", files=_upload()))
    assert seen["transcript"]["transcript"]["zh"] == "你好"
    assert seen["reply"]["reply"]["zh"] == "你好！你叫什么名字？"
    # The turn is still fully delivered; only the scores are missing, and the
    # `score` event says so rather than going silent.
    assert seen["score"]["pronunciation"] is None
    assert seen["score"]["tone_errors"] == []
    assert "done" in seen


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
    # `error` replaces `done`; a turn ends in exactly one of the two, so a
    # client that has seen neither knows the stream was cut rather than finished.
    assert "done" not in {e["stage"] for e in staged}


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
    from backend.speech import _azure

    # Real STT, not the fixture's stub — the failure being tested comes from the
    # recognizer refusing to build. `monkeypatch` is one instance shared with the
    # autouse fixture, so undo() drops its stubs; the turn never gets past STT.
    monkeypatch.undo()
    monkeypatch.setattr(_azure.config, "AZURE_SPEECH_KEY", "")

    resp = client.post("/api/turn", files=_upload())
    assert resp.status_code == 502
    assert "not configured" in resp.json()["detail"]


def test_dialogue_defaults_to_empty(monkeypatch):
    captured = {}

    async def fake_respond(*, kb_block, sketch, dialogue, user_text, forgiveness_level,
                           want_reading=True, client=None):
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

    async def fake_respond(*, kb_block, sketch, dialogue, user_text, forgiveness_level,
                           want_reading=True, client=None):
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


async def test_the_stream_is_the_only_contract():
    """Driving the generator directly gives the same turn the route serves.

    There is no second, collected code path to keep in sync — `orchestrator` used
    to export a `run_audio_turn` that merged the events back into one response,
    but once the route streamed, nothing in production called it. Tests that want
    the whole turn collect it themselves (`tests/helpers.py`).
    """
    seen = await collect_audio_turn()
    assert seen["transcript"].transcript.zh == "你好"
    assert seen["reply"].reply.zh == "你好！你叫什么名字？"
    assert seen["score"].pronunciation.overall == 80.0
    assert [t.syllable for t in seen["score"].tone_errors] == ["你"]


# --- WS1 Stage 0's timings survive the split (#19) -------------------------
#
# Splitting the coordinator in two is exactly the kind of refactor that quietly
# drops instrumentation: `prepare_audio_turn` owns STT, `stream_audio_turn` owns
# the rest, and a `Timer` created in the wrong half would silently under-report
# the turn by the one stage that sits in front of everything.


def test_every_event_carries_its_own_arrival_time():
    """`elapsed_ms` is one number per event: how old the turn was at flush.

    This is what staged delivery is actually about. Two turns with identical
    stage durations feel completely different depending on when each line went
    out, and only arrival distinguishes them — no combination of the stage
    durations can reconstruct it.
    """
    staged = events(client.post("/api/turn", files=_upload()))

    for event in staged:
        assert event["elapsed_ms"] is not None, event["stage"]
    # Monotonic, because it is measured off one timer started before STT.
    arrivals = [e["elapsed_ms"] for e in staged]
    assert arrivals == sorted(arrivals), arrivals


def test_the_stage_table_fills_in_as_the_turn_progresses():
    """`timings` is the stage table *as known at emit* — never a stage that
    hasn't finished, never a zero standing in for one."""
    seen = by_stage(client.post("/api/turn", files=_upload()))

    # The transcript flushes before PA and the worker have run at all.
    assert seen["transcript"]["timings"]["stt_ms"] is not None
    assert seen["transcript"]["timings"]["pa_ms"] is None
    assert seen["transcript"]["timings"]["claude_ms"] is None

    assert seen["score"]["timings"]["pa_ms"] is not None
    assert seen["reply"]["timings"]["claude_ms"] is not None

    # Only `done` sees the whole turn.
    for stage in ("stt_ms", "pa_ms", "claude_ms", "total_ms"):
        assert seen["done"]["timings"][stage] is not None, stage


def test_only_the_terminal_event_carries_a_total():
    """A `total_ms` before the turn is over is a total of an unfinished turn.

    That was the trap in reporting the full table on every line: `total_ms`
    meant "elapsed so far" on `transcript` and "the turn's cost" on the last
    one — the same field name for two different quantities.
    """
    staged = events(client.post("/api/turn", files=_upload()))

    for event in staged[:-1]:
        assert event["timings"]["total_ms"] is None, event["stage"]
    assert staged[-1]["stage"] == "done"
    assert staged[-1]["timings"]["total_ms"] is not None


def test_usage_rides_the_done_event():
    """Token usage is a whole-turn number, so it lands with the other ones.

    On `reply` it would be quoted while PA was potentially still running.
    """
    seen = by_stage(client.post("/api/turn", files=_upload()))
    assert "usage" in seen["done"]
    assert "usage" not in seen["reply"]


def test_total_includes_stt_despite_the_split():
    """`total_ms` covers the whole turn, not just the post-STT half.

    The regression this guards: a `Timer` constructed inside `stream_audio_turn`
    would start *after* STT, making every staged turn look ~1.3s faster than it
    is — and the p50 target is stated against `total`.
    """
    done = events(client.post("/api/turn", files=_upload()))[-1]
    assert done["timings"]["total_ms"] >= done["timings"]["stt_ms"]


def test_short_circuited_turn_is_still_measured(monkeypatch):
    """Empty recognition cost an STT round trip; dropping it would bias the p50."""

    async def silent(audio_wav, language="zh-CN"):
        return ""

    monkeypatch.setattr(stt, "transcribe", silent)
    done = events(client.post("/api/turn", files=_upload()))[-1]
    assert done["stage"] == "done"
    assert done["timings"]["stt_ms"] is not None
    assert done["timings"]["total_ms"] is not None
    # Neither branch ran, so neither reports a duration — a zero here would
    # flatter the p50 of exactly the stage the latency work turns on.
    assert done["timings"]["pa_ms"] is None
    assert done["timings"]["claude_ms"] is None


async def test_concurrent_branches_are_timed_separately(monkeypatch):
    """PA and Claude overlap, so neither may be charged the other's wall-clock.

    Which of the two dominates is the number the whole latency thread turns on
    (Stage 0 measured PA at 1.20s against Claude's 3.56s); a single figure for
    the pair cannot answer it.
    """

    async def fake_assess(audio_wav, reference_text, language="zh-CN"):
        await asyncio.sleep(0.01)
        return PronunciationScore(overall=90.0, syllables=[])

    async def fake_respond(**kwargs):
        await asyncio.sleep(0.05)
        return (
            Utterance(zh="你好", pinyin="nǐ hǎo"),
            TurnAnnotation(coherence="on_track"),
            Utterance(zh="你好", pinyin="nǐ hǎo"),
            _FakeUsage(),
        )

    monkeypatch.setattr(pronunciation, "assess", fake_assess)
    monkeypatch.setattr(conversation, "respond", fake_respond)

    seen = await collect_audio_turn()
    t = seen["done"].timings

    assert t.pa_ms < t.claude_ms, "the slow branch was charged to the fast one"
    # Total spans the serial critical path: STT, then the slower branch.
    assert t.total_ms >= t.stt_ms + t.claude_ms
    # ...but the overlapping branches are not summed into it.
    assert t.total_ms < t.stt_ms + t.claude_ms + t.pa_ms + 1000


async def test_pa_is_timed_even_when_it_fails(monkeypatch):
    """A PA call that burned two seconds and then failed is the most interesting
    timing there is; degrading to scores-off must not also lose the number."""

    async def slow_boom(audio_wav, reference_text, language="zh-CN"):
        await asyncio.sleep(0.01)
        raise pronunciation.PaError("boom")

    monkeypatch.setattr(pronunciation, "assess", slow_boom)

    seen = await collect_audio_turn()
    assert seen["score"].pronunciation is None
    assert seen["done"].timings.pa_ms > 0


async def test_done_surfaces_the_anthropic_usage_block(monkeypatch):
    """`cache_read_input_tokens > 0` is the whole prompt-caching claim; before
    WS1 Stage 0 the orchestrator dropped it on the floor."""

    async def fake_respond(**kwargs):
        return (
            Utterance(zh="你好", pinyin="nǐ hǎo"),
            TurnAnnotation(coherence="on_track"),
            Utterance(zh="你好", pinyin="nǐ hǎo"),
            _FakeUsage(),
        )

    monkeypatch.setattr(conversation, "respond", fake_respond)

    usage = (await collect_audio_turn())["done"].usage
    assert usage.cache_read_input_tokens == 4200
    assert usage.input_tokens == 120
    assert usage.output_tokens == 90


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
    assert (await _next(stream)).stage == "reply"


def test_score_event_carries_the_tone_errors():
    """Tone errors ride `score`, not `reply` — both are derived from PA.

    Keeping them on `reply` would re-gate the underlines on the worker even
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
    assert staged[-1]["stage"] == "done"


async def test_reply_is_not_delayed_by_a_slow_pa(monkeypatch):
    """The reply must not wait on scoring, even when PA is the slower branch.

    The inverse of the gating bug: deriving `reply`'s annotation from the PA
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

    reply = await _next(stream)
    assert reply.stage == "reply", "the reply waited on pronunciation scoring"
    assert not pa_may_finish.is_set()

    pa_may_finish.set()
    assert (await _next(stream)).stage == "score"
    # `done` still comes last: it is emitted after *both* branches settle, so a
    # slow PA moves it, rather than the stream ending on the reply.
    assert (await _next(stream)).stage == "done"


async def test_the_reply_annotation_never_carries_tone_errors(monkeypatch):
    """Scores stay off the annotation entirely — no merge, on either path.

    The earlier design rejoined them into `reply.annotation` so a collected view
    kept its old shape. Nothing collects any more, and re-attaching them would
    put PA-derived data back on the worker's event: a client reading the
    annotation would then be waiting on the slower branch for underlines the
    `score` event already delivered.
    """
    seen = await collect_audio_turn()
    assert seen["reply"].annotation.tone_errors == []
    assert [t.syllable for t in seen["score"].tone_errors] == ["你"]


async def test_the_spoken_turn_does_not_buy_a_reading_it_throws_away(monkeypatch):
    """STT has already produced the learner's 汉字 on this path, so the worker's
    reading of them is an echo the orchestrator drops.

    Asserted on the *call*, not the output: asking for the field and discarding
    the answer looks identical from the outside, and the whole point is the
    output tokens — ~40 of them, on the one branch the reply waits behind.
    """
    asked = {}

    async def fake_respond(*, want_reading=True, **kwargs):
        asked["want_reading"] = want_reading
        return (
            Utterance(zh="你好", pinyin="nǐ hǎo"),
            TurnAnnotation(coherence="on_track"),
            None,
            None,
        )

    monkeypatch.setattr(conversation, "respond", fake_respond)

    await collect_audio_turn()

    assert asked["want_reading"] is False


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


async def test_a_stalled_pa_still_lets_the_turn_finish(monkeypatch):
    """A branch that hangs must not hold the response open forever.

    This is the failure staging made expensive: `/api/turn` keeps an HTTP
    response open for the whole turn, so before the deadline a wedged Azure call
    parked a connection, a worker thread and the audio buffer indefinitely,
    with the client watching a bubble that would never resolve.

    Bounded, it lands where every other PA failure lands — `pronunciation: null`
    and a completed turn — so a scoring stall costs the underlines, not the
    conversation.
    """
    import time

    def never_returns(audio_wav, reference_text, language):
        time.sleep(30)

    # The real `assess`, so the deadline under test is the one that ships.
    monkeypatch.setattr(pronunciation, "assess", _REAL_ASSESS)
    monkeypatch.setattr(pronunciation, "_assess_sync", never_returns)
    monkeypatch.setattr(pronunciation.config, "PA_TIMEOUT_S", 0.05)

    seen = await collect_audio_turn()

    assert seen["score"].pronunciation is None
    assert seen["score"].tone_errors == []
    assert seen["reply"].reply.zh          # the conversation continued
    assert "done" in seen and "error" not in seen


async def test_a_worker_failure_never_escapes_the_stream(monkeypatch):
    """The generator reports the failure; it does not raise out of iteration.

    A `ConversationError` escaping here would surface as a truncated body and a
    500 mid-response — the exact outcome the in-band error event exists to
    avoid, and one no status code can express once the 200 is on the wire.
    """

    async def boom(**kwargs):
        raise conversation.ConversationError("worker refused the turn")

    monkeypatch.setattr(conversation, "respond", boom)

    seen = await collect_audio_turn()   # must not raise
    assert "refused" in seen["error"].detail
    assert "reply" not in seen and "done" not in seen


def test_route_threads_the_sessions_sketch_through_to_the_worker(monkeypatch):
    """`sketch` is client-held from `POST /api/session`, same contract as
    `dialogue` — resubmitted byte-identical on the form field of every turn."""
    captured = {}

    async def fake_respond(*, sketch, **kwargs):
        captured["sketch"] = sketch
        return (
            Utterance(zh="你好", pinyin="nǐ hǎo"),
            TurnAnnotation(coherence="on_track"),
            Utterance(zh="你好", pinyin="nǐ hǎo"),
            object(),
        )

    monkeypatch.setattr(conversation, "respond", fake_respond)

    resp = client.post(
        "/api/turn", files=_upload(), data={"sketch": "The vendor is brisk."}
    )
    assert resp.status_code == 200
    assert captured["sketch"] == "The vendor is brisk."


def test_route_sketch_defaults_to_empty(monkeypatch):
    async def fake_respond(*, sketch, **kwargs):
        assert sketch == ""
        return (
            Utterance(zh="你好", pinyin="nǐ hǎo"),
            TurnAnnotation(coherence="on_track"),
            Utterance(zh="你好", pinyin="nǐ hǎo"),
            object(),
        )

    monkeypatch.setattr(conversation, "respond", fake_respond)

    resp = client.post("/api/turn", files=_upload())
    assert resp.status_code == 200


def test_stream_asks_intermediaries_not_to_buffer():
    """Staged delivery fails silently behind a buffering proxy.

    The events still arrive, just all at once at the end — and no test in this
    file would notice, because `TestClient` collects the whole body regardless.
    `text/event-stream` is widely special-cased as do-not-buffer;
    `application/x-ndjson` is not, so the intent is stated in headers instead.
    """
    resp = client.post("/api/turn", files=_upload())
    assert resp.headers["x-accel-buffering"] == "no"
    assert resp.headers["cache-control"] == "no-cache"
