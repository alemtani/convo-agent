# CLAUDE.md

## Project purpose

Convo Agent is a Mandarin conversation practice tool for a single beginner
learner (HSK 3.0, bands 1–2). You speak; an AI partner replies in text
(汉字 + pinyin). Claude is the conversational + feedback engine; Azure Speech
Services provides speech-to-text and pronunciation/tone assessment.

Each topic is a small markdown **knowledge base** (vocab, grammar, dialogues);
a conversation is the *applied form* of that KB — generated from it, scored
against it. Input is spoken; the partner's reply is text (on-demand TTS is
deferred, not in the hot path).

## Tech stack

- Python 3.9+
- FastAPI + Uvicorn (stateless turn proxy)
- Anthropic SDK (Claude API) — Sonnet 4.6 on the per-turn loop; prompt caching
- Azure Cognitive Services Speech SDK (STT + Pronunciation Assessment)
- aiosqlite (async SQLite) — durable learning state only, not transcripts
- Pydantic for data validation
- python-dotenv for environment config
- Frontend: mobile-first responsive web (PWA); transcript held in `localStorage`

## Key files and directories

Target layout (per `docs/DESIGN.md`). The backend is currently a scaffold
(health endpoint + config only); most modules below are planned.

```
backend/
  main.py              # FastAPI app, auth gate, static serving, CORS
  orchestrator.py      # turn coordination, context assembly, caching, bounding
  workers/
    conversation.py    # Claude conversation worker (cached prefix)
    feedback.py        # annotations → feedback + proficiency deltas
    sketch.py          # session sketch generation
  speech/
    stt.py             # Azure STT
    pronunciation.py   # Azure PA (two-pass)
  kb.py                # load topic markdown, parse frontmatter
  profile.py           # covered-set + proficiency CRUD + selection weighting
  models.py            # Pydantic models
  db.py                # aiosqlite setup
  config.py            # env vars (API keys, Azure region)
  requirements.txt
  __init__.py
kb/zh/                 # knowledge base (git-versioned markdown)
  index.md
  <topic>/{topic,vocab,grammar,dialogues}.md
frontend/              # mobile-first PWA (DM thread, push-to-talk, localStorage)
schema.sql
.env.example
.gitignore
```

## How to run

```bash
source .venv/bin/activate
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

No test suite exists yet.

## Development conventions

- Async/await throughout (FastAPI async handlers, aiosqlite)
- Environment-based configuration — no hardcoded secrets; Anthropic/Azure keys
  stay server-side and never reach the client
- **Stateless proxy**: the server doesn't persist conversation transcripts — the
  client holds the running transcript and resubmits it each turn. The server
  persists only durable per-user learning state (covered-set, proficiency).
- **Prompt caching**: keep the stable prefix (system prompt + topic KB + sketch)
  byte-frozen behind a `cache_control` breakpoint; put volatile per-turn data
  after it. Verify `cache_read_input_tokens > 0`.
- **Build for one, design for many**: `user_id` and `language` are first-class
  (defaulted) columns so multi-user / multi-language stays additive.
- CORS allows `http://localhost:3000` for local frontend development
- Git commit messages use conventional commits (e.g. `feat: scaffold backend`)

## Design reference

See `docs/DESIGN.md` for the full architecture spec, data flow, data models,
agent/caching design, session lifecycle, end-to-end scenarios, MVP scope, build
order, and technical risk assessment.
