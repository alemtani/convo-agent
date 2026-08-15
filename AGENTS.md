# AGENTS.md

Shared brief for every coding agent (Grok, Claude Code, or anything else).
Read this first. Then read the file you are about to change.

Claude Code also loads [`CLAUDE.md`](CLAUDE.md) — hooks, tunnel lifetime, and
the `kb-topic` skill. If this file and `CLAUDE.md` disagree on **what is
built**, this file wins. Then fix the other file in the same PR.

---

## What this is

A Mandarin conversation practice tool for one beginner (HSK 3.0, bands 1–2).
The learner gets a situation and a goal. They speak (or type pinyin). An AI
partner replies in 汉字 + pinyin. The session ends when the goal is met, the
turn cap is hit, or the learner says goodbye twice.

Each topic is a markdown **knowledge base** (vocab, grammar, dialogues). A
conversation is the applied form of that KB — generated from it, scored
against it.

The goal is a **set of named binary slots**, not a model's opinion. Python
decides pass/fail. The verdict worker only explains.

Design *why*: [`docs/DESIGN.md`](docs/DESIGN.md),
[`docs/SCENARIOS.md`](docs/SCENARIOS.md),
[`docs/CURRICULUM.md`](docs/CURRICULUM.md).
Those files still contain target-state prose. Trust the **Status** sections
and the code over any sentence that still talks as if M2 is unbuilt.

---

## Status (2026-08)

Shipped, in the order a learner hits it:

- Passcode gate, Fly.io deploy, CI deploy on `main` (M1).
- Spoken loop: `POST /api/turn` streams NDJSON
  (`transcript` → `score` ∥ `reply` → `done`).
- Text loop: `POST /api/turn/text` (pinyin or 汉字).
- Session start: `POST /api/session` draws a topic, returns opening line +
  flavour sketch + English scenario card.
- Slot tracker + `termination.py` + end-of-session `POST /api/verdict`.
- On-demand `POST /api/tts` (slowed, cached by line). Beside the loop.
- Five topics, all scenario-ready: `greetings`, `self-intro`, `family`,
  `numbers-money`, `food-ordering`. Catalog: `GET /api/topics`.
- Mobile-first PWA (`frontend/index.html`). Transcript and session state
  live in `localStorage`.

Not built:

- `backend/db.py`, `backend/profile.py` — no covered-set, no proficiency,
  no weighted draw. Session start is uniform random.
  `schema.sql` exists; nothing reads it. No Fly volume.
- In-session coaching every N turns. The only feedback card is the verdict.
- Per-turn redo. "New" starts a fresh session.
- Learner-picked topic. The catalog is read-only.
- Syllabus / curriculum stages C0–C9 (`docs/CURRICULUM.md`). Open issues
  #44–#53, plus #51 (`HSK_BAND_CEILING` loaded and unused).

Recent follow-ups, not blockers: #63 (fetch timeouts, speech tunables,
dead `SessionState.topic_id`). Authoring bug: #56 (`annotate_pinyin.py`
sandhi).

The last product work on `main` is polish after M2: #58 (keep one machine
warm), #59 (session-start retry), #60 (score the transcript in place),
#62 (keep speech after a pause). A new session starts from that tip.

---

## How a turn runs

The server is a **stateless proxy**. It stores no transcript. The client
resubmits `dialogue`, `sketch`, and `state` every turn.

```
mic → STT → yield transcript
         ├─ Azure PA  → yield score (tone underlines)
         └─ Claude    → yield reply + new SessionState
         both done    → yield done
```

Partner path uses the forgiving STT transcript. Eval path uses Azure
accuracy scores. Azure does not report the tone the learner produced;
the UI shows accuracy, not expected-vs-actual.

A `request` slot fills only when the learner asks **and** the partner
answers. Pressure is a stage direction after the cache breakpoint:
leave the scene unresolved, stay in character, do not volunteer the
answer.

```
max_turns = n_slots + n_request_slots + 2
```

Coefficients live in `kb/zh/pacing.json`.

---

## Conventions

- **Branch + PR, never commit to `main`.** Conventional commits
  (`fix:`, `feat:`, `docs:`). Explain *why* in the PR body.
- **Failing test first** for any deterministic logic. Then make it pass.
- Verification is tiered. Pure logic: real TDD. Prompt cache: assert the
  assembled request is byte-identical across turns, breakpoint after the
  stable block. Claude/Azure: contract tests on the request we build and
  the recorded response we parse — never exact model text. Live API
  behavior: `@pytest.mark.live`, excluded from the default run.
- Frontend races: `tests/smoke/` (Playwright, fake mic, stubbed fetch).
  Own requirements. Not in the default run because of Chromium, not
  because it is flaky.
- A change that changes what the learner experiences needs a real phone
  check through `./scripts/tunnel.sh`. That includes prompt and KB
  changes, not only `frontend/`.
- **Prompt cache:** system prompt + topic KB + sketch stay byte-frozen.
  No timestamps, no `user_id`, no per-turn flags in the prefix.
- **Authoring tools import `backend`, never the reverse.**
- Secrets stay server-side. `APP_PASSCODE` unset = gate off (local).
  On a public host the gate must be on; `/health` reports `auth`.

---

## How to run

```bash
source .venv/bin/activate
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

```bash
pytest -q                          # default gate
pytest -m live                     # real keys, costs money
pytest -m smoke tests/smoke        # needs Playwright Chromium
python kb/zh/_tools/validate.py --all
```

Phone: `./scripts/tunnel.sh start`, serve on the printed port, then
`./scripts/tunnel.sh stop`. Claude Code's SessionEnd hook stops a
tunnel it opened. A hand-started `cloudflared` is outside that
guarantee.

---

## Two agents

Either agent may implement. The other reviews the PR the way a second
engineer would: the diff, the tests, and "does this change the session?"

When you ship a behavior change, update the Status section above in the
same PR. Do not leave this file describing last month's app.

Do not ask the user to re-explain the product. Start from this file,
the latest commits / PRs, and the code. For a Claude Code session you
are continuing, use the `resume-claude` skill rather than a paste.

---

## Where to look

| Question | File |
|---|---|
| Turn coordination | `backend/orchestrator.py` |
| End conditions, pressure | `backend/termination.py` |
| Partner + slot tracker | `backend/workers/conversation.py` |
| Opening line + flavour | `backend/workers/sketch.py` |
| Verdict (explains, does not decide) | `backend/workers/feedback.py` |
| Frozen system prompt | `backend/prompts.py` |
| Topic + scenario parse | `backend/kb.py` |
| Wire shapes | `backend/models.py` |
| Auth gate | `backend/auth.py` |
| The page | `frontend/index.html` |
| Topic seed | `kb/zh/<id>/topic.md` |
| Scenario guardrails | `kb/zh/_tools/validate.py` |
