# Validity — does the grade mean what it says?

Companion to [`SCENARIOS.md`](SCENARIOS.md), which specifies *how* a session is
graded. This one asks whether the grade is **earned**.

Status: **V0 measured; V1 does not ship.** The rest depends on
[`ACCESSIBILITY.md`](ACCESSIBILITY.md)'s A2.

> **V0's answer: `coherence` cannot carry a gate.** Across 21 runs over 7
> recorded cases, the partner tagged the gaming turn `on_track` every time and
> credited the slot every time — the tag does not notice the failure, so no
> threshold over it separates gaming from earned credit. No gate suppressed an
> earned turn either, so V1 would not have broken A2; it would simply never have
> fired. Evidence and method: [`evals/coherence/`](../evals/coherence/README.md).
> The motivating turn stays V2's to fix.

---

## Two ways a grade can be wrong

`SCENARIOS.md` made goal completion a set comparison so success would be a fact
rather than an opinion. That removed the *judge's* generosity. It did not make
the underlying facts true.

| Direction | What it looks like | Where it is handled |
|---|---|---|
| **False negative** | The learner established the fact and got no credit | `ACCESSIBILITY.md` A2 — the deterministic floor |
| **False positive** | The learner got credit without establishing the fact | Here |

One ends a session in frustration. The other ends it in a lie the learner has no
way to detect, and is the more expensive of the two however much better it feels.

## The observation

In `food-ordering`, the server asks 你要喝什么？ — *what would you like to
drink?* The learner ignores it and asks 什么菜最好吃？ — *which dish is best?*
The `recommendation` slot fills. They get the point.

Nobody had a conversation. The learner emitted a phrase that matched a criterion,
and the criterion did not care that it answered nothing.

Worse, **the partner cooperated.** It knows `recommendation` is a slot, so it
takes the non-sequitur and answers it. A person would say *"…I asked what you
wanted to drink."*

## The cause: the partner knows the rubric

[`ACCESSIBILITY.md`](ACCESSIBILITY.md) diagnoses the tracker failure as one call
doing two jobs that fight — stay in character and withhold, *and* annotate slots.
That is true and too weak. The conflict is **epistemic**:

> A conversation partner cannot be a natural interlocutor while knowing what the
> learner is being tested on.

Once the partner holds the rubric it stops being a person in a scene and becomes
a proctor who wants you to pass. It steers, it accepts near-misses, it treats an
irrelevant question as relevant — because it can see the checkbox behind it.

Under-annotation in A2 and gaming here are the same defect from two sides.

**A2's floor inherits it.** Content-token matching credits 什么菜最好吃 in an
exchange about drinks, because the floor was designed to ignore context and
context is what separates a question from a phrase. That is not an argument
against the floor — the failure it fixes is real and observed — but the floor
cannot be the thing that fixes this one.

---

## The design: a goal-blind converser and a separate grader

**Converser.** Situation, persona, topic KB, conversation history — and nothing
else. **Grader.** The transcript including the partner's new reply, plus the
slots. No character to hold, no reply to write, no reason to be generous.

### What the grader reads

**The grader does not need the partner's new reply.** It reads the *previous*
partner turn plus the learner's turn — exactly the pair that answers both
questions it is asked:

| Question | Input it needs |
|---|---|
| Did the learner respond to what was actually said? | the partner's **previous** turn + the learner's turn |
| Did the learner establish a fact, or ask for one? | the learner's turn + history |

That pairing is the whole judgment. *"You asked which dish is best when I asked
what you wanted to drink"* is legible from the previous turn and this one; the
reply the partner is composing right now adds nothing to it.

The learner's turn arrives as 汉字 by the same per-path split A2's floor uses:
the **STT transcript** on the spoken path, the converser's `user_reading` in
text mode. Taking `user_reading` from the converser rather than re-resolving
pinyin matters — two independent resolutions of `wo jiao xiao ming` could
disagree, and then the learner's own bubble and their grade would describe
different sentences.

### What each side sees

| | Converser | Grader |
|---|---|---|
| Authored `situation` | ✅ | ✅ |
| Authored `goal`, slot ids, `expressible_with` | ❌ | ✅ |
| System-prompt slot / withhold paragraphs | ❌ (they move) | ✅ |
| Persona from the sketch | ✅ | ❌ |
| Conversation history | ✅ | ✅ |
| The partner's new reply | writes it | does not need it |

The sketch prompt currently forbids leaking the goal and restricts itself to
flavour. V2 **widens** it: persona may now carry withholding (below), which is
character, not criteria. That amendment is part of this work.

**What moves to the grader:** `slots_filled`, `learner_closed`, `coherence`.
**What stays on the converser:** `grammar_notes` — a live verdict input about
the learner's Chinese rather than about the rubric — plus the unused fields,
which moving buys nothing.

