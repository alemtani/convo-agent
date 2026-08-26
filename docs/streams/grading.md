# Stream A — Make the grade true

**The question:** when the card says 3/4, did the learner do three of the four
things?

Right now it does not. Five sessions on 2026-08-23/24 produced four misses, and
every one of them was a turn that filled more than one slot and got credit for
fewer. The learner reads that as the app not listening. It is the single largest
threat to the product being worth using.

## What is wrong

### 1. The grader under-credits dense utterances

`prompts.py:_GRADER_PROMPT_TEMPLATE` asks for the slots **the learner's final
turn** established. Nothing in it says that one turn may establish several, in
one breath, in any order. The observed failures:

| Said | Expected | Got |
| --- | --- | --- |
| 你好，你要一个牛奶和三个饼干 | 3 slots | 1 |
| 我做电脑工作，你呢 | 4 slots | 3 |
| 好的，那我要一个夹子和一杯茶 | order a dish and a drink | 0 |

The prompt does carry one sentence about multi-fill ("One utterance may fill
several"), buried mid-paragraph after the instruction that shapes the answer.
That is not enough.

### 2. The grader carries a job that is not its job

`coherence` lives in the grader. The comment in the prompt argues the partner
cannot be trusted to judge coherence because it would take anything scoreable as
relevant. Under V2 that argument no longer holds: the partner is goal-blind
(`kb.load_converser_block`), so it cannot see what is scoreable.

Coherence is a question about what the partner just said and whether the reply
followed from it. The partner is the only party that knows what it meant.

**Goal-blindness is what makes the move safe.** The objection to giving the judge
a stake is the obvious one, and blindness is the answer: the partner cannot steer
toward slots it cannot see. The blindness invariant is asserted on the assembled
request, and it now protects a scoring decision as well as a conversational one.

The result is a grader with exactly one job: which slots did this turn fill.

### 3. The grader reads the whole transcript

It is sent the full dialogue for context. Once it only judges slots it needs the
partner's last line, the learner's turn, and the set of slots already filled.

This is mostly a latency change and Stream B counts the win — see
[latency.md](latency.md) B1, which names it as its largest lever. The work lives
here because it edits the same prompt as the multi-slot fix, and two streams
editing one file costs more than filing the task under the right heading.

There is a smaller accuracy argument too. `slots_filled` is scoped to the final
turn, so the rest of the transcript is material the grader must actively ignore.
Ten turns of history is ten chances to credit something from turn 3.

### 4. Dead weight in `ConverserAnnotation`

`topic_tags` and `should_give_feedback` are consumed by nothing. They are still
described in the system prompt, so the partner spends output tokens and attention
producing them on every turn.

`grammar_notes` goes too. The partner's only question is "did I understand you".
Judging whether a slip is worth coaching is a coach's question, and the partner
is not the coach. It is consumed (`frontend/index.html:874`), so dropping it is
not free: either the verdict worker derives the notes from the transcript it
already has, or the panel goes with the field.

The rule this follows, and the one to apply to any field added later: **a field
belongs to whichever of the two parties would actually notice it.** The partner
is the person you are talking to. The grader is the one who wants you at your
best.

`learner_said_goodbye` passes that test and stays. It drives termination
(`orchestrator.py:418`), and anyone notices a goodbye.

### 5. Prompts are long enough to dilute the instruction that matters

The system prompt is ~60 lines. The partner is asked to hold a persona, a band
ceiling, a scene, a reciprocity rule, a stay-in-character rule, a pinyin-reading
rule, a forgiveness level, and four annotation fields. The reply is one short
sentence of Mandarin.

Target: a partner prompt that is persona, scene, band ceiling, and how to read
messy pinyin. Nothing else.

### 6. `depends_on` earns nothing

`termination.py:229` logs a violation and keeps the fill. It has never blocked a
credit. It costs authoring effort in four `topic.md` files and a cycle check in
`validate.py`. `docs/SCENARIOS.md:837` already flags it as possibly not worth its
cost.

Remove it. It is cleanup, not a fix — it caused none of the misses above.

## What gets built

Cuts come before additions. Every cut changes a prompt, and every prompt change
invalidates cassettes, so doing them together means one re-record wave instead of
four. A trimmed prompt is also a cleaner baseline to measure the multi-slot fix
against.

### A0 — The cassette layer (first, everything else depends on it) — **shipped, PR #86**

Evals are the gate for this stream and the gate cannot cost money each run.

Record and replay every Anthropic call, keyed on
`sha256(model + system + tools + messages + params)`. Cassettes are committed.

- Key miss with `--record` → real call, write the cassette.
- Key miss without `--record` → fail loudly. A silent live call is how an eval
  suite becomes a bill.
- Change a prompt → keys change → only affected cases re-record.

Sits under `evals/`, wrapping the same seam `replay.py` already uses
(`orchestrator.run_text_turn`). It does not touch `backend/`.

**Built here, not taken off the shelf.** `pytest-recording` over VCR.py was
evaluated and declined: it matches on HTTP method, URI and body rather than on
the request we assemble, and the layer has to stay flexible enough to follow the
workers. Every Anthropic call today is a non-streaming `messages.parse`, which
makes a hand-rolled layer small. Extract it as a shared library later, if and
only if the same code gets written a second time somewhere else.

**Sampling, not jitter.** A single recording can be a lucky draw. The answer is
not a probabilistic live call — that makes CI non-deterministic, which is the
exact property this layer exists to remove, and a build that is green 90% of the
time is worse than one that is honestly stale. Instead: record N samples per key
(`replay.py` already has `--repeat` for this reason), store all N, and assert
against the distribution rather than one draw. A scheduled job — nightly or
weekly, never per-PR — re-records against live and diffs.

**Watch B2.** Streaming the verdict ([latency.md](latency.md)) introduces the one
thing a cassette layer is worst at. B2 must prove the layer survives before it
lands, or it quietly turns the eval gate off.

**What shipped, and what it taught us.** `evals/cassette/` — key, store, client,
shared CLI flags — plus `.github/workflows/rerecord.yml`. Two corrections to the
plan above, both found in review:

- The key is **everything in the payload except `timeout`**. Not just the five
  named parts: `max_tokens`, `thinking`, `output_config.effort` and any dial
  added later all shape the output, so all of them are hashed. An allow-list
  would have replayed a recording made at a different setting and said nothing.
- **No cassette was recorded in A0.** `evals/coherence/replay.py` was stale as
  of V2 — it called `termination.pressure_hint` (now `closing_hint`) and read
  `coherence` / `slots_filled` off the converser's annotation, which V2 moved to
  the grader — so it raised before reaching the network. A recording of a call
  the app no longer makes is worse than no recording. **A1 repointed it and
  recorded the first cassettes.**

That staleness is not an isolated slip. The `live` suite has the same disease
(A0.6), and both rotted for the same reason: nothing forced the spec and the
code to be reconciled at the moment they diverged. Hence the standing rule in
`AGENTS.md` — the stream doc's kickoff prompt is updated in the same PR as the
work it describes.

### A0.5 — Interception, so an end-to-end eval is free too

A0 covers the seam where a caller passes `client=`: the workers, the
orchestrator, `replay.py`. It does **not** cover a run against a live server.
`main.py` threads no client, so each worker falls back to its module-global
`_get_client()` and a real `POST /api/turn/text` spends real money. Today there
is no way to say "this run is an eval" at all.

**Never a flag inside `backend/`.** `if os.environ["CASSETTES"]` on the hot path
means one misconfigured variable on fly.io serves learners canned replies, and
it puts eval code in the production import path. **The differentiator is which
entrypoint you launched**, which is a thing that cannot ship to production.

- `cassette.install()` seeds each worker's module-global `_client`. All four
  workers already resolve `client or _get_client()` off a module global, so this
  is a real seam.
- `evals/server.py` calls `install()` and then exposes `backend.main:app`. An
  eval run is `uvicorn evals.server:app`; the ordinary
  `uvicorn backend.main:app` is unchanged and cannot be installed into.

**Its own step because it touches the critical path.** The diff adds no line to
`backend/`, but `install()` swaps the client object sitting on the hot path at
runtime — production module state, mutated by eval code. That is worth a review
of its own rather than a footnote in a cassette PR.

**Not a blocker for A1**, which runs at the worker seam where `client=` already
works. Either order.

**Azure stays real.** This layer wraps `messages.parse`; STT and PA are a
different SDK and a different shape. "Free end-to-end" means the text harness
until someone writes an Azure layer, and the audio path is not that.

### A0.6 — Repair the `live` suite and put it in CI — **shipped, this PR**

`tests/test_conversation_live.py` unpacks five values from
`conversation.respond`, which has returned four since the grader split. It fails
at the first call. It cannot have passed since that change landed, and nothing
noticed, because the suite is excluded from the default run and nobody runs it
by hand.

An excluded suite rots. That is the finding, and it is worth more than the fix.

**Split the marker rather than promoting it wholesale.** The six live files are
two different kinds of test:

- **Behavioral** — structural invariants over model output
  (`test_conversation_live`, `test_session_live`). Cassettes fit exactly. These
  become deterministic, run in CI for free, and are a merge gate. *(The plan
  also named "the Anthropic half of `test_turn_live`". There isn't one to
  split: that file drives the whole spoken loop, so Azure is on the same call
  path and it stays live entire.)*
- **Contact** — tests whose whole point is touching the real API.
  `cache_read_input_tokens > 0` is the sharp case: replaying a recorded `850`
  proves only that someone once wrote `850` into a JSON file. Cassetting it does
  not make it cheap, it makes it a lie. `test_stt`, `test_pronunciation` and
  `test_tts_live` are **Azure**, which A0's layer does not cover at all.

So: behavioral tests move onto cassettes and become required; a small `live` set
stays genuinely live, stays out of CI, and is what the scheduled job exercises.

**What shipped, and what the rot turned out to be.** Every one of the nine
Anthropic tests under the marker was broken, and in more ways than the
five-value unpack. Four distinct failures, each one older than the last time
anybody looked:

| What was stale | Since | What it would have raised |
| --- | --- | --- |
| `conversation.respond` unpacked as 5 values | the grader split | `ValueError` on the first call, in 6 tests |
| `annotation.coherence` / `annotation.slots_filled` | V2 moved both to `GraderResult` | `AttributeError` in 4 tests |
| `sketch.generate("greetings", client=…)` | `scenario` became a required positional | `TypeError` in 2 tests |
| `kb.load_kb_block` fed to the converser | V2 blindness (`load_converser_block`) | nothing — and that is the bad one |

A2 (PR #91) supplied the sharpest evidence for the claim, by accident. It edited
this very file — swapping `topic_tags` for `learner_said_goodbye` in
`test_live_reply_is_valid_structured_output` — two lines below an unpack that
raises `ValueError` before either assertion is reached. The file was maintained
without ever being run.

The last row is the finding worth keeping. The first three are loud: the test
dies before the network. The fourth is silent — it passes, spends money, and
measures the cache behaviour of a prefix the app stopped building. A test that
fails is a test that told you something. **These had been telling nobody
anything for months, and one of them would have gone on lying after the other
three were fixed.**

A second, quieter rot the split had to close: every live test skips itself when
its key is absent. A scheduled job with a missing secret is a green run that
asserted nothing — the same failure in a new place — so the `live` job checks
its three keys before pytest starts and fails with the name of the missing one.

Three of the moved tests changed meaning rather than just syntax. The M2-C
tracker cases asserted the converser's `slots_filled`, which V2 gave to the
grader; repointed, they also had to drop a `dialogue` that opened with a partner
turn, because no client sends one (the opening line is its own field). They were
grading a `messages` array production never assembles.

Where it landed:

- `evals/behavior/` — `cases.py` defines each worker call once, and both the
  test and `python -m evals.behavior.record` read it. A recorder that assembled
  its own request would key on a call the test never makes, and the first sign
  of that is a red build with no recording to fix it.
- `tests/test_worker_behavior.py` — eight tests, in the default run, off 15
  committed samples. Three draws per assertion, so an invariant has to hold on
  every recorded draw rather than a lucky one.
- `tests/test_conversation_live.py` and `tests/test_session_live.py` keep three
  contact tests between them. `test_stt`, `test_pronunciation`, `test_tts_live`
  and `test_turn_live` are unchanged — `test_turn_live` was the one Anthropic
  file that had *not* rotted, because it drives the orchestrator through
  `tests/helpers.py` rather than calling a worker signature directly.
- `.github/workflows/rerecord.yml` gains a `live` job on the weekly schedule,
  and a step that re-records the behavior cases alongside the coherence ones.

Merging A2 then demonstrated the layer doing its job: the trimmed partner prompt
moved the converser key, one cassette missed, and the build went red rather than
replaying a recording of a prompt that no longer exists. Re-recorded, and the
superseded cassette deleted — the grader and sketch keys did not move, because
A2 did not touch those prompts.

### A1 — The failing cases — **shipped, PR #90**

The record of the bug, written before anything is fixed. Fail-to-pass cases from
the four real sessions above.

They are written before the cuts even though the fix lands after, because a bug
with no case is a bug that comes back.

**What shipped, and what it taught us (PR #90).** `evals/coherence/replay.py` now calls
the grader, not the converser. Fixtures carry `opening_line`. Ten cassettes live
under `evals/cassettes/`, three samples each — the first recordings in the repo.

Three cases from the table. Two are strict xfails, and that is the point:

| case | Gold | Credited (3/3) |
| --- | --- | --- |
| `milk-and-biscuits` | `recommendation`, `drinks`, `order` | `recommendation`, `drinks` — `order` dropped |
| `computer-work-ni-ne` | `self_job`, `partner_origin`, `partner_job` | `self_job`, `partner_job` — `你呢` did not bounce origin |
| `clip-and-tea` | `order` | `order` — already green on this grader |

Two corrections to the table above:

- The food-ordering row quoted `你好，你要一个牛奶和三个饼干`. That clause is the
  order. "Expected 3 slots" only holds if the two asks are in the same breath,
  so the fixture packs them. Exact `你要一个牛奶和三个饼干` alone is one slot
  (`order`), and even that is often credited as none.
- `clip-and-tea` does not reproduce the live miss of 0. Opus 5 already credits
  `order` for 夹子 + 茶 after both answers. Do not treat that green as A3 done.

The two misses are `strict` xfails so CI stays a merge gate. A3 removes the
mark. An unexpected pass fails the build — that is how we notice the fix
landed early.

### A2 — The cuts — **shipped, this PR**

- `topic_tags`, `should_give_feedback`, `grammar_notes` — model, prompt,
  frontend. Decide the notes panel: verdict-derived, or gone.
- `depends_on` — model, four `topic.md` files (via the `kb-topic` skill, never by
  hand), `validate.py` cycle check, `termination.py` guard, `docs/SCENARIOS.md`.
- The partner prompt, trimmed to persona, scene, band ceiling, pinyin reading.

Re-run the full eval set after. This is the change most likely to move numbers in
a direction nobody intended.

**What shipped, and what it taught us.** The notes panel is gone, not
verdict-derived. Grammar coaching is a coach's job, and inventing that job
on the verdict worker is not a cut. Tone errors still reach the card
because Azure measured them. `learner_said_goodbye` stays; it drives
termination and anyone notices a goodbye.

`depends_on` is an unknown slot key. The cycle check went with it. The
packed-utterance info log remains.

The partner prompt no longer interpolates `forgiveness_level`. The worker
still takes the arg so the orchestrator contract does not move. Reciprocity
and stay-in-character went too: the scene block already says what the
place does not hand over.

The coherence eval keys did not move. Replay talks to the grader, and none
of these cuts touch the grader prompt. A1's two xfails still fail;
`clip-and-tea` stays green. That is the point of cutting before A3: the
baseline did not shift.

### A3 — The multi-slot fix

Rewrite the `slots_filled` instruction so multi-fill is the leading rule, not a
subordinate clause. A1's cases go green.

**The two xfails come off in this PR.** `tests/test_coherence_eval.py` marks
`milk-and-biscuits` and `computer-work-ni-ne` with `xfail(strict=True)` so A1
could merge. Remove both marks. The tests must pass as ordinary asserts.
Leaving an xfail on a now-green test is a silent skip of the whole point of
A1. `clip-and-tea` is already a real pass; keep it that way.

The prompt change invalidates cassette keys. Re-record the affected cases.

### A4 — Coherence moves to the partner, as a gate

Add `coherence` to `ConverserAnnotation`. Remove it from the grader prompt and
`GraderResult`.

**Binary.** Understood given what came before, or not. Three tags were built to
*measure*; once this is a gate, `drifting` has no defined consequence.

**Drifting counts as incoherent.** The gaming case this exists to catch — the
partner asks what you want to drink, the learner asks which dish is best — is
precisely `drifting`. That also means a legitimate topic change gets caught. It
is a real cost, accepted deliberately.

**The gold labels need remapping, not relabelling.** `evals/coherence/gold.json`
is three-tag. Collapsing to two is a defined map (`drifting` → incoherent), and
it must be done as an explicit, reviewed change to the label set.

**A gate, never a deduction.** An incoherent turn earns nothing *that turn*.
Points already earned are never taken back. A learner watching 3/4 become 2/4
reads that as a bug, not a judgment — and the gate at the moment of the gamed
turn already catches what retroactive cancellation was for.

The one exception is the owed-turn recovery path, where a turn is credited before
its coherence is known. `slots_filled_previously` is the only place a
cancellation is both meaningful and invisible to the learner mid-session.

**Where it lives: `_advance_or_echo`.** The grader and converser are concurrent
(`asyncio.wait(..., FIRST_COMPLETED)`) so neither is reliably first, and the gate
cannot live inside either branch. It belongs where both results meet.

The orchestrator already guarantees this ordering, at no cost:
`orchestrator.py:426-437` never emits `StateEvent` before `ReplyEvent` — a grade
that lands first is parked in `pending_grade` and released after the reply
flushes. So the annotation always exists when `_advance_or_echo` runs. The gate
is a new argument to a pure, already-tested function. No new latency, no new
ordering logic.

(The comment at `orchestrator.py:431` claiming the grader usually wins the race
is out of date. Delete it while you are in there.)

### A5 — The grader's input window

Send the partner's last line, the learner's turn, and the filled-slot set.

Keep the frozen prefix byte-identical (the cache invariant test still applies).
The window shrinks what goes *after* the breakpoint.

`render_window_note` and the owed-turn recovery path must keep working: a turn
settling a debt needs the earlier turns it never judged.

### A6 — The verdict reviews the session

`feedback.settle_outstanding_grades` already re-grades the last unsettled turn
before the card is written, and the verdict already holds the whole transcript.
Extend it to review every turn with hindsight.

This is strictly fairer. The grader at turn 3 did not know what turn 5 would
clarify.

**Review may add credit and may never remove it.** Non-negotiable. The card is
read after a session the learner already watched live; a card that takes away a
point they saw themselves earn is indistinguishable from a bug, and there is no
way for them to tell a correction from a defect.

### A7 — Feedback intake, and contesting a grade

A button in the app. It opens a GitHub issue with the session transcript, the
scenario, the slot state, and what the learner says went wrong.

A **contest** is the same mechanism pointed at one turn. It earns its own affordance
because there is currently no recourse at all: you cannot repeat yourself in a
conversation without it getting strange, so a turn graded wrong stays graded
wrong.

Both live in Stream A rather than Stream C because their output is eval cases. A
filed issue is handled by: write the failing case, then fix, then close. A
contested grade is a **labelled disagreement**, which is the highest-value thing
that can land in `gold.json`.

Needs a server-side token and a rate limit. The client never sees the token.

## Done when

- The cassette suite runs in CI, spends nothing, and is a merge gate.
- An eval can drive the real server end-to-end without spending anything.
- ~~The `live` suite runs — the behavioral half in CI, the contact half on a
  schedule. Neither can rot unnoticed again.~~ **Done (A0.6).**
- All four recorded misses pass.
- The grader returns slots and nothing else.
- The partner prompt fits on one screen.
- A learner can file a bug, or contest a grade, in three taps.

## Kickoff prompts

One per step, each runnable as written. **A0 (PR #86), A1 (PR #90), A2 (PR #91)
and A0.6 (this PR) are done** — their prompts are retired. A0.5 and A3 are
independent of each other and can run in parallel, in separate worktrees.

Every one of these ends the same way, so it is said once here: work in a git
worktree, write the failing test first, branch from `main`, conventional commits,
open a PR explaining the *why* — and **update this document in the same PR**:
mark what landed, correct what the work taught you, and leave the next prompt
runnable.

### A0.5 — interception

```
Read docs/streams/grading.md. Start Stream A at A0.5: interception, so an
end-to-end eval against the real server is free too.

A0 (evals/cassette/) covers callers that pass client=. It does not cover a run
against a live server: main.py threads no client, so each worker falls back to
its module-global _get_client() and a real POST /api/turn/text spends money.

Add cassette.install(), which seeds each worker's module-global _client, and
evals/server.py, which calls install() and then exposes backend.main:app. An
eval run is `uvicorn evals.server:app`. Do not add a flag inside backend/ — one
misconfigured env var on fly.io would serve learners canned replies. The
differentiator is which entrypoint was launched.

This touches the critical path even though it adds no line to backend/:
install() swaps the client object on the hot path at runtime. Say so plainly in
the PR, and test that an ordinary `uvicorn backend.main:app` process is
unaffected.

Azure stays real — this layer wraps messages.parse, so "free end-to-end" means
the text harness, not the audio path.
```

### A0.6 — repair the `live` suite

Retired: shipped. The `live` marker now means "a recording cannot stand in for
this call", the behavioral half is a merge gate off cassettes, and the weekly
job runs what is left.

### A3 — the multi-slot fix

```
Read docs/streams/grading.md. Start Stream A at A3: the multi-slot fix.

Rewrite the slots_filled instruction in prompts.py:_GRADER_PROMPT_TEMPLATE so
multi-fill is the leading rule, not a subordinate clause.

A1 recorded two misses as strict xfails in tests/test_coherence_eval.py
(A1_DENSE_CASES):

- milk-and-biscuits — drops order
- computer-work-ni-ne — 你呢 bounce drops partner_origin

Remove both xfail marks in this PR. The tests must pass as ordinary asserts.
Do not leave an xfail on a passing test — that skips the whole point of A1.
clip-and-tea is already green; do not break it.

The prompt change invalidates cassette keys. Re-record the affected cases
(python -m evals.coherence.replay --record --samples 3) and run the default
suite. It must be green with zero xfails on these three cases.
```
