# Validity — does the grade mean what it says?

Companion to [`SCENARIOS.md`](SCENARIOS.md), which specifies *how* a session is
graded. This one asks whether the grade is **earned**.

Status: **V0 measured; V1 does not ship; V2 built; V3 closed as decided; the
gate ships in Stream A (A4) on the other side of the split.**

> **V0's answer: `coherence` cannot carry a gate.** Across 21 runs over 7
> recorded cases, the partner tagged the gaming turn `on_track` every time and
> credited the slot every time — the tag does not notice the failure, so no
> threshold over it separates gaming from earned credit. No gate suppressed an
> earned turn either, so V1 would not have broken A2; it would simply never have
> fired. Evidence and method: [`evals/coherence/`](../evals/coherence/README.md).
> The motivating turn stays V2's to fix.
>
> **A4 ships one anyway, and V0's finding is why it can.** V0 measured a
> *goal-aware* partner's tag: it could see what was scoreable, so it called the
> gaming turn relevant. V2 made the partner goal-blind. A4 asks the same
> question of that partner, binary, and gates on the answer
> (`docs/streams/grading.md`). What V0 established stands — the signal it
> measured was silent — and so does the safety rule below, now enforced in code
> rather than reported on.

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

**Converser.** Situation, what the scene withholds, persona, topic KB,
conversation history — and nothing else. **Grader.** The conversation history,
the learner's turn, and the slots. No character to hold, no reply to write, no
reason to be generous.

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

**Turn 1 is the exception, and it needs a wire field.** The partner's opening
line is deliberately not part of `dialogue` — it costs the learner none of their
turn budget ([`SCENARIOS.md`](SCENARIOS.md), "Definition of a turn") — so on the
first turn the grader would otherwise have the learner's words and nothing they
are a response to. That is the turn most likely to be answering a greeting. The
client resubmits `opening_line` the way it resubmits `sketch`, and the grader
carries it as a prefix on the first *user* message: the Messages API requires
`messages[0]` to be `user`, and a lone leading assistant turn reads as prefill.

The learner's turn arrives as 汉字 by the same per-path split A2's floor uses:
the **STT transcript** on the spoken path, the converser's `user_reading` in
text mode. Taking `user_reading` from the converser rather than re-resolving
pinyin matters — two independent resolutions of `wo jiao xiao ming` could
disagree, and then the learner's own bubble and their grade would describe
different sentences.

### What each side sees

| | Converser | Grader |
|---|---|---|
| Authored `situation` | ✅ | ✅ — it is the *evidence*: "volunteered unasked" cannot be judged without knowing what the scene hands over |
| Authored `goal`, slot ids, `expressible_with` | ❌ | ✅ |
| Authored `withholding` prose | ✅ (verbatim) | ❌ (it is not a criterion) |
| System-prompt slot / withhold paragraphs | ❌ (they move) | ✅ |
| Persona from the sketch | ✅ | ❌ |
| Conversation history | ✅ | ✅ |
| The partner's new reply | writes it | does not need it |

The sketch prompt forbids leaking the goal and restricts itself to flavour. V2
widens it only so far: it is *shown* what the scene withholds, and told the
persona must not contradict it. **The withholding itself does not travel through
the sketch model** — the authored prose reaches the converser verbatim, in
`kb.render_scene_block`. A generated persona is softer than an instruction, and
this is the one place where that softness would cost credit.

**What moves to the grader:** `slots_filled`, `learner_closed`, `coherence`.
**What stays on the converser:** `learner_said_goodbye` — noticing that
someone is leaving needs no rubric. `grammar_notes`, `topic_tags`, and
`should_give_feedback` went in A2: the partner is not the coach, and the
notes panel went with the field rather than becoming a new verdict job.

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

`state` lands as soon as the grader returns and `termination.advance` still runs
once per turn — but it lands on **its own event** rather than on `reply`, which
is a wire change the client had to take. Reply and state used to commit
together; now a client that loses the connection between them records the turn
without its consequences. That is survivable (the client resubmits state every
turn) where holding the reply would be paid on every turn of every session.

