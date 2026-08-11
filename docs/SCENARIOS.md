# Goal-Oriented Scenarios — Design Specification

Companion to [`DESIGN.md`](DESIGN.md). That document specifies the turn loop,
caching, and session lifecycle. This one specifies **what a session is trying to
achieve, and how the system knows when it got there**.

Status: designed, not built. Covers milestone **M2 — scenarios** (issues #28–#32).

---

## The problem

A session should put the learner in a situation with an obstacle — *"buy three
pieces of fruit and find out what they cost"* — and grade whether they got
through it. Two things block that:

1. **A scenario can be finished in one turn.** `greetings` has no real obstacle;
   its stated goal is essentially the whole topic. Nothing in the KB format stops
   an author from writing a goal that one utterance satisfies.
2. **Nothing can tell exactly when the goal is met.** A prose "hidden success
   criteria" section gives a model an opinion to render, not a fact to check.

Both have the same cause: **the goal is authored as prose for a model to judge,
instead of as state for code to check.**

## The core idea

> A scenario's goal is a **set of named binary facts** the learner must establish
> through Chinese. Goal completion is a set comparison, not a judgment.

Everything below follows from that sentence.

Prior art, in short. Task-oriented dialogue systems solved goal completion before
LLMs: [MultiWOZ](https://arxiv.org/pdf/1810.00278) scores dialogues over
**slots** — *informable* slots are constraints the user must convey, *requestable*
slots are facts the user must extract — and Success means every requested slot was
provided. [τ-bench](https://openreview.net/forum?id=roNSXZpUDN) grades agents by
deterministically comparing final state against an annotated goal state,
irrespective of trajectory. Rubric research points the same way:
[CheckEval](https://arxiv.org/abs/2403.18771) shows that decomposing evaluation
into boolean checklists raises cross-model agreement by 0.45 and cuts variance,
and moving from partial credit to
[binary criteria](https://arxiv.org/html/2606.08625v2) adds roughly 20 points of
agreement. HealthBench ships 48,562 binary criteria on that basis.

We take the slot vocabulary from the first, final-state grading from the second,
and binary decomposition from the third.

---

## Seed format

Slots are **authored**, not generated. (Decided 2026-08-10: achievability inside a
band-1–2 vocabulary is exactly where a model drifts, and an unachievable scenario
is an unwinnable session.) They live in `topic.md` frontmatter, so they ride
inside the already-cached KB block — see [Caching](#caching) below.

```yaml
id: shopping
display_name: "Shopping (买东西)"
target_vocab: [买, 要, 水果, 三, 个, 多少, 钱, 块, 什么, 请问, 还, 别的, 谢谢, 再见]
scenario:
  situation: "You're at a fruit stall. The vendor greets you."
  goal: "Buy three pieces of fruit, and find out what they cost."
  target_turns: 4
  max_turns: 6
  slots:
    - id: item
      kind: inform
      description: "Say you want fruit"
      expressible_with: [水果, 要, 买]
    - id: quantity
      kind: inform
      description: "Say how many — three"
      expressible_with: [三, 个]
    - id: price
      kind: request
      description: "Find out what they cost"
      expressible_with: [多少, 钱]
      depends_on: [item]
```

| Field | Purpose |
| --- | --- |
| `situation`, `goal` | **English**, learner-visible. A band-1 learner cannot read a Chinese task description. Rendered on the scenario card. |
| `kind: inform` | The learner must **convey** this to the partner. |
| `kind: request` | The learner must **extract** this from the partner. |
| `description` | English. Feeds the verdict card ("you never found out the price"). |
| `expressible_with` | KB vocab that can express the slot. Lets `validate.py` check the slot is *achievable* with `target_vocab` at the current band ceiling. |
| `depends_on` | Slot ids that must be filled first. Makes the slot set a small DAG. |

`expressible_with` is a **hint to the extractor, not a string matcher.** The
extractor decides semantically whether the fact was established; the seed only
constrains *which facts count*. Rigid about **what**, flexible about **how**.

### One rule that is easy to miss

**A `request` slot is filled only when the learner asks and the partner answers.**
If the vendor volunteers the price unprompted, the learner never did the work, and
the slot must not be credited. This is why the phase hint (below) tells the partner
to leave room rather than to be helpful.

---

## Minimum turns is derived, never authored

A `request` slot cannot be filled in the learner's opening turn: they must ask,
and the partner must answer. So a floor falls out of the slot graph — the longest
path through the DAG, with each `request` edge costing an ask-and-answer
round-trip.

```
min_turns = longest_path(slots, depends_on)   # request edges cost a round-trip
```

For the fruit stall: `item` and `quantity` can share one turn (*我要三个水果*),
then `price` needs a turn to ask. **`min_turns = 2`.**

Do **not** author `min_turns` as a number. An authored integer drifts out of sync
with the criteria it is supposed to describe, and the drift is silent. Deriving it
means `validate.py` can **reject a scenario satisfiable in one turn** — which is
precisely the defect this milestone exists to fix.

The authoring lever for "make this take longer" is therefore a dependency edge or
another request slot, not a bigger integer.

---

## Runtime: three tiers

| Tier | When | Cost | Job | How it's verified |
| --- | --- | --- | --- | --- |
| **Slot tracker** | every turn | folded into the existing conversation call | which slot ids this turn newly satisfies | contract test on recorded transcripts; assert the exact id set |
| **Termination** | every turn | **no model** | `complete` iff all slots filled, or `turn ≥ max_turns` | pure unit test |
| **Verdict** | once, at `complete` | one call | *why* it went the way it did, plus an in-band model exchange | structural invariants only; judgment quality behind `live` |

Two deliberate departures from the obvious design.

**The per-turn signal is a set of ids, not a progress score.** A scalar cannot say
*which* fact is missing, so it can drive neither a useful phase hint nor a grounded
verdict. With ids, the hint can say *"the learner still hasn't asked the price."*
That is a pedagogical lever a scalar cannot provide. Compare
[process vs. outcome supervision](https://arxiv.org/pdf/2504.16828): step-level
signal should be cheap and mechanical; judgment belongs at the end.

**`goal_met` is computed, then handed to the verdict worker — the judge never
decides it.** A judge asked *"did they succeed?"* grades generously; a judge told
*"they never got the price — explain that and demonstrate the ask"* cannot. This
matters because our partner replies and our grader come from the same model
family, so leniency and self-preference bias are live risks, and the literature is
explicit that [instructing a judge out of its biases with a rubric line does not
work](https://arxiv.org/pdf/2604.23178). We remove the decision instead of
prompting against it.

Slot state is **monotone**: a filled slot never un-fills. That keeps correctness
honest and stops the UI oscillating between `wrapping` and `active`.

### Bounding

Three thresholds instead of two:

```
turn < min_turns          →  no wrap-up hint, ever. The goal is not yet reachable.
min_turns ≤ turn < target →  nudge toward the missing slot (not toward goodbye)
turn ≥ target_turns − 1   →  wrap up AND nudge the missing slot
turn ≥ max_turns          →  force the closing turn
```

The session ends on **all slots filled OR budget exhausted**. Budget alone makes a
learner who succeeds on turn 2 sit through pointless turns; slots alone never
terminate a failing session.

**Guard:** if the tracker reports every slot filled *before* `min_turns`, that is a
tracker bug or a scenario bug. Log it; do not silently accept it. This is the
endpoint-ambiguity failure mode that
[judge-free completion detection](https://arxiv.org/html/2511.03047) reports as
its own worst case — where "goals complete in initial turns with subsequent
clarifications" — showing up in our system.

### Caching

Slots are authored, so they already sit in the KB block behind the `cache_control`
breakpoint. **This adds no new cache surface.** The volatile per-turn addition is
just `filled: [ids]` and the phase hint, both after the breakpoint.

It also de-risks the sketch worker, which the build order calls the long pole: the
sketch generates **flavor only** — opening line, vendor persona, what's on the
stall. The criteria never pass through a model, so there is no generated block to
freeze and round-trip through the client.

---

## Worked example 1 — happy path

Scenario as authored above. `min_turns` 2 (derived) · `target_turns` 4 ·
`max_turns` 6.

**On screen, before the first turn:**

> **Your situation.** You're at a fruit stall. The vendor greets you.
> **Your goal.** Buy three pieces of fruit, and find out what they cost.

The slots are not shown. The learner sees the goal in English; the machine-checkable
form stays hidden.

**Opening line** (partner; does not consume budget):

> 你好！你要买什么？
> _nǐ hǎo! nǐ yào mǎi shénme?_ — "Hello! What do you want to buy?"

---

**Turn 1** — learner says *我要水果。* (`wǒ yào shuǐguǒ` — "I want fruit.")

```
tracker  → slots_filled: [item]
state    → {item}                     1/3
turn 1 < min_turns 2                  → wrap-up hint SUPPRESSED
hint     → "learner has not stated a quantity"
```

> 好！你要多少个？
> _hǎo! nǐ yào duōshao gè?_ — "Good! How many do you want?"

The partner asks a question expecting a 2–4 word answer. That's the
beginner-disfluency mitigation from `DESIGN.md`, and here it doubles as
scaffolding toward the `quantity` slot.

---

**Turn 2** — learner says *三个。* (`sān gè` — "Three.")

```
tracker  → slots_filled: [quantity]
state    → {item, quantity}           2/3
turn 2 == min_turns; price missing
hint     → "learner has not asked the price. Do NOT volunteer it. Leave room."
```

> 好，三个水果。
> _hǎo, sān gè shuǐguǒ._ — "OK, three pieces of fruit."

**This is the load-bearing moment.** A helpful partner would say *"that's nine
kuai"* — and the learner would reach the end of the session never having asked a
question. The `request` slot would be satisfied by the *partner's* effort. The
phase hint exists to stop exactly that.

---

**Turn 3** — learner says *多少钱？* (`duōshao qián?` — "How much?")

> 九块。
> _jiǔ kuài._ — "Nine kuai."

```
tracker  → slots_filled: [price]
state    → {item, quantity, price}    3/3
termination (pure Python): filled ⊇ required  → COMPLETE
```

Session ends at turn 3, under a target of 4. Mic disabled. No further turns —
success terminates the session rather than padding it out.

**Verdict worker receives:** `goal_met=True`, the fill order, the transcript, and
the accumulated tone/grammar notes. It explains; it does not decide.

> **You did it.** You said what you wanted, how many, and you asked the price —
> that last one is the hard part, and you got it in three turns.
> One thing to work on: your 3rd tone on 水 flattened toward 2nd. It should dip
> and come back up — *shuǐ*.

---

## Worked example 2 — unhappy path

Same scenario. The learner never asks the price.

**Opening line:**

> 你好！你要买什么？
> _nǐ hǎo! nǐ yào mǎi shénme?_

---

**Turn 1** — *我买水果。* (`wǒ mǎi shuǐguǒ` — "I'm buying fruit.")

```
state → {item}                        1/3     turn 1 < min_turns → hint suppressed
```

> 好！你要多少个？
> _hǎo! nǐ yào duōshao gè?_

**Turn 2** — *三个水果。* (`sān gè shuǐguǒ` — "Three pieces of fruit.")

```
state → {item, quantity}              2/3     turn 2 ≥ min_turns
hint  → "price unasked. Do not volunteer it. Create an opening to ask."
```

> 好。你还要问什么吗？
> _hǎo. nǐ hái yào wèn shénme ma?_ — "OK. Is there anything else you want to ask?"

An in-band invitation to ask, which hands over no answer. The learner has been
given a clean shot at the missing slot.

**Turn 3** — *谢谢。* (`xièxie` — "Thank you.")

```
tracker  → slots_filled: []            no new slots
state    → {item, quantity}            2/3
annotation → coherence: drifting       (learner is trying to close early)
turn 3 ≥ target_turns − 1              → wrap up AND nudge
```

> 不客气。三个水果，好。
> _bú kèqi. sān gè shuǐguǒ, hǎo._ — "You're welcome. Three pieces of fruit, right."

**Turn 4** — *再见。* (`zàijiàn` — "Goodbye.")

```
state → {item, quantity}               2/3     still missing: price
turn 4 < max_turns 6, but the learner has closed twice
```

> 再见！
> _zàijiàn!_

```
termination: filled ⊅ required, and the partner has closed  → COMPLETE (goal unmet)
```

**Verdict worker receives:** `goal_met=False`, `missing=[price]`, the transcript,
the notes. Note what it is *not* asked to decide. It is told the learner failed,
told which fact they never established, and asked to teach.

> **Not quite.** You bought the fruit — you said what you wanted and how many —
> but you never found out what it cost, which was the other half of the goal. You
> closed the conversation twice before asking.
>
> **What you could have said**, after *三个水果*:
>
> | 汉字 | pinyin | English |
> | --- | --- | --- |
> | 请问，多少钱？ | qǐng wèn, duōshao qián? | "Excuse me, how much is it?" |
> | 九块。 | jiǔ kuài. | "Nine kuai." |
> | 好，谢谢！ | hǎo, xièxie! | "OK, thanks!" |
>
> Every word there is in this topic's vocabulary. 多少钱 is the phrase to
> memorise — it works for anything you ever want to buy.

Two properties this exchange must satisfy, and which are **testable without
asserting model text**: every word is in the topic KB ∪ the HSK ceiling, and it
is 3–4 lines. A "what you should have said" the learner cannot read teaches
nothing.

---

## Worked example 3 — the authoring guardrail bites

The obvious way to write this scenario is *"buy three **apples**"*. Try it:

```yaml
    - id: item
      kind: inform
      description: "Say you want apples"
      expressible_with: [苹果, 要]
```

```
$ python kb/zh/_tools/validate.py kb/zh/shopping
ERROR shopping: slot 'item' expressible_with 苹果 is HSK band 3, above ceiling 2
ERROR shopping: slot 'item' expressible_with 苹果 not in target_vocab
```

苹果 is band **3**. At a ceiling of 2 the learner has never been taught it, so
the scenario is unwinnable — the exact failure mode the authored-not-generated
decision was made to prevent, caught here by a deterministic script instead of by
a confused learner. 水果 (band 1) is the in-band choice. 香蕉 (3) and 老板 (3)
fail the same way.

And a scenario with no obstacle:

```yaml
  slots:
    - id: greeting
      kind: inform
      description: "Greet the vendor"
      expressible_with: [你好]
```

```
ERROR shopping: no request slots and no depends_on edges — goal is
      satisfiable in one turn (derived min_turns = 1, minimum is 2)
```

That error message *is* the fix for problem 1. It is enforced at authoring time,
by the same script that already guards vocabulary scope — consistent with how
this repo guards the KB generally (`validate.py`, not pytest; see `CLAUDE.md`).

---

## What `validate.py` must reject

| Rule | Catches |
| --- | --- |
| every `expressible_with` word ∈ `target_vocab`, at or below the ceiling | unachievable slot (the 苹果 case) |
| zero `request` slots **and** zero `depends_on` edges | one-turn-satisfiable scenario |
| derived `min_turns` < 2 | same, via the derivation |
| derived `min_turns` > `target_turns` − 1 | budget cannot fit the goal |
| duplicate slot ids; cycle in `depends_on` | malformed DAG |
| `situation` / `goal` non-empty and ASCII-only | a Chinese task description a band-1 learner can't read |

---

## Testing

The reframe converts most of this into deterministic logic, which is exactly what
`CLAUDE.md` wants a failing test first for.

- **`min_turns` derivation** from a slot DAG — pure function, table-driven.
- **Termination** over `(filled_set, turn_index)` — pure function.
- **Monotonicity** — property test: replaying any transcript prefix never un-fills
  a slot.
- **`validate.py` rejections** — one fixture topic per rule above, each of which
  must fail.
- **Slot extraction** — contract test against recorded responses; assert the
  exact id set, never model text.
- **Verdict** — structural invariants only: valid JSON, `goal_met` is a real
  boolean matching the computed one, model answer ⊆ KB vocab ∪ ceiling, 3–4 lines.
- **Prompt-cache invariant** — the existing byte-stability assertions extend to
  cover the scenario block; slots are in the frozen prefix, `filled` is not.

**New: a simulated learner, for tuning the verdict worker offline.** A cheap model
(Haiku 4.5) roleplaying a band-1 learner with a scripted failure mode — never asks
the price, wrong measure word, closes early. Marked `@pytest.mark.live`. This is
how the verdict prompt gets iterated without a human in the loop and without
blind guessing; it bills `ANTHROPIC_API_KEY`, so batch it against recorded
transcripts rather than live sessions.

Caveat from the literature: user simulators are
[proxies, not ground truth](https://arxiv.org/pdf/2510.05444). Use the harness to
catch structural regressions, not to certify teaching quality.

---

## Sequencing (M2 — scenarios)

| Issue | Scope | Change from original plan |
| --- | --- | --- |
| **#28** | scenario slots in `topic.md`, six `validate.py` rules, `kb.py` parsing, migrate `greetings` | **Grows.** Now the load-bearing PR of the milestone. |
| **#30** | sketch worker — flavour only (opening line, persona, stall contents) | **Shrinks.** Criteria come from the KB; nothing that must not drift passes through a model. |
| **#31** | slot tracker in the conversation worker + pure-Python termination + slot-aware phase hints | **Absorbs** #32's `goal_met`. Scalar `goal_progress` replaced by an id set. |
| **#32** | verdict worker — explains a *computed* outcome, plus the in-band model exchange | **Explains rather than decides.** |
| **#29** | topics API + per-topic client store + author 6–8 topics with slots | **Moves last.** Prove the format on one scenario end-to-end before authoring seven more. |

The `GET /api/topics` and per-topic-store half of #29 has no dependency on
scenario mechanics and can land earlier if the hardcoded `topic_id` becomes
annoying first.

---

## Known risk

Authored slots make scenarios rigid. A learner who reaches the goal by an
unanticipated but valid route might not trip the slot.

The `expressible_with`-is-a-hint rule is the mitigation: the extractor judges
semantically whether the fact was established, and only the *set of facts that
count* is fixed. If real use shows the extractor being too strict, the fix is
extractor prompting, not more vocabulary in the seed.

Watch for the mirror-image failure too — a tracker that credits `price` when the
vendor volunteered it. Both directions are catchable with recorded-transcript
fixtures, which is why slot extraction gets a contract test rather than an eval.
