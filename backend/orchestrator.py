"""Turn coordination — the stateless seam between the route and the worker.

A plain Python coordinator (not an LLM): per turn it loads the topic KB, hands
the conversation worker the session sketch + forgiveness default, and wraps the
worker's reply + annotation into the response. It owns *which* sketch / model /
forgiveness apply; the worker stays a pure request builder.

Phase 3a drives this from text (`POST /api/turn/text`). Phase 3b adds the real
speech loop, in two halves: `prepare_audio_turn` runs everything that can still
fail with an HTTP status (KB load, STT), and `stream_audio_turn` races PA against
the conversation worker, emitting each stage as it resolves.
"""
import asyncio
import logging
from typing import AsyncIterator, List, Optional, Tuple, Union

from anthropic import AsyncAnthropic

from backend import config, kb, timing, tones, typed_pinyin
from backend.models import (
    ConversationTurnResponse,
    DialogueTurn,
    DoneEvent,
    ReplyEvent,
    ScoreEvent,
    TextTurnRequest,
    TranscriptEvent,
    TurnErrorEvent,
    TurnTimings,
    TurnUsage,
    Utterance,
)
from backend.pinyin import to_pinyin
from backend.prompts import SKETCH_STUB
from backend.speech import pronunciation, stt
from backend.workers import conversation

logger = logging.getLogger(__name__)

# One line of the NDJSON body `POST /api/turn` streams, discriminated by `stage`.
StagedEvent = Union[
    TranscriptEvent, ScoreEvent, ReplyEvent, DoneEvent, TurnErrorEvent
]

# Shown when nothing is recognized — an explicit "please say it again" so the
# learner knows to retry, not a bare greeting that reads like a fresh turn. Keeps
# the turn alive without spending a worker call on empty input; in-band (≤ band 2).
RETRY_REPLY = Utterance(zh="请再说一次。", pinyin=to_pinyin("请再说一次"))


async def run_text_turn(
    req: TextTurnRequest, client: Optional[AsyncAnthropic] = None
) -> ConversationTurnResponse:
    """Coordinate one text turn: load KB, run the worker, shape the response.

    The mirror of the spoken turn minus the speech stages, and still collected
    rather than staged: with only one worker call there is nothing to stage —
    the reply *is* the turn. `req.text` is usually
    *pinyin* — a beginner can't necessarily type 汉字 — so the transcript comes
    from the worker's reading of that input rather than from local romanization:
    it is the one component that can resolve `ta` into 他 or 她 from context.

    Tone errors are still computed here, never by the model. Where the audio path
    derives them from PA accuracy, text mode derives them from the tone digits the
    learner typed, which yields a real `said` instead of a sentinel (see
    `typed_pinyin`). Typing without tone digits simply produces none.

    Raises `kb.KbError` for an unknown topic and `conversation.ConversationError`
    on a refusal / unparseable reply — the route maps these to 404 / 502.
    """
    timer = timing.Timer()
    kb_block = kb.load_kb_block(req.topic_id)

    with timer.stage("claude"):
        reply, annotation, reading, usage = await conversation.respond(
            kb_block=kb_block,
            sketch=SKETCH_STUB,
            dialogue=req.dialogue,
            user_text=req.text,
            forgiveness_level=config.FORGIVENESS_LEVEL_DEFAULT,
            client=client,
        )

    tone_errors = typed_pinyin.tone_errors_from_typed(req.text, reading.zh)
    annotation = annotation.model_copy(update={"tone_errors": tone_errors})

    return ConversationTurnResponse(
        transcript=reading,
        reply=reply,
        annotation=annotation,
        timings=_report(timer, usage, mode="text"),
        usage=TurnUsage.from_sdk(usage),
    )


