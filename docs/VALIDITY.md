# Validity — does the grade mean what it says?

Companion to [`SCENARIOS.md`](SCENARIOS.md), which specifies *how* a session is
graded. This one asks whether the grade is **earned**.

Status: **not built.** Written 2026-08-17 from a design observation, not from a
session failure — unlike [`ACCESSIBILITY.md`](ACCESSIBILITY.md), which came out
of a learner drowning. Revised 2026-08-18 after an adversarial review found the
first draft's architecture contradicted its own inputs. Depends on that track's
A2.

---

## Two ways a grade can be wrong

`SCENARIOS.md` made goal completion a set comparison so that success would be a
fact rather than an opinion. That removed the *judge's* generosity. It did not
make the underlying facts true.

| Direction | What it looks like | Where it is handled |
|---|---|---|
| **False negative** | The learner established the fact and got no credit | `ACCESSIBILITY.md` A2 — the deterministic floor |
| **False positive** | The learner got credit without establishing the fact | Here |

Both say the same thing about the product: *the grade does not mean what it
says.* One ends a session in frustration. The other ends it in a lie the learner
has no way to detect — and is therefore the more expensive of the two, however
much better it feels.

## The observation

In `food-ordering`, the server asks 你要喝什么？ — *what would you like to
drink?* The learner ignores it and asks 什么菜最好吃？ — *which dish is best?*
The `recommendation` slot fills. They get the point.

Nobody had a conversation. The learner emitted a phrase that matched a criterion,
and the criterion did not care that it answered nothing.

Worse, **the partner cooperated.** It knows `recommendation` is a slot, so it
takes the non-sequitur and answers it. A person would not. A person would say
*"…I asked what you wanted to drink."*

## The cause: the partner knows the rubric

[`ACCESSIBILITY.md`](ACCESSIBILITY.md) diagnosed the tracker failure as one call
doing two jobs that fight — stay in character and withhold, *and* annotate slots.
That is true and too weak. The conflict is not that the two objectives pull in
different directions. It is **epistemic**:

> A conversation partner cannot be a natural interlocutor while knowing what the
> learner is being tested on.

Once the partner holds the rubric it stops being a person in a scene and becomes
a proctor who wants you to pass. It steers, it accepts near-misses, it treats an
irrelevant question as if it were relevant — because it can see the checkbox
behind the question.

Everything downstream inherits this. The under-annotation in A2 and the gaming
here are the same defect seen from two sides.

### It also indicts the floor we just specified

A2's floor matches **content tokens** from `expressible_with` against the
learner's own words. Say 什么菜最好吃 into an exchange about drinks and it credits
`recommendation`. The floor is the most gameable path in the system precisely
because it is the dumb one — it was designed to ignore context, and context is
exactly what distinguishes a question from a phrase.

This is not an argument against the floor. The failure it fixes is real and
observed. It is an argument that the floor needs a context gate — and, as V1
below now admits, that gate is **not** a one-line conditional on the field we
happen to have.

---

## The design: a goal-blind converser and a separate grader

**Converser.** Situation, persona, topic KB, conversation history. **No slot
block. No `pressure_hint`.** It replies as a person in a scene, and nothing more.

**Grader.** The transcript *including the partner's new reply*, and the slots. No
character to hold, no reply to write, no reason to be generous — the thing it is
asked cannot be gamed by the thing it is not asked.

### What the grader reads, per path

The learner's turn reaches the grader as 汉字, and where that comes from differs
by path — the same split A2's floor has to make:

| Path | Learner's words | Partner's reply |
|---|---|---|
| Spoken (`/api/turn`) | the STT transcript, already 汉字 | the converser's `partner_response` |
| Text (`/api/turn/text`) | the converser's `user_reading` | the converser's `partner_response` |

The text path takes `user_reading` from the converser rather than re-resolving
pinyin in the grader. Two independent resolutions of `wo jiao xiao ming` could
disagree, and then the learner's own bubble and their grade would describe
different sentences.

### What moves, and what does not

`turn_annotation` is not only `slots_filled`. Moving all of it is a bigger change
than this track needs, so:

| Field | Where it lives after V2 | Why |
|---|---|---|
| `slots_filled` | **grader** | The rubric is the thing the converser must not see |
| `learner_closed` | **grader** | Feeds `consecutive_closes`; same reason |
| `coherence` | **grader** | Judging whether a turn answered the scene is grading, not conversing |
| `grammar_notes` | converser | A live verdict input, about the learner's Chinese, not the rubric |
| `topic_tags`, `should_give_feedback` | converser | Unused today; moving them buys nothing |

