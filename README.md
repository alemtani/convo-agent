# Convo Agent

A Mandarin conversation practice agent powered by Claude and Azure Speech Services.

## Architecture

- **FastAPI** backend serving a REST API
- **Anthropic Claude API** for conversational AI
- **Azure Speech Services** for speech-to-text, text-to-speech, and pronunciation assessment
- **SQLite** (via aiosqlite) for async data persistence
- Frontend expected at `http://localhost:3000` (CORS pre-configured)

## Setup

1. **Clone the repo**

   ```bash
   git clone https://github.com/alemtani/convo-agent.git
   cd convo-agent
   ```

2. **Create a virtual environment and install dependencies**

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r backend/requirements.txt
   ```

3. **Configure environment variables**

   ```bash
   cp .env.example .env
   ```

   Fill in your keys in `.env`:

   ```
   ANTHROPIC_API_KEY=sk-ant-...
   AZURE_SPEECH_KEY=your-azure-speech-key
   AZURE_SPEECH_REGION=eastus
   ```

   **Getting an Azure Speech key** (needed from Phase 1; Anthropic isn't used
   until Phase 3): in the [Azure portal](https://portal.azure.com), create a
   **Speech** resource (Free **F0** tier is plenty), region **East US** (matches
   the `eastus` default in `backend/config.py`). Then under **Keys and Endpoint**,
   copy **KEY 1** → `AZURE_SPEECH_KEY` and the **Region** → `AZURE_SPEECH_REGION`.

## Running the dev server

```bash
source .venv/bin/activate
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

The health check is available at `GET /health`.

## The passcode gate

`/api/*` is protected by a single shared passcode exchanged for a signed session
cookie — not an account system, just the smallest thing that stops a public URL
from letting strangers spend your Anthropic and Azure quota.

**Locally the gate is off.** Leave `APP_PASSCODE` unset and nothing changes: no
login screen, no cookie. Startup logs a warning saying so.

**On any public deploy it is required.** Set `APP_PASSCODE` and confirm from
outside:

```bash
curl https://<your-host>/health
# -> {"status":"ok","auth":"enabled"}       ← "disabled" means you are wide open
```

The page itself stays reachable without a session (it *is* the login screen and
carries no keys); everything under `/api/` except `/api/auth` returns 401 until
you log in. The cookie is `HttpOnly`, `SameSite=Lax`, `Secure` over HTTPS, and
lasts `SESSION_TTL_DAYS` (default 30). Rotating `APP_PASSCODE` invalidates every
outstanding session — the signing key is derived from it, which is the only
revocation lever a single shared credential has.

## Try it yourself (manual validation)