async def prepare_audio_turn(
    audio_bytes: bytes, *, topic_id: str = "greetings"
) -> Tuple[Utterance, str, timing.Timer]:
    """Everything about a spoken turn that can still fail with an HTTP status.

    Split out from the stream so the route settles 404 (unknown topic) and 502
    (STT / Azure config) *before* committing to a 200 — once the first event is
    on the wire the status line is spent, and anything later can only be an
    in-band error event. KB load goes first because it's local and free: a bad
    topic shouldn't cost an Azure call.

    The `Timer` is started here rather than in the stream and handed back, so
    `total_ms` covers the whole turn including STT — splitting the coordinator in
    two must not split the measurement, or every staged turn would under-report
    by the one stage that sits in front of everything.

    Raises `kb.KbError` and `stt.SttError`.
    """
    timer = timing.Timer()
    kb_block = kb.load_kb_block(topic_id)
    with timer.stage("stt"):
        recognized = await stt.transcribe(audio_bytes)
    return Utterance(zh=recognized, pinyin=to_pinyin(recognized)), kb_block, timer


async def stream_audio_turn(
    audio_bytes: bytes,
    *,
    transcript: Utterance,
    kb_block: str,
    timer: Optional[timing.Timer] = None,
    dialogue: Optional[List[DialogueTurn]] = None,
    client: Optional[AsyncAnthropic] = None,
) -> AsyncIterator[StagedEvent]:
    """Coordinate one spoken turn, emitting each stage as it resolves.

    Two-pass speech (DESIGN.md Risk 1): `prepare_audio_turn` has already run STT;
    PA assesses the same audio against that transcript, and since both PA and the
    conversation worker depend only on the transcript they run concurrently to
    keep the hot path short. The worker stays text-only — `tone_errors` are
    computed here from the PA score, not by the model.

    The transcript is yielded first and alone. It is the one thing that exists
    early, and it's the learner's own words: holding it back until the worker
    answers leaves them watching a loading bubble with nothing of theirs above
    it.

    Every event carries `elapsed_ms` measured at the moment it is emitted, so the
    replay harness can report when each stage *arrived*, not just how long it
    ran. Two turns with identical stage durations feel completely different
    depending on when each event flushed, and only the arrival time says which.

    The turn always ends in exactly one terminal event — `done` or `error`. The
    stream closing is not a completion signal: it looks the same as a dropped
    connection.

    A PA failure degrades to scores-off; empty recognition short-circuits to a
    re-prompt without spending a worker call; a worker failure becomes a
    `TurnErrorEvent` rather than an exception, because the response has already
    committed to 200.
    """
    timer = timer or timing.Timer()

    yield TranscriptEvent(transcript=transcript, **_at_emit(timer))

    if not transcript.zh:
        # Nothing to assess and nothing to answer, so neither branch is started:
        # silence must not cost an Azure PA call or a Claude turn.
        yield ReplyEvent(reply=RETRY_REPLY, **_at_emit(timer))
        # Still a measured turn: it cost an STT round trip, and dropping the
        # short-circuit from the numbers would bias the p50 downward.
        yield DoneEvent(timings=_report(timer, None, mode="audio"),
                        elapsed_ms=timer.total_ms)
        return

    async def assess_branch():
        with timer.stage("pa"):
            return await _assess_or_degrade(audio_bytes, transcript.zh)

    async def respond_branch():
        with timer.stage("claude"):
            return await conversation.respond(
                kb_block=kb_block,
                sketch=SKETCH_STUB,
                dialogue=dialogue or [],
                user_text=transcript.zh,
                forgiveness_level=config.FORGIVENESS_LEVEL_DEFAULT,
                client=client,
            )

    # Each branch times itself rather than the gather timing the pair: the whole
    # question WS1 Stage 2 hangs on is which of PA and Claude is the slower one,
    # and a single number for both cannot answer it.
    #
    # They are also *emitted* separately, as each resolves, rather than gathered
    # into one event. Stage 0 measured PA at 1.20s against Claude's 3.56s, so the
    # scores exist ~2.4s before the reply does; publishing them together throws
    # that away — the same "hold it until everything's done" mistake the
    # transcript event exists to fix. Whichever branch lands first goes out
    # first, so neither stage can gate the other in either direction.
    pa_task = asyncio.ensure_future(assess_branch())
    worker_task = asyncio.ensure_future(respond_branch())
    pending = {pa_task, worker_task}
    usage = None

    try:
        while pending:
            done, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED
            )
            # `done` is a set, so when both branches land in the same round its
            # iteration order is arbitrary — drain in pipeline order instead, or
            # the event sequence is nondeterministic exactly when the two stages
            # are closest together.
            for task in (pa_task, worker_task):
                if task not in done:
                    continue
                if task is pa_task:
                    score = task.result()
                    yield ScoreEvent(
                        pronunciation=score,
                        # Derived here, never by the model. They ride `score`
                        # rather than `final` because they come from the same
                        # PA result — putting them on the annotation would
                        # re-gate the tone underlines on the worker.
                        tone_errors=(
                            tones.tone_errors_from_score(
                                score, threshold=config.TONE_ERROR_THRESHOLD
                            )
                            if score is not None
                            else []
                        ),
                        **_at_emit(timer),
                    )
                else:
                    reply, annotation, _reading, usage = task.result()

                    yield ReplyEvent(
                        reply=reply, annotation=annotation, **_at_emit(timer)
                    )

        # Both branches have settled, so this is the first point at which the
        # stage table and the token usage are complete.
        yield DoneEvent(
            timings=TurnTimings.from_stages(timer.as_dict()),
            usage=TurnUsage.from_sdk(usage),
            elapsed_ms=timer.total_ms,
        )
    except conversation.ConversationError as exc:
        logger.warning("conversation worker failed mid-turn: %s", exc)
        yield TurnErrorEvent(detail=str(exc), **_at_emit(timer))
    finally:
        # A failure in either branch leaves the other still running; without this
        # the losing branch outlives the request as an orphaned task.
        for task in (pa_task, worker_task):
            if not task.done():
                task.cancel()
        # One log line per turn, after both branches have settled — so it carries
        # the full picture (including `cache_read`) even though the events that
        # went out earlier could only report the stages finished at their moment.
        _report(timer, usage, mode="audio")


