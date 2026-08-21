# Goal-Oriented Scenarios — Design Specification

Companion to [`DESIGN.md`](DESIGN.md). That document specifies the turn loop,
caching, and session lifecycle. This one specifies **what a session is trying to
achieve, and how the system knows when it got there**.

Status: **built.** Milestone **M2 — scenarios** shipped as issues #28–#32
and #29 (closed 2026-08-12 / 2026-08-13). This document is the spec the
code implements. If a sentence below still talks as if M2 is future work,
the code and [`AGENTS.md`](../AGENTS.md) win.

What the first real session then found — a tracker too strict, and a verdict
that invents rules to explain it — is in [`Known risks`](#known-risks) and
planned in [`ACCESSIBILITY.md`](ACCESSIBILITY.md).

---

## The problem

A session should put the learner in a situation with an obstacle — *"buy three
pieces of fruit and find out what they cost"* — and grade whether they got through
it. Two things block that:

1. **A scenario can have no real obstacle.** `greetings` had none when this was
   written; its stated goal was essentially the whole topic. Nothing in the KB
   format stops an author writing a goal that one utterance satisfies. (It has
   one now — `topic.md` authors two `request` slots, and no amount of packing
   lets a learner know a name they were never told. The format problem this
   describes is what the slot design fixed.)
2. **Nothing can tell exactly when the goal is met.** A prose "hidden success
   criteria" section gives a model an opinion to render, not a fact to check.

Both have the same cause: **the goal is authored as prose for a model to judge,
instead of as state for code to check.**

## The core idea

> A scenario's goal is a **set of named binary facts** the learner must establish
> through Chinese. Goal completion is a set comparison, not a judgment.

Everything below follows from that sentence — including, as it turns out, the
absence of most of the turn counters an earlier draft of this document carried.

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
is an unwinnable session.) They live in `topic.md` frontmatter, so they ride inside
the already-cached KB block — see [Caching](#caching).

```yaml
id: shopping
display_name: "Shopping (买东西)"
target_vocab: [买, 要, 水果, 三, 个, 多少, 钱, 块, 什么, 请问, 谢谢, 再见]
scenario:
  situation: "You're at a fruit stall. Nothing on it is priced. The vendor greets you and waits."
  goal: "Buy three pieces of fruit, and find out what they cost."
  withholding: "Nothing at the stall carries a price tag. The vendor is busy and says little: they name a price only when a customer asks for one."
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
| `withholding` | **English**, partner-facing, never learner-visible. What this scene does not hand over unasked — see [The scene has to create the gap](#the-scene-has-to-create-the-gap). Required once the scenario has a `request` slot. |
| `kind: inform` | The learner must **convey** this to the partner. |
| `kind: request` | The learner must **extract** this from the partner. |
| `description` | English. Feeds the verdict card ("you never found out the price"). |
| `expressible_with` | KB vocab that can express the slot. Lets `validate.py` check the slot is *achievable* with `target_vocab` at the current band ceiling. |
| `depends_on` | Slot ids that must be filled first. Used **only** by the tracker sanity guard — see [Guards](#guards). It does not affect the turn budget. |

**No turn counts are authored.** The budget is derived — see
[Turn budget](#turn-budget).

`expressible_with` is a **hint to the extractor, not a string matcher.** The
extractor decides semantically whether the fact was established; the seed only
constrains *which facts count*. Rigid about **what**, flexible about **how**.

**One exception, added 2026-08-17.** The deterministic floor under the extractor
(see [Known risks](#known-risks)) *does* match on these tokens — on content
tokens only, and **one-directionally**: the floor can add credit the model
withheld, never withhold credit the model gave. So the sentence above still holds
where it matters. A learner who fills a slot by a route the seed never listed is
credited by the model exactly as before; the floor simply cannot be the thing
that fails them.

### The rule that makes a request slot mean something

**A `request` slot is filled only when the learner asks and the partner answers.**
If the vendor volunteers the price unprompted, the learner never did the work and
the slot must not be credited.

This is not a difficulty setting. It is what makes the scenario gradeable at all,
so it is on in every mode, permanently. It is also the one piece of realism we
knowingly give up: a real vendor might well announce the price unasked.

**The floor is the one exception, decided 2026-08-17.** The AND rule stays the
authored rule and the model's rule. The deterministic floor fires on the *ask*
alone, because Python cannot check whether a reply answered without judging it.
The cost is real and belongs here rather than only in
[`ACCESSIBILITY.md`](ACCESSIBILITY.md): a partner that counter-questions instead
of answering can leave a learner credited for a fact they never obtained. We take
it because the partner is instructed to answer — a deflection is our bug, not the
learner's — and because the floor only ever fires on the learner's own words, so
it can never cause the volunteered-price failure this rule exists to prevent.

### The scene has to create the gap

**Added 2026-08-20, with [`VALIDITY.md`](VALIDITY.md)'s V2.**

The rule above says the partner must not volunteer a `request` slot's answer.
Until now the partner enforced that itself: it was handed the slot list and told
not to give the answers away. V2 takes the slot list off it — a partner that
knows what is being scored plays along with anything that looks like scoring, and
that is the false positive `VALIDITY.md` exists to fix.

A blind partner cannot withhold a fact it does not know is wanted. So the
withholding has to come from somewhere else, and the only place left is the
**scene**:

> **Every situation must be authored so that a helpful, ordinary partner still
> leaves each `request` slot unanswered.**

The failure this prevents is not gaming. It is *politeness*. In `greetings` the
partner's own name is a `request` slot, and the first thing a friendly classmate
does is introduce themselves — 我叫小明，你呢？. Nobody did anything wrong and the
slot is gone before the learner speaks. Every original situation had this bug,
because every one of them was written for a partner that could see the rubric.

The fix is prose, in a `withholding:` field:

```yaml
  situation: "You sit down in a small restaurant. There are no menus. A server comes over and waits to take your order."
  withholding: "The kitchen prints no menu. The server keeps the day's dishes in their head and names a good one only when a customer asks."
```

Three properties make this different from the slot list it replaces:

- **It describes the world, not the rubric.** A server with no menu is a fact
  about a restaurant. "Do not reveal `recommendation` until asked" is a fact
  about a test. The partner behaves the same way; only one of them tells it that
  a checkbox exists.
- **It is one block of prose, never a per-slot mapping.** A `withholding:` keyed
  by slot id is the rubric with the labels filed off, and injecting it would
  rebuild the cooperation V2 removes. One paragraph about the scene, or nothing.
- **It never reaches the learner.** `ScenarioCard` carries `situation` and `goal`
  and nothing else, which is why the field lives beside them rather than inside
  `situation`.

**What this does not forbid: the partner asking questions.** Only *answering
unasked* is banned. Reciprocity is how the language actually works, and the
system prompt already treats a bounced 你呢？ as real skill — a partner that may
never ask anything cannot set that up. The partner may ask freely; it may not
answer a question nobody put to it.

**`validate.py` cannot check the prose.** No rule can read a situation and decide
whether it really leaves the gap open. What it checks is that the author answered
the question at all — the same bargain `max_turns_reason` strikes, and the reason
the field is required rather than inferred.

---

## The guardrail: substance and an obstacle

> **A scenario must have more than one slot, and at least one of them must be
> `request`.**

Two conditions, because they catch different degenerate shapes and neither implies
the other.

**`n_slots > 1` — substance.** A single-slot scenario is a one-exchange
conversation by construction. *"Find out whether it rains tomorrow"* is
明天下雨吗？ → 不下雨。 and it's over. That's a flashcard with a situation attached.

**`n_request_slots ≥ 1` — an obstacle.** A scenario with `inform` slots only,
however many, never makes the learner extract anything. That's a vocabulary drill.
A request slot is the irreducible obstacle: **no amount of packing lets the learner
know the price before the vendor says it.**

### This does not contradict the one-turn clear

An earlier draft tried to write the first condition as *"reject scenarios
satisfiable in one turn."* That rule cannot be written — every scenario is
satisfiable in one turn by a learner who packs (see
[example 3](#worked-example-3--the-one-turn-clear)).

The distinction: we reject scenarios whose **only** shape is one turn, and accept
that a strong learner may clear a substantive scenario in one turn. The rule is a
floor on the design, not a ceiling on the learner. `n_slots > 1` is a property of
the authored artifact and checkable statically; "how many turns did this take" is
a property of a session and none of the validator's business.

---

## Turn budget

There is **no minimum turn count**, because there is no honest way to define one.
The physical floor is 1 for every scenario. Any "expected" floor is a pacing
guess, and a pacing guess that gates behaviour will be wrong for the learner who
beats it.

So there is exactly one threshold, and it is the cap:

```
max_turns = n_slots + n_request_slots + 2
```

| Term | Reads as |
| --- | --- |
| `n_slots` | Expected pacing: at band 1–2 the partner elicits roughly one fact per turn, because answers are 2–4 words (`DESIGN.md`, beginner-disfluency mitigation). |
| `n_request_slots` | One retry per extraction. Extraction is where beginners fail — they don't know the question form — so give them a second shot at the hard part. |
| `+ 2` | One general recovery turn, plus the close. |

| Scenario | slots | request | `max_turns` | slack over expected pacing |
| --- | --- | --- | --- | --- |
| fruit stall | 3 | 1 | 6 | 3 |
| weather (will it rain tomorrow · is it cold) | 2 | 2 | 6 | 4 |
| directions (station → route) | 3 | 2 | 7 | 4 |
| ordering food (3 informs + total) | 4 | 1 | 7 | 3 |

The tightest budget the guardrail permits is **5 turns** (2 slots, 1 request).

Note the weather row: an earlier draft listed it as *"will it rain tomorrow"* with
a hand-waved inform slot to reach 2. Under the guardrail that scenario is a single
request slot and gets rejected, which is the rule doing its job — the fix is to
give it real substance (two things to find out) rather than to invent a slot that
isn't a distinct act of communication.

**What kind of claim this is.** `max_turns` is a **pacing policy**, not a fact:
derived as the default, overridable per topic *with a stated reason*, and if it
disagrees with real use the right response is to retune the coefficient. This is
deliberately a weaker claim than an earlier draft made about `min_turns`, which
called itself arithmetic and then contradicted its own worked example.

**The coefficients live in one file**, `kb/zh/pacing.json`, mirroring how
`_hsk/ceiling.json` is owned by the authoring workflow and consumed by `config`.
Retuning pacing is then one edit rather than eight topic edits. That file is also
where pacing-scales-with-band-ceiling would go, if it ever earns its place — a
ceiling-5 learner packs more per turn than a ceiling-2 one. Not built.

### Why there is no `target_turns` either

An earlier draft had a middle threshold meaning *"begin steering toward 再见."*
It has no job. Steering toward goodbye while a slot is outstanding is
counterproductive — you steer toward the **goal**. And the moment the goal is met
the session terminates, so there is never a state where "goal met, now wind down"
applies.

Both retired thresholds were turn counters standing in for a state question, and
the state is directly observable. See [Pressure](#pressure).

---

## Runtime: three tiers

| Tier | When | Cost | Job | How it's verified |
| --- | --- | --- | --- | --- |
| **Slot tracker** | every turn | folded into the existing conversation call | which slot ids this turn newly satisfies | contract test on recorded transcripts; assert the exact id set |
| **Termination** | every turn | **no model** | see [end conditions](#end-conditions) | pure unit test |
| **Verdict** | once, at `complete` | one call | *why* it went the way it did, plus an in-band model exchange | structural invariants only; judgment quality behind `live` |

Two deliberate departures from the obvious design.

**The per-turn signal is a set of ids, not a progress score.** A scalar cannot say
*which* fact is missing, so it can drive neither a useful phase hint nor a grounded
verdict. With ids, the pressure can target the gap. Compare
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
honest and stops the UI oscillating between states.

### End conditions

Three, all evaluated in Python:

```
1.  missing == ∅                  →  complete, goal met
2.  turn == max_turns             →  forced close, then complete
3.  consecutive learner closes ≥ 2 →  complete (goal unmet, if slots outstanding)
```

Condition 1 means a learner who succeeds early is **not** made to sit through the
remaining budget.

Condition 3 reconciles with `DESIGN.md`'s existing "…or partner says goodbye"
transition, and it exists for kindness: a learner who has said 再见 twice has
disengaged, and holding them in the scene for three more turns to satisfy a budget
is worse than letting them fail and reading the verdict. The count comes from an
annotation flag on the learner's turn; the decision is a Python comparison, not the
partner's discretion. It is not exploitable — closing early just means goal unmet.

### Pressure

Not a turn-count schedule. A state rule:

```
missing ≠ ∅  →  apply situational pressure toward one missing slot.
                Never toward goodbye. Withhold every request slot's answer.
missing = ∅  →  nothing to apply; the session has already ended.
```

**Pressure comes from the situation, not from the partner breaking character.** An
earlier draft had the vendor ask 你还要问什么吗？ — *"is there anything else you
want to ask?"* That is not something a fruit vendor says, and worse, it leaks the
criteria: it tells the learner a slot is outstanding.

Instead, the scene stays unresolved. The vendor bags the fruit and waits for
payment. The learner feels a gap where a phrase should go, which is how it works
in real life — you are not prompted, you get **stuck at the point where you need
the words**. That is realistic *and* it scaffolds, so it needs no difficulty mode.

The hint is injected **after the cache breakpoint**, so the frozen prefix keeps
hitting. It carries `missing: [ids]` and an instruction to withhold.

### Difficulty, deferred

The honest place for scaffolding is the **UI, not the fiction**: a "2 of 3"
indicator, or naming the outstanding goal on the scenario card. A HUD sits outside
the story; an out-of-character vendor sits inside it and damages it.

Default for now: withhold **on**, situational pressure **on**, progress HUD
**~~off~~ on** (revised 2026-08-17). The original rationale for off was that
*"the verdict card already teaches the missed phrase, so failing is cheap and
instructive."*

**The first real session falsified that clause.** Failing is not cheap when the
card cannot be trusted — that session withheld a slot the learner had plainly
established and then explained the miss with an invented rule. "You can never
discover you needed it if it was always there" assumes the learner comes back to
discover anything.

The scaffold was right and the default was wrong. It is still the honest kind of
scaffold — outside the story, made of already-authored English — and it is still
the one-line frontend addition this section called it. Chunk A2 in
[`ACCESSIBILITY.md`](ACCESSIBILITY.md); it is also the whole of the learner's
"no sense of progress" note.

When a difficulty setting does arrive it is the same shape as `forgiveness_level`:
a session constant **baked into the frozen prompt as a literal** by
`render_system_prompt`. It must not be injected per turn, or the prefix stops
being byte-stable.

### Guards

The tracker can hallucinate a fill. One structural check catches the sharp case:

- **a fill whose `depends_on` is unsatisfied** — `price` credited before `item` is
  nonsense under any pacing. Log as an error.

Deliberately **not** a guard: *"all slots filled on turn 1"* and *"more than one
request slot filled in one turn."* Both are legitimate for a strong learner who
packs an utterance. All-slots-on-turn-1 gets an **info log** — worth noticing,
resolvable only by reading the transcript.

A stronger check is available cheaply if hallucination turns out to be real: for a
numeric request slot like `price`, assert the partner's reply actually contained a
number. Not built; noted as the hook.

### Caching

Slots are authored, so they already sit in the KB block behind the `cache_control`
breakpoint. **This adds no new cache surface.** The volatile per-turn addition is
just `filled: [ids]`, `missing: [ids]`, and the withhold instruction — all after
the breakpoint.

It also de-risks the sketch worker, which the build order calls the long pole: the
sketch generates **flavour only** — opening line, vendor persona, what's on the
stall. The criteria never pass through a model, so there is no generated block to
freeze and round-trip through the client.

---

## Worked example 1 — happy path

Fruit stall, as authored above. 3 slots, 1 request → **`max_turns` 6**. No minimum.

**On screen, before the first turn:**

> **Your situation.** You're at a fruit stall. The vendor greets you.
> **Your goal.** Buy three pieces of fruit, and find out what they cost.

The slots are not shown. The learner sees the goal in English; the
machine-checkable form stays hidden.

**Amended 2026-08-17.** Still true of the slots themselves, but the card now
also carries `n_slots`, so the learner sees a **"0 of 3"** counter here that
advances as facts land. The count is not the slots — no id, no description, no
`expressible_with` — and it is what turns "the verdict teaches the missed phrase"
from the *only* feedback into the *last* feedback. See
[Difficulty, deferred](#difficulty-deferred).

**Opening line** (partner; does not consume budget):

> 你好！你要买什么？
> _nǐ hǎo! nǐ yào mǎi shénme?_ — "Hello! What do you want to buy?"

---

**Turn 1** — learner says *我要水果。* (`wǒ yào shuǐguǒ` — "I want fruit.")

```
tracker  → slots_filled: [item]
state    → {item}                    missing: [quantity, price]
pressure → toward quantity; withhold price
```

> 好！你要多少个？
> _hǎo! nǐ yào duōshao gè?_ — "Good! How many do you want?"

The partner asks a question expecting a 2–4 word answer — the
beginner-disfluency mitigation from `DESIGN.md`, doubling here as scaffolding
toward the `quantity` slot.

---

**Turn 2** — learner says *三个。* (`sān gè` — "Three.")

```
tracker  → slots_filled: [quantity]
state    → {item, quantity}          missing: [price]
pressure → the vendor bags the fruit and waits for payment. Do NOT state the total.
```

> 好，三个水果。
> _hǎo, sān gè shuǐguǒ._ — "OK, three pieces of fruit."

**This is the load-bearing moment.** A helpful vendor says *"that's nine kuai"* —
and the learner reaches the end of the session never having asked a question. The
`price` slot would be credited to the *partner's* effort. The scene is left
unresolved on purpose: fruit bagged, hand out, no number.

Note what the partner does **not** do: ask whether the learner has questions. The
pressure is in the situation, not in an out-of-character prompt.

---

**Turn 3** — learner says *多少钱？* (`duōshao qián?` — "How much?")

> 九块。
> _jiǔ kuài._ — "Nine kuai."

```
tracker  → slots_filled: [price]
state    → {item, quantity, price}   missing: ∅
termination (pure Python): end condition 1  → COMPLETE, goal met
```

Session ends at turn 3 of a 6-turn budget. Mic disabled. Success terminates the
session rather than padding it out.

**Verdict worker receives:** `goal_met=True`, the fill order, the transcript, and
the accumulated tone/grammar notes. It explains; it does not decide.

> **You did it.** You said what you wanted, how many, and you asked the price —
> that last one is the hard part, and you got it in three turns.
> One thing to work on: your 3rd tone on 水 flattened toward 2nd. It should dip
> and come back up — *shuǐ*.

---

## Worked example 2 — unhappy path

Same scenario, `max_turns` 6. The learner never asks the price.

**Opening line:** 你好！你要买什么？

---

**Turn 1** — *我买水果。* (`wǒ mǎi shuǐguǒ` — "I'm buying fruit.")

```
state → {item}                       missing: [quantity, price]
```

> 好！你要多少个？

**Turn 2** — *三个水果。* (`sān gè shuǐguǒ` — "Three pieces of fruit.")

```
state    → {item, quantity}          missing: [price]
pressure → vendor bags the fruit, waits. Withhold the total.
```

> 好，三个水果。给你。
> _hǎo, sān gè shuǐguǒ. gěi nǐ._ — "OK, three pieces of fruit. Here you go."

The bag is handed over and no number is stated. The gap is now physically obvious
in the scene, and the learner has a clean shot at the missing slot.

---

**Turn 3** — *谢谢。* (`xièxie` — "Thank you.")

```
tracker    → slots_filled: []        no new slots
state      → {item, quantity}        missing: [price]
annotation → coherence: drifting
pressure   → unchanged: the transaction is still open
```

> 不客气。
> _bú kèqi._ — "You're welcome."

---

**Turn 4** — *再见。* (`zàijiàn` — "Goodbye.")

```
learner_closed: true                 consecutive closes: 1
state → {item, quantity}             missing: [price]
```

> 好。三个水果，九…
> _hǎo. sān gè shuǐguǒ, jiǔ…_ — "OK. Three pieces of fruit, nine…"

The partner does not close while a slot is outstanding, and does not complete the
number either. Last opening.

---

**Turn 5** — *再见。* (`zàijiàn`)

```
learner_closed: true                 consecutive closes: 2
termination: end condition 3         → COMPLETE, goal unmet
```

> 再见！

The learner has disengaged twice, so the session ends at turn 5 rather than holding
them to the 6-turn cap. Failing and reading the verdict beats being kept in a scene
you have left.

**Verdict worker receives:** `goal_met=False`, `missing=[price]`, the transcript,
the notes. Note what it is *not* asked to decide. It is told the learner failed,
told which fact they never established, and asked to teach.

> **Not quite.** You bought the fruit — you said what you wanted and how many —
> but you never found out what it cost, which was the other half of the goal. The
> vendor handed you the bag and waited; that was your cue to ask.
>
> **What you could have said**, after 三个水果:
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
asserting model text**: every word is in the topic KB ∪ the HSK ceiling, and it is
3–4 lines. A "what you should have said" the learner cannot read teaches nothing.

---

## Worked example 3 — the one-turn clear

Same scenario. The learner packs everything into one utterance.

**Opening line:** 你好！你要买什么？

**Turn 1** — *我要三个水果，多少钱？*
(`wǒ yào sān gè shuǐguǒ, duōshao qián?` — "I want three pieces of fruit, how much?")

> 九块。
> _jiǔ kuài._

```
tracker  → slots_filled: [item, quantity, price]
state    → missing: ∅
guards   → depends_on satisfied (item precedes price in the utterance) → no error
           all slots on turn 1 → INFO log, not a warning
termination: end condition 1         → COMPLETE, goal met
```

**Nothing is wrong here.** The learner demonstrated more competence, not less.
This is exactly why there is no minimum turn count: a floor would have flagged a
correct pass as a bug.

> **One turn.** You said what you wanted, how many, and asked the price in a
> single sentence. That's fluent for this level.

**The right response is content, not runtime.** Padding the session to hit a turn
count would be busywork. Give this learner a scenario with more request slots, or
raise the band ceiling.

That also makes a useful signal for the deferred proficiency system: **a session
completing in fewer turns than `n_slots` is evidence the scenario is too easy for
this learner** — "promote the topic," not "stretch the session." Noted as a hook;
not built.

---

## Worked example 4 — the authoring guardrail bites

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

苹果 is band **3**. At a ceiling of 2 the learner has never been taught it, so the
scenario is unwinnable — the exact failure the authored-not-generated decision was
made to prevent, caught by a deterministic script instead of by a confused
learner. 水果 (band 1) is the in-band choice. 香蕉 (3) and 老板 (3) fail the same
way.

Now the two degenerate shapes. A scenario with no obstacle:

```yaml
  slots:
    - id: greeting
      kind: inform
      description: "Greet the vendor"
      expressible_with: [你好]
```

```
ERROR shopping: 1 slot — a one-exchange scenario. Needs more than one.
ERROR shopping: no request slots — the learner never has to extract anything.
                This is a vocabulary drill, not a scenario.
```

Both rules fire. And the more interesting case, which trips only the first — a
single *request* slot, so there is a genuine obstacle but nothing to sustain a
conversation:

```yaml
  slots:
    - id: rain
      kind: request
      description: "Find out whether it will rain tomorrow"
      expressible_with: [明天, 下雨]
```

```
ERROR weather: 1 slot — a one-exchange scenario. Needs more than one.
```

明天下雨吗？ → 不下雨。 and the session is over. The learner did extract something,
so the obstacle rule passes — but there is no scenario here, only a question. This
is the shape that most easily slips past an author writing an "easy" topic, which
is why `n_slots > 1` is a separate rule rather than a consequence of the other.

These messages *are* the fix for problem 1. Enforced at authoring time by the same
script that already guards vocabulary scope — consistent with how this repo guards
the KB generally (`validate.py`, not pytest; see `CLAUDE.md`).

---

## What `validate.py` must reject

| Rule | Catches |
| --- | --- |
| every `expressible_with` word ∈ `target_vocab`, at or below the ceiling | unachievable slot (the 苹果 case) |
| **`n_slots > 1`** | one-exchange scenario — a flashcard with a situation attached |
| **`n_request_slots ≥ 1`** | vocabulary drill masquerading as a scenario |
| duplicate slot ids; cycle in `depends_on`; `depends_on` naming an unknown id | malformed graph |
| an authored `max_turns` override below the derived value, or without a reason | pacing override that starves the goal |
| `situation` / `goal` non-empty and ASCII-only | a Chinese task description a band-1 learner can't read |
| a `request` slot with no `withholding` prose (ASCII) | a scene that answers its own question, which a goal-blind partner cannot stop |

---

## Testing

The reframe converts most of this into deterministic logic, which is exactly what
`CLAUDE.md` wants a failing test first for.

- **`max_turns` derivation** — pure function over `(n_slots, n_request_slots)`,
  table-driven, including the four scenario shapes above.
- **Termination** — pure function over `(filled, required, turn, max_turns,
  consecutive_closes)`. One case per end condition, plus the one-turn clear.
- **Monotonicity** — property test: replaying any transcript prefix never un-fills
  a slot.
- **The `depends_on` guard** — a fixture where `price` fills before `item` must
  raise; a fixture filling everything on turn 1 must **not**.
- **`validate.py` rejections** — one fixture topic per rule above, each of which
  must fail.
- **Slot extraction** — contract test against recorded responses; assert the exact
  id set, never model text.
- **Verdict** — structural invariants only: valid JSON, `goal_met` equals the
  computed boolean, `missing` echoed faithfully, model answer ⊆ KB vocab ∪
  ceiling, 3–4 lines.
- **Prompt-cache invariant** — existing byte-stability assertions extend to the
  scenario block; slots are in the frozen prefix, `missing` and the withhold
  instruction are not.

**New: a simulated learner, for tuning the verdict worker offline.** A cheap model
(Haiku 4.5) roleplaying a band-1 learner with a scripted failure mode — never asks
the price, wrong measure word, closes early. Marked `@pytest.mark.live`. This is
how the verdict prompt gets iterated without a human in the loop and without blind
guessing; it bills `ANTHROPIC_API_KEY`, so batch it against recorded transcripts
rather than live sessions.

Caveat from the literature: user simulators are
[proxies, not ground truth](https://arxiv.org/pdf/2510.05444). Use the harness to
catch structural regressions, not to certify teaching quality.

---

## Sequencing (M2 — scenarios)

All five issues closed. The table stays as the record of how the plan
moved, not as a backlog.

| Issue | Scope | What changed in the plan | Shipped |
| --- | --- | --- | --- |
| **#28** | scenario slots in `topic.md`, six `validate.py` rules, derived `max_turns`, `kb.py` parsing, migrate `greetings` | **Grew.** The load-bearing PR of the milestone. | #38 |
| **#30** | sketch worker — flavour only (opening line, persona, stall contents) | **Shrank.** Criteria come from the KB; nothing that must not drift passes through a model. | #40 |
| **#31** | slot tracker + pure-Python termination (three end conditions) + state-based situational pressure | **Absorbed** #32's `goal_met`. Scalar `goal_progress` replaced by an id set; both turn thresholds retired. | #42 |
| **#32** | verdict worker — explains a *computed* outcome, plus the in-band model exchange | **Explains rather than decides.** | #42 |
| **#29** | topics API + author topics with slots | **Moved last**, then closed at **five** topics rather than 6–8. | #54, #55, #57 |

`GET /api/topics` landed on its own (#54). Session start still draws the
topic; the catalog is read-only. A learner-picked topic is curriculum
work (C8, #53), not leftover M2.

---

## Known risks

**Authored slots make scenarios rigid — and real use confirmed it.** A learner
who reaches the goal by an unanticipated but valid route might not trip the slot.
The `expressible_with`-is-a-hint rule is the mitigation: the extractor judges
semantically whether the fact was established, and only the *set of facts that
count* is fixed.

This risk is now **observed, not predicted.** In the first session run by the
learner (2026-08-16) the extractor withheld `wellbeing` in `greetings` from a
turn that plainly asked 你最近怎么样, and withheld credit for pinyin typed
`zui jian` that it granted to the same word typed `zuijian` — despite the system
prompt telling it to accept pinyin "spaced or run together."

**And the mitigation named above is what failed.** The prompt already said judge
meaning not wording; it already accepted spaced pinyin; it already credited
你呢？. More of it is not the fix, because the cause is structural: the partner
is asked in one call to stay in character and withhold the request answer, *and*
to decide whether a slot just filled. A partner tuned to withhold
under-annotates.

So the fix is a **deterministic floor under the model**, not better instructions
to it — one-directional, so it can only add credit, and gated on the learner's
own `user_reading`, so it can never cause the mirror-image failure below.
`expressible_with` stays a hint for the model and becomes a matcher only for the
floor. Chunk A2 in [`ACCESSIBILITY.md`](ACCESSIBILITY.md).

**A withheld slot does not stay contained — the verdict rationalizes it.** This
one was not predicted, and it is the more damaging half. The verdict worker is
told the computed outcome as fact and instructed not to re-grade it. Handed a
transcript where the learner clearly asked and an outcome saying they did not,
the model reconciles the two the only way left to it: it **invents a criterion**.
The session above told the learner their reply "didn't confirm you understood her
answer" — a rule that appears in no KB, no slot, and no line of
`termination.py`.

So the guardrail that stops a judge grading generously also makes it fabricate
instruction when the tracker misfires. Prompting harder is not the fix; the
worker is asked to explain *why* a slot is unfilled, and Python never told it
why, because Python does not know. Narrowing that brief is chunk A2.

**The mirror-image failure is worse.** A tracker that credits `price` when the
vendor volunteered it turns every session into a pass. Both directions are
catchable with recorded-transcript fixtures, which is why slot extraction gets a
contract test rather than an eval.

**`depends_on` may not earn its authoring cost.** After the turn budget stopped
using the DAG, its only remaining job is the tracker guard. That guard is real —
`price` before `item` is a genuine hallucination signal — and the field is one
line to write. But if it proves annoying across eight topics, it can be dropped
and the design survives; the guard degrades to the info-level log.

**`n_slots` as a pacing proxy is a guess.** It is the one hand-tuned assumption
left, and it only affects `max_turns` — a policy number in a policy formula, in a
file built to be retuned. If sessions routinely hit the cap with one slot
outstanding, raise the coefficient rather than the per-topic override.
