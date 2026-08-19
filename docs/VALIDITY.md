# Validity — does the grade mean what it says?

Companion to [`SCENARIOS.md`](SCENARIOS.md), which specifies *how* a session is
graded. This one asks whether the grade is **earned**.

Status: **not built.** Depends on [`ACCESSIBILITY.md`](ACCESSIBILITY.md)'s A2.

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

### What each one sees

| | Converser | Grader |
|---|---|---|
| Authored `situation` | ✅ | ✅ |
| Authored `goal`, slot ids, `expressible_with` | ❌ | ✅ |
| System-prompt slot / withhold paragraphs | ❌ (they move) | ✅ |
| Persona from the sketch | ✅ | ❌ |
| Conversation history | ✅ | ✅ |
| The partner's new reply | writes it | reads it |

The sketch prompt currently forbids leaking the goal and restricts itself to
flavour. V2 **widens** it: persona may now carry withholding (below), which is
character, not criteria. That amendment is part of this work.

**What the grader reads as 汉字** follows the same per-path split as A2's floor:
the STT transcript on the spoken path, the converser's `user_reading` on the text
path. Taking `user_reading` from the converser rather than re-resolving pinyin in
the grader matters — two independent resolutions of `wo jiao xiao ming` could
disagree, and then the learner's own bubble and their grade would describe
different sentences.

**What moves:** `slots_filled`, `learner_closed`, and `coherence`. **What
stays:** `grammar_notes`, which is a live verdict input about the learner's
Chinese rather than about the rubric, plus the unused fields, which moving buys
nothing.

### The sequence

The grader needs the partner's reply, so it **cannot** join the existing
`PA ∥ converser` fan-out — that reply does not exist when those two start.

```
mic → STT → yield transcript
         ├─ Azure PA ──────────────────────→ yield score
         └─ converser → yield reply ─→ grader → yield state
                                       (may overlap leftover PA)
            all done                        → yield done
```

Consequences the implementation has to honour:

- `ReplyEvent` carries the partner's line as soon as the converser returns, and
  **no longer carries `state`.** A new event does. `CLAUDE.md` states that state
  rides `ReplyEvent`; that line changes with this work.
- `termination.advance` runs only on the grader's annotation.
- The client must not submit the next turn, or treat the session as complete,
  until that state arrives.
- **If the grader fails:** the reply stands, the turn returns normally, and the
  previous `SessionState` is echoed unchanged. No slot is credited — and no close
  is counted either, so a learner saying goodbye through a grader outage falls
  back on the turn cap.
- `/api/turn/text` gets the same contract, as **two serial Claude calls** where
  today there is one.

### What it costs

**Time to the partner's line: better.** The annotation leaves the converser's
output schema; the spoken path already splits its schema to keep ~40 output
tokens off this branch.

**Time to the next turn: worse.** State waits on a second model call that starts
after the first finishes. The learner reads the reply while the grader runs,
which is what makes the trade defensible — but it is a trade, not a free win. If
it measures badly the fallback is a smaller grader model, not a re-merge.

**Caching: one more prefix per session.** The converser's prefix shrinks — the
scenario block leaves it — and the grader gets its own. `SCENARIOS.md` "Caching"
says the slots live in the conversation worker's frozen prefix; V2 inverts that
and amends it.

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
| **V0** | A recorded-transcript fixture set and the first measurement of `coherence` against gold labels. Reports thresholds, or reports that none is safe. **Ships no gate.** | nothing |
| **V1** | Gate the floor at whatever V0 supports. Session-level coherence fact on `VerdictCard`. | V0, and A2's floor |
| **V2** | Goal-blind converser; grader after the reply; withholding as persona; scene design replacing `pressure_hint`. | a rewritten situation that proves the gap survives |
| **V3** | Re-open A2's floor-on-ask compromise if the grader evaluates ask-AND-answer reliably. | V2 |

**V2 does not flip all five topics at once.** It runs where the request slots are
the partner's own facts, or where the topic carries an authored withholding
field. `food-ordering` is the rewrite target. `validate.py` cannot judge whether
a situation creates an opening — that is prose — so the guardrail is an authored
field, not a linter rule.

**V0 measures `coherence` on the converser; V2 moves it to the grader.** The
matrix has to be re-run after V2 before the same gate is trusted.

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
