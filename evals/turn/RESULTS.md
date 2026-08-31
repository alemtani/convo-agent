# A1.5 — the partner, measured

`evals/turn/replay.py` through `orchestrator.run_text_turn`, 5 samples
each, recorded 2026-08-28 against `main` at A6. Conversation on
`claude-sonnet-5`, grader on `claude-opus-5`, withholding judge on
`claude-sonnet-5`.

This is the first eval that runs the partner. A2 cut a third of the
system prompt with nothing to replay, and A4 made the partner's
`coherent` tag a gate on the learner's credit with nothing to replay
either. These numbers are that baseline.

The first recording of this wave was taken before A3–A6 landed. Those
changes moved the partner and grader prompts, so every key missed on the
merge and the wave was re-recorded. What follows is the current one; the
earlier findings that no longer hold are called out below.

## Does the partner's `coherent` agree with a fair reader?

**Yes, on this corpus — 55 of 55.**

```
coherence — gold \ observed
  coherent    coherent=40  incoherent=0
  incoherent  coherent=0   incoherent=15
```

Eleven cases, five draws each, against the labels in
`tests/fixtures/sessions/gold.json`. No false positive and no false
negative. A tag that fired on a coherent turn would be a point the
learner earned and did not get; none did.

Read this as a floor, not a ceiling. The corpus is eleven recorded
turns chosen because they were hard for the *grader*, so it is thin on
the near-misses a coherence judgment is worst at. The number that
matters is that the gate A4 shipped unmeasured is no longer unmeasured.

## Does the partner honour `withholding`?

**On the probes written to catch it: yes, 10/10.**

The failure seen in real sessions was: the learner says 你好, the partner
names the day's dish, and `recommendation` is gone before the learner
asks. `greeting-only` and `i-am-hungry` are that moment, constructed so
the learner asks for nothing.

| probe | replies (5 samples) | volunteered |
| --- | --- | --- |
| `greeting-only` (你好) | 你好，要吃什么？/ 想吃什么？/ 你要吃什么？/ 想吃点什么？ | none, 5/5 |
| `i-am-hungry` (我很饿) | 你想吃什么？ ×5 | none, 5/5 |

The partner asks. It does not name a dish, and it does not list drinks.
A2's scene-as-withholding holds on the red-team turns written to break
it.

Two leaks remain, and neither is a greeting.

### A stuck learner still gets the drinks list — 2/5

`derailed-input` is a recorded turn: the partner has just asked
你要喝什么？, and the learner says they do not know how to say this,
half in English.

| reply | volunteered |
| --- | --- |
| 没关系，你想吃什么？ ×3 | none |
| 没关系，你想喝茶还是水？ | `drinks` |
| 没关系，你要喝茶还是水？ | `drinks` |

The learner did not ask what there is to drink. The partner asked, then
answered its own question when the learner froze. A brisk server doing
that is natural. It is also the `drinks` slot handed over, and a point
the learner can now never earn.

That is either a prompt bug — being a person wins over the scene when
the customer is stuck — or an authoring bug, `withholding` not covering
the recovery path. This PR does not decide. It records the case.

### An incoherent ask gets answered anyway — 5/5

`nonsequitur-slot-fill` is new since A4 and only visible now. The partner
asked about drinks; the learner asks 什么菜最好吃？. The partner answers
鱼最好吃 every run, and the judge calls `recommendation` volunteered
every run.

The learner *did* ask, so on the face of it this is the scene working.
It reads as volunteering because of how the two halves compose: A4 makes
an incoherent turn earn nothing, so `slots_filled` is empty, so the
"already credited on this turn" exemption in `withholding.candidates`
never fires, so the answered slot is still a candidate.

Which is the bug is a real question, not a rounding error:

- **The instrument.** The exemption keys on *credit*, and it should
  arguably key on the *ask*. A learner who asked was answered.
- **The scene.** The learner asked incoherently, was told the answer,
  and can now never earn `recommendation` — which is precisely the
  failure this eval exists to catch, gate or no gate.

Not decided here either. It is the composition of A4 and the withholding
judge, and it deserves its own change.

No other session case volunteered anything.

## Did A2's trimmed prompt change the partner?

The partner still answers when asked, stays in band, and keeps replies
to one short sentence. Cutting reciprocity, stay-in-character, and
`forgiveness_level` did not produce the "names the day's dish on 你好"
failure. The scene block is doing the job those sentences were written
for.

What *is* newly visible is the converser's **reading**, because this
runner grades that reading rather than the fixture's 汉字.

| case | fixture 汉字 | reading (5 samples) | slots |
| --- | --- | --- | --- |
| `clip-and-tea` | 我要一个夹子和一杯茶 | 饺子, 5/5 | `order`, 5/5 — same as grader-only |
| `milk-and-biscuits` | 你要一个牛奶和三个饼干 | 我要 ×4, 你要 ×1 | all three slots 5/5 — same as grader-only |
| `computer-work-ni-ne` | 我做电脑工作，你呢 | same sense, 5/5 | `partner_origin` 3/5; grader-only 5/5 |

The first recording of this wave reported `milk-and-biscuits` as a place
where the turn runner credited `order` and the grader-only runner did
not, and warned against reading that as A3. **That gap is gone**: A3
shipped, and both runners now credit all three slots on every draw. The
warning is retired with it.

`computer-work-ni-ne` is the one case where the two runners still differ.
The learner's 你呢 bounces both of the partner's questions back, and
`partner_origin` is a `request` slot, so the ask alone earns it: the
grader-only runner credits it 5/5. The turn runner credits it 3/5.

The partner's reply is the same in substance across all five draws —
我是学生, sometimes with 我在大学学习 — and it states no origin in any
of them. So the 3/5 is not the partner behaving differently on two runs;
it is the grader wavering once the reply is in its window and the reply
answers only half of what was asked. Worth knowing, and worth watching:
a slot the learner earned by asking should not depend on whether the
partner got round to answering it. Not chased here.

## The gate

Replay is free off the committed cassettes. A key miss fails CI, and a
miss means a prompt moved. The probes assert `volunteered == ()`. The two
leaks above are written up, not asserted: one is 2/5, the other is a
design question, and pinning either as an expectation would freeze a bug
into the corpus.

```bash
python -m evals.turn.replay --repeat 3
python -m evals.turn.replay --repeat 3 --cases-dir evals/turn/cases \
    --out evals/turn/observations.probes.json
```

The probe corpus has no `gold.json` and needs none: the probes ask
nothing, so there is no coherence judgment to score. `replay.py` skips
the 2×2 when a corpus ships no labels, and still refuses a *partial*
label file — that is a hole, not a different question.
