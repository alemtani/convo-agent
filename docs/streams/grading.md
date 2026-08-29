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

**This applies to the grader too, and A6 found the receipt.** The item below was
written about the partner, and A2 cut the partner. Meanwhile the grader's prompt
grew — A3's multi-slot language, A4's split, A5's window — to 612 words in which
`slots_filled` gets five paragraphs and `slots_filled_previously` gets **one
sentence telling the model to leave it empty**. Everything that then asks for
earlier turns (the window note, the review note) arrives after that, behind the
cache breakpoint, arguing with a frozen prefix that has already said no. See A8.

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

### A0.6 — Repair the `live` suite and put it in CI — **shipped, PR #92**

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

### A1.5 — The turn runner, so the *partner* is measured too — **shipped, PR #93 + #96**

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

- **The turn runner cannot observe `coherence`** — *settled by A4.* At the time
  `ConversationTurnResponse` carried none: it was the grader's judgment, and it
  reached the client only through its consequence. A4 put the field on the
  partner's annotation, so this runner is now the only place it can be observed;
  `TurnObservation` carries the tag and `main` scores it against `gold.json`. It
  scores nothing until these cassettes exist.
- **Probes are not recordings.** `evals/turn/cases/` holds constructed red-team
  turns (the learner asks nothing, so anything the reply establishes was
  volunteered) and is kept apart from `tests/fixtures/sessions/`, which holds
  real turns from real sessions. A fabricated turn filed among recordings is a
  lie a later reader cannot detect. Probes carry no gold: gold answers what the
  *learner* deserved, and a probe asks about the partner.

**What shipped, and what it taught us.** Cassettes recorded, 3 samples per
key. The runner is now a merge gate: pytest replays the probes and every
session case, a new `eval` CI job walks both this corpus and the
grader-only one, and `.github/workflows/rerecord.yml` refreshes both.

The actual deliverable is what the recording found
([`evals/turn/RESULTS.md`](../../evals/turn/RESULTS.md)):

- **The probes honour `withholding`.** `greeting-only` and `i-am-hungry`
  volunteer nothing, 3/3. The partner asks 几位 / 要吃什么. It does not
  name a dish. The failure seen in real sessions — 你好, then the day's
  best dish — does not reproduce on A2's trimmed prompt.
- **A stuck learner still gets the drinks list.** `derailed-input`, 2/3
  runs: 你想喝茶还是水？ after the learner said they could not speak.
  That is either the prompt being a person when the customer freezes, or
  `withholding` not covering the recovery path. Not fixed here — which
  one it is deserves its own change.
- **A2 did not break the partner in the way nobody was watching.**
  Reciprocity, stay-in-character, and `forgiveness_level` went; the
  scene block is doing that job on the probes. What the turn runner
  newly shows is the converser's reading: `milk-and-biscuits` becomes
  我要 on 2/3 runs and those runs credit `order`. Do not treat that as
  A3. The grader-only runner still drops `order` 3/3, and that is the
  case A3 has to clear.

The volunteering that remains is on a recorded session case, not on the
probes. The probes stay a hard assert (`volunteered == ()`). The stuck
turn is written up, not asserted: it is 2/3, and pinning a flaky xfail
would hide the next real miss.

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
PR's first draft would have shipped, and A1.5 (PR #96) then recorded.

So each runner writes down what it reached (`--used-out`) and
`evals.cassette.sweep` takes the **union**. A missing manifest is an error, not
an empty set: a runner that crashed must not read as "reached nothing".
`--used-out` is refused alongside `--case` for the same reason, and
`store.keys()` now only counts filenames that are actual sha256 digests, so a
manifest or a README parked in the directory can never read as a stale key.

### A4 — Coherence moves to the partner, as a gate — **shipped, PR #97**

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

