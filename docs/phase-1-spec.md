# Phase 1 spec — Push-to-talk → Azure STT → hardcoded 你好 reply

> Low-level implementation spec for **Phase 1** of the walking skeleton
> (`docs/DESIGN.md` §Build Order, line 533). Tracks scope, prerequisites, and the
> test plan. A progress artifact — may be pruned once Phase 1 ships.

## Context

`convo-agent` is built as a **walking skeleton**: a thin end-to-end slice runs
first, then each integration is deepened *in place*, with not-yet-built parts
hardcoded so the app stays runnable every phase. Phase 0 (merged) proved
browser→backend→page transport with `GET /api/hello` + a static page.

**Phase 1 adds exactly one user-visible capability:**

> Push-to-talk upload → Azure STT; hardcoded 你好 reply.
> *Visible:* Speak → see your words transcribed + a fixed 汉字 reply.

This is the first *speech-pipeline* phase. It proves the audio path (mic →
upload → Azure speech-to-text → rendered transcript) before Phase 2 adds
pronunciation assessment and Phase 3 adds the real Claude conversation worker.

**Explicitly NOT in Phase 1** (deferred; stays hardcoded/absent):

- Anthropic/Claude — **no Anthropic key needed yet** (Phase 3a).
- Azure Pronunciation Assessment / tone scores (Phase 2).
- KB loading (`kb.py`), orchestrator, sketch worker, prompt caching (Phase 3a).
- Multi-turn / client-held transcript, feedback, persistence (Phase 4+).

The partner reply is a **fixed constant** `{"zh": "你好", "pinyin": "nǐ hǎo"}`.

## Decisions

- **Audio format: WAV-in-browser.** Frontend captures mic via Web Audio at
  16 kHz mono, encodes 16-bit PCM WAV, and POSTs it. Backend feeds bytes
  straight to the Azure SDK. **No system dependencies** (no ffmpeg, no
  GStreamer) — keeps the eventual Fly/Railway deploy clean.
- **Reply model: nested.** `TurnResponse{transcript, reply: PartnerReply{zh,
  pinyin}}`. The 汉字+pinyin pair mirrors DESIGN's `partner_response` shape and
  the KB dialogue pairs, so Phase 3's conversation worker maps onto it with no
  response-shape churn.

## Prerequisites

1. **Python deps** — already pinned in `backend/requirements.txt`
   (`azure-cognitiveservices-speech==1.42.0`, `python-multipart==0.0.20`).
   Install: `source .venv/bin/activate && pip install -r backend/requirements.txt`.

2. **Provision an Azure Speech resource** (the API key needed for Phase 1):
   - portal.azure.com → **Create a resource** → search **"Speech"** → Create.
   - Region: **East US** (matches the `eastus` default in `backend/config.py`);
     Name: e.g. `convo-agent-speech`; Pricing tier: **Free F0**.
   - After deploy → resource → **Keys and Endpoint** → copy **KEY 1** and the
     **Location/Region**.

3. **Configure `.env`** (git-ignored; keys stay server-side):
   ```
   AZURE_SPEECH_KEY=<KEY 1 from portal>
   AZURE_SPEECH_REGION=eastus
   ```
   `backend/config.py` already reads both. `ANTHROPIC_API_KEY` can stay empty.

4. **Browser/mic note:** `getUserMedia` requires a secure context — `localhost`
   counts as secure, so `http://localhost:8000` works for dev. Reaching it from a
   phone over a LAN IP would need HTTPS (a Phase 8 deploy concern, out of scope).

## Implementation

### Backend

**`backend/speech/stt.py`** (new) — the Azure STT boundary, the only module that
imports the Speech SDK:

- `async def transcribe(audio_wav: bytes, language: str = "zh-CN") -> str`
- Builds `SpeechConfig(subscription=AZURE_SPEECH_KEY, region=AZURE_SPEECH_REGION)`
  from `backend.config`; sets `speech_recognition_language = language`.
