import logging
import os

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.models import TurnResponse, Utterance
from backend.pinyin import to_pinyin
from backend.speech import pronunciation, stt

logger = logging.getLogger(__name__)

app = FastAPI(title="Convo Agent", version="0.1.0")

# Phase 1 hardcoded reply — every turn echoes this fixed greeting. Replaced by
# the Claude conversation worker in Phase 3.
PARTNER_REPLY = Utterance(zh="你好", pinyin="nǐ hǎo")

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
async def turn(audio: UploadFile = File(...)) -> TurnResponse:
    """One conversation turn: transcribe speech, score it, return a fixed reply.

    Two-pass speech: STT transcribes, then PA assesses the same audio against
    that transcript for per-syllable tone scores. Phase 3 swaps the hardcoded
    reply for the conversation worker's output.
    """
    audio_bytes = await audio.read()
    try:
        recognized = await stt.transcribe(audio_bytes)
    except stt.SttError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    transcript = Utterance(zh=recognized, pinyin=to_pinyin(recognized))

    # Tone scores are an enhancement, not the turn's payload: with no recognized
    # text there is nothing to assess, and a PA failure degrades to scores-off
    # rather than costing the user their transcript.
    pronunciation_score = None
    if recognized:
        try:
            pronunciation_score = await pronunciation.assess(audio_bytes, recognized)
        except pronunciation.PaError as exc:
            logger.warning("pronunciation assessment failed: %s", exc)

    return TurnResponse(
        transcript=transcript,
        reply=PARTNER_REPLY,
        pronunciation=pronunciation_score,
    )


# Static page is mounted last so explicit API routes above take precedence;
# html=True serves frontend/index.html at "/".
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