**The gate is this turn's, not the session's.** It applies to `slots_filled`
only. `slots_filled_previously` — credit owed to earlier turns whose grade
failed — is never dropped for *this* turn being incoherent: those turns are not
this one, and a non-sequitur now must not cancel points earned before it (raised
in review, PR #97). Gating owed credit on each earlier turn's *own* coherence
would be more correct, but those flags are not persisted — future work, and the
same per-turn coherence state the end-of-session challenge ("I'm done") needs.

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

**What shipped, and what it taught us.** `ConverserAnnotation.coherent: bool`,
defaulting to `True`. `GraderResult` is `slots_filled` and
`slots_filled_previously` and nothing else. `StateEvent` lost its `coherence`
field — the tag ships on `reply` now, with the branch that produced it.

Four things the work settled:

- **The default is the generous one, and that is a design decision.** The gate's
  two errors are not symmetric. A turn wrongly called incoherent silently costs
  a point the learner earned and they cannot tell it happened; a turn wrongly
  called coherent costs them nothing they can see. So a partner that omits the
  field is read as having understood, and the gate is only ever closed by a
  partner that says so out loud.

- **The prompt has to say "do not judge their Chinese".** The target learner is
  HSK 1–2 and will say the wrong pronoun, drop a measure word, and send
  untoned run-together pinyin. A partner asked "did that follow?" without that
  instruction would answer "no" to a beginner's grammar, which is the exact
  failure the generous default exists to avoid. It is the same rule A3 wrote
  into the grader as the beginner-slip paragraph, arriving on the other worker.

- **Docstrings are prompt, and the removal notice is the trap.** The first draft
  explained on `GraderResult` itself which field had moved and why — text
  `messages.parse` renders into the request as the schema `description`, so the
  grader would have been told about coherence in the act of documenting that it
  no longer judges it. It lives in a comment above the class now, the same place
  `ConverserAnnotation`'s reasoning already lived.

- **Prompt wording moved the numbers more than the field removal did.** Three
  55-run waves were recorded. The first two carried a closing "and nothing
  else" and a schema docstring that said "no other question"; both scored worse
  on missed credit (2 and 4 of 55) than the third, which is the minimal diff —
  the coherence bullet deleted, every other word of A3's prompt left alone.
  Restrictive language aimed at a field that no longer exists costs credit
  somewhere else. The lesson generalises: when removing an instruction, remove
  it; do not replace it with a sentence about its absence.

### Numbers

Five draws over eleven cases, same replay. The gold was reframed in review (see
below), so the spurious row is scored against the reframed labels:

| | A3 | A4 |
| --- | --- | --- |
| dense cases at the 4/5 gate | 3/3 pass | **3/3 pass** (5/5 each) |
| missed credit | 0 (of 55) | **2** (of 55) |
| spurious credit | 6 (of 55) | **0** (of 55) — see the gold reframe below |

**The regression is real and it is one case.** Both misses are `wellbeing` on
`elliptical-ni-ne` — 我很好，你呢？, where the slot rides entirely on 你呢
bouncing one question back. All three waves showed it, so it is not one unlucky
draw: taking the coherence bullet out of the grader's prompt cost something on
the turn where "did this answer what was asked?" and "did this fill the slot?"
are the same question. Nothing else in the corpus moved.

**The gold was reframed in review, and spurious credit went to zero.** The
grader still credits `recommendation` on all five draws of the gaming turn
(`nonsequitur-slot-fill`), but that is the *right* answer now: a goal-blind
grader is a pure extractor, so it credits the fact the words state and leaves
coherence to the partner. Gold credits it too (`slots_established:
[recommendation]`), so slot accuracy reads those five draws as exact, not
spurious. The gaming turn is caught by the gate — gold still labels it
incoherent and the learner sees no credit — and whether the partner tags it so
is scored against gold in `evals/turn`, not here. This is why the grader's slot
numbers did not "improve" against the old gold: the grader was never the party
that should suppress the gaming turn, and scoring it as if it were hid a clean
extractor behind a coherence failure that was always the gate's to catch.

**A0.6 landed first, and the rebase found two things it left.** The behavioral
suite (`tests/test_worker_behavior.py`) asserts the partner's annotation, so its
cassettes were re-recorded here too, and its "the partner never reports
coherence" test was inverted — it passed on the new code by accident, because
the field is `coherent` and it looked for `coherence`. A test that documents the
opposite of what the code does is worse than no test.

The second is a defect in the weekly job. `--samples 5` is a ceiling and
`--repeat` is how many times a run visits each case, so at the default
`--repeat 3` the scheduled re-record wrote three draws into a cassette the dense
gate demands five of. It has been recording shallow since A3. Both steps now
pass `--repeat 5`.

**The partner's tag is measured as of A1.5 (PR #96).** Scoring `coherent`
against gold needs the turn runner's cassettes, and they are now recorded.
`TurnObservation` carries the tag and `evals.turn.replay` prints the 2×2
against `gold.json`. The gate stays asserted where it is deterministic too —
`_advance_or_echo`, in `tests/test_orchestrator.py`.

### A5 — The grader's input window — **shipped, PR #98**

Send the partner's last line, the learner's turn, and the filled-slot set.

Keep the frozen prefix byte-identical (the cache invariant test still applies).
The window shrinks what goes *after* the breakpoint.

`render_window_note` and the owed-turn recovery path must keep working: a turn
settling a debt needs the earlier turns it never judged.

**What shipped, and what it taught us.** `build_request` now sends the grader
only the tail of `dialogue` — `2*window - 1` entries — instead of the whole
transcript. On a healthy turn (`window == 1`) that is one line: the partner's
last, folded into the learner's turn. On an owed turn it is that pair per turn
still owed a grade. What the transcript used to carry about earlier progress —
which slots are already filled — is now sent directly, as
`render_filled_note`, a volatile note riding the messages after the breakpoint.

Three things the work settled:

- **The frozen prefix did not move, so most cassettes did not either.** A5 edits
  only what goes *after* the breakpoint, so `render_grader_prompt` is
  byte-identical and the cache invariant holds. Better still, a turn-1 case has
  no history to cut and nothing filled, so its whole request is byte-identical
  and its A4 cassette is still valid — proof, not assertion, that A5 does not
  touch those turns. Only the seven history-bearing cases (and one behavioral
  grader case) re-recorded. `_prefix_text` keeps the turn-1 opener's
  string-join shape precisely so that stays true.

- **The window opens on an assistant turn, which the API will not take first.**
  The partner's last line leads the tail, but `messages[0]` must be `user`. It
  folds into the learner turn it precedes — the same shape turn 1 has always
  used for the opening line — so the fix was a fold, not a new message role.

- **`elliptical-ni-ne` recovered.** A4's two misses were both on 我很好，你呢？,
  the turn where "did it follow?" and "did it fill the slot?" are one question.
  A5 cuts the learner's earlier self-introduction (already filled, and a
  distraction) while keeping the partner's 你最近怎么样？ — the line the bounce
  answers. The case went 5/5. That is a thin measurement (5 draws cannot
  separate a real fix from a lucky wave) and does **not** retire the dedicated
  你呢 step the A5 kickoff still describes below; it is one clean wave, not a
  proof.