**`/api/turn/text` serializes, and cannot do otherwise.** An earlier draft of
this section claimed it runs its two calls concurrently. It cannot: in text mode
the learner's 汉字 comes from the converser's `user_reading`, by the very rule
stated above, so the grader has nothing to read until the converser returns.
That is the one place the two paths differ, and it is the mic-free harness
rather than the learner's path.

**There is no lag: the grader credits on the ask.** An earlier draft had the AND
rule (*learner asked* **and** *partner answered*) resolving one turn late, since
the answer to a turn-N ask lands in the turn-N reply the turn-N grader has not
seen. **That premise was rejected on 2026-08-20.** A `request` slot is a claim
about the *learner's* Chinese: they either formed the question or they did not.
Whether the partner answered is the partner's performance, and grading the
learner on it grades the wrong party.

This is a stronger argument than the one in
[`SCENARIOS.md`](SCENARIOS.md#a-request-slot-needs-both-halves), which credits on
the ask only because Python cannot judge whether a reply answered. The answer was
never the interesting half, regardless of who can check it. So: one call, one
turn, and no lag — and **V3 closes as decided rather than built**.

**This is not contradicted by the recovery pass below.** There *is* a session-end
grader pass, but it exists to judge turns that were never judged, never to
re-audit turns that were. The rule stays one grade per turn, credited on the ask;
what the recovery pass recovers is a grade that failed to happen, not a verdict
that was already reached.

**If the grader fails: the turn is owed, not lost.** The reply stands and no slot
is credited *yet* — but the turn is not forgotten either. `SessionState` carries
`last_graded_turn`, a watermark; a turn whose grade never landed leaves it
behind, and the next turn's grader judges the outstanding turn as well as the
current one. Grading is a pure function of (history, learner turn) and the client
resubmits the history every turn, so an ungraded turn is deferred work, not lost
information.

A watermark rather than a count of ungraded turns, because a count has to be
incremented *by the client* when a grade does not arrive — and not receiving the
`state` event is the entire failure mode.

The grader returns the window **attributed**, not unioned: `slots_filled` for the
current turn, `slots_filled_previously` for the owed ones.
`termination.advance` resets the close counter on a turn that carried content, so
a slot credited late must not count as content this turn carried — otherwise an
old grade landing swallows a goodbye the learner actually said.

`learner_closed` is not in the window at all. **It moved to the converser**:
noticing that someone is leaving needs no rubric, and the converser cannot fail
independently of the reply, so a close is applied on time even through a total
grader outage.

**Three ungraded turns ends the session** (`end_reason: "ungraded"`). That is an
outage, not a backlog, and spending the learner's remaining turns on a session
that cannot grade them is worse than stopping.

**A session that ends still owing grades gets one final pass** before the
verdict. The card is computed from state, so an unsettled debt would tell the
learner they missed something they established — the A2 false negative at the
moment it is most visible. If that pass completes the goal it supersedes the
recorded `end_reason`: someone who established everything did not leave
unfinished, `stuck` least of all.

### Model: a stronger grader, a faster converser

The two roles want different models, and today they share one:
`CONVERSATION_MODEL` (`config.py:38`) is used by the conversation worker, the
sketch worker, and the verdict worker alike.

| Role | Model | Why |
|---|---|---|
| Converser | `claude-sonnet-5` | HSK band-2 dialogue is not a reasoning problem. Fast and cheap is the right trade on the branch the learner waits behind. |
| Grader | `claude-opus-5`, `effort: medium` | Judgment is where capability pays. Off the *reply* path but **not off the turn**: the learner cannot speak again until the grade lands, because they are waiting to find out whether they got their point across. So effort here is latency they sit in, and `medium` is the trade. |

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

**Cost is reported per call, not summed.** The turn buys a Sonnet 5 reply and an
Opus 5 judgment at `effort: high` with thinking on. `TurnUsage.grader` carries
the second separately, because one token count across two models describes a
price nothing charges — and reporting only the converser's hides the more
expensive half, on the branch whose cost was the reason for the split.

**Caching: one more prefix per session.** The converser's prefix *shrinks* — the
scenario block leaves it — and the grader gets its own. `SCENARIOS.md`'s
"Caching" section says the slots live in the conversation worker's frozen
prefix; **V2 inverts that and amends it.**

**It vindicates A2's compromise rather than retiring it.** A2 floors on the ask
alone because Python cannot judge whether a reply answered. The grader credits on
the ask because the answer is not the learner's to be graded on. The two now
implement the *same rule* — one cheaply, one with judgment — so the floor stops
being a knowing divergence from the model's rule and becomes a cheaper
implementation of it. That is why V3 closes.

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

`coherence` came from `DESIGN.md`'s original annotation schema as
`on_track | drifting | off_track` on the converser. It **predated the slot
tracker**, was computed on every turn since the conversation worker shipped, and
was read by **no code path at all**. V2 moved it to the grader; A4 moved it back
to the (now goal-blind) partner as a binary field and made it a gate.

**Do not withhold credit *already earned* on a bad coherence tag.** That
reintroduces the false negative A2 exists to fix, and would fail the learner
whose Chinese was fine and whose conversational timing was not. A4's gate obeys
this by construction: it withholds only the credit of the turn it judged, it
cannot reach `state.filled_at`, and a learner therefore never watches a score go
down (`orchestrator._advance_or_echo`).

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
| **V1** | ❌ **Not shipping the gate** — *superseded by A4.* V0 found nothing for it to gate on, because the partner it measured could see the rubric. Once V2 made the partner goal-blind, the objection expired and Stream A's A4 shipped the gate: binary, on the partner's annotation, applied in `_advance_or_echo`. | V0 said no; A4 said yes |
| **V2** | ✅ **Built.** Goal-blind converser; grader as a third fan-out branch reading the *previous* partner turn; withholding as authored scene prose; `closing_hint` replacing `pressure_hint`. Splits the model: Sonnet 5 converses, Opus 5 grades. Staged: (1) ✅ verdict worker → Opus 5; (2) ✅ the authoring rule + all five situations rewritten; (3) ✅ the converser/grader change. | done |
| **V3** | ❌ **Closed as decided, not built.** It asked whether to re-open A2's floor-on-ask compromise once a grader could evaluate ask-AND-answer. The grader can, and we do not want it to: the partner's answer is the partner's performance. | decided |

**V2 flips all five topics at once — revised 2026-08-20.** The earlier plan
staged it per topic, with `food-ordering` as the one rewrite target and the rest
following. Reading the other four scenarios killed that: **every one of them has
the same bug**, and `greetings` has it worst. The partner's own name is a
`request` slot there, and the first thing a friendly classmate does is introduce
themselves. `food-ordering` was not the exception, it was the example.

Politeness, not gaming, is what deletes those slots — so there is no subset of
topics where a blind converser is safe today, and a per-topic condition would
only be a flag over a rule that has to hold everywhere. Every situation is
rewritten and every scenario carries `withholding`, enforced by `validate.py`.
The rule is specified in [`SCENARIOS.md`](SCENARIOS.md#the-scene-has-to-create-the-gap).

`validate.py` still cannot judge whether a situation creates an opening — that is
prose. It checks that the author answered the question, which is the same bargain
`max_turns_reason` strikes.

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

**Persona-as-withholding may drift** — which is why the built version does not
rely on it. A generated persona is softer than an instruction, and a server told
to be brisk may still helpfully recommend a dish. So the authored `withholding`
prose reaches the converser **verbatim**, through `kb.render_scene_block`, and
the sketch prompt gets it only as a constraint so the persona cannot describe a
different person. The residual risk is that the authored prose itself is not
strong enough, which is a phone session's question, not a code one.

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
