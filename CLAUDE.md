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
- pypinyin — server-side pinyin romanization of recognized speech (also an
  authoring-tool dep; see `kb/zh/_tools/`)
- python-dotenv for environment config
- pytest + pytest-asyncio + httpx (test suite; `live` marker for real-API tests)
- Frontend: mobile-first responsive web (PWA); transcript held in `localStorage`

## Key files and directories

Target layout (per `docs/DESIGN.md`). The backend currently serves Phases 0–1
(health, `/api/hello`, and `POST /api/turn` → Azure STT + pinyin); the remaining
modules below are planned.

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
  pinyin.py            # romanize recognized speech for display (pypinyin)
  models.py            # Pydantic models
  db.py                # aiosqlite setup
  config.py            # env vars (API keys, Azure region)
  requirements.txt
  __init__.py
kb/zh/                 # knowledge base (git-versioned markdown)
  index.md
  <topic>/{topic,vocab,grammar,dialogues}.md
frontend/              # mobile-first PWA (DM thread, push-to-talk, localStorage)
tests/                 # pytest; mirrors backend/ modules; fixtures/ holds recorded responses
schema.sql
pytest.ini             # asyncio_mode=auto; default run excludes the `live` marker
.env.example
.gitignore
```

## How to run

```bash
source .venv/bin/activate
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

## Testing & verification

Run the suite: `pytest -q` (repo root, venv active). A **Stop hook**
(`.claude/hooks/run-tests.sh`) runs it after every turn and blocks the turn from
ending on failure — a turn is not "done" until tests pass. Show the test output
as evidence; don't claim success without it.

**YOU MUST write a failing test first for any deterministic logic**, then make
it pass. Verification is tiered by how deterministic the code is:

- **Pure logic & local I/O — real red-green TDD.** `kb.py` parsing, `profile.py`
  selection-weighting (pure math) + CRUD (temp SQLite), `orchestrator.py`
  turn-bounding, `models.py` validation, `main.py` routes (httpx `TestClient`,
  workers mocked).
- **Prompt-cache invariant — assert without spending tokens.** The cacheable
  prefix must be **byte-identical across turns**, and the `cache_control`
  breakpoint must sit *after* the stable block. Assert the assembled request,
  not a live response.
- **Claude / Azure boundaries — contract tests.** Mock the SDK client; assert
  the *request we build* (model id, breakpoint placement, message roles) and
  that we *parse a recorded response* correctly. Never assert exact model text.
- **LLM / Azure behavior — evals, not asserts.** Structural invariants (valid
  JSON, feedback cites only KB vocab, reply stays in HSK band) and the live
  `cache_read_input_tokens > 0` smoke test are marked `@pytest.mark.live` —
  they need keys and cost money, so they are **excluded from the default run**.
  Invoke explicitly with `pytest -m live`.

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

## Delivery — branch + PR, always

Ship every unit of work as a **branch + GitHub PR for review**, never commit
straight to `main`. The reviewer reads the diff and leaves inline comments; when
addressing them, push fixes and **reply on each review thread**. Steps: branch
from `main` → commit (conventional) → `git push -u` → `gh pr create` with a body
explaining the *why*. This is the standing workflow, not just for large changes.

## Knowledge-base authoring (separate from the service)

Authoring/updating topic KBs is a **dev-time workflow you invoke directly** (the
`kb-topic` skill, `.claude/skills/kb-topic/`), not part of the FastAPI app —
nothing in `backend/` imports it and it never runs in the request path. Its
guardrail is `kb/zh/_tools/validate.py` (run by the skill and by hand), **not** a
pytest suite — the Stop-hook test gate is for `backend/` correctness only. Tools:
`kb/zh/_hsk/build.py` (regenerate the pinned `word→band` index),
`kb/zh/_tools/annotate_pinyin.py` (dialogue pinyin from curated vocab),
`kb/zh/_tools/validate.py` (scope/membership guardrail). The band ceiling is
universal and lives in `kb/zh/_hsk/ceiling.json` (consumed by `config`, never the
reverse).

## Design reference

See `docs/DESIGN.md` for the full architecture spec, data flow, data models,
agent/caching design, session lifecycle, end-to-end scenarios, MVP scope, build
order, and technical risk assessment.
