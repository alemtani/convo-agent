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

### A0 — The cassette layer (first, everything else depends on it)

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

### A1 — The failing cases

The record of the bug, written before anything is fixed. Fail-to-pass cases from
the four real sessions above.

They are written before the cuts even though the fix lands after, because a bug
with no case is a bug that comes back.

### A2 — The cuts

- `topic_tags`, `should_give_feedback`, `grammar_notes` — model, prompt,
  frontend. Decide the notes panel: verdict-derived, or gone.
- `depends_on` — model, four `topic.md` files (via the `kb-topic` skill, never by
  hand), `validate.py` cycle check, `termination.py` guard, `docs/SCENARIOS.md`.
- The partner prompt, trimmed to persona, scene, band ceiling, pinyin reading.

Re-run the full eval set after. This is the change most likely to move numbers in
a direction nobody intended.

### A3 — The multi-slot fix

Rewrite the `slots_filled` instruction so multi-fill is the leading rule, not a
subordinate clause. A1's cases go green.

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
- All four recorded misses pass.
- The grader returns slots and nothing else.
- The partner prompt fits on one screen.
- A learner can file a bug, or contest a grade, in three taps.

## Kickoff prompt

```
Read docs/streams/grading.md. Start Stream A at A0: the cassette layer for the
eval harness.

Build a record/replay wrapper for Anthropic calls under evals/, keyed on
sha256 of model + system + tools + messages + params. Cassettes commit to the
repo. A key miss fails loudly unless --record is passed. It wraps the same seam
evals/coherence/replay.py already uses; it must not touch backend/.

Build it rather than adopting pytest-recording/VCR.py — that was evaluated and
declined; see the spec. Every Anthropic call today is a non-streaming
messages.parse, which keeps this small.

Record N samples per key and assert against the distribution, not one draw
(replay.py already has --repeat). No probabilistic live calls: CI stays
deterministic and freshness is handled by a scheduled re-record job.

Write the failing tests first. Branch from main, conventional commits, open a PR
explaining why the eval gate had to stop costing money before the grader work
could start.
```
