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
  `main`. See the merge-conflict note at the bottom. **WS3 landed 2026-07-27**;
  WS1 and WS2 remain open.
- **Goal-oriented ("targeted convo") sessions are deferred** — design note in
  §4, tackled after WS1–3 land.
- **Streaming the mic** — the shared root of WS1's latency and WS2's chat feel —
  is researched in §5 but is *not* chartered yet: WS1 measures first.

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
capture worklet in `frontend/recorder-worklet.js`). Re-verified against `main`
on 2026-07-28, after WS3 landed — every item below still holds:

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
- **No optimistic echo (added 2026-07-28).** In text mode the learner's own
  bubble doesn't appear until the server answers — `sendText` renders *both*
  bubbles from the response, because `transcript` is the worker's 汉字 reading
  of the typed pinyin. Every real chat app paints your own message instantly.
  Fix direction: render the user bubble immediately from the typed pinyin, then
  upgrade it in place to 汉字 + tone marks when the response lands. The spoken
  path can't do this without partial transcripts — which is the WS1 tie-in
  below.

**On "is there a template for this?"** Surveyed the field; the answer is *use
the patterns, not the package*. The chat UI kits that come up
([Stream Chat](https://getstream.io/blog/implement-stream-chat-with-vanilla-js/),
[chat-bubble](https://github.com/dmitrizzle/chat-bubble),
Flowbite/Penguin UI bubbles) each assume something we don't have or want: a
hosted messaging backend, a bot-scripting JSON format, or a Tailwind build step.
This app is one 638-line file with no build, and its bubbles already carry
per-syllable tone underlines that no generic component models. What's worth
lifting from those templates is three well-known patterns, ~60 lines total:
a three-dot pulse keyframe for the pending bubble; `overflow-anchor` plus a
"near the bottom?" check for stick-to-bottom scrolling; and optimistic send.

**Charter.** Make the thread behave like a real DM app: instant mic start,
loading bubble while waiting, bottom-anchored scroll, optimistic echo of the
learner's own turn. Client-only; no backend changes expected (the streaming
work in §5 is a separate, later step).

**Testing.** The frontend has no test suite, and the charter's "verify by
driving the app" is weak for exactly the races this workstream is about (lost
first frames, scroll timing). Chrome can supply a *deterministic* microphone:
launched with `--use-fake-device-for-media-stream` and
`--use-file-for-fake-audio-capture=<file>.wav`, `getUserMedia` returns a stream
that plays a recorded WAV instead of live audio. That makes the spoken path
scriptable end-to-end — press, hold, release, assert the bubbles. Proposal:
add a thin Playwright smoke suite (kept out of the default `pytest` run, like
the `live` marker) covering: first press captures frames from frame zero;
a pending bubble exists between submit and response; reload with history lands
at the bottom without animation; scrolled-up users aren't yanked down; the
optimistic bubble upgrades in place rather than duplicating.

**Files:** `frontend/index.html`, `frontend/recorder-worklet.js`.

---

## WS3 — Text-only mode (pinyin input) — ✅ COMPLETE (2026-07-27, PR #13)

Shipped as described below. `POST /api/turn/text` is first-class: the worker
returns `user_reading` (the 汉字 it read the typed pinyin as), which the response
carries as `transcript`; `backend/typed_pinyin.py` derives tone errors from typed
tone digits; the dock has a ⌨️/🎙️ toggle with a pinyin composer. The section is
kept for the record — the findings below are history, not a plan.

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

WS3 has landed, so the anticipated three-way race is down to two. WS2 rebases
onto the WS3 frontend: `index.html` now carries the dock's mode toggle and
composer, and WS2's edits (audio graph, scroll, pending bubble, optimistic echo)
sit in different regions, so the merge stays mechanical — but WS2's optimistic
echo does touch `sendText`, which WS3 wrote. WS1 and WS2 don't overlap at all
today. Each workstream branches from `main` and ships as its own PR per the
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

---

## §5 — Where WS1 and WS2 converge: streaming the mic — research note

*Not a workstream yet. Research done 2026-07-28; sized as a follow-on to WS1's
measurement step, not a substitute for it.*

**The hunch.** Today the client buffers the whole utterance, WAV-encodes it on
release, uploads it, and only then does the server start Azure STT — recognition
can't begin until speaking has finished. If audio streamed to the recognizer
*while* the learner talks, STT would be nearly done the moment they release.
That is simultaneously WS1's biggest latency lever and WS2's most convincing
chat affordance (a bubble that fills in as you speak), which is why the two
workstreams keep pointing at each other.

**What Azure actually supports.**