### The sequence

Because the grader needs nothing the turn does not already have, **it joins the
existing fan-out** rather than waiting on the converser:

```
mic → STT → yield transcript
         ├─ Azure PA ──────────────→ yield score
         ├─ converser ─────────────→ yield reply
         └─ grader ────────────────→ yield state
            all done               → yield done
```

Everything the current shape guarantees survives: `state` still lands as soon as
the grader returns, `termination.advance` still runs once per turn, and
`/api/turn/text` runs two calls **concurrently** rather than in series.

**The AND rule resolves one turn late, and that is already fine.** A `request`
slot is authored as *learner asked* **and** *partner answered*; the answer to a
turn-N ask lands in the turn-N reply, which the turn-N grader has not seen — the
turn-N+1 grader does. This costs nothing today, because
[`ACCESSIBILITY.md`](ACCESSIBILITY.md)'s A2 **already decided to credit on the
ask alone**. Only V3, which re-opens strict AND, has to choose between the
one-turn lag and one final grader pass at session end.

**If the grader fails:** the reply stands, the turn returns normally, and the
previous `SessionState` is echoed unchanged. No slot is credited and no close is
counted — so a learner saying goodbye through a grader outage falls back on the
turn cap.

### Model: a stronger grader, a faster converser

The two roles want different models, and today they share one:
`CONVERSATION_MODEL` (`config.py:38`) is used by the conversation worker, the
sketch worker, and the verdict worker alike.

| Role | Model | Why |
|---|---|---|
| Converser | `claude-sonnet-5` | HSK band-2 dialogue is not a reasoning problem. Fast and cheap is the right trade on the branch the learner waits behind. |
| Grader | `claude-opus-5` | Judgment is where capability pays, and it is off the reply path. |

Standard practice, and small at one learner: Opus 5 is $5/$25 per Mtok against
Sonnet 5's $3/$15 — 1.67×, on a call that runs once per turn against a short
prefix. Caches are per-model, so the grader gets its own entry either way; Opus 5
caches from 512 tokens where Sonnet 5 needs 1024, which suits the smaller
grader prefix.

**Thinking is the catch.** All three workers today set
`thinking: {"type": "disabled"}` deliberately, because `max_tokens` caps
thinking *plus* output and an overrun returns `stop_reason: max_tokens` with
nothing parsed. A grader wants thinking **on** — it is the judgment — so it
needs real `max_tokens` headroom, not the conversation worker's 1024. On Opus 5
thinking is on by default and disabling it is rejected above `high` effort, so
the grader must decide this explicitly rather than inherit it.

**The verdict worker is the cheapest place to test the split.** It is already a
judgment role, already one call per session, already off the turn path — and
already on Sonnet 5 with thinking disabled. Moving it to Opus 5 needs no
architecture change and none of V2.

### What it costs

**Latency improves.** The annotation leaves the converser's output schema
entirely, on a path that already splits its schema to keep ~40 output tokens off
the branch the reply waits behind (`SpokenConversationResult`). Nothing waits on
the grader that did not already wait on the converser.

**Caching: one more prefix per session.** The converser's prefix *shrinks* — the
scenario block leaves it — and the grader gets its own. `SCENARIOS.md`'s
"Caching" section says the slots live in the conversation worker's frozen
prefix; **V2 inverts that and amends it.**

**It may retire A2's compromise.** A2 floors on the ask alone because Python
cannot judge whether a reply answered. A grader that holds no character can
evaluate the authored AND rule properly — with the one-turn lag above, or a
final pass at session end. If it does so reliably, the cost A2 accepted stops
being necessary. That is V3.

### What breaks, and how it is fixed

**Withholding.** `SCENARIOS.md` requires the partner never volunteer the answer
to a `request` slot. That needs slot knowledge, which a blind converser lacks.

*Encode withholding as character, not criteria.* "A brisk server who does not
recommend dishes unless asked" is a persona. The sketch worker already generates
persona per session, so this is a sketch-prompt change and an authoring rule —
not rubric knowledge smuggled back in.

**Pressure.** `pressure_hint` steers the scene toward whichever fact is
outstanding. A blind partner cannot steer, and for a beginner an opening that
never comes is a session that cannot be won.

**A2's HUD does not cover this.** A2 ships a *count* — "2 of 3" — which says
something is outstanding, not that it is the price. So **scene design is the
substitute, and the count is not**: the situation must be authored so the gap is
structural, a server who brings no menu, a stall with no prices shown. That is
content work and it is V2's real dependency. This track does not push a
naming-HUD requirement onto A2; that is A2's decision on its own evidence.

