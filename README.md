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
   git clone git@github.com:alemtani/convo-agent.git
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

## Try it yourself (manual validation)

This section always shows **only what the app does right now** — one walkthrough,
**updated in place** as each phase ships, never an append-only pile of old steps.
Because phases are cumulative (each builds on the last), running the current
walkthrough exercises everything underneath it. The full phase plan lives in
[`docs/DESIGN.md`](docs/DESIGN.md#build-order--walking-skeleton).

### What works today: Phase 1 — push-to-talk → speech-to-text

Hold a button, speak Mandarin, and see your words transcribed plus a fixed 你好
reply. Proves the audio path (mic → upload → Azure speech-to-text → rendered
transcript). **Needs an Azure Speech key** (Anthropic is not used until Phase 3).

1. Set `AZURE_SPEECH_KEY` / `AZURE_SPEECH_REGION` in `.env` (see
   [Setup](#setup) above for how to provision the Azure Speech resource).

2. Start the server:

   ```bash
   source .venv/bin/activate
   uvicorn backend.main:app --reload --port 8000
   ```

3. Open **http://localhost:8000/** in your browser (use `localhost`, not a LAN
   IP — the mic needs a secure context, and `localhost` counts as one).
4. **Hold** the *🎙️ Hold to talk* button, say a Mandarin phrase (e.g. 你好老师),
   and release.
5. ✅ **Expected:** a green bubble with your transcribed words, then a grey bubble
   reading **你好 / nǐ hǎo**. If the transcript matches what you said, Azure STT
   and the upload path work. (No speech detected shows *(nothing recognized)*.)

<details>
<summary>Prefer the command line?</summary>

```bash
# Post any 16 kHz mono WAV; the reply is always the fixed 你好.
curl -F "audio=@your-clip.wav" http://localhost:8000/api/turn
# -> {"transcript":"…","reply":{"zh":"你好","pinyin":"nǐ hǎo"}}
curl http://localhost:8000/health      # -> {"status":"ok"}
```
</details>

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
- ⏳ Phase 2 — Azure pronunciation assessment (per-syllable tone scores) alongside STT.
- ⏳ Phases 3+ — the Claude conversation partner, feedback, bounded sessions, and
  durable progress. Not yet implemented.

Also in place from the scaffold: CORS, `/health`, config loading API keys from
`.env`, and the knowledge-base tooling under `kb/zh/`.
