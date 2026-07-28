# Workstreams — post-3b fixes (latency, chat UX, text mode)

Three user-reported problems with the Phase-3b spoken loop, each chartered as an
independent workstream: one session owns the whole arc — investigation → root
cause analysis → fix → branch + PR — per workstream. The findings below come
from an initial code investigation (2026-07) and are the starting RCA, not the
final word: **verify them against the current code before building on them.**

Decisions already made (don't relitigate):

- ~~**WS3 text input is hanzi-only.**~~ **Reversed 2026-07-27.** The learner is a
  beginner who can't reliably type 汉字, so text mode takes **pinyin** and the
  conversation worker reads it — see WS3 below. No pinyin→hanzi converter was
  built: the worker already has the context needed to resolve it.
- The three workstreams run **fully concurrent** on separate branches from
  `main`. See the merge-conflict note at the bottom.
- **Goal-oriented ("targeted convo") sessions are deferred** — design note in
  §4, tackled after WS1–3 land.

---

## WS1 — Turn latency (target: p50 < 3s)

**Problem.** A spoken turn (`POST /api/turn`) takes 5+ seconds wall-clock even
with prompt caching in place.

**Findings so far (starting RCA).**

- The critical path in `backend/orchestrator.py` (`run_audio_turn`) is
  `STT (fully serial) + max(PA, Claude)`. Pronunciation assessment and the
  Claude call already overlap via `asyncio.gather`; **STT overlaps nothing**
  and gates everything downstream (PA needs the transcript as reference text,
  the worker needs it as the user turn).
- Every Azure call constructs fresh SDK objects — `SpeechConfig`,
  `AudioConfig`, `SpeechRecognizer` — plus a new service connection and a
  `NamedTemporaryFile` per request (`backend/speech/_recognizer.py`). Both STT
  and PA use one-shot `recognize_once` wrapped in `asyncio.to_thread`. The
  audio is sent to Azure twice by design (two-pass PA), but the second pass
  overlaps Claude, not STT.
- The Claude call (`backend/workers/conversation.py`) is **non-streaming**
  structured output via `client.messages.parse` (model `claude-sonnet-4-6`,
  `max_tokens=512`). The whole JSON blob is awaited at once, so time-to-first-
  token is fully absorbed into the turn. The cache breakpoint is correctly
  placed after system prompt + KB + sketch (~9 KB prefix — small, so caching
  helps but was never going to fix a multi-second turn).
- **There is zero timing instrumentation.** No timers, no duration logging, no
  middleware; the Anthropic `usage` block (incl. `cache_read_input_tokens`) is
  discarded in the orchestrator, and `TurnResponse` (`backend/models.py`)
  carries no timing metadata.

**Charter.**

1. **Measure first.** Add per-stage timing — STT, PA, Claude, total — logged
   server-side and surfaced to the client (response field or headers), and stop
   discarding `usage`. The bottleneck must be measured, not guessed.
2. **Attack the measured winner.** Candidate fixes, in rough expected-value
   order: reuse/pre-warm the Azure recognizer or connection (kill the
   per-request handshake); feed audio via the SDK's push-stream input instead
   of a temp file; stream the Claude response (or trim `max_tokens`); overlap
   audio upload with STT.
3. **Success criterion:** p50 spoken turn < 3s as measured by the new
   instrumentation, over a handful of real turns.

**Files:** `backend/orchestrator.py`, `backend/speech/*`,
`backend/workers/conversation.py`, `backend/models.py`, `backend/main.py`.
Tests follow the tiered verification rules in `CLAUDE.md` (timing plumbing is
deterministic → TDD; Azure/Claude behavior → contract tests / `live` evals).

---

## WS2 — Genuine chat experience (frontend)

**Problem.** The app doesn't feel like a chat app: the mic lags before
recording starts, the pending state is a faint "transcribing…" status line
instead of a typing-indicator bubble, and the thread doesn't sit at the newest
messages.

**Findings so far.** All in `frontend/index.html` (vanilla JS, single file;
capture worklet in `frontend/recorder-worklet.js`):

- **Mic start lag:** on *every* press, `startRecording()` serially awaits
  `getUserMedia`, constructs a fresh 16 kHz `AudioContext`, and awaits
  `audioWorklet.addModule("/recorder-worklet.js")`. Worse, the `recording`
  flag/red-button flip happens *before* the worklet finishes wiring, so early
  frames can be silently lost. Fix direction: acquire the mic stream +
  `AudioContext` + worklet **once** (at startup or first press) and keep a warm
  graph to reuse; only flip the UI to "recording" once frames actually flow.
- **No typing indicator:** while a turn is pending, the only feedback is
  `setStatus("transcribing…")` in the `#status` div and a disabled button. Fix
  direction: insert a partner-side loading bubble (reuse `addBubble("partner")`
  and the existing `.bubble.partner` CSS; add a small dot-pulse keyframe — none
  exists yet) when the turn is submitted, and replace it in place with the real
  reply (or an error state) when the response lands.
- **Scroll:** the only scroll logic is a smooth `scrollIntoView` inside
  `addBubble`. There's no jump-to-bottom after `restoreThread()` on load (the
  per-bubble smooth scrolls race), no stick-to-bottom behavior, and no
  re-anchoring on resize (mobile keyboard / URL bar with `100dvh`). Fix
  direction: instant (non-smooth) scroll-to-bottom on load; stick-to-bottom
  when the user is already at/near the bottom, leave them alone when they've
  scrolled up.

