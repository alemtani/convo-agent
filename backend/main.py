import json
import logging
import os

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from backend import kb, orchestrator
from backend.models import (
    ConversationTurnResponse,
    DialogueTurn,
    TextTurnRequest,
    TurnResponse,
)
from backend.speech import stt
from backend.speech._recognizer import SpeechConfigError
from backend.workers import conversation

logger = logging.getLogger(__name__)

app = FastAPI(title="Convo Agent", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), os.pardir, "frontend")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/hello")
async def hello():
    return {"message": "hello world"}


@app.post("/api/turn", response_model=TurnResponse)
async def turn(
    audio: UploadFile = File(...),
    topic_id: str = Form("greetings"),
    dialogue: str = Form("[]"),
) -> TurnResponse:
    """One spoken conversation turn: real Claude reply + transcript + tone scores.

    The Phase 3b loop, coordinated by `orchestrator.run_audio_turn`: STT
    transcribes, then PA (two-pass) and the conversation worker run concurrently,
    and per-syllable tone errors are merged into the annotation. Stateless: the
    client holds the running transcript and resubmits it as `dialogue` (a JSON
    array of prior `{role, zh}` turns) so the partner has memory across turns;
    the server appends this turn's STT text as the latest user turn.
    """
    try:
        history = [DialogueTurn.model_validate(t) for t in json.loads(dialogue)]
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=f"invalid dialogue: {exc}") from exc

    audio_bytes = await audio.read()
    try:
        return await orchestrator.run_audio_turn(
            audio_bytes, topic_id=topic_id, dialogue=history
        )
    except kb.KbError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (stt.SttError, conversation.ConversationError, SpeechConfigError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/turn/text", response_model=ConversationTurnResponse)
async def turn_text(req: TextTurnRequest) -> ConversationTurnResponse:
    """One text-only conversation turn: real Claude reply + turn annotation.

    Not on the production hot path as of Phase 3b — the PWA speaks, so the real
    loop is `POST /api/turn`. This endpoint is retained as a **mic-free dev/test
    harness**: it exercises the conversation worker and the cached prefix without
    Azure (handy for prompt iteration and a fast `curl` smoke test), and its
    coverage lives in `tests/test_turn_text.py`.

    TODO(phase-4): reassess when multi-turn lands — either promote it to a
    first-class text-input mode (dialogue is already wired) or remove it if the
    audio path fully subsumes it. Tracked so it doesn't linger unowned.
    """
    try:
        return await orchestrator.run_text_turn(req)
    except kb.KbError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except conversation.ConversationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# Static page is mounted last so explicit API routes above take precedence;
# html=True serves frontend/index.html at "/".
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
