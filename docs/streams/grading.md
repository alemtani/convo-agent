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

Merging A1.5 and A3 did it twice more, and taught the step two things it did not
know when it was written:

- **A gate that loops must prove its cassette is deep enough.** These tests
  assert over five draws. A replay *cycles* the samples it has, so a cassette
  recorded at `--samples 1` would hand back the same answer five times and read
  as five passes. `_draws` now checks the recorded depth, the same guard A3 put
  on the dense cases — and for the same reason: a rate computed from a recording
  that never measured one.
- **Every recording step must come before the sweep.** `evals.cassette.sweep`
  deletes what no manifest claims, so the behavior runner needed `--used-out`
  like the other two. Without it the scheduled job would have deleted this
  corpus every week and silently re-bought it — the exact bill the layer exists
  to avoid. One store, three runners now.

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

### A1.5 — The turn runner, so the *partner* is measured too — **code shipped (PR #93); cassettes outstanding**

Every eval in the repo called `grader.grade` directly. That measures the judge
and never the thing being judged: the partner's prompt had no coverage at all,
which is how A2 cut three annotation fields and a third of the system prompt
with nothing to replay.

`evals/turn/replay.py` drives `orchestrator.run_text_turn`, which threads **one**
client into `conversation.respond` and `grader.grade` alike — so a single
cassette-backed run covers the reply, the grade computed against that reply, and
the state it advances to. The grader-only runner stays: it holds the partner
still, which is what A3 needs while the grader prompt moves.

**Over-volunteering is the headline check** (`evals/turn/withholding.py`). The
partner handing over a `request` slot before the learner asks does not help
them — it removes a point from the board, because you cannot ask for what you
have already been told and repeating yourself to a partner does not work. It was
seen twice in real sessions. `withholding` in `topic.md` is the authored
constraint against it and nothing had ever checked that the partner honours it.

Only unasked, unfilled `request` slots are candidates: a partner answering what
the learner just asked is the scene working. That composition — the grade and
the reply from the same call — is why the check belongs on this runner.

Two things the work settled:

- **The turn runner cannot observe `coherence`.** `ConversationTurnResponse`
  carries none: it is the grader's judgment, and it reaches the client only
  through its consequence, whether the slot advanced. So coherence accuracy
  stays the grader-only runner's measurement against `gold.json` — until **A4**
  puts the field on the partner's annotation, at which point this runner becomes
  where it is observed and `TurnObservation` grows the field.
- **Probes are not recordings.** `evals/turn/cases/` holds constructed red-team
  turns (the learner asks nothing, so anything the reply establishes was
  volunteered) and is kept apart from `tests/fixtures/sessions/`, which holds
  real turns from real sessions. A fabricated turn filed among recordings is a
  lie a later reader cannot detect. Probes carry no gold: gold answers what the
  *learner* deserved, and a probe asks about the partner.

**Cassettes are not recorded yet.** The runner is a merge gate only once they
are, and recording spends money — one wave, two calls per case plus a judge call
where there is a candidate slot.

### A2 — The cuts — **shipped, PR #91**

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

### A3 — The multi-slot fix — **shipped, PR #95**

Rewrite the `slots_filled` instruction so multi-fill is the leading rule, not a
subordinate clause. A1's cases go green.

**The two xfails come off in this PR.** `tests/test_coherence_eval.py` marks
`milk-and-biscuits` and `computer-work-ni-ne` with `xfail(strict=True)` so A1
could merge. Remove both marks. The tests must pass as ordinary asserts.
Leaving an xfail on a now-green test is a silent skip of the whole point of
A1. `clip-and-tea` is already a real pass; keep it that way.

The prompt change invalidates cassette keys. Re-record the affected cases.

**What shipped, and what it taught us.** Four changes to the grader's
`slots_filled` instruction, all of them about *where* a rule sits rather than
what it says:

- The leading sentence is now "one turn usually fills more than one slot", and
  the instruction is a **loop over the slot list** — take each slot in turn and
  ask whether this utterance established it. The old text asked for "the slots
  the final turn established", which reads as one search with one answer.
- The scoping rules moved into their own paragraph *after* the loop, and they
  are scoped to the **field**: "only what this turn established goes in
  `slots_filled`". The first draft said "judge this turn, and only this turn",
  which flatly contradicted `render_window_note` ("judge them too") and was
  resolved only by the note landing later in the message. Review caught it.