def _at_emit(timer: timing.Timer) -> dict:
    """The two arrival fields every mid-turn event carries.

    `timer.stages` rather than `as_dict()` on purpose: `as_dict()` injects
    `total`, and a total on an event emitted while a branch is still running is
    a total of an unfinished turn. Mid-turn, the honest whole-turn number is
    `elapsed_ms` — how old the turn was when this line flushed.
    """
    return {
        "timings": TurnTimings.from_stages(timer.stages),
        "elapsed_ms": timer.total_ms,
    }


async def _assess_or_degrade(audio_bytes, reference_text):
    """Run PA, degrading a failure to no-scores rather than failing the turn."""
    try:
        return await pronunciation.assess(audio_bytes, reference_text)
    except pronunciation.PaError as exc:
        logger.warning("pronunciation assessment failed: %s", exc)
        return None


def _report(timer: timing.Timer, usage, *, mode: str) -> TurnTimings:
    """Log the turn's cost and return it as the wire model (WS1 Stage 0).

    One line per turn, at INFO so it shows up under a plain `uvicorn` run without
    a flag. The critical path is `stt + max(pa, claude)`, so the stages are
    printed side by side — the gap between their sum and `total` is the server's
    own overhead, and `cache_read` says whether the frozen prefix is being
    reused at all.
    """
    stages = timer.as_dict()
    logger.info(
        "turn timings mode=%s %s cache_read=%s cache_write=%s in=%s out=%s",
        mode,
        " ".join(f"{name}={stages[name]:.0f}ms"
                 for name in timing.STAGE_ORDER if name in stages),
        getattr(usage, "cache_read_input_tokens", None),
        getattr(usage, "cache_creation_input_tokens", None),
        getattr(usage, "input_tokens", None),
        getattr(usage, "output_tokens", None),
    )
    return TurnTimings.from_stages(stages)