### Numbers

Five draws over eleven cases, same gold, same replay:

| | A4 | A5 |
| --- | --- | --- |
| dense cases at the 4/5 gate | 3/3 pass (5/5 each) | **3/3 pass** (5/5 each) |
| missed credit | 2 (of 55) | **0** (of 55) |
| spurious credit | 0 (of 55) | **0** (of 55) |

Both of A4's misses were `wellbeing` on `elliptical-ni-ne`; A5 has none. Nothing
regressed.

### A6 — The verdict reviews the session — **shipped, PR #99**

`settle_outstanding_grades` re-graded the turns whose grade never landed. It is
now `feedback.review_session`: one grader call over the whole conversation
before the card, judging every turn again with the rest of the session in view.
Recovery became a case of review rather than a separate job.

This is strictly fairer. The grader at turn 3 did not know what turn 5 would
clarify.

**Review may add credit and may never remove it.** Non-negotiable. The card is
read after a session the learner already watched live; a card that takes away a
point they saw themselves earn is indistinguishable from a bug, and there is no
way for them to tell a correction from a defect.

**What shipped, and what it taught us.**

- **The one-way rule is enforced in Python, not asked for in the prompt.** New
  ids union into `filled_at`; an already-earned slot keeps the turn it was
  earned on, and `status`/`end_reason` are untouched. The note says so too, but
  only so the model does not spend judgment on a decision it does not own.

- **The pass is skipped when it could not change the answer.** Every slot filled
  means an add-only review has nothing to add, so the happy path still costs
  nothing — the same property the debt-only pass had, kept for a different
  reason.

- **A completed card no longer excuses turns it did not check.** `unchecked_turns`
  is zero whenever the goal was met. That block exists to stop the card blaming
  the learner for a turn our grader never read; with nothing missing it excused a
  miss that was not there — and a completed session is exactly the one whose
  watermark the review now leaves stale.

- **The review note is its own note.** The owed-turn note reports a grading
  *failure* and asks for the turns it lost; this one asks for a re-reading of
  turns that were graded fine at the time. The first draft closed on "nothing
  you leave out is taken away", which reads as licence to leave things out; it
  is now an imperative — work turn by turn, sweep the whole slot list once per
  turn, report everything any earlier turn established.