**`pressure_hint` also carries the final-turn instruction** — answer normally,
then close the scene in character. Retiring it drops that. The cap-turn close
needs a home, in the converser's own prompt rather than a per-turn injection.

---

## The penalty for gaming

`coherence` — `on_track | drifting | off_track` on `WorkerAnnotation`
(`models.py:301`) — comes from `DESIGN.md`'s original annotation schema. It
**predates the slot tracker**, has been computed on every turn since the
conversation worker shipped, and is read by **no code path at all**.

**Do not withhold credit on a bad coherence tag.** That reintroduces the false
negative A2 exists to fix, and would fail the learner whose Chinese was fine and
whose conversational timing was not.

**The threshold is an output of measurement, not an input.** `off_track` is
defined as *unintelligible or derailed*; `drifting` is *wandering*. The
motivating example — asking about dishes when asked about drinks — is in-scene
wandering, so a `!= off_track` gate lets it straight through. V0 measures the
signal and reports what, if anything, it can carry. **It may report that no safe
threshold exists**, and that is a real outcome: then the floor stays ungated and
V2 carries the whole fix.

**V1 does not close the observation above.** Even gated, the floor only stops
*itself* from adding credit; the model tracker still grants the slot, because the
partner still holds the rubric and still plays along. **Cooperation is V2's to
fix.** V1's job is narrower: stop the mechanical path from adding credit on turns
the measurement says not to.

**In the verdict**, a session-level fact computed in Python and put on
`VerdictCard`, passed the way `goal_met` is. Not a list of per-turn tags for the
model to interpret — that would repeat the fabrication A2 fixes, since a worker
given material to explain and no computed conclusion invents one.

**With a blind partner the penalty is mostly emergent.** A partner that does not
know `recommendation` is a slot notices the non-sequitur the way a person does.
The verdict record then describes what went wrong rather than punishing it.

---

## Staging

| | What | Blocked on |
|---|---|---|
| **V0** | ✅ **Done.** A recorded-transcript fixture set and the first measurement of `coherence` against gold labels. Reported that **no threshold is safe** — every candidate gate never fires. Shipped no gate. | nothing |
| **V1** | ❌ **Not shipping the gate.** V0 found nothing for it to gate on. The session-level coherence fact on `VerdictCard` is separable and still open — but it is describing a signal we now know is silent on the failure that matters, so it waits for V2's grader to produce a tag worth recording. | V0 said no |
| **V2** | Goal-blind converser; grader as a third fan-out branch reading the *previous* partner turn; withholding as persona; scene design replacing `pressure_hint`. Splits the model: Sonnet 5 converses, Opus 5 grades. | a rewritten situation that proves the gap survives |
| **V3** | Re-open A2's floor-on-ask compromise if the grader evaluates ask-AND-answer reliably. | V2 |

**V2 does not flip all five topics at once.** It runs where the request slots are
the partner's own facts, or where the topic carries an authored withholding
field. `food-ordering` is the rewrite target. `validate.py` cannot judge whether
a situation creates an opening — that is prose — so the guardrail is an authored
field, not a linter rule.

**V0 measures `coherence` on the converser; V2 moves it to the grader** — and
onto a stronger model. The matrix has to be re-run after V2 before the same gate
is trusted.

**The model split is separable from V2.** The verdict worker is already a
judgment role on the conversation model, off the turn path, one call per
session. Moving it to `claude-opus-5` is a config change that needs none of the
blindness work, and it is the cheapest way to find out whether a stronger judge
actually reads these sessions better.

## Risks

**A blind partner may make sessions unwinnable.** The scene has to create the
opening `pressure_hint` used to manufacture, and authored situations have never
carried that weight. The check is a phone session; if it fails, the answer is
better scenes, not a partner that peeks at the rubric.

**Persona-as-withholding may drift.** A generated persona is softer than an
instruction, and a server told to be brisk may still helpfully recommend a dish.
Checkable with recorded transcripts, and the one place blindness genuinely costs
reliability.

**V1 can re-break what A2 fixed.** The floor exists to rescue a turn the model
under-annotated, and a confused turn is exactly when both a bad coherence tag and
an under-annotation are likely. Gating the floor can therefore suppress the
fallback on the very turn that needed it. V0's fixture set must include an
earned-but-under-annotated turn, or V1 will trade a false positive for the false
negative that started all of this.

**Two calls can disagree about what happened.** Taking `user_reading` from the
converser closes the worst version on the text path; the seam is still new.

**`coherence` has never been measured, and there is nothing to measure it with.**
`tests/fixtures/` holds `greeting.wav` and `kb_scenarios/` — no session
transcripts. That is why V0 is a chunk and not a checkbox.
