# A1.5 — first look at the partner

`evals/turn/replay.py` through `orchestrator.run_text_turn`, 3 samples
each, recorded 2026-08-26. Conversation on `claude-sonnet-5`, grader on
`claude-opus-5`, withholding judge on `claude-sonnet-5`.

This is the first eval that runs the partner. A2 cut a third of the
system prompt with nothing to replay. These numbers are that baseline.

## Does the partner honour `withholding`?

**On the probes written to catch it: yes. On a stuck learner: not always.**

The failure seen in real sessions was: the learner says 你好, the partner
names the day's dish, and `recommendation` is gone before the learner
asks. `greeting-only` and `i-am-hungry` are that moment, constructed so
the learner asks for nothing.

| probe | reply (3 samples) | volunteered |
| --- | --- | --- |
| `greeting-only` (你好) | 你好，几位？ / 你好，要吃什么？ ×2 | none, 3/3 |
| `i-am-hungry` (我很饿) | 你要吃什么？ / 你想吃什么？ ×2 | none, 3/3 |

The partner asks. It does not name a dish. It does not list drinks. A2's
scene-as-withholding holds on the red-team turns that were supposed to
break it.

The leak that remains is not a greeting. It is a stuck learner.

`derailed-input` is a recorded turn: the partner has just asked
你要喝什么？, and the learner says they do not know how to say this,
half in English. Two of three replies then offer the drinks:

| run | reply | volunteered |
| --- | --- | --- |
| 1 | 没关系，你想喝茶还是水？ | `drinks` |
| 2 | 没事，你要吃什么？ | none |
| 3 | 没关系，你要喝茶还是水？ | `drinks` |

The learner did not ask what there is to drink. The partner asked, then
answered its own question when the learner froze. A brisk server doing
that is natural. It is also the `drinks` slot, handed over, and a point
the learner can now never earn.

That is either a prompt bug (being a person wins over the scene when
the customer is stuck) or an authoring bug (`withholding` does not
cover the recovery path). This PR does not decide. It records the
case.

No other session case volunteered. Asked slots were answered; unasked
ones were not, except this.

## Did A2's trimmed prompt change the partner?

The partner still answers when asked, stays in band, and keeps replies
to one short sentence. Cutting reciprocity, stay-in-character, and
`forgiveness_level` did not produce the "names the day's dish on 你好"
failure. The scene block is doing the job those sentences were written
for, on the probes.

What *is* newly visible is the converser's **reading**, because this
runner grades that reading rather than the fixture's 汉字.

| case | fixture 汉字 | reading (samples) | grade vs grader-only |
| --- | --- | --- | --- |
| `clip-and-tea` | 我要一个夹子和一杯茶 | 饺子, 3/3 | `order` both runners |
| `milk-and-biscuits` | 你要一个牛奶和三个饼干 | 你要 ×1, 我要 ×2 | `order` on the two 我要 runs; grader-only drops `order` 3/3 |
| `computer-work-ni-ne` | 我做电脑工作，你呢 | same sense, 3/3 | `partner_origin` dropped both runners, 3/3 |

Do not treat the turn runner crediting `order` on two `milk-and-biscuits`
runs as A3. Those wins are 你要 becoming 我要 in the reading, not the
grader noticing a dense turn. The grader-only runner still drops
`order` 3/3, and that is the case A3 has to clear.

`computer-work-ni-ne` is unchanged: 你呢 does not bounce `partner_origin`.
One partner reply even asks 你从哪儿来？ instead of answering origin —
a different miss, and not volunteering.

## The gate

Replay is free off the committed cassettes. A key miss fails CI. The
probes assert `volunteered == ()`. `derailed-input` is written up, not
asserted: it is 2/3, and fixing it is a prompt-or-authoring decision
of its own.

```bash
python -m evals.turn.replay --repeat 3
python -m evals.turn.replay --repeat 3 --cases-dir evals/turn/cases
```
