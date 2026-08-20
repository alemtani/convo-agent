# AGENTS.md

Shared brief for every coding agent (Grok, Claude Code, or anything else).
Read this first. Then read the file you are about to change.

Claude Code also loads [`CLAUDE.md`](CLAUDE.md) — hooks, tunnel lifetime, and
the `kb-topic` skill. Leave that file to Claude Code. Do not edit it from
this track.

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
  (`transcript` → `score` ∥ `reply` → `done`).
- Text loop: `POST /api/turn/text` (pinyin or 汉字).
- Session start: `POST /api/session` draws a topic, returns opening line +
  flavour sketch + English scenario card. An optional `topic_id` in the body
  replays a scenario the server issued earlier ("Try this again").
- Slot tracker + `termination.py` + end-of-session `POST /api/verdict`.
- On-demand `POST /api/tts` (slowed, cached by line). Beside the loop.
- Five topics, all scenario-ready: `greetings`, `self-intro`, `family`,
  `numbers-money`, `food-ordering`. Catalog: `GET /api/topics`.
- Mobile-first PWA (`frontend/index.html`). Transcript and session state
  live in `localStorage`.
- A1 of the accessibility track (#66): "I'm stuck" ends a session the learner
  cannot finish (`end_reason: "stuck"`, written by the client), the verdict card
  offers "Try this again" / "Try something else", and the controls say what they
  do in words — session management sits under a ⋯ menu.

Limits of the running app (not a backlog, just what is missing today):

- No `backend/db.py` / `backend/profile.py`. No covered-set, no proficiency
  store, no weighted draw. Session start is uniform random.
  `schema.sql` exists; nothing reads it. No Fly volume.
- No in-session coaching. The only feedback card is the verdict — reachable
  early with "I'm stuck", but still only at the end of a session.
- No per-turn redo. "Try something else" starts a fresh session.
- The topic catalog is read-only. The learner does not pick.
- **A stuck learner can leave, but not get unstuck in place.** "I'm stuck"
  ends the session into the verdict (A1, #66). What is still missing is help
  *during* a turn: no translation, no way to ask for the words. A3 (#68) is
  gated on whether a session after A2 still drowns for vocabulary.
- **The partner holds the rubric.** One call both converses and annotates
  slots, so a non-sequitur that lands on a slot gets credited and played
  along with. `coherence` is computed every turn and read by nothing.

The last product work on `main` is polish after M2: #58 (keep one machine
warm), #59 (session-start retry), #60 (score the transcript in place),
#62 (keep speech after a pause). A new session starts from that tip.

---

## What's next

Three tracks. All open. Growing the surface and fitting the session are
independent; **validity depends on accessibility** and cannot lead it.
The issue list is the detail; this is the split.

### Grow the surface — more topics, scenarios, languages

Five Mandarin topics is where the MVP stopped. The next content is more
scenes for this learner, then the same scene shape in another language.

- Author more `kb/zh/<id>/` topics with a real scenario (more than one
  slot, at least one `request`). The cut slate — time/date, weather,
  directions — is the hard shape: two things to find out, plus one to
  tell. See [`docs/CURRICULUM.md`](docs/CURRICULUM.md).
- A second language is designed, not built: `kb/<lang>/lang.json`, a
  pinned lexicon, romanization as a capability. C5 (#48), C9 (#50).
  C7 (#52) is the live-edit hook: a session pins a `content_hash`, so
  new topics apply to the *next* session, not mid-turn.
- Authoring must stay honest as the slate grows. C4 (#47) hardens
  `validate.py` into the generation gate. #56 is the known pinyin-sandhi
  bug in `annotate_pinyin.py`.

### Fit the session to this learner

The loop works. It does not yet know what you have learned or where you
are weak. Every session is the same draw, the same band-2 partner, the
same pacing.

That is the curriculum track ([`docs/CURRICULUM.md`](docs/CURRICULUM.md),
issues #44–#53):

| Issue | Stage | What it changes for the learner |
|---|---|---|
| #51 | C0 | The partner actually reads `HSK_BAND_CEILING`. Today the prompt hardcodes band 2. |
| #44 | C1 | A syllabus record: which units they just finished. |
| #45 | C2 | Prefer covered vocab. Soft, never a hard filter. |
| #46 | C3 | Weighted draw from recency, weakness, staleness — not uniform random. |
| #48 | C5 | Split **band** (which words) from **stage** (how hard the conversation is). |
| #49 | C6 | Purpose facet — family, travel, work — reweights the draw. |
| #53 | C8 | In-app topic list: covered, weak, stale, pin, tap to play. |

C3 and C8 need the Phase 7 store (`db.py`, `profile.py`). C0 does not —
it is the first thing that makes a ceiling bump mean anything.

Leftover loop polish from the bug bash sits beside this track, not
instead of it: #63 (fetch timeouts, speech tunables, dead
`SessionState.topic_id`).

### Make it survivable when the learner is stuck

The first real session by the learner it was built for (2026-08-16) found
the loop works and the learner drowns. Being stumped has no exit but
guessing or quitting, and one turn was graded wrong and then explained
with a rule nobody wrote.

That is the accessibility track
([`docs/ACCESSIBILITY.md`](docs/ACCESSIBILITY.md)):

| Chunk | What it changes for the learner |
|---|---|
| A1 | "I'm stuck, end it" ends the session into the verdict. "Try this again." Buttons say words, not emoji. |
| A2 | The card becomes true: a Python floor under the slot tracker, a verdict that names facts without inventing causes, the progress HUD on. |
| A3 | Only if a session after A2 still drowns for words: translate on tap, word-level hints. |

A1 and A2 are independent. **A3 is gated on evidence, not scheduled** —
decide it after one more phone session by the same learner.

Two things deliberately *not* here. Difficulty is C0 (#51): the partner
is pinned to band 2 by a literal in `prompts.py` while
`HSK_BAND_CEILING` is loaded and never read. The topic catalog is C8
(#53): five topics and one learner make re-roll enough, and note 8 is
agency, not stuckness.

### Make the grade mean what it says

Accessibility fixes a grade that withheld credit the learner earned.
The other direction is credit they did not: the partner holds the
rubric, so a non-sequitur that happens to land on a slot is both
credited *and* cooperated with. A person would have said "…I asked if
you wanted a drink."

That is the validity track ([`docs/VALIDITY.md`](docs/VALIDITY.md)):

| Chunk | What it changes |
|---|---|
| V0 | A recorded-transcript set, and the first evaluation of `coherence`. Yields the thresholds V1 needs. |
| V1 | The floor is gated at that threshold. A session-level coherence fact on `VerdictCard`. |
| V2 | The converser goes goal-blind; the grader runs **after** the reply. Withholding becomes persona, `pressure_hint` retires into authored scene design. |
| V3 | Re-open A2's floor-on-ask compromise if the grader evaluates ask-AND-answer reliably. |

**V0 first, and it ships no gate.** `coherence` has been computed on
every turn since the conversation worker shipped and read by no code
path, and `tests/fixtures/` has no session transcripts to judge it
with. V0 measures it and reports what it can carry — possibly nothing.
Gating on an unmeasured signal manufactures the false negative A2
exists to remove.

**V1 does not close the gaming case; V2 does.** Even gated, the floor
only stops itself from adding credit. The model tracker still grants
the slot while the partner still holds the rubric.

**The grader reads the *previous* partner turn, not the new one.** That
is the pair the judgment needs — did the learner answer what was
actually said — so the grader joins the `PA ∥ converser` fan-out instead
of waiting on the reply. The wire shape is unchanged and the reply gets
*faster*, since the annotation leaves the converser's output schema. A
`request` slot's "partner answered" half then resolves one turn late,
which costs nothing while A2 credits on the ask alone.

**V2 also splits the model.** Sonnet 5 converses; **Opus 5 grades** —
judgment is where capability pays, and it is off the reply path. Today
`CONVERSATION_MODEL` serves the conversation, sketch, *and* verdict
workers alike. The grader wants thinking **on**, so it needs `max_tokens`
headroom the current workers deliberately avoid.

**It depends on the accessibility track and cannot lead it** — but the
dependency is **authored scene design**, not A2's HUD. A2 ships a count
("2 of 3"), which says something is outstanding, not which fact it is.

The A2 floor is the load-bearing decision. `SCENARIOS.md` predicted a
strict extractor and prescribed extractor prompting — and that is the
mitigation that failed, because the partner is asked in one call to
withhold answers *and* to annotate slots. Deterministic logic gets a
failing test first; a `live` eval set is a weather report, not a gate.

Three things about that floor are decided, not open. It reads
`user_reading` on the text path and the **STT transcript** on the spoken
path — `SpokenConversationResult` drops `user_reading` deliberately, so a
floor gated on it would not run where the learner actually is. It matches
**content tokens** from `expressible_with`, since all-tokens fails
`我叫小明` and any-token fires on a bare `我`. And it runs on the turn
path **before `termination.advance`**, never on the verdict path, or the
HUD and the verdict can disagree.

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