- A **beginner-slip** rule: a wrong pronoun, a missing measure word, a
  near-miss word does not unmake what the learner did. Naming the slip is the
  coach's job at the end of the session, not the grader's.
- The 你呢 example bounces back **both** of what the partner asked.

### The thing this step actually taught us

**A1's test asserted a lucky draw, and A3 shipped green on one.**

The first version of this PR reported "all three cases pass on all three
samples" and that was true. Measured at ten draws, the same prompt got
`milk-and-biscuits` right 7/10 and `computer-work-ni-ne` 7/10. The test replays
the first three committed recordings, so it goes green iff those three happened
to be right — about one time in eight at a true rate of 0.5. A gate you pass by
luck is worse than no gate, because it is read as evidence.

A0 already said this: *"record N samples per key, store all N, and assert
against the distribution rather than one draw."* A1 asserted one draw, three
times. The lesson is not that A1 was careless — it is that **a stochastic
property tested by replaying a fixed recording looks deterministic**, and
nothing about a green run tells you which it was.

So the dense gate is now a **rate**: `DENSE_SAMPLES = 5`, `DENSE_MIN_EXACT = 4`,
with the observed rate in the failure message so 4/5 does not read like 5/5.
Five rather than ten because the numbers say so — at a true rate of 0.95, a 4/5
gate false-fails 2% of the time, and the extra five draws buy power to catch a
*mediocre* case rather than protection for a good one. Re-recording is the only
thing that spends money, so the sample count is the weekly job's bill and not
every PR's; CI replays a fixed recording and never re-draws.

### What `milk-and-biscuits` was really about

It sat at 5–7/10 while every other case was 10/10. The cause was not the
multi-slot rule. The learner wrote **你要一个牛奶和三个饼干** — "*you* want one
milk and three biscuits". Same turn with 我要 instead: 5/5. The grader was
wobbling on whether a sentence that assigns the wanting to the server is an
order at all, which is a fair reading.

That is what the beginner-slip rule fixes, and it is a rule about the product
rather than about this fixture: the target learner is HSK 1–2 and *will* say 你
for 我. Whether a fact got across is the grader's question; whether it was said
correctly is the verdict card's.

### Numbers

Five draws over eleven cases, against the same gold:

| | A1 baseline | A3 |
| --- | --- | --- |
| dense cases at the 4/5 gate | 0/3 pass | **3/3 pass** |
| missed credit | 6 (of 30) | **0** (of 55) |
| spurious credit | 4 (of 30) | 6 (of 55) — same rate, same two cases |

Every spurious run is `nonsequitur-slot-fill` (the deliberate gaming case, which
the recommended `on_track` gate blocks) or the single `self_name` on
`elliptical-ni-ne` that was there at baseline. **Nothing pushed the grader
toward crediting more.** Credit went up only where gold said it was owed.

### Two things that came out of review

**The owed-turn path had no coverage at all.** Every case had
`last_graded_turn: None`, so `grading_window` was 1 across the whole corpus and
`render_window_note` was prompt nobody had ever measured — which is why the
first draft's contradiction with it went unnoticed. New fixture
`owed-drinks-then-order`: watermark at turn 1, turn 3 under test, so the grade
owes turn 2. `Observation` now carries `slots_filled_previously`, because
`slots_filled` alone cannot tell a grader that *merged* the two lists from one
that dropped the earlier turn. 5/5 exact.

**Stale cassettes are the scheduled job's problem, not a PR's.** A prompt edit
changes every key, and the recordings under the old ones become unreachable —
no code can produce that key again, and the filename is a hash. The first draft
of this PR deleted them by hand. They are kept now, and
`.github/workflows/rerecord.yml` sweeps them.