This section always shows **only what the app does right now** — one walkthrough,
**updated in place** as each phase ships, never an append-only pile of old steps.
Because phases are cumulative (each builds on the last), running the current
walkthrough exercises everything underneath it. The full phase plan lives in
[`docs/DESIGN.md`](docs/DESIGN.md#build-order--walking-skeleton).

### What works today: Phase 2 — speech-to-text + per-syllable tone scores

Hold a button, speak Mandarin, and see your words transcribed **with a tone
score on each syllable**, plus a fixed 你好 reply. Proves the two-pass speech
path (mic → upload → Azure STT → Azure pronunciation assessment against that
transcript → rendered, color-coded transcript). **Needs an Azure Speech key**
(Anthropic is not used until Phase 3).

1. Set `AZURE_SPEECH_KEY` / `AZURE_SPEECH_REGION` in `.env` (see
   [Setup](#setup) above for how to provision the Azure Speech resource).

2. Start the server:

   ```bash
   source .venv/bin/activate
   uvicorn backend.main:app --reload --port 8000
   ```

3. Open **http://localhost:8000/** in your browser (use `localhost`, not a LAN
   IP — the mic needs a secure context, and `localhost` counts as one).
4. **Hold** the *🎙️ Hold to talk* button — a live mic-level meter fills as you
   speak, so you can see the mic is capturing — say a Mandarin phrase (e.g.
   你好老师), and release.
5. ✅ **Expected:** a green bubble with your transcribed words, each syllable
   underlined by how well you pronounced it (green = good, amber = ok, red = off)
   and an overall **tone NN/100** badge; then a grey bubble reading
   **你好 / nǐ hǎo**. Hover a syllable for its pinyin + score. (No speech detected
   shows *(nothing recognized)*; if scoring fails the turn still shows your
   transcript, just without tone colors.)

<details>
<summary>Prefer the command line?</summary>

```bash
# Post any 16 kHz mono WAV; the reply is always the fixed 你好.
curl -F "audio=@your-clip.wav" http://localhost:8000/api/turn
# -> {"transcript":{…},"reply":{"zh":"你好","pinyin":"nǐ hǎo"},
#     "pronunciation":{"overall":80.0,"syllables":[{"hanzi":"老","pinyin":"lǎo","accuracy":97.0}, …]}}
curl http://localhost:8000/health      # -> {"status":"ok"}
```
</details>

## Measuring turn latency

Every turn reports what it cost. The server logs one line per turn and returns
the same numbers on the response, so the page, the log, and the replay harness
never disagree about how long something took:

```
turn timings mode=audio stt=1103ms pa=884ms claude=2612ms total=3721ms cache_read=5120 …
```

The thread shows a quiet line under each exchange — round trip, server total,
each stage, and `cache_read` tokens. On the spoken path PA and Claude run
concurrently, so `stt + max(pa, claude)` is the critical path and the stages
deliberately sum to more than the total.

To get distributions rather than anecdotes, replay recorded turns at a running
server (**this spends real Azure/Anthropic quota**):

```bash
.venv/bin/python scripts/replay.py --runs 10                    # audio
.venv/bin/python scripts/replay.py --mode both --runs 10        # audio + text
.venv/bin/python scripts/replay.py --wav "recordings/*.wav"     # your own clips
```

It prints p50/p95 per stage with the run count each percentile rests on. Text
mode is the useful control: it is the same worker call without STT or PA, so the
gap between the two modes is the speech stack's share of the wait.

## Knowledge base & topic authoring

Conversation topics live as version-controlled markdown under `kb/zh/`, **separate
from the running service** — they're authored/edited at dev time, not by the app.

```
kb/zh/
  index.md              # catalog of topics
  _hsk/
    hsk-3.0.json        # authoritative word→band membership list (band-drift guard)
    ceiling.json        # the learner's universal HSK band ceiling (what vocab is fair game)
    build.py            # regenerate hsk-3.0.json from the pinned upstream
  _tools/
    validate.py         # scope/membership guardrail for a topic
    annotate_pinyin.py  # dialogue pinyin, derived from a topic's curated vocab
  <topic>/
    topic.md vocab.md grammar.md dialogues.md
```

**Two ways to manage topics:**

- **Assisted (Claude Code):** invoke the `kb-topic` skill — type `/kb-topic` (e.g.
  `/kb-topic add a family topic`) or just describe the task. It drafts/edits the
  files, runs validation, and opens a PR. See `.claude/skills/kb-topic/SKILL.md`
  for the workflow and the authoring rules it enforces.
- **Manual (any editor):** edit the markdown, then run the tools yourself:

  ```bash
  pip install -r kb/zh/_tools/requirements.txt          # one-time (pypinyin)

  # validate a topic (or --all) — fails on out-of-scope vocab
  python kb/zh/_tools/validate.py kb/zh/greetings
  python kb/zh/_tools/validate.py --all

  # generate the pinyin line for a dialogue (matches the topic's curated vocab)
  python kb/zh/_tools/annotate_pinyin.py kb/zh/greetings/vocab.md "你好吗？"

  # raise the band ceiling, then re-validate every topic
  #   edit band_ceiling in kb/zh/_hsk/ceiling.json
  python kb/zh/_tools/validate.py --all

  # regenerate the HSK wordlist (pinned upstream commit)
  python kb/zh/_hsk/build.py
  ```

See `kb/zh/_hsk/README.md` for how the wordlist and band ceiling work.

## Current status

Built incrementally in user-visible phases (see **Try it yourself** above):

- ✅ **Phase 0 — hello world:** static page served by FastAPI round-trips a
  string through `GET /api/hello`. Proves the page → backend path end-to-end.
- ✅ **Phase 1 — push-to-talk → speech-to-text:** the page records 16 kHz WAV in
  the browser and uploads it to `POST /api/turn`; Azure STT transcribes it and the
  backend returns a fixed 你好 reply. Proves the audio path before adding scores.
- ✅ **Phase 2 — pronunciation assessment (per-syllable tone scores):** a second
  pass runs Azure PA against the STT transcript and returns a per-syllable
  accuracy breakdown the page renders as color-coded underlines. PA failures
  degrade to transcript-only rather than failing the turn.
- ⏳ Phases 3+ — the Claude conversation partner, feedback, bounded sessions, and
  durable progress. Not yet implemented.

Also in place from the scaffold: CORS, `/health`, config loading API keys from
`.env`, and the knowledge-base tooling under `kb/zh/`.
