import contextlib
import json
import logging
import os

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from backend import auth, config, kb, orchestrator
from backend.models import (
    ConversationTurnResponse,
    DialogueTurn,
    PasscodeRequest,
    SessionState,
    SessionStartResponse,
    TextTurnRequest,
    TtsRequest,
    VerdictCard,
    VerdictRequest,
)
from backend.speech import stt, tts
from backend.speech._azure import SpeechConfigError
from backend.workers import conversation
from backend.workers import feedback
from backend.workers import sketch as sketch_worker

logger = logging.getLogger(__name__)

@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    """Say out loud when the app is serving `/api/*` to anyone who finds it.

    A gate that defaults to off is only safe if "off" is impossible to miss:
    this is the one line in the log that distinguishes a correct local run from
    a deploy that forgot `APP_PASSCODE`.
    """
    if not auth.is_enabled():
        logger.warning(
            "APP_PASSCODE is not set — /api/* is UNAUTHENTICATED. Fine locally; "
            "on a public deploy every caller spends your Anthropic/Azure quota."
        )
    yield


app = FastAPI(title="Convo Agent", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), os.pardir, "frontend")

# Reachable without a session. `/health` is the platform's liveness probe —
# gating it fails the deploy rather than securing anything — and `/api/auth` is
# how you *get* a session, so gating it would be a closed loop. Everything else
# under `/api/` costs money per call and stays behind the gate.
_PUBLIC_API_PATHS = frozenset({"/health", "/api/auth"})


@app.middleware("http")
async def require_session(request: Request, call_next):
    """Gate `/api/*` on a valid session cookie.

    Middleware rather than a per-route dependency so a future route is gated by
    default: forgetting to add a dependency is a silent hole, while forgetting
    to add a path to `_PUBLIC_API_PATHS` is a visible 401 during development.

    The static page is deliberately *not* gated — it carries no keys and no
    learner data, and it is itself the login screen, so it has to render before
    a session exists.
    """
    path = request.url.path
    gated = auth.is_enabled() and path.startswith("/api/") and path not in _PUBLIC_API_PATHS
    if gated and not auth.verify_token(request.cookies.get(auth.COOKIE_NAME)):
        return JSONResponse({"detail": "authentication required"}, status_code=401)
    return await call_next(request)


@app.get("/health")
async def health():
    """Liveness probe, plus the one externally-visible fact about the gate.

    `auth` is here so a single curl against the deployed host answers "did the
    passcode actually get set?" — the question you most want to ask right after
    a deploy, and the one you cannot answer by looking at the page.
    """
    return {
        "status": "ok",
        "auth": "enabled" if auth.is_enabled() else "disabled",
    }


@app.post("/api/auth")
async def login(req: PasscodeRequest, request: Request) -> JSONResponse:
    """Exchange the shared passcode for a signed session cookie.

    `Secure` is derived from the request scheme rather than configured: over
    HTTPS (the deploy, via `X-Forwarded-Proto` and uvicorn's proxy headers) the
    cookie is Secure; over plain HTTP on localhost it must not be, or the
    browser drops it and local development can't log in. One less env var to get
    wrong in exactly the place where getting it wrong is invisible.
    """
    if not auth.check_passcode(req.passcode):
        # No detail about *why* — with a single shared credential there is
        # nothing to distinguish, and the 401 is the whole message.
        raise HTTPException(status_code=401, detail="invalid passcode")

    response = JSONResponse({"status": "ok"})
    response.set_cookie(
        auth.COOKIE_NAME,
        auth.issue_token(),
        max_age=int(config.SESSION_TTL_DAYS * 86400),
        httponly=True,          # the cookie is the credential; script can't read it
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/",
    )
    return response


@app.get("/api/hello")
async def hello():
    return {"message": "hello world"}


@app.post("/api/session", response_model=SessionStartResponse)
async def session_start() -> SessionStartResponse:
    """Start a session: pick a topic, generate its opening line + flavour.

    No request body — the topic is the server's choice, not the caller's.
    The frontend has no business knowing which topics exist; it gets
    `topic_id` back on the response and echoes it on every turn after, the
    same opaque way it already echoes `sketch`. One `sketch` worker call, from
    the topic KB (`docs/SCENARIOS.md`, `backend/workers/sketch.py`). The
    client freezes the response and resubmits `sketch` byte-identical on
    every turn for the rest of the session (`TextTurnRequest.sketch`, the
    `sketch` form field on `POST /api/turn`) — the server never stores it
    (stateless proxy). `scenario_card` is `situation` + `goal` straight from
    the authored seed, in English; slots are never shown.
    """
    try:
        return await orchestrator.start_session()
    except kb.KbError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except sketch_worker.SketchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _refuse_if_complete(state: SessionState) -> None:
    """409 a turn submitted against a session that has already ended.

    The client disables its own controls at `complete`, but it must not be the
    only gate: a second tab, a restored background tab, or any client/server
    disagreement would otherwise keep answering turns in a scenario that is over
    — and the verdict the learner is about to read would describe a transcript
    that has since moved on.
    """
    if state.status == "complete":
        raise HTTPException(
            status_code=409, detail="this session is already complete"
        )