**The sweep is not a flag on a runner, and that is the point.** A1.5 (PR #93)
gave the store a second writer: `evals.turn.replay` records the whole turn,
`evals.coherence.replay` records the grader alone, and **neither reaches the
other's keys**. A `--prune` that deleted what its own run did not touch would
therefore delete the other runner's entire corpus — which is exactly what this
PR's first draft would have shipped, armed to fire the moment A1.5 records.

So each runner writes down what it reached (`--used-out`) and
`evals.cassette.sweep` takes the **union**. A missing manifest is an error, not
an empty set: a runner that crashed must not read as "reached nothing".
`--used-out` is refused alongside `--case` for the same reason, and
`store.keys()` now only counts filenames that are actual sha256 digests, so a
manifest or a README parked in the directory can never read as a stale key.

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

- The cassette suite runs in CI, spends nothing, and is a merge gate — the
  grader's cases and the partner's alike.
- ~~The `live` suite runs — the behavioral half in CI, the contact half on a
  schedule. Neither can rot unnoticed again.~~ **Done (A0.6).**
- All four recorded misses pass.
- The grader returns slots and nothing else.
- The partner prompt fits on one screen.
- A learner can file a bug, or contest a grade, in three taps.

## Kickoff prompts

One per step, each runnable as written. **A0 (PR #86), A1 (PR #90), A2 (PR #91),
A3 (PR #95) and A0.6 (this PR) are done** — their prompts are retired. A1.5 is
what runs next.

Every one of these ends the same way, so it is said once here: work in a git
worktree, write the failing test first, branch from `main`, conventional commits,
open a PR explaining the *why* — and **update this document in the same PR**:
mark what landed, correct what the work taught you, and leave the next prompt
runnable.

### A1.5 — record the turn runner's cassettes

```
Read docs/streams/grading.md. Finish Stream A at A1.5: the turn runner is
merged (PR #93) but records nothing, so it is not a gate yet.

`evals/turn/replay.py` drives `orchestrator.run_text_turn` — one client threads
into both workers, so one run covers the reply, the grade computed against that
reply, and the state it advances to. `evals/turn/withholding.py` then asks
whether the reply gave away a `request` slot the learner had not asked for.

Record and commit the cassettes. This spends money, one wave:

    python -m evals.turn.replay --record --samples 3
    python -m evals.turn.replay --record --samples 3 --cases-dir evals/turn/cases

Two calls per case, plus a judge call wherever a candidate slot exists. The
second command is the red-team probes, which are the point: on `greeting-only`
and `i-am-hungry` the learner asks for nothing, so any slot the partner's reply
establishes was volunteered — a point the learner can now never earn.

Then report what the recording found, which is the actual deliverable:

- Does the partner honour `withholding`? It was seen breaking it twice in real
  sessions and nothing has ever checked it.
- Did A2's trimmed prompt (PR #91) change the partner in a way nobody was
  watching? A third of the system prompt went with no partner-side eval in
  existence. This is the first look.

Wire the runner into the CI eval job beside the coherence one, and into
`.github/workflows/rerecord.yml` so it cannot go stale unnoticed.

If a probe shows the partner volunteering, do **not** fix it in this PR. Write
it up: it is either a prompt bug for its own change or an authoring bug in
`withholding`, and which one it is deserves deciding on its own.
```

### A0.6 — repair the `live` suite

Retired: shipped. The `live` marker now means "a recording cannot stand in for
this call", the behavioral half is a merge gate off cassettes, and the weekly
job runs what is left.

### A4 — coherence moves to the partner

```
Read docs/streams/grading.md. Start Stream A at A4: coherence moves to the
partner, as a gate.

Add a binary coherence field to ConverserAnnotation. Remove `coherence` from
_GRADER_PROMPT_TEMPLATE and from GraderResult. The grader is then a
slots-only worker, which is the point of the step.

Three tags collapse to two. `drifting` counts as incoherent — that is a
deliberate cost, not an oversight, and it means a legitimate topic change gets
caught. Remap tests/fixtures/sessions/gold.json (and gold.second-opinion.json)
as an explicit reviewed change, never a relabelling pass.

The gate lives in orchestrator._advance_or_echo, where the two concurrent
branches meet, as a new argument to that pure function. It blocks credit for
the incoherent turn and never removes credit already earned — with the single
exception of slots_filled_previously on the owed-turn recovery path. Delete the
out-of-date comment at orchestrator.py:431 while you are in there.

The A1 dense cases in tests/test_coherence_eval.py are a rate, not a run:
DENSE_MIN_EXACT of DENSE_SAMPLES draws must be exact. Keep them passing. Both
prompts change, so every cassette key changes: re-record
(python -m evals.coherence.replay --record --samples 5) and run the default
suite. Do not delete the stale recordings by hand — the weekly job prunes them.
A3's numbers are the baseline to beat: 3/3 dense cases at the 4/5 gate, 0
missed credit over 55 runs.

This changes what the learner sees on the HUD, so raise a tunnel and take a
real turn on the phone before the PR is ready.
```
