import json
import logging
import os

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from backend import kb, orchestrator
from backend.models import (
    ConversationTurnResponse,
    DialogueTurn,
    TextTurnRequest,
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


@app.post("/api/turn")
async def turn(
    audio: UploadFile = File(...),
    topic_id: str = Form("greetings"),
    dialogue: str = Form("[]"),
) -> StreamingResponse:
    """One spoken conversation turn, streamed as NDJSON.

    The Phase 3b loop, coordinated by `orchestrator.stream_audio_turn`: STT
    transcribes, then PA (two-pass) and the conversation worker run concurrently,
    and per-syllable tone errors are merged into the annotation.

    The body is one JSON object per line — a `transcript` event as soon as STT
    resolves, then `final` (or `error`). Staged because STT finishes in a
    fraction of the worker's time: answering once at the end means the learner
    stares at a loading bubble with none of their own words above it, which reads
    as if their turn never happened. See `models.TranscriptEvent`.

    Unknown topic (404) and STT/Azure-config failure (502) are settled before the
    first byte; a worker failure after that can only be an `error` event.

    Stateless: the client holds the running transcript and resubmits it as
    `dialogue` (a JSON array of prior `{role, zh}` turns) so the partner has
    memory across turns; the server appends this turn's STT text as the latest
    user turn.
    """
    try:
        history = [DialogueTurn.model_validate(t) for t in json.loads(dialogue)]
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=f"invalid dialogue: {exc}") from exc

    audio_bytes = await audio.read()
    try:
        transcript, kb_block, timer = await orchestrator.prepare_audio_turn(
            audio_bytes, topic_id=topic_id
        )
    except kb.KbError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (stt.SttError, SpeechConfigError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    async def body():
        async for event in orchestrator.stream_audio_turn(
            audio_bytes,
            transcript=transcript,
            kb_block=kb_block,
            timer=timer,
            dialogue=history,
        ):
            yield event.model_dump_json() + "\n"

    return StreamingResponse(body(), media_type="application/x-ndjson")


@app.post("/api/turn/text", response_model=ConversationTurnResponse)
async def turn_text(req: TextTurnRequest) -> ConversationTurnResponse:
    """One typed conversation turn: real Claude reply + turn annotation.

    Text mode — the first-class alternative to `POST /api/turn` for practising
    where you can't speak. Same stateless contract as the spoken loop (the client
    holds the transcript and resubmits it as `dialogue`) and the same orchestrator
    seam, minus STT and PA. Input is **hanzi-only**: the learner types 汉字 with
    their keyboard's pinyin IME, and `TextTurnRequest` rejects romanization with
    422. The response echoes their turn with derived pinyin so the client renders
    typed and spoken turns identically; it carries no tone scores, since those
    need audio.
    """
    try:
        return await orchestrator.run_text_turn(req)
    except kb.KbError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except conversation.ConversationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


class NoCacheStaticFiles(StaticFiles):
    """Serve the frontend with caching off.

    The whole client — markup, styles, and every line of JavaScript — is one
    `index.html`, so a cached copy runs stale logic against a current server and
    gives no sign of it: the page loads, it just behaves like an older build.
    Debugging that costs far more than re-fetching a few KB, and on a phone (or
    through a dev tunnel) there is no convenient hard-reload. Correctness over
    bandwidth for a single-user practice tool.
    """

    def is_not_modified(self, response_headers, request_headers) -> bool:
        return False   # ignore ETag/Last-Modified revalidation; always resend

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-store"
        return response


# Static page is mounted last so explicit API routes above take precedence;
# html=True serves frontend/index.html at "/".
app.mount("/", NoCacheStaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
