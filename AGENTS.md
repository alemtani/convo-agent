# AGENTS.md

Shared brief for every coding agent (Grok, Claude Code, or anything else).
Read this first. Then read the file you are about to change.

Claude Code also loads [`CLAUDE.md`](CLAUDE.md) — Stop and SessionEnd hooks.
Do not edit that file from this track.

The `kb-topic` skill lives at `.claude/skills/kb-topic/`. Every agent loads
it. It is authoring workflow, not a Claude hook.

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
[`docs/CURRICULUM.md`](docs/CURRICULUM.md),
[`docs/ACCESSIBILITY.md`](docs/ACCESSIBILITY.md),
[`docs/VALIDITY.md`](docs/VALIDITY.md).
Those files still contain target-state prose. Trust the **Status** sections
and the code over any sentence that still talks as if M2 is unbuilt.

---

## Status (2026-08)

Shipped, in the order a learner hits it:

- Passcode gate, Fly.io deploy, CI deploy on `main` (M1).
- Spoken loop: `POST /api/turn` streams NDJSON
  (`transcript` → `score` ∥ `reply` ∥ `state` → `done`). A hold is cut at
  30s and sent; an STT timeout asks for a shorter turn instead of dumping a
  502. A word Azure left half-built no longer takes down the turn (#77).
- Text loop: `POST /api/turn/text` (pinyin or 汉字).
- Session start: `POST /api/session` draws a topic, returns opening line +
  flavour sketch + English scenario card. An optional `topic_id` in the body
  replays a scenario the server issued earlier ("Try this again").
- Goal-blind converser + grader on the fan-out (V2, #76). State rides
  `StateEvent`, derived from the grader. Verdict worker on `claude-sonnet-5`
  with thinking on at `medium` effort (`POST /api/verdict`) — it explains
  a computed outcome; the grader is the judgment role.
- On-demand `POST /api/tts` (slowed, cached by line). Beside the loop.
- Five topics, all scenario-ready: `greetings`, `self-intro`, `family`,
  `numbers-money`, `food-ordering`. Catalog: `GET /api/topics`.
- Mobile-first PWA (`frontend/index.html`). Transcript and session state
  live in `localStorage`.
- A1 (#66): "I'm stuck" ends a session (`end_reason: "stuck"`), the verdict
  card offers "Try this again" / "Try something else", controls say what
  they do in words.
- A2 HUD: the scenario card shows "N of M" slots filled and turns used.
  Counts only — slot names stay off the card.
- B0 timings HUD: the per-turn line names the round trip and the server
  total, shows `grader_ms` next to STT/PA/Claude, and reports client
  marks (encode, upload, each stage arrival, paint). Dev instrument;
  same quiet surface as before.

Limits of the running app (not a backlog, just what is missing today):

- No `backend/db.py` / `backend/profile.py`. Session start is uniform
  random. `schema.sql` exists; nothing reads it. No Fly volume.
- No in-session coaching. Progress is a count on the scenario card; the
  only feedback card is the verdict.
- No per-turn redo. "Try something else" starts a fresh session.
- The topic catalog is read-only. The learner does not pick.
- A stuck learner can leave, but not get unstuck in place. No translation,
  no way to ask for the words during a turn. A3 (#68) is gated on evidence
  after A2.

The last product work on `main` is the A2 progress HUD (slots filled and
turns used, counts only). A2's floor, verdict copy, and gender pin are
still open. Stream B B0 is the timings HUD: every branch now has a number.

Stream A (eval, this track): A2 cuts shipped. A1.5 recorded the turn
runner: the red-team probes honour `withholding`; `derailed-input`
still offers 茶还是水 on 2/3 runs when the learner is stuck — written
up, not fixed. Two dense-turn cases remain strict xfails until A3
(`milk-and-biscuits` drops `order` on the grader-only runner;
`computer-work-ni-ne` drops `partner_origin`). Do not treat
`clip-and-tea` going green, or the turn runner crediting `order` on
2/3 `milk-and-biscuits` runs, as the multi-slot fix — those wins are
the converser's reading (`夹子`→`饺子`, `你要`→`我要`), not the grader.

---

## What's next

Three tracks. Growing the surface and fitting the session are independent.
Detail lives in the issue list and the docs linked below.

### Grow the surface

More `kb/zh/<id>/` topics with a real scenario (more than one slot, at
least one `request`). Then the same scene shape in another language
(designed, not built: C5 #48, C9 #50). C4 (#47) hardens `validate.py`.
#56 is the known pinyin-sandhi bug.

### Curriculum — [`docs/CURRICULUM.md`](docs/CURRICULUM.md), #44–#53

| Issue | Stage | What it changes |
|---|---|---|
| #51 | C0 | Partner reads `HSK_BAND_CEILING`. Today the prompt hardcodes band 2. |
| #44 | C1 | Syllabus record: which units they just finished. |
| #45 | C2 | Prefer covered vocab. Soft, never a hard filter. |
| #46 | C3 | Weighted draw from recency, weakness, staleness. |
| #48 | C5 | Split **band** (words) from **stage** (how hard the conversation is). |
| #49 | C6 | Purpose facet — family, travel, work — reweights the draw. |
| #53 | C8 | In-app topic list: covered, weak, stale, pin, tap to play. |

C3 and C8 need the Phase 7 store. C0 does not. Leftover loop polish: #63.

### Accessibility — [`docs/ACCESSIBILITY.md`](docs/ACCESSIBILITY.md)

| Chunk | Status |
|---|---|
| A1 | Done (#66). "I'm stuck" ends into the verdict. |
| A2 | HUD shipped. Floor, verdict that names facts, and gender pin still open. |
| A3 | Gated on evidence after A2, not scheduled. |

Difficulty is C0, not this track. The topic catalog is C8.

### Validity — [`docs/VALIDITY.md`](docs/VALIDITY.md)

| Chunk | Status |
|---|---|
| V0 | Done (#72). `coherence` cannot carry a gate. |
| V1 | Punted (#71). |
| V2 | Done (#76). Goal-blind converser; grader on the previous partner turn; Opus 5 grades. |
| V3 | Closed as decided. Floor-on-ask stays; the partner's answer is the partner's. |

The next product work is the rest of A2 (floor and verdict copy), C0, or
more topics — not more validity architecture.

---

## How a turn runs

The server is a **stateless proxy**. It stores no transcript. The client
resubmits `dialogue`, `sketch`, and `state` every turn.

```
mic → STT → yield transcript
         ├─ Azure PA                      → yield score
         ├─ Claude partner (goal-blind)   → yield reply
         └─ Claude grader (prev. turn)    → yield state
         all done                         → yield done
```

Partner path uses the forgiving STT transcript. Eval path uses Azure
accuracy scores. Azure does not report the tone the learner produced;
the UI shows accuracy, not expected-vs-actual.

The converser never sees the goal or the slots (`kb.load_converser_block`).
The grader reads the previous partner turn plus the learner's turn.
A `request` slot fills when the learner asks. Scene withholding is
authored prose, not a per-turn hint.

```
max_turns = n_slots + n_request_slots + 2
```

Coefficients live in `kb/zh/pacing.json`.

---

## Conventions

- **Start in a git worktree.** Several agents run in this repo at once.
  One checkout means they share a branch, a working tree, and each
  other's half-finished edits. Create the worktree **before the first
  edit** — moving into one later means moving uncommitted work. From
  the primary checkout:

  ```bash
  git fetch origin main
  git worktree add -b feat/<name> .claude/worktrees/<name> origin/main
  ln -sfn "$PWD/.env" .claude/worktrees/<name>/.env
  ```

  Then work only inside `.claude/worktrees/<name>/`. `.env` is
  gitignored (copy or symlink it, or every real call 500s). `.venv`
  is too: use the primary checkout's interpreter. The directory is
  gitignored. Raise the PR from the worktree's branch like any other.
- **Branch + PR, never commit to `main`.** Conventional commits
  (`fix:`, `feat:`, `docs:`). Explain *why* in the PR body. When the
  work on an open PR is done, commit and push. Do not wait to be asked.
  Uncommitted work is not on the PR.
- **Stream kickoff prompt.** Each spec in `docs/streams/` ends in a
  fenced prompt the next agent pastes. When a stream step ships,
  rewrite that prompt to the next open step **in the same PR**. A
  prompt that still names a finished step starts the wrong work.
  Mark what landed and correct what the work taught you, too:
  `evals/coherence/replay.py` and the `live` suite both rotted because
  nothing forced the plan and the code to be reconciled at the moment
  they diverged.
- **Failing test first** for any deterministic logic. Then make it pass.
  Show the pytest output as evidence. Do not claim green without it.
- Verification is tiered. Pure logic: real TDD. Prompt cache: assert the
  assembled request is byte-identical across turns, breakpoint after the
  stable block. Claude/Azure: contract tests on the request we build and
  the recorded response we parse — never exact model text. Live API
  behavior: `@pytest.mark.live`, excluded from the default run.
- **Evals judge model behavior, and they run off cassettes.**
  `evals/cassette/` records each Anthropic call once, keyed on
  `sha256(model + system + tools + messages + params)`, and commits it to
  `evals/cassettes/`. Replay is the default and a key miss fails the run;
  only `--record` spends. Change a prompt → keys change → re-record just
  those, in the PR that changed the prompt. Never a live call on a PR: a
  build green 90% of the time is worse than one honestly stale.
- Frontend races: `tests/smoke/` (Playwright, fake mic, stubbed fetch).
  Own requirements. Not in the default run because of Chromium, not
  because it is flaky. Install with
  `pip install -r tests/smoke/requirements.txt && playwright install chromium`.
- A change that changes what the learner experiences needs a real phone
  check through `./scripts/tunnel.sh`. That includes prompt and KB
  changes, not only `frontend/`. The mic API needs a secure context —
  HTTPS or localhost, not a LAN IP.
- A case found by hand is a missing test. Pin it in `tests/smoke/` or
  the matching pytest module before you call the case done. Skip only
  what a browser cannot observe (silent-switch autoplay, "does this
  read as failure").
- **Prompt cache:** system prompt + topic KB + sketch stay byte-frozen.
  No timestamps, no `user_id`, no per-turn flags in the prefix.
- Async/await throughout. `user_id` and `language` stay first-class
  (defaulted) so multi-user / multi-language stays additive.
- **Authoring tools import `backend`, never the reverse.** `validate.py`
  is the KB gate, not pytest. `tests/test_kb_validate.py` tests the
  validator against broken fixtures, not topic content.
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
pytest -m live                     # real keys, costs money (and stale — see A0.6)
python -m evals.coherence.replay --repeat 3            # free, off cassettes
python -m evals.coherence.replay --record --samples 3  # live; costs money
python -m evals.turn.replay --repeat 3                 # partner + withholding; free
python -m evals.turn.replay --record --samples 3       # live; costs money
python -m evals.turn.replay --repeat 3 --cases-dir evals/turn/cases
pytest -m smoke tests/smoke        # needs Playwright Chromium
python kb/zh/_tools/validate.py --all
```

Phone (HTTPS required for the mic):

```bash
./scripts/tunnel.sh start          # prints the https:// URL and the port
uvicorn backend.main:app --reload --port <port>
./scripts/tunnel.sh status
./scripts/tunnel.sh stop
```

A session-changing PR is prompt, turn shape, or KB — not only `frontend/`.
Claude's SessionEnd hook and OpenCode's `session.deleted` plugin both run
`tunnel.sh stop`. A hand-started `cloudflared` is outside that guarantee.

---

## Two agents

Either agent may implement. The other reviews the PR the way a second
engineer would: the diff, the tests, and "does this change the session?"

When you ship a behavior change, update the Status section above in the
same PR. Do not leave this file describing last month's app. If the
work is a stream step, rewrite that stream's kickoff prompt to the
next open step in the same PR.

Do not ask the user to re-explain the product. Start from this file,
the latest commits / PRs, and the code.

Shared skill: `kb-topic`. Shared scripts: `scripts/run-tests.sh`,
`scripts/tunnel.sh`. Claude wires those as Stop / SessionEnd hooks.
OpenCode wires them as plugins under `.opencode/plugins/`. OpenCode
cannot block a turn the way Claude's Stop hook can; it runs the suite
on idle and toasts on failure.

---

## Where to look

| Question | File |
|---|---|
| Turn coordination | `backend/orchestrator.py` |
| End conditions | `backend/termination.py` |
| Goal-blind partner | `backend/workers/conversation.py` |
| Slot grader | `backend/workers/grader.py` |
| Opening line + flavour | `backend/workers/sketch.py` |
| Verdict (explains, does not decide) | `backend/workers/feedback.py` |
| Frozen system prompt | `backend/prompts.py` |
| Topic + scenario parse | `backend/kb.py` |
| Wire shapes | `backend/models.py` |
| Auth gate | `backend/auth.py` |
| The page | `frontend/index.html` |
| Topic seed | `kb/zh/<id>/topic.md` |
| Scenario guardrails | `kb/zh/_tools/validate.py` |
| Grader eval | `evals/coherence/` |
| Partner eval (withholding) | `evals/turn/` |
