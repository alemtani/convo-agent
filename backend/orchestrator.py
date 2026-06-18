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
from typing import List, Optional

from anthropic import AsyncAnthropic

from backend import config, kb, tones
from backend.models import (
    ConversationTurnResponse,
    DialogueTurn,
    TextTurnRequest,
    TurnResponse,
    Utterance,
)
from backend.pinyin import to_pinyin
from backend.prompts import SKETCH_STUB
from backend.speech import pronunciation, stt
from backend.workers import conversation

logger = logging.getLogger(__name__)

# Gentle re-prompt when nothing is recognized — keeps the turn alive without
# spending a worker call on empty input. In-band greetings vocab.
RETRY_REPLY = Utterance(zh="你好？", pinyin=to_pinyin("你好"))


async def run_text_turn(
    req: TextTurnRequest, client: Optional[AsyncAnthropic] = None
) -> ConversationTurnResponse:
    """Coordinate one text turn: load KB, run the worker, shape the response.

    Raises `kb.KbError` for an unknown topic and `conversation.ConversationError`
    on a refusal / unparseable reply — the route maps these to 404 / 502.
    """
    kb_block = kb.load_kb_block(req.topic_id)

    reply, annotation, _usage = await conversation.respond(
        kb_block=kb_block,
        sketch=SKETCH_STUB,
        dialogue=req.dialogue,
        user_text=req.text,
        forgiveness_level=config.FORGIVENESS_LEVEL_DEFAULT,
        client=client,
    )

    return ConversationTurnResponse(reply=reply, annotation=annotation)


async def run_audio_turn(
    audio_bytes: bytes,
    *,
    topic_id: str = "greetings",
    dialogue: Optional[List[DialogueTurn]] = None,
    client: Optional[AsyncAnthropic] = None,
) -> TurnResponse:
    """Coordinate one spoken turn: STT, then PA ∥ worker, then merge tone errors.

    Two-pass speech (DESIGN.md Risk 1): STT transcribes, then PA assesses the same
    audio against that transcript. PA and the conversation worker depend only on
    the transcript, so they run concurrently to keep the hot path short. The
    worker stays text-only — `tone_errors` are computed here from the PA score, not
    by the model. A PA failure degrades to scores-off; empty recognition
    short-circuits to a re-prompt without spending a worker call.

    Raises `stt.SttError`, `kb.KbError`, `conversation.ConversationError`; the
    route maps these to HTTP. Phase 3b is one greeting turn, so `dialogue` is
    normally empty.
    """
    recognized = await stt.transcribe(audio_bytes)
    transcript = Utterance(zh=recognized, pinyin=to_pinyin(recognized))
    if not recognized:
        return TurnResponse(transcript=transcript, reply=RETRY_REPLY)

    # Load KB up front so an unknown topic fails before any API work.
    kb_block = kb.load_kb_block(topic_id)

    score, (reply, annotation, _usage) = await asyncio.gather(
        _assess_or_degrade(audio_bytes, recognized),
        conversation.respond(
            kb_block=kb_block,
            sketch=SKETCH_STUB,
            dialogue=dialogue or [],
            user_text=recognized,
            forgiveness_level=config.FORGIVENESS_LEVEL_DEFAULT,
            client=client,
        ),
    )

    tone_errors = (
        tones.tone_errors_from_score(score, threshold=config.TONE_ERROR_THRESHOLD)
        if score is not None
        else []
    )
    annotation = annotation.model_copy(update={"tone_errors": tone_errors})

    return TurnResponse(
        transcript=transcript,
        reply=reply,
        pronunciation=score,
        annotation=annotation,
    )


async def _assess_or_degrade(audio_bytes, reference_text):
    """Run PA, degrading a failure to no-scores rather than failing the turn."""
    try:
        return await pronunciation.assess(audio_bytes, reference_text)
    except pronunciation.PaError as exc:
        logger.warning("pronunciation assessment failed: %s", exc)
        return None
