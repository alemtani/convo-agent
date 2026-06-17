import os

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.models import TurnResponse, Utterance
from backend.pinyin import to_pinyin
from backend.speech import stt

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
    """One conversation turn: transcribe uploaded speech, return a fixed reply.

    Phase 1 deepens in place — Phase 2 adds tone scores, Phase 3 swaps the
    hardcoded reply for the conversation worker's output.
    """
    audio_bytes = await audio.read()
    try:
        recognized = await stt.transcribe(audio_bytes)
    except stt.SttError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    transcript = Utterance(zh=recognized, pinyin=to_pinyin(recognized))
    return TurnResponse(transcript=transcript, reply=PARTNER_REPLY)


# Static page is mounted last so explicit API routes above take precedence;
# html=True serves frontend/index.html at "/".
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
