"""Turn coordination — the stateless seam between the route and the worker.

A plain Python coordinator (not an LLM): per turn it loads the topic KB, hands
the conversation worker the session sketch + forgiveness default, and wraps the
worker's reply + annotation into the response. It owns *which* sketch / model /
forgiveness apply; the worker stays a pure request builder.

Phase 3a drives this from text (`POST /api/turn/text`). Phase 3b adds
`run_audio_turn`: the real speech loop (STT → PA ∥ worker → merged tone errors).
"""
import asyncio
import logging
from typing import AsyncIterator, List, Optional, Tuple, Union

from anthropic import AsyncAnthropic

from backend import config, kb, timing, tones, typed_pinyin
from backend.models import (
    ConversationTurnResponse,
    DialogueTurn,
    FinalEvent,
    TextTurnRequest,
    TranscriptEvent,
    TurnErrorEvent,
    TurnResponse,
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
TurnEvent = Union[TranscriptEvent, FinalEvent, TurnErrorEvent]

# Shown when nothing is recognized — an explicit "please say it again" so the
# learner knows to retry, not a bare greeting that reads like a fresh turn. Keeps
# the turn alive without spending a worker call on empty input; in-band (≤ band 2).
RETRY_REPLY = Utterance(zh="请再说一次。", pinyin=to_pinyin("请再说一次"))


async def run_text_turn(
    req: TextTurnRequest, client: Optional[AsyncAnthropic] = None
) -> ConversationTurnResponse:
    """Coordinate one text turn: load KB, run the worker, shape the response.

    The mirror of `run_audio_turn` minus the speech stages. `req.text` is usually
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
) -> AsyncIterator[TurnEvent]:
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

    Every event carries `timings` measured at the moment it is emitted, so the
    replay harness can report when each stage *arrived*, not just how long it
    ran. Two turns with identical stage durations feel completely different
    depending on when each event flushed, and only the arrival time says which.

    A PA failure degrades to scores-off; empty recognition short-circuits to a
    re-prompt without spending a worker call; a worker failure becomes a
    `TurnErrorEvent` rather than an exception, because the response has already
    committed to 200.
    """
    timer = timer or timing.Timer()

    yield TranscriptEvent(
        transcript=transcript, timings=TurnTimings.from_stages(timer.as_dict())
    )

    if not transcript.zh:
        # Still a measured turn: it cost an STT round trip, and dropping the
        # short-circuit from the numbers would bias the p50 downward.
        yield FinalEvent(
            reply=RETRY_REPLY, timings=_report(timer, None, mode="audio")
        )
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
    try:
        score, (reply, annotation, _reading, usage) = await asyncio.gather(
            assess_branch(), respond_branch()
        )
    except conversation.ConversationError as exc:
        logger.warning("conversation worker failed mid-turn: %s", exc)
        yield TurnErrorEvent(
            detail=str(exc), timings=TurnTimings.from_stages(timer.as_dict())
        )
        return

    tone_errors = (
        tones.tone_errors_from_score(score, threshold=config.TONE_ERROR_THRESHOLD)
        if score is not None
        else []
    )
    annotation = annotation.model_copy(update={"tone_errors": tone_errors})

    yield FinalEvent(
        reply=reply,
        pronunciation=score,
        annotation=annotation,
        timings=_report(timer, usage, mode="audio"),
        usage=TurnUsage.from_sdk(usage),
    )


async def run_audio_turn(
    audio_bytes: bytes,
    *,
    topic_id: str = "greetings",
    dialogue: Optional[List[DialogueTurn]] = None,
    client: Optional[AsyncAnthropic] = None,
) -> TurnResponse:
    """Collect a spoken turn into one `TurnResponse`.

    The stream is the contract the route serves; this is the same turn without
    the staging, for callers that want the merged result rather than the delivery
    (the live smoke test, orchestrator tests). It's built *on* the stream rather
    than beside it so the two can't drift.

    Raises `stt.SttError`, `kb.KbError`, `conversation.ConversationError` — the
    in-band error event is re-raised so this keeps the exception contract callers
    already have.
    """
    transcript, kb_block, timer = await prepare_audio_turn(
        audio_bytes, topic_id=topic_id
    )

    final: Optional[FinalEvent] = None
    async for event in stream_audio_turn(
        audio_bytes,
        transcript=transcript,
        kb_block=kb_block,
        timer=timer,
        dialogue=dialogue,
        client=client,
    ):
        if isinstance(event, TurnErrorEvent):
            raise conversation.ConversationError(event.detail)
        if isinstance(event, FinalEvent):
            final = event

    assert final is not None, "the stream always ends in a final or error event"
    return TurnResponse(
        transcript=transcript,
        reply=final.reply,
        pronunciation=final.pronunciation,
        annotation=final.annotation,
        timings=final.timings,
        usage=final.usage,
    )


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
