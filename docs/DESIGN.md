# Convo Agent — Design Specification

## Project Purpose

A Mandarin conversation practice tool for a single learner who is working
through beginner lessons (HelloChinese-style, HSK 3.0 bands 1–2) and wants to
*apply* what each lesson taught in a real, spoken back-and-forth.

You speak; an AI conversation partner replies in text (汉字 + pinyin). The
system listens with two ears: a forgiving partner that keeps the conversation
flowing, and a silent evaluator that scores your pronunciation and tones and
feeds back coaching periodically.

The mental model is a **knowledge base per topic**: each topic (greetings,
shopping, weather…) is a small, version-controlled wiki of vocab, grammar, and
example dialogues. A conversation is the *applied form* of that knowledge base —
generated from it, scored against it.

---

## Locked Architecture & Tech Stack

| Component | Choice | Rationale |
|---|---|---|
| **Platform** | Mobile-first responsive web (PWA), publicly hosted | Primary use is on a phone; no app store needed |
| **Interaction** | Push-to-talk, **speech in → text out** | Input must be spoken (the whole point); text reply keeps the pipeline simple. On-demand TTS is a later add, not in the hot path |
| **STT** | Azure Speech-to-Text | Stays in the Azure ecosystem |
| **Pronunciation** | Azure Pronunciation Assessment (PA) | Only production-grade Mandarin tone scoring without building a research system |
| **LLM** | Claude API (Anthropic) | Conversation engine + feedback generation |
| **Backend** | Python 3.9+, FastAPI, **stateless turn proxy** | Holds the API keys, owns the cacheable prefix, persists almost nothing |
| **Storage** | SQLite (aiosqlite) on a persistent volume | Only durable per-user learning state — not transcripts |
| **Knowledge base** | Markdown in the git repo | Human-readable, git-diffable, LLM-maintainable; DB stores only a pointer |
| **Hosting** | One container on Fly.io / Railway | Cheap, HTTPS, persistent volume, reachable from a phone |
| **Curriculum** | HSK 3.0, bands 1–2 | Matches current HelloChinese-style study |

**Design principle: build for one, design for many.** Single user and Chinese
only today, but `user_id` and `language` are first-class (defaulted) columns
everywhere, so adding a second user or language later is data, not a re-architecture.

---

## Interaction Model

```
[mobile mic] → hold-to-talk → record → upload audio blob to backend
        │
backend (stateless proxy):
        ├─ Azure STT  ─┐ (parallel)
        └─ Azure PA   ─┘  → transcript + per-syllable tone scores
        │
        → Conversation worker (Claude) → partner reply (汉字 + pinyin) as TEXT
        │
[client] → render reply in the DM thread, append to local transcript
```

Dropping TTS from the critical path removes the slowest, most failure-prone leg
and an entire Azure surface. A 🔊 "play this line" affordance (on-demand TTS) can
be added later without touching the loop.

---

## Hosting & User State

- **Auth (MVP):** one passcode / bearer token in env → signed session cookie.
  No real account system. This is the *only* seam that changes to go multi-user;
  nothing in the data model does.
- **Deploy:** single container (FastAPI + static PWA) on Fly.io / Railway, with a
  persistent volume for SQLite. The KB markdown is baked into the image at deploy.
- **DB:** SQLite via aiosqlite. Every domain row is `user_id`/`language`-scoped,
  so an eventual move to Postgres / multi-tenant is mechanical.
- **Keys never reach the client.** The Anthropic and Azure keys live server-side.
  "Stateless" means the server doesn't *persist* conversation state — not that the
  client calls Claude directly (see *Stateless Proxy* below).

---

## Knowledge Base Architecture

**Reference KB = version-controlled markdown in the repo. The DB stores only a
pointer + your per-user learning state. Never markdown blobs in the DB.**