- **No cassette re-recorded.** The live-turn request is byte-identical: the
  review note rides `messages` behind the breakpoint, and `review=False` renders
  exactly what it did before.

### What the live measurement found — the ceiling is recall, not the note

Two real sessions through the tunnel, then targeted draws against the live API
(`greetings`, Opus grader). Three things, and the third is the finding:

- **The mechanism works end to end.** A perfect session skips the pass and pays
  nothing. An imperfect one logs `reviewing all 3 turn(s)`, and the card it
  produces takes nothing away.

- **Wording is not the lever it looked like.** Draws on the same transcript with
  the review note, the owed-turn note, a per-turn-sweep variant, and a widened
  `GraderResult` docstring (`slots_filled_previously` described as *earlier*
  rather than *owed*) all land inside each other's noise. The docstring change
  was reverted rather than spending a full grader re-record on a wave that
  showed nothing.

- **The grader's recall on earlier turns is weak, and that is A6's ceiling.**
  On a transcript whose turn 2 is 你叫什么名字？ — unambiguous, and credited
  ~always when it *is* the turn being graded — the pass reported it in
  `slots_filled_previously` in about 1 draw in 5. The same turn's 我叫小明 came
  back 5/5. So the review recovers an obvious earlier fill reliably and a
  question unreliably, and the owed-turn path scores the same, which means this
  is not something A6 introduced: **the recovery pass has always had it**, since
  A1, unmeasured.

  A6 is still strictly better than no pass — it only adds — but "the verdict
  re-grades with hindsight" is worth roughly one slot in five on earlier turns
  today, not the full re-read the step name promises. Raising that is the next
  piece of work and it needs a labelled set, not another prompt draft: the
  measurement above is four hand-built waves of five draws on one topic.

  **A6.5 built that set, and this paragraph is wrong.** The real rate is 86%
  (190/220 owed slots, 200 draws, four topics). One slot in five was this one
  transcript, which is the corpus's only failure — five draws could not tell
  a defect from a wave any more than three could tell A1 a fix from one.
  Left standing above because it is what A6 believed, and the correction is the
  point: see A6.5.

### A6.5 — Earlier-turn recall, measured — **shipped, PR #101**

A6 closed on a number: the session review "is worth roughly one slot in five on
earlier turns today". A6.5 built the corpus to check it, and **the number was
wrong**.

`evals/review/` is the third eval corpus in this stream, built like the other
two: finished sessions in their own files, gold labels held apart from them,
replayed off committed cassettes so the gate spends nothing. A case is a whole
session as `/api/verdict` receives it — the opening line, every turn, and the
`SessionState` the *live* grades produced, under-credited on purpose. The runner
drives `feedback.review_session` itself rather than assembling the grader call,
and records the **diff**: which slots entered `filled_at` that the client did not
submit. Ten sessions, four topics, twenty draws each.

**Baseline: 190 of 220 owed slots, over 200 draws. 0 spurious.**
Full numbers in [`evals/review/RESULTS.md`](../../evals/review/RESULTS.md).

Three things, and the third is the one that changes what A8 should do.

- **86%, not 20%.** Nine of the eleven owed slots come back at 19/20 or 20/20
  across four topics. A6's figure came from four waves of five draws on one
  transcript — and that transcript turned out to be the corpus's only real
  failure. This is A1's mistake again from the other side: A1 read three passes
  as a fix, A6 read five draws as a defect.

- **The add-only rule holds in practice.** Twenty draws of a session where the
  learner asks nothing, with two slots sitting there to be wrongly given, and
  the review gave neither. 0 spurious in 200. That is now asserted at every
  draw rather than as a rate — a slot the pass invents reaches a card the
  learner reads as truth and cannot be taken back.

- **The whole loss is one slot, and it is not explained by either obvious
  story.** `greetings/partner_name` — 你叫什么名字？ — moved through three
  positions, holding topic, slot and wording still:

  | where the question sits | recovered |
  |---|---|
  | the final turn | **20/20** |
  | the oldest turn | 11/20 |
  | mid-session | **0/20** |

  Not the wording: the same seven characters recover 20/20 as the final turn.
  Not the position: `family-size-question-mid-session` and
  `food-drinks-question-mid-session` are the same shape — three turns, the only
  owed slot on the middle one — and both recover 20/20. What is left is the
  interaction. In the 0/20 session the learner gives their own name *first*, the
  partner answers it, and `self_name` is already in the filled-slot note when
  the name is asked back. Every draw reports `self_name` and `wellbeing` and
  never mentions `partner_name`: the review reads the name exchange as one piece
  of business already credited, and the second slot inside it disappears.

