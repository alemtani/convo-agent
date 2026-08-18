# Convo Agent

A Mandarin conversation practice agent powered by Claude and Azure Speech Services.

## Architecture

- **FastAPI** backend serving the API and the PWA (`frontend/`)
- **Anthropic Claude API** for the partner, the session sketch, and the verdict
- **Azure Speech Services** for speech-to-text, pronunciation assessment, and on-demand TTS
- **Client-held session state** in `localStorage` — the server is a stateless turn proxy
- Durable learning state (SQLite) is designed, not built. See [`AGENTS.md`](AGENTS.md).
- CORS also allows `http://localhost:3000` for a separately-hosted frontend

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
   `ANTHROPIC_API_KEY` — the partner, the opening line, and the verdict
   all call Claude.

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

## Deploying (Fly.io)

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

No persistent volume is configured — SQLite/durable learning state is cut from
MVP scope (Phase 4+), so every deploy is stateless on disk. The KB markdown
under `kb/zh/` is baked into the image at build time, not mounted.

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

## Try it yourself (manual validation)

This section always shows **only what the app does right now** — one walkthrough,
**updated in place** as each phase ships, never an append-only pile of old steps.
Because phases are cumulative (each builds on the last), running the current
walkthrough exercises everything underneath it. The full phase plan lives in
[`docs/DESIGN.md`](docs/DESIGN.md#build-order--walking-skeleton).

### What works today: a bounded scenario, spoken or typed

The server draws one of five topics, pins an English situation and goal above
the thread, and plays a partner who will not volunteer the facts you are
supposed to extract. You have a derived turn cap. The session ends with a
verdict: did you hit the goal, and what should you have said.

**Needs both keys** — Azure for speech, Anthropic for the partner, the
opening line, and the verdict.

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
   (`ni hao` or `ni3hao3`).
6. ✅ **Expected:** your words appear with per-syllable tone underlines
   (green / amber / red) on the spoken path; the partner replies in 汉字 +
   pinyin. You can tap to hear a reply. The session ends when you fill
   every goal slot, you hit the turn cap, or you say goodbye twice. A
   verdict card explains the outcome. *New* draws a fresh topic.

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

A spoken turn is an NDJSON stream (`transcript`, then `score` / `reply`,
then `done`), not one JSON object. Use `scripts/replay.py` to measure
latency, or `scripts/walk_scenario.py` to prove a topic is winnable.
Both spend real quota.
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

The walking-skeleton phases live in
[`docs/DESIGN.md`](docs/DESIGN.md#build-order--walking-skeleton).
[`AGENTS.md`](AGENTS.md) is the short map of what is live.

- ✅ **Phases 0–3b** — page, spoken loop, pronunciation underlines, Claude
  partner with a cached prefix, multi-turn history on the client.
- ✅ **M1** — passcode gate, Fly.io deploy, CI deploy on `main`, phone
  AudioContext unlock.
- ✅ **M2** — authored scenario slots, sketch worker, slot tracker,
  state-driven end conditions, verdict card. Five topics. Topic catalog
  (`GET /api/topics`); session start still draws the topic.
- ✅ **M4** — on-demand TTS, beside the loop.
- ⏳ **Phase 6** — per-turn redo. Not built. *New* starts a fresh session.
- ⏳ **Phase 7** — `db.py` / `profile.py`, covered-set, proficiency,
  weighted draw. Not built.

In-session coaching every N turns (the original Phase 4 feedback worker)
did not ship. The only coaching card is the end-of-session verdict.

The first real session by the learner it was built for (2026-08-16) found
the loop works and the learner drowns: being stumped has no exit but
guessing or saying goodbye twice. The plan for that is
[`docs/ACCESSIBILITY.md`](docs/ACCESSIBILITY.md) — a next move that always
exists, priced instead of hidden.
