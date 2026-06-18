"""Turn coordination — the stateless seam between the route and the worker.

A plain Python coordinator (not an LLM): per turn it loads the topic KB, hands
the conversation worker the session sketch + forgiveness default, and wraps the
worker's reply + annotation into the response. It owns *which* sketch / model /
forgiveness apply; the worker stays a pure request builder.

Phase 3a drives this from text (`POST /api/turn/text`). Phase 3b reuses
`run_text_turn` unchanged with `text` sourced from Azure STT.
"""
from typing import Optional

from anthropic import AsyncAnthropic

from backend import config, kb
from backend.models import ConversationTurnResponse, TextTurnRequest
from backend.prompts import SKETCH_STUB
from backend.workers import conversation


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