- Writes the uploaded WAV bytes to a `tempfile.NamedTemporaryFile(suffix=".wav")`
  and uses `AudioConfig(filename=...)` so the SDK parses the RIFF/WAV header
  itself (robust; avoids hand-stripping a `PushAudioInputStream`).
- `recognize_once()` is blocking → wrap with `await asyncio.to_thread(...)`.
- Result handling by `result.reason`:
  - `RecognizedSpeech` → `result.text`.
  - `NoMatch` → `""` (nothing intelligible).
  - `Canceled` → raise a clear error (route surfaces as 502-class).

**`backend/models.py`** (new) — minimal Pydantic:

- `PartnerReply(zh: str, pinyin: str)`
- `TurnResponse(transcript: str, reply: PartnerReply)`

**`backend/main.py`** (extend) — add the turn endpoint later phases deepen:

- `POST /api/turn`, accepts `audio: UploadFile = File(...)` (multipart).
- Reads bytes → `await stt.transcribe(bytes)` → returns
  `TurnResponse(transcript=..., reply=PARTNER_REPLY)`.
- `PARTNER_REPLY` is a module constant marked "Phase 1 hardcoded; replaced by the
  conversation worker in Phase 3".
- Keep the existing `app.mount("/")` static mount **last** so the API wins.

### Frontend

**`frontend/index.html`** (extend Phase 0 page) — push-to-talk + two bubbles:

- Hold-to-talk button. On press: `getUserMedia({audio:true})` →
  `new AudioContext({sampleRate: 16000})` (captures at 16 kHz directly, no
  resampling) → an AudioWorklet (preferred, served as `recorder-worklet.js`) or
  ScriptProcessor fallback collects Float32 PCM chunks.
- On release: concatenate chunks → encode a mono **16-bit PCM WAV** → `POST
  /api/turn` as `multipart/form-data` field `audio`.
- Render `transcript` as a user bubble and `reply.zh` + `reply.pinyin` as a
  partner bubble (汉字-then-pinyin, as in `kb/zh/.../dialogues.md`).

CORS unchanged — the page is same-origin from `:8000`.

## Test plan (tiered per CLAUDE.md "Testing & verification")

Write the failing test **first** for deterministic backend logic. The Stop hook
runs `pytest -q` and blocks turn completion on failure. Frontend → manual.

1. **Route test — real red-green, Azure mocked** (`tests/test_turn.py`,
   `TestClient`): `monkeypatch` the STT call to return a fixed transcript; POST a
   dummy file to `/api/turn`; assert `200` and
   `{"transcript": "<fixed>", "reply": {"zh": "你好", "pinyin": "nǐ hǎo"}}`.
   Missing `audio` → `422`.

2. **Azure boundary — contract test, SDK mocked** (`tests/test_stt.py`): mock
   `SpeechConfig`/`SpeechRecognizer`; assert the request we build (key/region
   from config, `speech_recognition_language == "zh-CN"`) and that we parse a
   fake result correctly: `RecognizedSpeech` → `.text`; `NoMatch` → `""`;
   `Canceled` → raises. Never assert real recognition output.

3. **Model test** (`tests/test_models.py`): `TurnResponse`/`PartnerReply`
   validate and round-trip the expected JSON shape.

**Live STT smoke test — deferred.** Needs a recorded WAV fixture; skipped for
Phase 1. Real Azure recognition is validated manually (below).

## Verification

1. **Automated:** `pytest -q` (repo root, venv active) — all mocked tests green.
2. **Manual app run:**
   `uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000`, open
   `http://localhost:8000`, grant mic permission, **hold** the button and speak a
   Mandarin phrase, release → user bubble shows the transcript, partner bubble
   shows `你好 / nǐ hǎo`.

## Delivery

Branch `feat/phase1-stt-slice` from `main` → conventional commits → `gh pr
create` with a *why*-focused body. Never commit to `main`.
