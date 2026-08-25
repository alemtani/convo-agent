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
followed from it. The partner is the only party that knows what it meant. Move
it.

The result is a grader with exactly one job: which slots did this turn fill.

### 3. The grader reads the whole transcript

It is sent the full dialogue for context. Once it only judges slots it needs the
partner's last line, the learner's turn, and the set of slots already filled.
Everything else is input tokens and thinking time on the branch that gates the
session.

### 4. Dead weight in `ConverserAnnotation`

`topic_tags` and `should_give_feedback` are consumed by nothing. They are still
described in the system prompt, so the partner spends output tokens and attention
producing them on every turn.

`grammar_notes` is consumed (`frontend/index.html:874`) but sits on the wrong
worker. The partner's only question is "did I understand". Judging whether a
grammar slip is worth coaching is a coach's question. Either move it to the
grader or drop it and let the verdict worker read the transcript.

`learner_said_goodbye` stays. It drives termination (`orchestrator.py:418`), and
noticing a goodbye is something any listener does.

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

Non-determinism is real: the same key can produce different text on a real call.
That is what the cassette pins. Recording is deliberate and reviewed in the diff.

### A1 — The multi-slot fix

Fail-to-pass cases, from the real sessions above, before any prompt edit.

Then rewrite the `slots_filled` instruction so multi-fill is the leading rule,
not a subordinate clause.

### A2 — Coherence moves to the partner

Add `coherence` to `ConverserAnnotation`. Remove it from the grader prompt and
`GraderResult`.

Watch: `evals/coherence/` measures `coherence` against `gold.json`. The label set
survives the move — the same turns get the same labels — but the harness reads
the field off a different worker. `endpoint.py` already leaves `coherence`
unobserved on the wire and stays that way.

### A3 — The grader's input window

Send the partner's last line, the learner's turn, and the filled-slot set.

Keep the frozen prefix byte-identical (the cache invariant test still applies).
The window shrinks what goes *after* the breakpoint.

`render_window_note` and the owed-turn recovery path must keep working: a turn
settling a debt needs the earlier turns it never judged.

### A4 — Prompt trim and dead-field removal

Cut `topic_tags` and `should_give_feedback` from the model, the prompt, and the
frontend. Decide `grammar_notes`: move to grader, or drop.

Trim the partner prompt. Re-run the full eval set after — this is the change most
likely to move numbers in a direction nobody intended.

### A5 — Remove `depends_on`

Model, four `topic.md` files (via the `kb-topic` skill, never by hand),
`validate.py` cycle check, `termination.py` guard, `docs/SCENARIOS.md`.

### A6 — Feedback intake

A button in the app. It opens a GitHub issue with the session transcript, the
scenario, the slot state, and what the learner says went wrong.

This is in Stream A and not Stream C because its output is eval cases. A filed
issue is handled by: write the failing case, then fix, then close.

Needs a server-side token and a rate limit. The client never sees the token.

## Done when

- The cassette suite runs in CI, spends nothing, and is a merge gate.
- All four recorded misses pass.
- The grader returns slots and nothing else.
- The partner prompt fits on one screen.
- A learner can file a bug in three taps.

## Kickoff prompt

```
Read docs/streams/grading.md. Start Stream A at A0: the cassette layer for the
eval harness.

Build a record/replay wrapper for Anthropic calls under evals/, keyed on
sha256 of model + system + tools + messages + params. Cassettes commit to the
repo. A key miss fails loudly unless --record is passed. It wraps the same seam
evals/coherence/replay.py already uses; it must not touch backend/.

Write the failing tests first. Branch from main, conventional commits, open a PR
explaining why the eval gate had to stop costing money before the grader work
could start.
```
