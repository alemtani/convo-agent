# Convo Agent

A Mandarin conversation tutor for one beginner (HSK 3.0, bands 1–2). The
learner gets a situation and a goal, speaks or types pinyin, and an AI partner
replies in 汉字 + pinyin. A **separate** AI grades whether they earned each
goal slot. Python decides pass/fail. The session ends when the goal is met,
the turn cap is hit, the learner says goodbye twice, or they tap "I'm stuck."

<!-- TODO: phone screenshot of a live session + the verdict card -->

Each topic is a markdown knowledge base (vocab, grammar, dialogues). A
conversation is the applied form of that KB — generated from it, scored
against it. The goal is a set of named binary slots, not a model's opinion.

Spoken and typed, end to end, as a mobile-first PWA. Five topics, all
scenario-ready: greetings, self-intro, family, numbers-money, food-ordering.

## How practice works

A session puts you in a situation with a gap: no menu, no prices, a stranger
who has not offered their name. The partner is the other person in that
scene. They do not know what you are trying to accomplish, and they will
not volunteer the facts you are supposed to extract. You have to ask, the
way you would if you were actually there.

A flashcard with a situation attached is not practice. This is.

### The partner does not know the goal

A partner handed the slot list will take a question about dishes as an answer
to a question about drinks, because it can see the checkbox behind it. So it
is no longer told.

`load_converser_block` (`backend/kb.py`) structurally omits the goal and the
slot graph. The converser gets only `# SCENE` — the situation, and authored
withholding prose. The grader (`backend/workers/grader.py`) gets the rubric as
Python data and never touches that string block. Two subtractions enforce it:
the scene renderer never emits `goal` or slots, and author notes written in
rubric terms are stripped before they reach the partner. A test asserts the
converser's output schema cannot carry `slots_filled`.

> A server with no menu is a fact about a restaurant; "do not reveal
> `recommendation` until asked" is a fact about a test.

### You either did the thing or you didn't

`backend/termination.py` is pure — no I/O, no clock, no model call. Slot fill
is set comparison against the authored rubric. The verdict worker
(`backend/workers/feedback.py`) recomputes `goal_met` server-side and will
correct a `goal` or `cap` `end_reason` that does not square with the slots;
the rest are trusted, not verified. The model writes the explanation; it
does not get a vote.

## How a turn runs

The server is a stateless proxy. It stores no transcript. The client
resubmits `dialogue`, `sketch`, and `state` every turn.

```
mic → STT → transcript
        ├─ Azure pronunciation assessment → score
        ├─ Claude converser  (goal-blind)  → reply
        └─ Claude grader     (sees rubric) → state
                                             done
```

PA, converser, and grader start together. Events emit as they resolve, except
`state`, which is held until `reply` has been sent: the client commits the
turn to `dialogue` on `reply`, so a credited slot must not outlive a failed
converser.

The typed path is serial on purpose — the grader is fed the converser's 汉字
rendering of the pinyin, so the learner's bubble and their grade describe the
same sentence.

```
max_turns = n_slots + n_request_slots + 2
```

A `request` slot fills when the learner asks, whether or not the partner
answered. Scene withholding is authored prose, not a per-turn hint.

## Evaluating the agent

The interesting failure is not "the model said something wrong." It is
**unearned credit** — the partner treating a non-sequitur as a slot fill
because it could see the checkbox. The unit of measurement is slot accuracy,
split so one number cannot hide which failure it is:

- **spurious** — credit the learner did not earn
- **missed** — credit the learner earned and was not given

The fixture corpus lives in [`tests/fixtures/sessions/`](tests/fixtures/sessions/)
(7 recorded turns). Gold labels are kept in a separate file from the
transcripts. A second-opinion label set exists; that labeller had repo access,
so it is corroboration, not independence.

The grader-only harness ([`evals/coherence/replay.py`](evals/coherence/replay.py))
holds the partner still. The turn harness ([`evals/turn/replay.py`](evals/turn/replay.py))
drives `orchestrator.run_text_turn`, so one cassette-backed run covers the
reply, the grade computed against that reply, and whether that reply gave
away a `request` slot. Both replay off committed cassettes; a key miss
fails CI. Nothing in `pytest -q` spends tokens.

A measurement that killed a feature: V0 measured the converser's `coherence`
tag against gold and found no threshold that could gate on it safely. No gate
shipped. See [`docs/VALIDITY.md`](docs/VALIDITY.md) and
[`evals/coherence/`](evals/coherence/).