### The sequence, and the wire contract

**This is the correction the review forced.** The first draft called the grader "a
third branch in the fan-out `orchestrator.py:300` already runs" while also saying
it reads the partner's reply. **Those are incompatible** — the reply does not
exist when PA and the converser start. The real shape is:

```
mic → STT → yield transcript
         ├─ Azure PA ──────────────────────→ yield score
         └─ converser → yield reply ─→ grader → yield state
                                       (may overlap leftover PA)
            all done                        → yield done
```

The grader starts **when the converser returns**, and may overlap whatever PA has
left. That has consequences the spec must state rather than imply:

- `ReplyEvent` carries the partner's line **as soon as the converser returns**,
  as it does today.
- `ReplyEvent` **no longer carries `state`.** A new event does, after the grader.
  This breaks a documented invariant — `CLAUDE.md` says state "rides
  `ReplyEvent`, never `DoneEvent`" — and that line has to change with this work.
- `termination.advance` runs **only** on the grader's annotation.
- The client must not submit the next turn, and must not treat the session as
  complete, until that state arrives.
- **If the grader fails:** the reply stands and is rendered, the turn does not
  fail, and no slot is credited for it. A dropped grade is recoverable — the
  learner keeps talking and the next turn re-reads the same transcript. A dropped
  reply is not.
- `/api/turn/text` gets the same contract, and there it is **two serial Claude
  calls** where today there is one.

### What it costs

**The first draft claimed "latency: better, not worse." That was false as
stated**, and it is worth splitting into the two claims it conflated:

- **Time to the partner's line: better.** The annotation leaves the converser's
  output schema entirely. The spoken path already splits its schema to keep ~40
  output tokens off this branch (`SpokenConversationResult`); this removes the
  rest.
- **Time to the next turn: worse.** State now waits on a second model call that
  starts after the first finishes. On the text path, that is two serial calls
  where there was one.

That trade is defensible — the learner reads the reply while the grader runs, and
reading is the slow part — but it is a trade, not a free win. If it measures
badly, the fallback is a smaller/faster grader model, not a re-merge.

**Caching: one more prefix per session.** The converser's prefix *shrinks* — the
scenario block leaves it. The grader gets its own stable prefix.
[`SCENARIOS.md`](SCENARIOS.md) "Caching" currently says the slots live in the
conversation worker's frozen prefix; **V2 inverts that and must amend it.**

**Model: the grader need not be the conversationalist's equal.** Structured
extraction off the reply path is the cheapest thing in the system to try on a
smaller model. Worth measuring; not decided here.

**It may let us revisit A2's compromise.** A2 floors on the *ask* alone because
Python cannot tell whether a reply answered. A grader that sees the partner's
reply and holds no character can evaluate the authored AND rule properly. If it
does so reliably, the floor-on-ask cost — a counter-questioning partner crediting
a fact never obtained — stops being one we have to accept. That is V3.

### What breaks, and how it is fixed

**Withholding.** `SCENARIOS.md` requires the partner never volunteer the answer
to a `request` slot — *"a helpful answer nobody asked for takes the practice
away."* That rule needs slot knowledge, which a blind converser does not have.

*Fix: encode withholding as **character**, not criteria.* "A brisk server who
does not recommend dishes unless asked" is a persona. The sketch worker already
generates persona per session (`workers/sketch.py`), so this becomes a sketch
prompt requirement and an authoring rule — not rubric knowledge smuggled back in.
A partner withholds because of who it is, not because it has seen the test.

**Pressure.** `pressure_hint` steers the scene toward whichever fact is still
outstanding, so the gap lands where the learner needs the words. A blind partner
cannot steer, and for a beginner an opening that never comes is a session that
cannot be won.

**The first draft said A2's HUD covers this. It does not.** A2 ships a **count** —
`n_slots` on `ScenarioCard`, rendered "2 of 3" — and says so explicitly: *"Ship
the count, not the names."* A count tells the learner that something is
outstanding. It does not tell them it is the price.

So the honest position is: **scene design is the substitute for `pressure_hint`,
and the count is not.** The situation has to be authored so the gap is structural
— a server who brings no menu, a stall with no prices shown. That is content
work, it is the real dependency for V2, and it is not free.

Two things follow. This track does **not** add a naming-HUD dependency to A2:
naming outstanding facts is a disclosure decision that belongs to that track on
its own evidence. And before V2 ships, at least one topic needs a rewritten
situation demonstrating that the gap survives with no partner steering — which
then becomes an authoring rule `validate.py` can check.