**What this hands A8.** Its premise — that the prefix's weight dilutes the
instruction that matters — is now open rather than established. A corpus at 86%
with one localised failure between two sibling slots is not the picture that
premise predicts, and cutting words may not touch it. Cut the prompt on its own
merits, split it by caller because a live turn and a review are different jobs,
and measure both against this gate. If the cut does not move
`greetings-name-question-mid-session`, the honest report is that it did not.

### A7 — Feedback intake, and contesting a grade — **shipped**

A button in the app. It opens a GitHub issue with the session transcript, the
scenario, the slot state, and what the learner says went wrong.

A **contest** is the same mechanism pointed at one turn. It earns its own affordance
because there was no recourse at all: you cannot repeat yourself in a
conversation without it getting strange, so a turn graded wrong stayed graded
wrong and the learner watched it happen.

Both live in Stream A rather than Stream C because their output is eval cases. A
filed issue is handled by: write the failing case, then fix, then close. A
contested grade is a **labelled disagreement**, which is the highest-value thing
that can land in `gold.json`.

**What shipped.** `POST /api/feedback` (`backend/issues.py`), one route for both
affordances. The frontend reaches it three ways: `🐞 Report a problem` under
`⋯`, and on the verdict card; `⚑ graded wrong?` on every one of the learner's
own bubbles. The flag is the primary route for a contest — the moment you notice
a turn was graded wrong is the moment you are looking at it, and a control that
lived only on the card would ask the learner to remember which turn it was.

**The issue body is the deliverable, so it is built to be replayed.** Under the
learner's prose sits a fenced JSON block in the exact shape
`evals/coherence/cases.py` loads. For a contest it is sliced at the disputed
turn: the history *before* it, the turn itself, and the state as it stood
(`filled_at` entries earlier than the turn — the disputed slot is excluded by
construction). Handling an issue is: paste the block into
`tests/fixtures/sessions/<id>.json`, label it in `gold.json`, watch it fail, fix,
close.

The learner's claim rides *beside* the case as `claim`, never as a label, and it
carries no `coherence` tag. They are a party to the disagreement; pre-filling a
gold entry from one side is exactly the manufactured consent
`evals/coherence/cases.py` splits its label file to prevent.

**Three refusals, three codes, and the client renders each differently** — a 429
says come back, a 503 says this build cannot file at all, a 422 says the report
itself is wrong. Collapsing them into "something went wrong" would be the same
as having no recourse.

**The rate limit is global, not per-IP.** The resource being protected is a
public issue tracker, and a per-IP budget is one proxy away from unlimited. Four
an hour, `FEEDBACK_RATE_LIMIT`. A character budget (`FEEDBACK_MAX_CHARS`) sits
beside the turn-count cap because `max_length` bounds how many turns arrive, not
how big they are.

The token lives in the environment beside the Anthropic and Azure keys and never
reaches the client — not in a response, and not inside an error detail: a GitHub
failure is reported as its status, never its body.

**What it does not do.** Nothing reads these issues back automatically. Turning a
filed contest into a case is a person copying one JSON block, which is the point
at which someone decides whether the learner was right.

### A8 — Prompt weight, and moving what is left out of the prompt

**The hypothesis A6 hands to this step: the `slots_filled_previously` weakness
is context pollution, not a missing instruction.** Four wording variants moved
nothing (A6). What none of them changed is the shape of the request they sit in.

**A6.5 measured that weakness and it is smaller and stranger than this section
assumes.** The review recovers 86% of what it owes (190/220 owed slots, 200
draws, four topics) and invents nothing. The whole loss is one slot, and it
survives both explanations prompt weight would offer: the same question recovers
20/20 as the final turn, and the same mid-session shape recovers 20/20 on two
other topics. So the case for cutting stands on its own merits — a shorter
prompt is cheaper, faster and easier to reason about — and *not* on a promise to
fix recall. Measure; report what moves and what does not.