```
kb/
  zh/
    index.md                     # catalog: every topic, one-line summary
    greetings/
      topic.md                   # frontmatter + overview, HSK band, cross-links
      vocab.md                   # 你好 / 您好 / 再见 … pinyin + gloss
      grammar.md                 # patterns in scope
      dialogues.md               # 2–4 seed exchanges (feed the sketch)
    family/ …
    shopping/ …
```

`topic.md` frontmatter drives the app:

```yaml
id: family
display_name: "Family (家人)"
target_vocab: [家人, 妈妈, 爸爸, 哥哥, 姐姐, 几, 口]
related: [greetings, self-intro]
```

A topic does **not** declare an HSK band. The band ceiling ("what vocab is fair
game") is a property of the *learner*, not the topic — it's universal
(`config.HSK_BAND_CEILING`) and applies to every topic at once. A topic's own
highest band is *derived* from its vocab (for ordering/gating), not authored.
The ceiling lives in `kb/zh/_hsk/ceiling.json`, owned by the KB authoring
workflow and consumed by `config.HSK_BAND_CEILING` (tooling → service, not the
reverse).

- **DB row** = `topic_id → kb_path` pointer + `content_hash` (to detect when the
  committed KB changed). Not the content.
- **Read path:** at session start the orchestrator loads only the *active* topic
  markdown (vocab + grammar + dialogues) and folds it into the cached system
  prefix. The conversation is generated *from* the KB — its applied form.
- **Write path (deferred, designed-for):** feedback currently writes only to the
  proficiency profile. The layout reserves `kb/zh/_users/<id>/<topic>/mistakes.md`
  for a future per-user wiki of persistent errors and hard-won vocab (the LLM
  maintaining it, Karpathy-style). Not built in MVP.

### Generating the seed KBs

HSK 3.0 bands 1–2 vocabulary, beginner grammar points, and topic groupings are
public, well-documented material. Structure / grammar / dialogues are drafted from
model knowledge; **`vocab.md` word lists are verified against an authoritative
public HSK 3.0 list** rather than recalled from memory, to avoid band drift.

---

## Topic Selection — Accumulating, Weighted by Recency + Weakness

Topics are an **accumulating covered-set**, not a linear track.

- Topics start **locked**. You mark one **covered** as you finish the matching
  lesson(s). Covered is monotonic — every covered topic stays fair game forever.
- Two timestamps that pull in opposite directions:
  - `covered_at` — when you *learned* it. Recently learned ⇒ practice **more**
    (the "drill the lessons I just did" case).
  - `last_practiced` — when you last *drilled* it here. Stale ⇒ practice **more**.
- Plus `derived_strength` (weakness).

Session selection weight:

```
weight(topic) =
    w_weak  · (1 − derived_strength)      # weaker → more
  + w_fresh · freshness(covered_at)       # recently LEARNED → more (decays ~2 wks)
  + w_stale · staleness(last_practiced)   # not drilled lately → more
  + w_focus · pinned_this_session         # explicit "practice these" override
```

The `freshness` term makes "I did 3 lessons today, drill *these*" work without
micromanagement: newly-covered topics get a temporary boost that fades, so right
after marking them they dominate the draw while older covered topics linger at
lower weight (accumulation). On top of that, an explicit **per-session "focus"
pin** is the direct lever ("I just finished 购物 and 天气 — weight them hard").

Covered-set, `covered_at`, `last_practiced`, and strengths are the durable
per-user state that *does* live on the server.

---

## Agent Architecture — Orchestrator + Workers + Prompt Caching

The orchestrator is **plain Python, not an LLM**. It coordinates narrow,
individually cacheable Claude calls so context and cost stay bounded.

```
                         ┌───────────────────────────────────────┐
   audio ──► STT + PA ──►│              ORCHESTRATOR              │
                         │ (assembles requests, routing, caching, │
                         │  derives turn index, drives bounding)  │
                         └───┬───────────────┬──────────────┬────┘
              session start  │      per turn │   every N turns│
                             ▼               ▼                ▼
                    ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
                    │  Sketch (1×) │ │ Conversation │ │   Feedback   │
                    │  Sonnet      │ │   worker     │ │   worker     │
                    │              │ │  Sonnet 4.6  │ │ Sonnet / Opus│
                    └──────────────┘ └──────────────┘ └──────────────┘
```

### Stateless proxy + client-held history

The Messages API is stateless — you send full history each turn. So:

- **The client owns the running transcript** (visible turns + PA scores +
  per-turn annotations) in `localStorage`, and resubmits it each turn ending with
  the latest user utterance.
- **The server is a stateless proxy.** Per turn it re-injects the stable cached
  prefix it owns, appends the client-supplied dialogue, calls Azure + Claude,
  returns the result — and persists nothing about the conversation itself.
- This eliminates server-side transcript storage and makes any heavier
  context-compaction machinery unnecessary for MVP (sessions are short).

### The cacheable prefix (where prompt caching pays)

Per session the orchestrator builds one **stable prefix** and marks it with a
`cache_control` breakpoint:

```
[ system role + forgiveness rules ]              ← frozen, never interpolated
[ active topic KB: vocab + grammar + dialogues ] ← the big token chunk
[ conversation sketch for this session ]         ← generated once at session start
        ⤷ cache_control: {type: "ephemeral"}      (breakpoint #1)
--- volatile, after the breakpoint ---
[ client-supplied dialogue so far ]
[ this turn: transcript + PA scores + phase hint ]
        ⤷ cache_control on the latest turn block  (breakpoint #2, incremental)
```

Rules that protect the cache:

- **Topic KB is the expensive payload** and is byte-identical across every turn of
  a session → cache reads at ~0.1× instead of full price. On Sonnet 4.6 the
  2048-token minimum cacheable prefix is easily cleared by a topic's
  vocab + grammar + dialogues. (Min prefix is 4096 on Haiku/Opus.)
- **Keep the system prompt byte-frozen** — no `user_id`, `datetime.now()`, or
  per-turn flags interpolated into it; any of those bust the whole prefix. Per-turn
  data goes *after* the breakpoint.
- **Don't change the tool set or model mid-session** — both are model-scoped cache
  invalidators. Pick the model per session and hold it.
- Turns are seconds–minutes apart, so the 5-minute ephemeral TTL stays warm; bump
  a resumed session to `ttl: "1h"`.
- The second breakpoint on the latest turn gives incremental multi-turn caching.
  Mind the **20-block lookback** — a turn that appends many blocks may need an
  intermediate breakpoint. Max 4 breakpoints per request.

### Role-scoped inputs (cost + clarity)

- The **feedback worker never sees raw transcript** — only the accumulated compact
  annotations (coherence tag, grammar notes, tone errors, topic tags) the client
  sends up. Cheaper and clearer.
- Only the active topic(s) enter context, never the whole `kb/` corpus.

### Structured outputs

The conversation worker returns validated JSON (`output_config.format`):

```json
{
  "partner_response": {"zh": "你今天怎么样？", "pinyin": "nǐ jīntiān zěnmeyàng?"},
  "turn_annotation": {
    "coherence": "on_track",
    "grammar_notes": [],
    "tone_errors": [{"syllable": "ma", "expected": 3, "said": 1}],
    "topic_tags": ["greetings"]
  },
  "should_give_feedback": false
}
```

### Model recommendation (per role)

For a per-turn, latency- and cost-sensitive loop, tier the models rather than
running Opus everywhere:

| Role | Frequency | Recommended | Model ID | Why |
|---|---|---|---|---|
| Conversation worker | every turn | **Sonnet 4.6** ($3/$15) | `claude-sonnet-4-6` | Fast, cheap, plenty for HSK 1–2 partner dialogue |
| Feedback worker | every N turns | **Sonnet 4.6**, or **Opus 4.8** for richer coaching | `claude-sonnet-4-6` / `claude-opus-4-8` | Infrequent; quality > cost |
| Sketch generator | once per session | Sonnet 4.6 | `claude-sonnet-4-6` | One-off |

Opus 4.8 ($5/$25) everywhere is the safe-quality fallback and works with all the
caching above; it just costs more per turn (and has a 4096-token min cache prefix).
Use adaptive thinking; keep effort modest on the hot-path conversation turn.

---

## Dual-Path Processing (Forgiveness vs. Evaluation)

Standard STT does contextual disambiguation — say "mā" with the wrong tone in a
sentence where 妈 is the only plausible word and it transcribes 妈 anyway, hiding
the mistake. So the partner and the evaluator operate on different representations:

- **Conversation path** uses the best-guess STT transcript. The partner
  "understands" you the way a patient relative would, filling gaps from context.
  A `forgiveness_level` parameter (default 0.8 = patient) in the cached system
  prompt governs when to notice errors vs. let them slide; only truly
  unintelligible / derailing input triggers a gentle "对不起，你能再说一次吗？"
- **Evaluation path** uses Azure PA scores (per-syllable tone correctness via a
  two-pass approach: STT transcribes, PA assesses against that transcript). These
  are logged silently in the turn annotation and surfaced during feedback rounds.

**Expected vs. actual tone (tracked for Phase 3b).** The PA evaluation path
yields, per syllable, the tone the learner *should* have produced and the tone
they actually produced. **Phase 3b** populates `turn_annotation.tone_errors`
(`{syllable, expected, said}`) from those scores and **surfaces expected-vs-actual
in the frontend** — so the learner sees what their pronunciation actually sounded
like, next to the target, even though the conversation path "interpreted" the
most likely word and may have hidden the slip. In Phase 3a (text-only) there is
no audio, so `tone_errors` is wired through the contract but always empty.

---

## Conversation Bounding & Session Lifecycle

A session has an arc and a clean end. Two thresholds set in the sketch:

```
target_turns ≈ 10   # soft — begin steering toward goodbye
max_turns    ≈ 16   # hard — force the closing turn
```

(A "turn" = one user utterance + one partner reply.)

Enforcement, caching-safe:

- The stateless server derives the **turn index from the length of the submitted
  history** — no server counter.
- The orchestrator injects a **phase hint** *after* the cache breakpoint (volatile,
  so the stable prefix keeps hitting):
  - `turn ≥ target_turns − 2` → "begin wrapping up; steer toward 再见."
  - `turn ≥ max_turns` → "final turn; say goodbye." Worker sets
    `should_give_feedback: true`; session marked **complete**.

```
active ──(turn ≥ target−2)──► wrapping ──(turn ≥ max, or partner says goodbye)──► complete
```

On **complete**, the client disables the mic, runs the final feedback round, and
the proficiency writeback happens — then offers "Start new session." Every session
closes with feedback and a progress update rather than trailing off.

---

## Redo (Client-Side)

Because the server never stores turns, redo is purely client-side:

- **Redo a turn:** the client truncates its local log to just before that turn and
  resubmits from the audio step. The stable prefix still cache-hits (KB/sketch
  unchanged), so the redo is cheap to serve.
- **Redo the conversation:** the client clears its log and asks the server for a
  fresh sketch.
- A `parent_turn_id` field in the *client* log reserves room to branch instead of
  overwrite later. MVP overwrites.

---

## Conversation UI — DM Thread, Mobile-First

```
┌─────────────────────────┐
│  Family (家人)      ⋯    │   ← topic header, session menu (redo / restart)
├─────────────────────────┤
│  阿姨                    │
│  你今天怎么样？           │   ← partner bubble (left)
│  nǐ jīntiān zěnmeyàng?  │     pinyin toggle
│                         │
│            我很好，谢谢   │   ← your bubble (right): STT transcript
│         🔁 redo  ⚠️ tone │     per-turn affordances
├─────────────────────────┤
│  〔 hold to talk 🎙 〕    │   ← push-to-talk
└─────────────────────────┘
```

- Ongoing-DM layout; partner left, you right.
- Your bubble shows the STT transcript; a subtle tone/grammar marker appears when
  the eval path flagged something. Full detail lands in the periodic feedback card,
  rendered inline as a system bubble every N turns and at session end.
- Per-bubble redo. Pinyin toggle. Responsive PWA (installs to the home screen).

---

## Error Surfacing (Light, Not Defensive)

No circuit-breakers or custom retry layers — the Anthropic SDK already retries
429/5xx with backoff. The work is surfacing, in two buckets:

| Bucket | Examples | UX |
|---|---|---|
| **Transient** (auto-retrying) | Claude 429/529, Azure timeout | Inline "…retrying" on the pending bubble; resolves or escalates |
| **Terminal** (needs you) | STT returned nothing, hard failure after retries | Inline system bubble: "Didn't catch that — tap to retry" |

The backend returns a typed `{category, user_message}`; the client maps it to a
bucket. **The local log is never mutated on failure**, so every error is
non-destructive — retry just resubmits the same client-held history (idempotent,
cached prefix still hits). A separate operator-facing banner covers misconfiguration
(bad Anthropic/Azure key at deploy).

---

## Data Model (SQLite, all `user_id`/`language`-scoped)

The server persists durable learning state only — **no transcripts, no turns.**

```sql
user(id, handle, created_at)

topic(id, language, kb_path, content_hash, max_band)
      -- pointer to kb/<language>/<id>/, NOT the content.
      -- max_band is DERIVED from the topic's vocab (highest HSK band used), for
      -- ordering/gating; the fair-game ceiling itself is universal, not per-topic.

covered_topic(user_id, language, topic_id, covered_at)   -- accumulating set

proficiency(user_id, language, topic_id,
            measured_scores_json, derived_strength, last_practiced)
            -- the only writeback (periodic + end-of-session feedback rounds)

session_summary(id, user_id, language, topics_json, started_at,
                turn_count, aggregate_scores_json)        -- lightweight, for trends
```

The conversation transcript lives in the client's `localStorage`.

---

## Planned Project Structure

```
convo-agent/
├── backend/
│   ├── main.py            # FastAPI, auth gate, static serving
│   ├── orchestrator.py    # turn coordination, context assembly, caching, bounding
│   ├── workers/
│   │   ├── conversation.py  # Claude conversation worker (cached prefix)
│   │   ├── feedback.py      # annotations → feedback + proficiency deltas
│   │   └── sketch.py        # session sketch generation
│   ├── speech/
│   │   ├── stt.py           # Azure STT
│   │   └── pronunciation.py # Azure PA (two-pass)
│   ├── kb.py              # load topic markdown, parse frontmatter
│   ├── profile.py         # covered-set + proficiency CRUD + selection weighting
│   ├── models.py          # Pydantic
│   ├── db.py              # aiosqlite
│   └── config.py          # env-based keys/config
├── kb/zh/                 # the knowledge base (git-versioned markdown)
│   ├── index.md
│   └── <topic>/{topic,vocab,grammar,dialogues}.md
├── frontend/             # mobile-first PWA (DM thread, push-to-talk, localStorage)
├── schema.sql
├── docs/DESIGN.md        # this file
└── README.md
```

---

## End-to-End Scenarios

Notation: **[C]** client · **[S]** stateless backend · **[Claude]** · **[Azure]**.
🟢 = cache hit · 💾 = the only server-persisted writes.

### Happy path — a practice turn

1. **[C]** Open PWA; request covered-set + proficiency. **[S]** reads 💾 store, returns it.
2. **[C]** "New session," pin 购物 + 天气. Send `{topics, pinned}` to **[S]**.
3. **[S]** computes the weighted draw, loads active topic KBs, calls **[Claude]** once
   for the **sketch**; returns sketch + opening line *"你好！你今天买东西了吗？"*. Persists nothing.
4. **[C]** Render partner bubble; hold sketch + turn locally.
5. **[C]** Hold-to-talk: *"我买了一些水果。"* Upload audio to **[S]**.
6. **[S]** **[Azure STT]** + **[Azure PA]** in parallel → transcript + tone scores.
7. **[S]** assemble request: stable prefix `[system + KB + sketch]` 🟢 + client dialogue
   + this turn `[transcript + PA + phase hint]`.
8. **[Claude]** (Sonnet 4.6) → JSON: `partner_response`, `turn_annotation`,
   `should_give_feedback: false`.
9. **[S]** return `{partner_response, annotation}`. Persist nothing.
10. **[C]** Append partner + your bubbles (and the annotation) to the local log. Loop to 5.
11. Every 3rd turn: **[S]** separate **[Claude]** feedback call fed only accumulated
    annotations → feedback card + proficiency deltas.
12. **[C]** Render feedback inline. **[S]** writes 💾 proficiency deltas + bumps
    `last_practiced`. *The only durable writes.*

### Critical A — unintelligible / off-topic input

5–7 as above, but STT is garbled and PA sparse. **[Claude]**, per the forgiveness rules
in the cached prefix, returns `"对不起，你能再说一次吗？"`, `coherence: "derailing"`. Client
shows the gentle re-ask; your retry re-enters at step 5. Nothing persisted.

### Critical B — wrong tone, right word in context (dual-path)

You say *mā* where 马 (mǎ) is needed but context forces 马. **[Azure STT]** transcribes
马; **[Azure PA]** flags `ma` expected-3 / said-1. The conversation path keeps flowing
(partner replies naturally); the evaluation path records the tone error silently. At the
feedback boundary it surfaces ("watch your 3rd tone on 马") and 购物's pronunciation score
takes the hit 💾. This is the point of splitting forgiving STT from honest PA.

### Critical C — redo a turn (client-only)

Tap 🔁. **[C]** truncates its local log to just before that turn — no server call, since
the server stored nothing. Re-record and resubmit from step 5; the stable prefix still
cache-hits 🟢, so the redo is cheap.

### Critical D — bounded session close

As `turn` approaches `target_turns`, **[S]** injects a wrap-up phase hint (post-breakpoint,
cache intact); the partner steers toward 再见. At `max_turns` the hint forces a goodbye and
`should_give_feedback: true`. **[C]** marks the session **complete**, disables the mic, runs
the final feedback round, **[S]** writes 💾 proficiency + a `session_summary`, and "Start new
session" is offered.

### Edge — dependency failure

STT/PA or Claude failure → **[S]** returns a typed error; **[C]** shows the transient or
terminal affordance and keeps the local log intact (retry is idempotent). Tab closed
mid-session loses only the in-flight chat (localStorage may hold it for resume); proficiency
from completed feedback rounds is already 💾, so learning progress survives.

---

## MVP Scope

### Included

- Speech-in / text-out turn loop (Azure STT + PA → Claude conversation → text).
- Orchestrator + cached conversation worker (Sonnet 4.6) with structured output.
- Stateless proxy; client holds transcript in `localStorage`.
- 10 HSK 3.0 (bands 1–2) topics authored as markdown KBs.
- Accumulating covered-set with recency + weakness + focus weighting.
- Feedback every 3 turns and at session end → proficiency writeback.
- Bounded sessions (`target`/`max` turns) with a clean close.
- Client-side turn redo.
- DM-style mobile PWA with push-to-talk.
- Passcode auth; deployed to Fly/Railway, reachable from a phone.
- Two-bucket error surfacing.

### Deferred (designed-for)

- On-demand TTS for partner lines.
- Per-user KB writeback (LLM-maintained mistake/vocab wiki).
- Conversation-level redo / branching UI.
- Proficiency charts / EMA decay (start with simple averages).
- Tunable forgiveness level (hardcoded 0.8 first).
- Multi-user accounts; second language.
- Topic-generator skill (hand-author markdown until the cadence hurts).

### Build Order — walking skeleton

Built as a **walking skeleton**: a thin end-to-end slice (audio in → text out)
runs first, then each integration is deepened *in place*. Parts not yet built are
hardcoded so the app stays runnable and demoable every phase, and each phase adds
one **user-visible** capability (the "supported" line). This refines — and
supersedes — an earlier horizontal order (KB → worker → speech → …) that wasn't
demoable until late. Per-phase how-to-validate steps live in the README's
**Try it yourself** section (updated in place each phase, never appended).

| Phase | Adds | Supported (visible) |
| --- | --- | --- |
| 0 | `GET /api/hello` + static page (FastAPI static mount) | Page round-trips a string through the backend. |
| 1 | Push-to-talk upload → Azure STT; hardcoded 你好 reply | Speak → see your words transcribed + a fixed 汉字 reply. |
| 2 | Azure PA in parallel with STT (two-pass) | …plus per-syllable tone scores. |
| 3a | `kb.py` + conversation worker, **text-only**; cached prefix (opening line hardcoded) | Text in → real Claude greeting reply + annotation; **cache hits proven**. |
| 3b | Wire speech (2) into the worker (3a); populate `turn_annotation.tone_errors` from PA (expected vs. actual per syllable) and surface expected-vs-actual in the frontend | Speak → real Mandarin partner reply + per-syllable tone scores showing **expected vs. actual** tone (greetings, 1 turn). |
| 4 | Multi-turn (client-held transcript, ~3 turns) + feedback worker (in-session text, **no persistence**) | A short greetings exchange with coaching feedback. |
| 5 | Full bounding (`active→wrapping→complete`, phase hints) + sketch worker (replaces hardcoded opening) | A complete, naturally-bounded session start→goodbye. |
| 6 | Turn redo (client-side; server stays stateless) | Redo a turn / redo the conversation. |
| 7 | `db.py` + `profile.py`: covered-set + proficiency writeback + weighting | Progress persists across sessions. |
| 8 | Passcode auth gate + DM PWA + deploy | Real session, gated, on a phone. |

Phase 3 is split (3a text-only worker + cache proof, then 3b speech wiring)
because it bundles the KB loader, cached-prefix invariant, opening line, and
structured output — too much to land or test at once. Durable state (DB,
proficiency, covered-set) is deferred to Phase 7; "feedback" before then is
in-session text only.

**Deferred UX polish (Phase 2).** Add a live **mic-level meter** during
push-to-talk recording — right now the only "is it capturing?" feedback is the
button turning red. A natural fold-in when Phase 2 reshapes the recording UI for
tone scores. Cheap and client-only: tap an `AnalyserNode` off the existing Web
Audio source (or reuse the Float32 frames already collected in `index.html`),
animate a bar on `requestAnimationFrame`, and reset it on release. The meter is
for the *recording* window only; the post-release transcribe wait keeps the
existing "transcribing…" status.

---

## Technical Risks & Mitigations

1. **Azure PA reference-text problem** — PA scores against a known reference. Mitigation:
   two-pass (STT transcribes, PA assesses against that transcript); per-syllable tone
   correctness is still reported.
2. **Latency** — STT + PA + LLM in sequence. Mitigation: run STT and PA in parallel; stream
   the conversation reply; modest effort on the hot-path turn. Target < 3s.
3. **Cache misses** — a silent invalidator (timestamp/UUID in the system prompt, varying
   tool set, mid-session model switch) drops the cache to full price. Mitigation: keep the
   prefix byte-frozen, verify `cache_read_input_tokens > 0` in testing.
4. **Sketch rigidity vs. uselessness** — too detailed = a script; too vague = no coherence
   judgment. Mitigation: loose sketches (phase labels + topic pools), tuned from real use.
5. **Beginner disfluency** — long pauses and false starts can confuse Azure PA. Mitigation:
   keep early utterances short (partner asks simple questions expecting 2–4 word answers),
   ramp complexity gradually.