**In flight.** V2 moved grading onto a dedicated worker. The matrix has to be
re-run against the grader before the same conclusions hold. The corpus also
cannot yet demonstrate the under-annotation floor earning its keep — that
needs a case drawn from a real session, not one written from imagination.

## Architecture

- **FastAPI** backend serving the API and the PWA (`frontend/`)
- **Anthropic Claude** — partner and sketch on Sonnet 5, thinking off (hot
  path); grader and verdict on Opus 5, thinking on (verdict at high effort).
  Separate cache prefixes; usage tracked separately, because the prices differ.
- **Azure Speech** — STT, pronunciation assessment, on-demand TTS
- **Client-held session state** in `localStorage` — the server is a
  stateless turn proxy
- Durable learning state (SQLite) is designed, not built. `schema.sql`
  exists; nothing reads it. See [`AGENTS.md`](AGENTS.md).
- CORS also allows `http://localhost:3000` for a separately-hosted frontend

576 tests in the default gate; 105 more sit behind `live` / `smoke` markers
(real keys, or Playwright).

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

   **Getting an Azure Speech key:** in the [Azure portal](https://portal.azure.com),
   create a **Speech** resource (Free **F0** tier is plenty), region **East US**
   (matches the `eastus` default in `backend/config.py`). Then under
   **Keys and Endpoint**, copy **KEY 1** → `AZURE_SPEECH_KEY` and the
   **Region** → `AZURE_SPEECH_REGION`. A spoken session also needs
   `ANTHROPIC_API_KEY` — the partner, the opening line, the grader, and the
   verdict all call Claude.

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

<details>
<summary>Deploying (Fly.io)</summary>

The mic needs a secure context, so this is what makes the app reachable from a
phone at all. `Dockerfile` and `fly.toml` are checked in; `fly launch` and
`fly secrets set` touch your Fly account and are run by hand, not by CI.

1. **Install flyctl and sign in** — see
   [fly.io/docs/flyctl/install](https://fly.io/docs/flyctl/install/), then:

   ```bash
   fly auth login
   ```

2. **Create the app** (this repo's `fly.toml` already has the rest of the
   config — region, health check, no volume):

   ```bash
   fly apps create <your-app-name>
   ```

   Edit `app = "convo-agent"` in `fly.toml` to `<your-app-name>` if you picked
   something else.

3. **Set secrets** — same four keys as `.env`, never committed:

   ```bash
   fly secrets set \
     APP_PASSCODE=<a-passcode-only-you-know> \
     ANTHROPIC_API_KEY=sk-ant-... \
     AZURE_SPEECH_KEY=your-azure-speech-key \
     AZURE_SPEECH_REGION=eastus
   ```

   `APP_PASSCODE` is the one that matters most here: without it the deploy is
   open to anyone who finds the hostname (see [The passcode gate](#the-passcode-gate)).

4. **Deploy:**

   ```bash
   fly deploy
   ```

   After the first manual deploy you can hand this to CI. Merges to `main`
   deploy automatically once one repo secret is set — see
   [Automatic deploys](#automatic-deploys) below.

5. **Verify the gate is actually on** — this is the release blocker check:

   ```bash
   curl https://<your-app-name>.fly.dev/health
   # -> {"status":"ok","auth":"enabled"}       ← "disabled" means stop and fix secrets
   ```

6. Open `https://<your-app-name>.fly.dev` on your phone, log in with the
   passcode, and confirm a spoken turn completes end to end.

No persistent volume is configured — SQLite/durable learning state is Phase 7,
not built, so every deploy is stateless on disk. The KB markdown under `kb/zh/`
is baked into the image at build time, not mounted.

### Automatic deploys

Merges to `main` deploy to Fly automatically, from the `deploy` job in
[`.github/workflows/ci.yml`](.github/workflows/ci.yml). It runs only after both
test jobs pass, and only on a push to `main` — a pull-request build never
reaches production.

One-time setup, run from a checkout with `flyctl` signed in:

```bash
fly tokens create deploy -x 999999h        # a deploy-scoped token, not your login
gh secret set FLY_API_TOKEN                # paste the token (the whole FlyV1 ... string)
```

The token is scoped to deploying this one app. It is the only credential CI
holds; `fly launch`, `fly apps create` and `fly secrets set` stay manual,
because they create resources and handle the API keys.

After deploying, the job re-runs the `/health` gate check from step 5 above and
fails the build if `auth` comes back `disabled`. That failure mode is worth
automating precisely because it fails *open*: the app serves normally, so the
only symptom is an unauthenticated endpoint spending your Anthropic and Azure
quota on a public hostname.

</details>

## Try it yourself

The server draws one of five topics, pins an English situation and goal above
the thread, and plays a partner who will not volunteer the facts you are
supposed to extract. You have a derived turn cap. The session ends with a
verdict: did you hit the goal, and what should you have said.

**Needs both keys** — Azure for speech, Anthropic for the partner, the
opening line, the grader, and the verdict.

1. Set `ANTHROPIC_API_KEY`, `AZURE_SPEECH_KEY`, and `AZURE_SPEECH_REGION`
   in `.env` (see [Setup](#setup)).

2. Start the server:

   ```bash
   source .venv/bin/activate
   uvicorn backend.main:app --reload --port 8000
   ```

3. Open **http://localhost:8000/** in your browser (use `localhost`, not a LAN
   IP — the mic needs a secure context, and `localhost` counts as one).
4. Read the scenario card. The partner speaks first. That line does not
   spend a turn.
5. **Hold** *Hold to talk* — a live mic-level meter fills as you speak —
   answer in Mandarin, and release. Or tap the keyboard and type pinyin
   (`ni hao` or `ni3hao3`). The scenario card shows how many parts of the
   goal you have done, and how many turns you have used.
6. Your words appear with per-syllable tone underlines (green / amber / red)
   on the spoken path; the partner replies in 汉字 + pinyin. You can tap to
   hear a reply. The session ends when you fill every goal slot, you hit the
   turn cap, you say goodbye twice, or you tap *I'm stuck*. A verdict card
   explains the outcome. *Try this again* replays the same topic; *Try
   something else* draws a fresh one.

   No speech detected replies 请再说一次 and does not spend a turn. If
   scoring fails, the turn still shows your transcript, just without tone
   colors.

<details>
<summary>Prefer the command line?</summary>

```bash
curl http://localhost:8000/health
# -> {"status":"ok","auth":"disabled"}     locally; "enabled" on a public host

curl -s -X POST http://localhost:8000/api/session
# -> topic_id, display_name, scenario_card, opening_line, sketch
```

A spoken turn is an NDJSON stream (`transcript` → `score` ∥ `reply` ∥
`state` → `done`), not one JSON object. Use `scripts/replay.py` to measure
latency, or `scripts/walk_scenario.py` to prove a topic is winnable.
Both spend real quota.
</details>

## Measuring turn latency

Every turn reports what it cost. The server logs one line per turn and returns
the same numbers on the response, so the page, the log, and the replay harness
never disagree about how long something took:

```
turn timings mode=audio stt=1103ms pa=884ms claude=2612ms grader=1840ms total=3721ms cache_read=5120 …
```

The thread shows a quiet line under each exchange — round trip, server total,
each stage, and `cache_read` tokens. On the spoken path PA, Claude, and the
grader run concurrently, so `stt + max(pa, claude, grader)` is the critical
path and the stages deliberately sum to more than the total.

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

- **Assisted:** invoke the `kb-topic` skill (Claude Code, OpenCode, or anything
  else that loads `.claude/skills/kb-topic/`). It drafts/edits the files, runs
  validation, and opens a PR. See `.claude/skills/kb-topic/SKILL.md` for the
  workflow and the authoring rules it enforces.
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

## Project status

The walking-skeleton phases live in
[`docs/DESIGN.md`](docs/DESIGN.md#build-order--walking-skeleton).
[`AGENTS.md`](AGENTS.md) is the short map of what is live.

| | Status |
|---|---|
| Spoken + typed loop, five topics, verdict card | Shipped |
| Passcode gate, Fly.io, CI deploy on `main` (M1) | Shipped |
| Goal-blind converser + dedicated grader (V2) | Shipped |
| `coherence` measured against gold; no gate (V0) | Shipped |
| "I'm stuck" exit + retry card (A1) | Shipped |
| On-demand TTS | Shipped |
| Progress HUD — slots filled and turns used (A2) | Shipped |
| Python floor under the tracker, verdict that names facts (A2 remainder) | Open |
| Partner reads `HSK_BAND_CEILING` (C0) | Open |
| Per-turn redo | Not built |
| Durable learning state / weighted topic draw (Phase 7) | Not built |

The next product work is the rest of A2 (floor and verdict copy), C0, or
more topics — not more validity architecture. The next *eval* work is re-running the V0 matrix against the
V2 grader. Detail in [`docs/VALIDITY.md`](docs/VALIDITY.md),
[`docs/ACCESSIBILITY.md`](docs/ACCESSIBILITY.md),
[`docs/CURRICULUM.md`](docs/CURRICULUM.md).