The grader's frozen prefix is 612 words. Of those, `slots_filled` gets five
paragraphs — check every slot, judge meaning not wording, credit on the ask, a
beginner's slip does not unmake it — and `slots_filled_previously` gets one
sentence: *"normally empty — leave it so."* It then closes on *"Grade the
learner's final turn. The history is context for reading it."*

So the review's instruction to sweep every turn is a note, after the breakpoint,
arguing against a prefix that has already told the model twice to do the
opposite — and the earlier turns it is asking about sit in the middle of the
messages, which is where models read worst. That is a plausible mechanism for
5/5 on the nearest earlier turn and ~1/5 on the one before it, and it explains
why wording the note differently did nothing: the note was never the load-bearing
text.

What to do about it, cheapest first:

- **Cut.** The prompt is carrying instructions for two different jobs on every
  call. Most of it is real (A3's multi-slot sweep is the fix for this stream's
  headline bug) but it is all present all the time.
- **Split the prefix by caller.** A live turn and a review are different jobs;
  giving the review its own frozen prefix — one that says *judge every turn*
  rather than *judge the final turn* — stops the note arguing with the prompt.
  It costs a second cache entry and re-records the grader's cassettes.
- **Move content out of the prompt entirely.** This is the idea worth exploring
  and it needs a correction before anyone starts: **Agent Skills do not attach to
  a plain `messages.parse` call.** They require `container={"skills": [...]}`,
  the `code_execution` tool and two beta headers — a container per call, on the
  path a learner waits behind. The API-native form of progressive disclosure here
  is deferred tool loading (`tool_search`), or simply fetching the rubric as a
  tool result rather than pinning it in the prefix. Whether either is worth it on
  a call this small is an open question; the instinct — *stop shipping every
  instruction on every call* — is right regardless of which mechanism answers it.

Ordering: this is downstream of A6.5's baseline. Cutting a prompt with no
measurement is how you lose credit you had.

## Done when

- The cassette suite runs in CI, spends nothing, and is a merge gate — the
  grader's cases and the partner's alike.
- ~~The `live` suite runs — the behavioral half in CI, the contact half on a
  schedule. Neither can rot unnoticed again.~~ **Done (A0.6).**
- All four recorded misses pass.
- ✅ The session review is measured, not asserted from a hand-built wave (A6.5): 190/220 owed slots over 200 draws, 0 spurious, gated in CI.
- ✅ The grader returns slots and nothing else (A4).
- The partner prompt fits on one screen, and the grader's does too (A8).
- ✅ A learner can file a bug, or contest a grade, in three taps (A7).

## Kickoff prompts