**Charter.** Make the thread behave like a real DM app: instant mic start,
loading bubble while waiting, bottom-anchored scroll. Client-only; no backend
changes expected. Frontend has no test suite — verify by driving the app
(record a turn, reload mid-history, scroll up then send).

**Files:** `frontend/index.html`, `frontend/recorder-worklet.js`.

---

## WS3 — Text-only mode (hanzi input)

**Problem.** Practicing in public means talking into a phone. There should be a
text-only mode: type instead of speak.

**Findings so far.**

- The backend seam already exists: `POST /api/turn/text` (`backend/main.py` →
  `orchestrator.run_text_turn`) calls the real conversation worker with the
  same KB/sketch/dialogue wiring, skipping STT, PA, and tone-error derivation.
  It's currently documented as a mic-free dev/test harness with a
  `TODO(phase-4)` to either promote or remove it — this workstream resolves
  that TODO by **promoting it**.
- Its response model (`ConversationTurnResponse` in `backend/models.py`) omits
  `transcript` and `pronunciation`. The frontend renders bubbles from
  `{zh, pinyin}`, so the endpoint should return display pinyin for the user's
  typed hanzi (reuse `to_pinyin` in `backend/pinyin.py`) so text turns render
  exactly like spoken ones.
- **Input is pinyin** (decision reversed 2026-07-27 — see above). `pinyin.py` is
  hanzi→pinyin only and can't help: `to_pinyin("nihao")` returns `"nihao"`
  unchanged. Rather than build a converter, the **conversation worker reads the
  pinyin** and returns `user_reading` — the 汉字 it understood — alongside its
  reply. It already holds the conversation and the topic KB, so it resolves
  `ta` → 他/她 from context and handles words outside the topic vocab (names).
- The frontend is currently mic-only — no text box, nothing calls
  `/api/turn/text`.
- Scope limit, and the twist: text turns have no *pronunciation* feedback (PA
  needs audio). But tone feedback gets **better**, not worse. Azure PA reports
  accuracy, not a produced tone, so the spoken path's `ToneError.said` is the
  sentinel `tones.SAID_UNKNOWN` — it knows a syllable was off but not how. A
  learner who types `ni2hao3` has stated their belief outright, so `said` is
  real and the misconception is nameable. Tone digits are optional; typing
  `nihao` is a normal turn with no tone signal.

**Charter.**

1. Backend: promote `/api/turn/text` to first-class — `ConversationResult` gains
   `user_reading`, the response returns it as `transcript`, and `typed_pinyin`
   derives tone errors from typed digits. Keep it on the shared orchestrator seam.
2. Frontend: add a text-input mode — input box + send in the dock (toggle or
   alongside push-to-talk), posting to `/api/turn/text`, feeding the same
   transcript/localStorage/bubble flow as spoken turns.

**Files:** `backend/main.py`, `backend/orchestrator.py`, `backend/models.py`,
`backend/prompts.py`, `backend/typed_pinyin.py`, `backend/workers/conversation.py`,
`frontend/index.html`, `tests/test_turn_text.py`, `tests/test_typed_pinyin.py`.

---

## Concurrency & merge conflicts

WS2 and WS3 **both edit `frontend/index.html`** — the entire frontend is one
file — so with all three running concurrently, a conflict there is *expected*,
not a surprise. Whichever PR lands second rebases and resolves by hand (the
edits are in different regions — audio graph/scroll vs. dock input — so the
resolution should be mechanical). WS1 and WS3 both touch `main.py` /
`orchestrator.py` / `models.py` lightly — low but nonzero conflict risk, same
rule. Each workstream branches from `main` and ships as its own PR per the
standing delivery workflow in `CLAUDE.md`.

---

## §4 — Later: goal-oriented ("targeted convo") sessions — design note

*Not a workstream. Tackled after WS1–3 land, as part of Phases 4–5.*

**Idea.** A session should present a clear objective up front — e.g. "introduce
yourself and learn your partner's name" — and the learner uses their Chinese to
navigate the situation and accomplish the goal. Today's sessions are open-ended
interlocutor chat with a loose arc; nothing tells the learner what success
looks like, and nothing grades it.

**What exists.** The raw material is already authored:
`kb/zh/greetings/topic.md` has an explicit **"Conversation goal"** section, and
`dialogues.md` carries beats + a close condition for the sketch generator. But
the current design grades **coherence + tone accuracy only** — goal completion
is a genuinely new success axis, absent from the Phase 4–8 design.

**Natural home.** Layer it onto the two planned workers rather than inventing
new machinery:

- The **Phase-5 sketch worker** encodes the topic's stated goal as the arc's
  target and surfaces it to the learner at session start.
- The **Phase-4 feedback worker** grades goal completion when the session hits
  `complete`, alongside the existing coaching feedback.
- Carrying seams that already exist in the design: the sketch spec,
  `TurnAnnotation`, `should_give_feedback`, and
  `session_summary.aggregate_scores_json`.
