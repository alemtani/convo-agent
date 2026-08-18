# Accessibility — a session the learner can finish and believe

Companion to [`DESIGN.md`](DESIGN.md) and [`SCENARIOS.md`](SCENARIOS.md). Those
documents specify the turn loop and how a session is graded. This one is about
the first session run by the learner it was built for, on 2026-08-16, and what
has to be true before they open another one.

Status: **not built.** Revised 2026-08-17 after review; the first draft is
answered at the end.

---

## The evidence

The demo went fine. The session after it did not. The learner's own words:

> *"I was just so stumped. It felt like I was graded on saying exactly the right
> words rather than my intention."*

Ten notes came out of it.

| # | Note | Where it lands |
|---|---|---|
| 1 | Closing should be judged, not hardcoded to 再见 | Already true. Not reopening — see below |
| 2 | When stumped, end now and read the feedback | A1 |
| 4 | 👁/🙈 should say "Show text" / "Hide text" | A1 — it is copy |
| 6 | The scenario doesn't change on refresh | A1 |
| 7 | Gender pronouns are handled badly | A2, but as a reading-echo bug, not a tracker bug |
| 10 | `zui jian` was not credited; `zuijian` was | A2 |
| 9 | There is no sense of progress | A2 — the HUD `SCENARIOS.md` already specified |
| 8 | Pick a scenario from a list | Not this track. C8 (#53) |
| 3 | Translate the partner's line inline | A3, only if a later session still needs it |
| 5 | Asking for a hint should mean something | A3, and smaller than the first draft made it |

## The diagnosis

Two things failed, and only one of them is "the learner was stuck."

**No exit.** Being stumped had two moves: guess, or say 再见 twice. Both end the
session badly.

**No trust.** The session graded a turn wrong and then explained the wrong grade
with a rule nobody wrote. That is the half that stops someone coming back. An
exit button does not repair it.

So the sentence for this app, this month, is not about pricing help:

> **Let them leave a drowning session, and make the card they land on true.**

Order matters and it is not negotiable: an exit into a verdict that invents rules
just delivers the bad teaching faster.

---

## The two stacked bugs

The learner asked 你最近怎么样 — *how have you been lately* — which is the
`wellbeing` slot in `greetings`, authored as *"Find out how they have been
lately."* The verdict said:

> *"you actually asked ni zui jin zen me yang at one point, but then your reply
> didn't confirm you understood her answer…"*

**No such requirement exists.** The slot is `kind: request`, and `prompts.py`
fills one when *"the learner drove it, and your reply answers it."* Comprehension
is not a criterion in the KB, in `termination.py`, or anywhere else.

### 1. The tracker withheld a slot it should have granted

Note #10 is the same failure with a different trigger: `zui jian` spaced was not
credited where `zuijian` run together was — though `prompts.py` explicitly
accepts pinyin *"spaced or run together."* The alignment code is not at fault;
`typed_pinyin.py` skips separators.

[`SCENARIOS.md`](SCENARIOS.md#known-risks) predicted a strict extractor and named
the fix: *"extractor prompting, not more vocabulary in the seed."*

**That mitigation is what just failed.** The prompt already said judge meaning
not wording, already accepted spaced pinyin, already credited 你呢？. Real use
missed anyway. Repeating the mitigation because it is written down is how a
design document stops being evidence.

The reason it fails is structural. The partner does two jobs in one call:

- Stay in character. Withhold the request answer. One short sentence.
- Also decide whether a named slot just filled.

A partner tuned to withhold under-annotates. More prompt does not resolve a
conflict of objectives; it just moves which one loses. And a
`@pytest.mark.live` eval set is not a gate — it is a weather report, excluded
from the default run by design.

**So A2 puts a Python floor under the model** (below). This repo's own rule is
that deterministic logic gets a failing test first, and "did the learner's own
words cover this slot" is deterministic.

### 2. The verdict rationalized the miss

Not predicted, and the more damaging half. `workers/feedback.py` states the
computed outcome as fact and forbids re-grading — *"no matter how well the
conversation reads."* Handed a transcript that contradicts the outcome, the model
reconciles them the only way left: **it invents a criterion.**

A missed slot is a bad grade. A fabricated rule is bad teaching.

The fix is not more prompt. The worker is asked to explain *why* a slot is
unfilled, and Python never told it why, because Python does not know. **Cut that
from the brief.** Name what the learner did and which facts are still open;
never assert a cause.

### Note #1 is already done

`learner_closed` is a model judgment, not a string match. `termination.py`
requires two in a row, and a close carrying real content resets the counter.
Nothing to fix. Not reopening it.

---

## A1 — let them leave

Three small things, one PR. This is the week's work.

- [ ] **"I'm stuck — end it."** A new `end_reason` into the existing verdict
      path. Everything the verdict needs is already client-held.
- [ ] **"Try this again"** — restart with the same `topic_id`.
- [ ] **"Show text" / "Hide text"** in words. This is copy. The learner asked for
      words; it does not wait for anything.

Note 6 ("scenario doesn't change on refresh") is working as designed —
restore-on-load protects a phone that locks mid-conversation. What is missing is
a way to *decline* the restore, and **`↺ New` already re-rolls**. So this is
labelling, not a feature: `End this` / `Try this again` / `Different one`.

**Not in A1: the topic catalog.** The first draft put a picker here as a down
payment on C8 (#53). That delays the bail-out to pay a deposit on someone else's
issue. Five topics, one learner — re-roll is enough. Note 8 is agency, not
stuckness.

## A2 — make the card true

- [ ] **Cut the causal claim from the verdict brief.** What they did, what is
      open. No *why*.
- [ ] **A Python floor under the extractor**, with fixtures for `zui jian`,
      `zuijian`, 你最近怎么样, and 你呢？.
- [ ] **Pin the partner's gender in the sketch** (note 7).
- [ ] **The progress HUD** — see below.
- [ ] One more phone session by the same learner, before anything else ships.

### The floor, precisely

The worker already returns `user_reading` — the learner's turn in 汉字, as it
understood them. Normalize it the way `typed_pinyin.py` already walks input, and
compare against the slot's `expressible_with`.

Two properties make this safe, and both need stating:

**It runs one way. It can only add credit, never withhold it.** `expressible_with`
is documented everywhere as *"a hint to the extractor, never a string matcher,"*
and that contract survives: the model can still credit an unanticipated route the
floor would miss. The floor exists solely to stop a false negative we have
already seen.

**It is gated on the learner's own words**, so it cannot cause the failure
`SCENARIOS.md` calls worse — a slot credited because the *partner* volunteered
the fact. The floor cannot fire on anything the learner did not say.

One decision A2 has to make rather than assume. A `request` slot is authored as
*learner asked* **and** *partner answered*. Python can check the first half from
the reading; it cannot check the second without judging. Recommendation: **floor
on the ask alone.** The partner is instructed to answer, so a partner that
deflects is our bug, and the learner should not eat it. Log when the model and
the floor disagree — that log is what tells us whether the semantic tracker is
worth keeping as the primary.

### Note 7 is not a tracker bug

None of the `greetings` slots need a third-person pronoun — they are `self_name`,
`partner_name`, `wellbeing`. 他 and 她 are both `ta`, the prompt says *"pick from
context,"* and the sketch never fixes the partner's gender, so there is no
context to pick from. That is a reading-echo bug with a one-line fix. It does not
belong inside extractor evals.

### Progress is note 9, and it was already designed

[`SCENARIOS.md`](SCENARIOS.md) already specified the honest scaffold: *"a '2 of
3' indicator, or naming the outstanding goal on the scenario card,"* explicitly
because a HUD sits outside the story where an out-of-character partner sits
inside it and damages it.

It was defaulted **off**, with this rationale: *"the verdict card already teaches
the missed phrase, so failing is cheap and instructive."*

**The first session falsified that clause.** Failing is not cheap when you cannot
reach a true verdict. Flip the default. The slots are authored English and the
doc calls it a one-line frontend addition.

This is also the whole of note 9. Progress the learner can see *during* the
session, which is when they asked for it — not a badge afterwards.

## A3 — only if a later session still drowns for words

Gated on evidence, not scheduled. If the phone session after A2 shows the learner
stuck for vocabulary rather than for trust:

| Need | Ship |
|---|---|
| I cannot hear it | Show text — already renamed in A1 |
| I cannot understand it | Translate this line, **on tap** |
| I cannot produce it | The outstanding slot's `expressible_with` plus its English description |

**Words, not a winning sentence.** Handing over the line that fills the slot
turns the conversation into a prompted production drill — the pattern
`SCENARIOS.md` was written against, where the learner is meant to get *"stuck at
the point where they need the words."* Recording the tap does not make retrieval
happen.

**Translation is a request beside the loop, like TTS** — not a field on every
reply. The partner is told to speak only Mandarin and the page is audio-first; a
per-turn gloss is a new per-turn job and English on every turn, not "the verdict's
gloss, earlier."

**Count taps on a pass only.** A fail already has missing slots; adding *"and you
used help"* to it is piling on.

## Not on this track

- **The topic catalog.** C8 (#53).
- **Difficulty.** The partner is pinned to band 2 by a literal in `prompts.py`
  while `HSK_BAND_CEILING` is loaded and never read — C0 (#51).
- **Phase 7.** "Unassisted slots filled" is a good number and it does **not**
  need `db.py`. One phone, one learner: `localStorage` holds a clean-run flag
  fine. Pulling the store forward so a badge can persist is curriculum work
  wearing a different hat, and it would stall both tracks.
- **Support counts feeding the band ceiling.** Heavy translate use might mean the
  partner is out of band. It might mean they were tired, or the audio failed.
  Do not wire a shame signal into C3 before C0 reads the ceiling at all.
- **More scenarios per topic.** One scenario per topic is the variety ceiling and
  it is a content problem. Re-roll is enough until someone authors a second scene.

## Open threads, answered

**How much help is too much?** Do not decide it in verdict copy. A goal met with
help is a pass — they said the thing. Revisit after there are counts.

**Is the semantic tracker still the primary?** A2's disagreement log answers it.
If Python's floor catches most of what the model misses and the model rarely adds
anything the floor did not, the honest conclusion is that extraction should be
deterministic and the model should stop being asked.

---

## What changed from the first draft

The first draft made a **support ledger** — help free in the moment, priced in
the verdict — the load-bearing idea, and organized four chunks around it.

That was the wrong shape for this fire.

- The ledger is a later product. It answers "help should mean something," which
  is a real note, but not the one that ends sessions.
- A ledger on top of a grader that still withholds credit is actively worse: the
  learner used help *and* still failed.
- Tier 3 as a composed sentence fought `SCENARIOS.md`'s own obstacle design.
- Progress got routed through Phase 7 and a badge, when the HUD was specified
  two documents ago and defaulted off for a reason the session disproved.
- A1 was a bag: notes 2, 4, 6, and 8 in one chunk, and note 4 appearing in two
  chunks at once — a contradiction that was the tell.

Kept from it: the bail-out as the first ship, A2 before A3, difficulty staying on
C0, note #1 already done, and the cut to the verdict's causal claim.