One per step, each runnable as written. **A0 (PR #86), A1 (PR #90), A2 (PR #91),
A3 (PR #95), A0.6 (PR #92), A1.5 (PR #96), A4 (PR #97), A5 (PR #98), A6
(PR #99), A6.5 (PR #101) and A7 (this PR) are done** — their prompts are
retired. **A8 is what is left**: it is measured against the corpus A6.5
built, and A7 keeps feeding `gold.json` with the labelled disagreements
learners file.

Every one of these ends the same way, so it is said once here: work in a git
worktree, write the failing test first, branch from `main`, conventional commits,
open a PR explaining the *why* — and **update this document in the same PR**:
mark what landed, correct what the work taught you, and leave the next prompt
runnable.

### A1.5 — record the turn runner's cassettes

Retired: shipped. The wave is recorded, both corpora replay off cassettes, and
the runner is a merge gate in the `eval` CI job. What it found is in
[`evals/turn/RESULTS.md`](../../evals/turn/RESULTS.md); the one open finding —
a stuck learner is still handed the drinks list — is deliberately not fixed
there.

### A0.6 — repair the `live` suite

Retired: shipped. The `live` marker now means "a recording cannot stand in for
this call", the behavioral half is a merge gate off cassettes, and the weekly
job runs what is left.

### A5 — the grader's input window

Retired: shipped. The grader reads the window (`2*window - 1` tail entries),
never the whole transcript, and `render_filled_note` sends the filled-slot set
that the transcript used to carry. Turn-1 cases stayed byte-identical, so only
history-bearing cassettes re-recorded. 0 missed of 55, `elliptical-ni-ne`
recovered to 5/5 — but that is one thin wave, so the 你呢 measurement below is
**not** retired with it.

**Still owed from A5's known issue — the `elliptical-ni-ne` 你呢 nudge.** A5
took the case to 5/5 by cutting the distracting self-introduction, but 5 draws
cannot tell a fix from a lucky wave. 我很好，你呢？ is the turn where "did it
follow?" and "did it fill the slot?" are the same question — 你呢 alone carries
the `wellbeing` request. A targeted 你呢 nudge tried during A4 review showed no
recovery at 5 draws, which is noise, not evidence. Doing this right is its own
step: measure the case alone at ~20–30 draws for a real baseline, *then* test
candidate nudges (prompt, or the slot's own `description`/`expressible_with`)
against it. Do it only if a wider wave shows the case is still soft; A5's clean
wave may already have settled it.

### A6 — the verdict reviews the session

Retired: shipped. `feedback.review_session` re-reads the whole conversation
before the card, adds credit and never removes it, and skips the pass on a
session with every slot already filled. What it did **not** settle is below.

### A7 — feedback intake, and contesting a grade

Retired: shipped. `POST /api/feedback` files a bug or a contested turn as a
GitHub issue whose body is a replayable eval case; the flag rides every learner
bubble, and the rate limit is global.

**What it hands the next person.** The first real contest to arrive is a case
waiting to be written. The handling loop is deliberately manual — paste the JSON
block into `tests/fixtures/sessions/`, label it in `gold.json`, watch it fail,
fix, close — because deciding whether the learner was right is the judgment the
whole stream is about, and it should not be automated on day one.

### A6.5 — earlier-turn recall, measured

Retired: shipped. `evals/review/` measures the session review against labelled
finished sessions, twenty draws a case, off cassettes. The baseline is 190/220
owed slots with 0 spurious, and the failure it found is narrower and stranger
than A6 described — see the A6.5 section above and
[`evals/review/RESULTS.md`](../../evals/review/RESULTS.md).

**Still open from it, and now a real question rather than a hunch:** why
`greetings/partner_name` is never recovered mid-session when the same words
recover 20/20 as the final turn and the same *shape* recovers 20/20 on two other
topics. A8 should test it; if a prompt cut does not move it, the next candidate
is the one A6.5's evidence points at — a slot pair the review collapses into one
already-credited event — and that is an authoring question
(`description` / `expressible_with`) before it is a prompt one.

### A8 — prompt weight

```
Read docs/streams/grading.md, the A8 section. Cut the grader's prompt, and decide
what should not be in a prompt at all.

Do A6.5 first. This step changes what the grader reads on every call, and
cutting a prompt with no baseline loses credit you had — the measurement is the
gate, not the intuition.

The finding this starts from: the frozen prefix is 612 words, `slots_filled`
gets five paragraphs, `slots_filled_previously` gets one sentence saying to
leave it empty, and the prompt closes on "Grade the learner's final turn." A6
tried four wordings of the review's note against that and moved nothing, which
is the evidence that the note is not the load-bearing text.

Read A6.5's numbers before you believe the rest of that. The review recovers
86% of what it owes (190/220 owed slots, 200 draws, four topics) with nothing
invented, and the entire loss is one slot that fails for a reason prompt length
does not explain — the same question recovers 20/20 as the final turn, and the
same mid-session shape recovers 20/20 on two other topics. **Cut the prompt
because a shorter prompt is cheaper, faster and easier to reason about, not
because it is going to fix recall.** If the cut does not move
`greetings-name-question-mid-session`, say so.

In order:

1. **Cut.** Every sentence justifies its place against the A6.5 gate. A3's
   multi-slot sweep is this stream's headline fix and stays; find what does not.
2. **Split the prefix by caller.** A live turn and an end-of-session review are
   different jobs arguing over one prompt. Give the review its own frozen prefix
   — one that says *judge every turn* rather than *judge the final turn*. It
   costs a second cache entry (they are per-model and per-prefix) and re-records
   the grader's cassettes.
3. **Then ask what should not be shipped on every call at all.** One correction
   before you start: **Agent Skills do not attach to a plain `messages.parse`
   call** — they need `container={"skills": [...]}`, the code-execution tool and
   two beta headers, which is a container per call on the path the learner waits
   behind. The API-native form of progressive disclosure here is deferred tool
   loading (`tool_search`), or fetching the rubric as a tool result instead of
   pinning it in the prefix. On a 612-word prefix that may not pay; establish
   that it does before building it.

Report every change as a rate against the A6.5 baseline, with its sample count.
A prompt cut that reads better and grades worse is a regression.
```