@app.post("/api/turn")
async def turn(
    audio: UploadFile = File(...),
    topic_id: str = Form("greetings"),
    dialogue: str = Form("[]"),
    sketch: str = Form(""),
    state: str = Form("{}"),
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
    user turn. `sketch` is likewise client-held, from `POST /api/session`
    (`SessionStartResponse.sketch`); it defaults to empty for a turn sent
    before a session has been started, which degrades to no flavour.
    """
    try:
        history = [DialogueTurn.model_validate(t) for t in json.loads(dialogue)]
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=f"invalid dialogue: {exc}") from exc

    try:
        session_state = SessionState.model_validate(json.loads(state))
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=f"invalid state: {exc}") from exc

    _refuse_if_complete(session_state)

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
            sketch=sketch,
            scenario=kb.load_scenario(topic_id),
            state=session_state,
        ):
            yield event.model_dump_json() + "\n"

    return StreamingResponse(
        body(),
        media_type="application/x-ndjson",
        # Staged delivery is defeated *silently* by a buffering intermediary:
        # the events still arrive, just all at once at the end, and nothing in
        # the suite catches it (`TestClient` collects the whole body by
        # construction). `text/event-stream` is widely special-cased as
        # do-not-buffer; `application/x-ndjson` is not, so say it explicitly.
        # A mitigation, not a proof — see the follow-up on an end-to-end flush
        # test against a real uvicorn.
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


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
    _refuse_if_complete(req.state)
    try:
        return await orchestrator.run_text_turn(req)
    except kb.KbError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except conversation.ConversationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/verdict", response_model=VerdictCard)
async def verdict(req: VerdictRequest) -> VerdictCard:
    """The end-of-session card: what the learner achieved, and what to say next.

    Sits **beside** the turn loop rather than inside it (`/api/tts` is here for
    the same reason): one uncached call per session, made after the last reply
    has already rendered, so no turn ever waits on it.

    Gated on a finished session. The unmet card carries the model exchange —
    literally the sentence the learner has not yet worked out — so serving it
    mid-session would hand over the answer. The gate is `status == "complete"`
    and nothing more: a second, KB-side re-derivation would 409 forever if a
    topic gained a slot while a session was open on someone's phone, stranding a
    verdict they had actually earned.

    `goal_met` and `missing` are recomputed inside the worker from the KB, so a
    client claiming success it didn't have gets the truth back.
    """
    if req.state.status != "complete":
        raise HTTPException(
            status_code=409, detail="this session is not complete yet"
        )
    try:
        return await feedback.verdict(req)
    except kb.KbError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except feedback.FeedbackError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/tts")
async def tts_route(req: TtsRequest) -> Response:
    """Speak one line — the partner's reply, as slowed Mandarin MP3.

    Its own endpoint rather than a stage of `/api/turn`, and keyed on the text
    alone. That is what makes replay free: the client asks for the same line and
    gets it from its own buffer, or from the server's cache, without the turn
    ever growing by the length of a synthesis. It also means a reply the learner
    never asks to hear again costs one call, not two.

    Any upstream failure — canceled, timed out, missing credentials — is a 502,
    because the client's response to all of them is the same: reveal the text.
    Audio-only mode with no audio and no text is a dead session.

    The 422 from `TtsRequest` is a different kind of failure and the client
    treats it as one. A line past `TTS_MAX_CHARS` is not temporarily
    unavailable, it is unspeakable by this endpoint, so the page renders that
    reply as text only and retires its 🔊 instead of offering a retry that can
    never succeed.
    """
    try:
        audio = await tts.synthesize(req.text)
    except (tts.TtsError, SpeechConfigError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    # Replay is the client's in-memory buffer and the cache above, by design.
    # Nothing in between should hold learner audio.
    return Response(
        content=audio,
        media_type="audio/mpeg",
        headers={"Cache-Control": "no-store"},
    )


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