---

## The penalty for gaming

`coherence` already exists: `on_track | drifting | off_track` on
`WorkerAnnotation` (`models.py:301`), specified in `DESIGN.md`'s original
annotation schema — it **predates the slot tracker**, has been computed on every
turn since the conversation worker shipped, and is read by **no code path at
all**.

**Do not withhold credit on a bad coherence tag.** That reintroduces the exact
false negative A2 exists to fix, and it would fail the learner whose Chinese was
fine and whose conversational timing was not.

### V1's threshold is not `!= off_track`

The first draft gated the floor on `coherence != "off_track"` and claimed that
closed the hole. **It does not, and the review was right to kill it.** The system
prompt defines `off_track` as *unintelligible or derailed* and `drifting` as
*wandering*. The motivating example — a learner asking about dishes when asked
about drinks — is in-scene wandering. It is `drifting`, or even `on_track`, and a
`!= off_track` gate lets it straight through.

`SCENARIOS.md`'s worked example 2 already tags a comparable miss (谢谢 while
`price` is open) as `drifting`, which is the same point from the other direction.

So the threshold is **an output of the recorded-transcript check, not an input to
it.** V1 cannot be specified further than this until that check exists, and
saying otherwise invites a one-line conditional that closes nothing.

What V1 can honestly claim: it blocks the floor on *derailed* turns. **In-scene
non-sequiturs are V2's job**, because the fix for a partner that plays along is a
partner that does not know to.

### Recording it in the verdict

The verdict needs a **session-level fact computed in Python**, passed to the
worker the way `goal_met` already is — not a list of per-turn tags for the model
to interpret.

Handing raw tags to the verdict worker would repeat the failure A2 just fixed:
that worker, given material it must explain and no computed conclusion, invents
one. The rule (for example *"any turn tagged `off_track`"*, or *"not every turn
`on_track`"*) is a threshold decision and comes out of the same transcript check.
It then lives on `VerdictCard`, with `render_verdict_prompt` given the sentence
rather than the evidence.

**With a blind partner the penalty is mostly emergent anyway.** A partner that
does not know `recommendation` is a slot answers a non-sequitur the way a person
does: by noticing it. The conversation itself pushes back, in character, at the
moment it happens. The verdict record is then a description of what went wrong,
not a punishment bolted on afterwards.

---

## Staging

| | What | Blocked on |
|---|---|---|
| **V0** | A recorded-transcript set, and an evaluation of `coherence` against it. Yields the threshold V1 needs and the rule the verdict fact uses. | nothing |
| **V1** | Gate the floor at the threshold V0 produced. Session-level coherence fact on `VerdictCard`. | V0, and A2's floor |
| **V2** | Goal-blind converser; grader after the reply; the wire contract above. Withholding becomes persona; `pressure_hint` retires into authored scene design. | at least one rewritten situation |
| **V3** | Re-open A2's floor-on-ask compromise, if the grader evaluates ask-AND-answer reliably. | V2 |

**V0 is new in this revision and it is the real first step.** The first draft had
V1 shipping inside A2 as a one-line conditional; that was wrong twice over — the
threshold was wrong, and it gated on a signal nobody has ever checked.

**V2 must also state what happens to A2's floor.** It still runs, against the
grader's reading rather than the converser's, and it remains one-directional:
it can add credit the grader withheld, never remove credit the grader gave. V3
assumes exactly this.

## Risks

**A blind partner may make sessions unwinnable.** This is the one that needs a
real session, not an argument. The scene has to create the opening that
`pressure_hint` used to manufacture, and authored situations have never been
asked to carry that weight. The check is a phone session, and if it fails the
honest answer is better scenes, not a partner that peeks at the rubric.

**Persona-as-withholding may leak or drift.** A generated persona is softer than
an instruction. A server told to be brisk may still helpfully recommend a dish.
This is checkable with recorded transcripts the same way slot extraction is, and
it is the one place where blindness genuinely costs reliability.

**Two calls can disagree about what happened.** The converser's reply and the
grader's reading are produced independently. The `user_reading` rule above closes
the worst version of this on the text path; the seam is still new.

**`coherence` has never been evaluated, and there is nothing to evaluate it
with.** `tests/fixtures/` holds `greeting.wav` and `kb_scenarios/` — no session
transcripts. V0 has to build that set before it can judge the signal, which is
why it is a chunk and not a checkbox.