- Pronunciation assessment "supports uninterrupted streaming mode… as long as
  you don't stop recording, the evaluation process doesn't finish"
  ([docs](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/how-to-pronunciation-assessment)).
  Streaming is a first-class mode for both STT and PA, not a workaround.
- The **JavaScript SDK runs in the browser**, microphone included, and ships a
  prebuilt bundle usable from a plain `<script>` tag via
  [jsDelivr](https://www.jsdelivr.com/package/npm/microsoft-cognitiveservices-speech-sdk)
  — no build step, which matters for a single-file frontend. Continuous
  recognition exposes `recognizing` (partial) and `recognized` (final) events.
- Browser clients authenticate with a **short-lived token**, not the
  subscription key: the key stays server-side and a backend endpoint exchanges
  it at `/issueToken` for a token valid ~10 minutes, which the page refreshes.
  This is the documented, supported pattern — it does not violate the
  "keys never reach the client" convention in `CLAUDE.md`, but it *is* a new
  trust boundary and should be written down as one.
- **Unscripted PA** (empty `referenceText`) returns recognition *and* scores in
  one pass, which would collapse the two-pass design. Tempting, but Microsoft
  warns plainly: "for unscripted assessment, the speech-to-text model used is
  different from Azure STT… if you need assessment based on highly accurate
  recognized text, we recommend first calling Azure STT to obtain the reference
  text, and then performing scripted assessment." That is a direct endorsement
  of the two-pass choice in DESIGN.md Risk 1. Treat one-pass as an *experiment*
  to measure (accuracy delta vs. latency win), not a default.
- Constraint worth noting: prosody assessment is en-US only, and syllable-level
  accuracy is documented as en-US only. Our zh-CN per-syllable scores come from
  Azure's grapheme-level breakdown, which `pronunciation.py` already keys by
  hanzi — unchanged by any of this.

**Three designs, cheapest first.**

1. **Server-side streaming, same HTTP shape.** Keep everything where it is;
   replace the `NamedTemporaryFile` + `recognize_once` in
   `backend/speech/_recognizer.py` with a `PushAudioInputStream`, and pre-open
   the Azure connection so each turn skips the handshake. No client change, no
   new trust boundary. Removes disk I/O and connection setup, but *not* the
   fundamental serialization — the server still can't start until the upload
   lands.
2. **WebSocket to the backend.** The client streams PCM frames over a socket as
   they're captured; the server feeds them into a push stream against a
   continuous recognizer and returns partials. Full latency win, keys stay
   entirely server-side, partial transcripts available for the live bubble.
   Cost: a stateful per-connection endpoint in an app whose stated design is a
   stateless proxy (defensible — the state is per-connection, not per-user, and
   nothing is persisted — but it deserves an explicit note in DESIGN.md).
3. **Azure SDK in the browser, token-brokered.** The page talks to Azure
   directly for STT (and optionally PA); the backend mints tokens and otherwise
   only runs the Claude turn. Biggest latency win — the spoken turn's p50
   collapses toward "the Claude call" — and the simplest client code, since the
   SDK owns capture, streaming, and reconnection. Costs: a ~1 MB SDK bundle on a
   deliberately dependency-free page, a token endpoint, and the server now
   *trusts client-supplied scores*. For a single-learner practice tool that
   trust is fine; it would not be for a graded product.

A useful intermediate regardless of which lands: once STT no longer gates the
reply, **PA stops being on the critical path at all**. Render the partner's
reply as soon as Claude answers, then decorate the learner's bubble with tone
underlines when scores arrive a beat later. Progressive rendering hides latency
that can't be removed.

**How to test any of this.**

- **Latency, honestly:** WS1's instrumentation first — per-stage timers
  (capture → upload → STT → PA → Claude → total) logged server-side and
  surfaced client-side, so every claim below is a measured delta on the same
  fixed set of recorded utterances, not a vibe. Replay a handful of WAVs N times
  and compare p50/p95 before and after.
- **Streaming correctness, deterministically:** the same Chrome fake-microphone
  flags WS2 proposes (`--use-file-for-fake-audio-capture`) feed a known WAV
  through the real capture path, so partial-transcript behavior is assertable
  rather than eyeballed.
- **The one-pass experiment:** run scripted (two-pass) and unscripted (one-pass)
  assessment over the same recorded set and compare recognized text against a
  hand-checked reference plus the resulting tone errors. This is an eval, not an
  assert — `@pytest.mark.live`, excluded from the default run, per `CLAUDE.md`.
- **Contract tests** for whatever replaces `recognizer_for`: assert the push
  stream is fed and closed correctly and that a *recorded* Azure response still
  parses into `PronunciationScore` — never assert Azure's actual output.
