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
- Anthropic SDK (Claude API) — Sonnet 5 on the per-turn loop; prompt caching
- Azure Cognitive Services Speech SDK (STT + Pronunciation Assessment)
- aiosqlite (async SQLite) — durable learning state only, not transcripts
- Pydantic for data validation
- pypinyin — server-side pinyin romanization of recognized speech (also an
  authoring-tool dep; see `kb/zh/_tools/`)
- python-dotenv for environment config
- pytest + pytest-asyncio + httpx (test suite; `live` marker for real-API tests)
- Playwright (`tests/smoke/`) — frontend smoke suite, `smoke` marker; browser-only
  dep, installed from `tests/smoke/requirements.txt`, never by the backend
- Frontend: mobile-first responsive web (PWA); transcript held in `localStorage`

## Key files and directories

Target layout (per `docs/DESIGN.md`). The backend serves through M2-D: the full
spoken loop `POST /api/turn` → Azure STT → PA ∥ conversation worker (cached
prefix) → merged `tone_errors`; the mic-free `POST /api/turn/text` harness;
`POST /api/session`, the sketch worker's one call per session (opening line +
flavour, plus the pinned scenario card — `docs/SCENARIOS.md`);
`POST /api/verdict`, the end-of-session card; and `POST /api/tts` (M4). The last
two both sit *beside* the loop rather than inside it, for the same reason: one
speaks a line, one explains a finished session, and neither is something a turn
should wait on. Live modules: `main.py`, `orchestrator.py`, `termination.py`,
`kb.py`, `pinyin.py`, `tones.py`, `models.py`, `config.py`, `prompts.py`,
`workers/{conversation,sketch,feedback}.py`,
`speech/{stt,pronunciation,tts,_azure}.py`. Still planned: `db.py`,
`profile.py` (Phases 7–8).

Session state is client-held, like `sketch`: `termination.py` computes it from
the tracker fields on the worker's annotation and the client resubmits it every
turn. It rides `ReplyEvent`, never `DoneEvent` — state is ready when the reply
is, and `done` waits on the PA branch too.

```
backend/
  main.py              # FastAPI app, static serving, CORS (auth gate: Phase 8)
  orchestrator.py      # turn coordination, context assembly, caching, bounding
  termination.py       # slot state → end conditions + situational pressure (pure)
  workers/
    conversation.py    # Claude conversation worker (cached prefix) + slot tracker
    feedback.py        # verdict card: explains a computed outcome, once
    sketch.py          # session sketch generation
  speech/
    _azure.py          # shared credentialed SpeechConfig + recognizer
    stt.py             # Azure STT
    pronunciation.py   # Azure PA (two-pass)
    tts.py             # Azure TTS (slowed SSML, cached by line)
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
  pacing.json          # scenario turn-cap coefficients (consumed by kb.py)
  <topic>/{topic,vocab,grammar,dialogues}.md
frontend/              # mobile-first PWA (DM thread, push-to-talk, localStorage)
tests/                 # pytest; mirrors backend/ modules; fixtures/ holds recorded responses
  smoke/               # Playwright frontend suite (fake mic + stubbed fetch); own requirements.txt
schema.sql
pytest.ini             # asyncio_mode=auto; default run excludes `live` and `smoke`
.env.example
.gitignore
```

## How to run

```bash
source .venv/bin/activate
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

### Testing on a real phone

A phone cannot use the mic against `http://<mac-ip>:8000` — the mic API needs a
secure context, and browsers count https:// and localhost but not a LAN IP. Use
a Cloudflare quick tunnel to give the local server a real HTTPS origin:

```bash
./scripts/tunnel.sh start          # prints the https:// URL and the port to serve on
uvicorn backend.main:app --reload --port <port>
./scripts/tunnel.sh status         # url + port
./scripts/tunnel.sh stop
```

**Raise a PR that changes what the learner experiences → start a tunnel and check
it on the phone.** The test is *"does this change the session?"*, not *"does this
touch `frontend/`"*. Backend work reaches the learner just as directly:

- **Anything that changes the prompt** — the system prompt, what goes in the
  cached KB block, a new instruction or a new model. The suite asserts the
  request we *build*; it cannot see that the partner now replies differently.
- **Anything that changes the turn's shape** — new fields on the response, a
  changed timeout or error path, a different failure the client has to render.
- **Anything that changes the KB the partner reads** — new vocab, a scenario, a
  raised band ceiling.

A green suite plus a live check are answering different questions. Tests say the
code does what we specified; a real turn on a real phone says the specification
was any good — and a prompt change that lands *before* the prompt work that gives
it meaning (a KB block the system prompt doesn't yet explain) is exactly the case
tests are blind to.

Mobile Safari is the target device and differs from the smoke suite's desktop
Chromium where it matters (autoplay, `AudioContext` unlock, safe areas, keyboard),
so a frontend PR still always earns a tunnel — it is now the floor, not the rule.

State is keyed per Claude session and each session picks its own free port, so
concurrent agents in this repo never collide or kill each other's tunnel. A
**SessionEnd hook** runs `tunnel.sh stop`, so a tunnel cannot outlive the session
that opened it — the hook is the guarantee, not this convention, because an
abrupt exit gives Claude no turn in which to clean up. Tunnels started by hand
with `cloudflared` directly are outside that guarantee.

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
  `cache_read_input_tokens > 0` check are marked `@pytest.mark.live` — they need
  keys and cost money, so they are **excluded from the default run**. Invoke
  explicitly with `pytest -m live`.
- **Frontend behavior — a browser, deterministically.** `tests/smoke/` drives
  `frontend/index.html` in Chromium (`@pytest.mark.smoke`) to pin the races that
  clicking around is worst at catching: mic frames lost before the button turns
  red, a bubble duplicated instead of upgraded in place, a scroll that lands in
  the wrong spot. It is deterministic — the mic is a generated WAV
  (`--use-file-for-fake-audio-capture`), `/api/turn*` is a `fetch` stub whose
  responses the test releases by hand, and nothing waits on a clock — so **it
  runs in CI**, in its own job. It sits out the default run only because it
  needs a Chromium install, not because it's unreliable; that's the line —
  non-determinism stays out of CI, browser weight just gets its own job. Run it
  with `pytest -m smoke tests/smoke` after
  `pip install -r tests/smoke/requirements.txt && playwright install chromium`.

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
`kb/zh/_tools/validate.py` (scope/membership + scenario guardrail). The band
ceiling is universal and lives in `kb/zh/_hsk/ceiling.json` (consumed by
`config`, never the reverse); scenario pacing coefficients live the same way in
`kb/zh/pacing.json` (consumed by `kb.py`).

`validate.py` imports `backend.kb` for the `topic.md` parser, so the guardrail
reads a topic exactly as the service will. That is the one coupling and it runs
**one way** — authoring tools may import `backend`, never the reverse. Its
scenario rules do have pytest coverage (`tests/test_kb_validate.py`), which is
not a contradiction of the line above: those tests exercise *the validator*
against deliberately broken fixtures under `tests/fixtures/kb_scenarios/`, and
assert nothing about KB content.

## Design reference

See `docs/DESIGN.md` for the full architecture spec, data flow, data models,
agent/caching design, session lifecycle, end-to-end scenarios, MVP scope, build
order, and technical risk assessment.

See `docs/SCENARIOS.md` for the goal-oriented scenario design (M2): slot-based
goals, state-driven bounding with one derived `max_turns`, the three-tier slot
tracker / termination / verdict split, `validate.py` guardrails, and four worked
traces (happy, unhappy, one-turn clear, authoring rejection).
