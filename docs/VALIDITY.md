# Validity — does the grade mean what it says?

Companion to [`SCENARIOS.md`](SCENARIOS.md), which specifies *how* a session is
graded. This one asks whether the grade is **earned**.

Status: **not built.** Written 2026-08-17 from a design observation, not from a
session failure — unlike [`ACCESSIBILITY.md`](ACCESSIBILITY.md), which came out
of a learner drowning. Depends on that track's A2.

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

> The partner asks whether you would like something to drink. You ignore it and
> ask what food they have. The `food` slot fills. You get the point.

Nobody had a conversation. The learner emitted a phrase that matched a criterion,
and the criterion did not care that it answered nothing.

Worse, **the partner cooperated.** It knows `food` is a slot, so it takes the
non-sequitur and answers it. A person would not. A person would say *"…I asked
if you wanted a drink."*

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
learner's own words. Say 最近怎么样 into an unrelated exchange and it credits
`wellbeing`. The floor is the most gameable path in the system precisely because
it is the dumb one — it was designed to ignore context, and context is exactly
what distinguishes a question from a phrase.

This is not an argument against the floor. The failure it fixes is real and
observed. It is an argument that the floor needs a context gate, which is V1
below.

---

## The design: a goal-blind converser and a separate grader

**Converser.** Situation, persona, topic KB, conversation history. **No slot
block. No `pressure_hint`.** It replies as a person in a scene, and nothing more.

**Grader.** The transcript including the partner's reply, and the slots. No
character to hold, no reply to write, no reason to be generous — the thing it is
asked cannot be gamed by the thing it is not asked.

### What breaks, and how it is fixed

**Withholding.** `SCENARIOS.md` requires the partner never volunteer the answer
to a `request` slot — *"a helpful answer nobody asked for takes the practice
away."* That rule needs slot knowledge, which a blind converser does not have.

*Fix: encode withholding as **character**, not criteria.* "A brisk vendor who
does not quote prices unless asked" is a persona. The sketch worker already
generates persona per session (`workers/sketch.py`), so this becomes a sketch
prompt requirement and an authoring rule — not rubric knowledge smuggled back in.
A partner withholds because of who it is, not because it has seen the test.

**Pressure.** `pressure_hint` steers the scene toward whichever fact is still
outstanding, so the gap lands where the learner needs the words. A blind partner
cannot steer, and for a beginner an opening that never comes is a session that
cannot be won.

*Fix: the scaffold moves from the fiction to the UI — which is what
`SCENARIOS.md` said it should be in the first place:* **"The honest place for
scaffolding is the UI, not the fiction."** A2's HUD shows the learner what is
still open, so **the learner** creates the opening instead of the partner
manufacturing it. What is left over is scene design: author the situation so the
gap is structural.

**This is why validity depends on A2 and cannot ship before it.** Removing
`pressure_hint` without the HUD takes away the only thing pointing at the gap.

### What it costs, and one thing it buys back

**Latency: better, not worse.** The grader is a third branch in the fan-out
`orchestrator.py:300` already runs (Azure PA ∥ conversation worker, emitted as
each resolves; `done` waits on both). Nothing the learner waits on gets slower —
and the annotation leaves the reply's output entirely. The spoken path already
splits its schema to keep ~40 output tokens off that branch
(`SpokenConversationResult`); this removes the rest of the annotation from it
too.

**Caching: one more prefix per session.** The converser's prefix *shrinks* — the
scenario block leaves it. The grader gets its own stable prefix. The spoken/text
schema split already established that per-session prefix count is a cost we pay
once, not per turn.

**Model: the grader need not be the conversationalist's equal.** Structured
extraction off the critical path is the cheapest thing in the system to run on a
smaller model. Worth measuring; not decided here.

**It may let us revisit A2's compromise.** A2 floors on the *ask* alone because
Python cannot tell whether a reply answered. A grader that sees the partner's
reply and holds no character can evaluate the authored AND rule properly. If it
does so reliably, the floor-on-ask cost — a counter-questioning partner crediting
a fact never obtained — stops being one we have to accept.

---

## The penalty for gaming

`coherence` already exists: `on_track | drifting | off_track`, on
`WorkerAnnotation` (`models.py:301`), computed on **every turn since the tracker
shipped**, and read by **no code path at all**. The signal is on the wire and
already paid for.

**Do not withhold credit on `off_track`.** That reintroduces the exact false
negative A2 exists to fix, and it would fail the learner whose Chinese was fine
and whose conversational timing was not.

Instead:

- **Gate the floor on it.** The floor fires only when the turn is not
  `off_track`. The mechanical path gets the conservative rule; the model's own
  credit rests on its own judgment. This is V1, and it is small.
- **Record it in the verdict.** *"You got there, but the conversation didn't hold
  together."* That is a validity measure — it describes the thing the app is
  for — and it is not a help ledger by another name.

**With a blind partner the penalty is mostly emergent anyway.** A partner that
does not know `food` is a slot answers a non-sequitur the way a person does: by
noticing it. The conversation itself pushes back, in character, at the moment it
happens. Measurement is then a record of what went wrong, not a punishment
bolted on afterwards.

---

## Staging

| | What | Depends on |
|---|---|---|
| **V1** | Gate the floor on `coherence`. Record coherence in the verdict. | A2's floor |
| **V2** | Goal-blind converser; grader as a third fan-out branch. Withholding becomes persona; `pressure_hint` retires. | A2's HUD |
| **V3** | Re-open A2's floor-on-ask compromise, if the grader evaluates AND reliably. | V2 |

V1 ships inside A2 rather than waiting for this track — it closes a hole A2 opens
and it is a conditional on an existing field.

## Risks

**A blind partner may make sessions unwinnable.** This is the one that needs a
real session, not an argument. The scene has to create the opening that
`pressure_hint` used to manufacture, and authored situations have never been
asked to carry that weight. Mitigation is the HUD plus scene design; the check is
a phone session, and if it fails the honest answer is better scenes, not a
partner that peeks at the rubric.

**Persona-as-withholding may leak or drift.** A generated persona is softer than
an instruction. A vendor told to be brisk may still helpfully quote a price. This
is checkable with recorded transcripts the same way slot extraction is, and it is
the one place where blindness genuinely costs reliability.

**Two calls can disagree about what happened.** The converser's reply and the
grader's reading are produced independently. They are not required to agree, and
nothing downstream reads the converser's view once annotation moves — but the
seam is new and worth watching.

**`coherence` has never been evaluated.** It has been computed and discarded
since it shipped, so we have no evidence it is any good. Before V1 gates anything
on it, check it against recorded transcripts. A gate on a bad signal is worse
than no gate.
