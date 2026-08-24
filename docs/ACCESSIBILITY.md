# Accessibility — a session the learner can finish and believe

Companion to [`DESIGN.md`](DESIGN.md) and [`SCENARIOS.md`](SCENARIOS.md). Those
documents specify the turn loop and how a session is graded. This one is about
the first session run by the learner it was built for, and what has to be true
before they open another one.

Status: **A1 built** (#66). A2 HUD built. Floor, verdict copy, and gender pin
still open. A3 not built.

> **A2's floor is no longer a compromise — updated 2026-08-20.** It credits a
> `request` slot on the learner's ask because Python cannot judge whether a
> reply answered. [`VALIDITY.md`](VALIDITY.md)'s V2 grader now credits on the ask
> too, for a stronger reason: the partner's answer is the partner's performance,
> not the learner's. The two implement the same rule, so the floor is a cheaper
> implementation of the model's rule rather than a knowing divergence from it —
> and V3, which existed to re-open that question, is closed as decided.

---

## The evidence

The demo went fine. The session after it did not. The learner's own words:

> *"I was just so stumped. It felt like I was graded on saying exactly the right
> words rather than my intention."*

Ten notes came out of it.

| # | Note | Where it lands |
|---|---|---|
| 2 | When stumped, end now and read the feedback | A1 |
| 4 | 👁/🙈 should say "Show text" / "Hide text" | A1 |
| 6 | The scenario doesn't change on refresh | A1 |
| 7 | Gender pronouns are handled badly | A2 |
| 9 | There is no sense of progress | A2 — the HUD `SCENARIOS.md` already specified |
| 10 | `zui jian` was not credited; `zuijian` was | A2 |
| 3 | Translate the partner's line inline | A3, if a later session still needs it |
| 5 | Asking for a hint should mean something | A3 |
| 1 | Closing should be judged, not hardcoded to 再见 | Already true |
| 8 | Pick a scenario from a list | Not this track. C8 (#53) |

Note 1 needs nothing: `learner_closed` is a model judgment, `termination.py`
requires two closes in a row, and a close carrying real content resets the
counter, so a topic that *teaches* 再见 does not end itself.

## The diagnosis

Two things failed, and only one of them is "the learner was stuck."

**No exit.** Being stumped had two moves: guess, or say 再见 twice. Both end the
session badly.

**No trust.** The session graded a turn wrong and then explained the wrong grade
with a rule nobody wrote. That is the half that stops someone coming back.

> **Let them leave a drowning session, and make the card they land on true.**

Both halves matter, and the order is about shipping, not blocking: **A1 and A2
are independent and can land in either order.** What must not happen is calling
the fire out after A1 alone — an exit into a verdict that invents rules just
delivers the bad teaching faster.

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

Note 10 is the same failure with a different trigger: `zui jian` spaced was not
credited where `zuijian` run together was, though `prompts.py` explicitly accepts
pinyin *"spaced or run together."*

[`SCENARIOS.md`](SCENARIOS.md#known-risks) predicted a strict extractor and
prescribed extractor prompting. **That is the mitigation that failed.** The
prompt already said judge meaning not wording, already accepted spaced pinyin,
already credited 你呢？. Real use missed anyway.

The cause is structural. The partner does two jobs in one call — stay in
character and withhold the request answer, *and* decide whether a named slot just
filled — and a partner tuned to withhold under-annotates. More prompt does not
resolve a conflict of objectives; it moves which one loses. A `live` eval set is
not a gate either: it is excluded from the default run by design.

So A2 puts a **deterministic floor** under the model. This repo's rule is that
deterministic logic gets a failing test first, and "did the learner's own words
cover this slot" is deterministic.

> The deeper cause — that a partner holding the rubric cannot be a natural
> interlocutor at all — is [`VALIDITY.md`](VALIDITY.md). It is a separate track
> and it depends on this one.

### 2. The verdict rationalized the miss

`workers/feedback.py` states the computed outcome as fact and forbids re-grading,
*"no matter how well the conversation reads."* Three good instructions interact
badly:

1. The outcome is stated as fact.
2. Re-grading is forbidden.
3. The transcript contradicts the outcome.

Explaining the outcome then requires reconciling it with what the model can see.
The rule is the only free variable, so the model supplies one.

`prompts.py` already asks it to *"name what they did establish and what they
didn't"* — it never asks why. So the fix is an **addition**, not a cut: *do not
assert a cause for a missing fact, and do not introduce a criterion that is not
in the brief.* Name what they did, name what is open, stop.

A missed slot is a bad grade. A fabricated rule is bad teaching.

---

## A1 — let them leave

- [x] **"I'm stuck"** — a new `end_reason` into the existing verdict path.
- [x] **"Try this again"** — restart on the same `topic_id`.
- [x] **"Show text" / "Hide text"** in words. This is copy; it waits for nothing.

Note 6 is working as designed — restore-on-load protects a phone that locks
mid-conversation — and the re-roll already exists. What is missing is a way to
*decline* the restore, so this is labelling.

The screen map, because where a control lives is half of whether it helps. A
bail-out that appears only on the verdict card is useless: the card exists only
once the session is over.

| Control | Where | Live when |
|---|---|---|
| **I'm stuck** | dock, the one solid button | active, ≥1 learner turn, no turn in flight |
| **👁 Show all text** | dock | always |
| **⋯** | dock | always |
| **⌨️ Type instead** / **🎙️ Speak instead** | in ⋯ | always |
| **🔀 Try something else** | in ⋯ | always |
| **Try this again** | verdict card | always |
| **Try something else** | verdict card | always |
| **Retry** | verdict card | only when the verdict fetch failed |

What the learner needs *while drowning* stays on the surface; what manages the
session goes one tap down. The dock keeps the two moves available to someone who
cannot cope with the line in front of them, and the escape route is still
complete without the buried copy — "I'm stuck" lands on the card, and the card
offers both restarts.

The ellipsis is the menu; it does not need the word "More". The two items keep
the words — a glyph alone is note 4's coin flip, on the control note 4 did not
name — and put the destination emoji next to them, so neither signal is on
its own.

Colour differentiates by **fill, not hue**. This UI already spends green / amber
/ red on tone scores, and red on the bail-out would tell the learner that asking
for help is a failure — the exact reading A1 exists to prevent.

Two contracts, because neither is a bullet's worth of work:

**The new `end_reason` needed a value and a sentence.** `"stuck"` is now in
both `Literal`s (`models.py`, on `SessionState` and `VerdictCard`) and
`render_verdict_prompt` has copy for it beside `cap` and `closed`. A reason with
no copy would have produced a card that does not say why the session ended — on
the one exit built for the learner who most needs to know. That copy is written
*against* the `closed` block next to it: `closed` is deliberately gently
corrective, and a model reading `stuck` the same way would tell someone who
asked for help that they should have pushed on.

**"Try this again" must clear the terminal state.** Both endings write
`status: "complete"` — the turn path via `termination.advance`, and now "I'm
stuck" from the client — and `main.py` 409s any turn against a complete
session. The
load-time guard only drops state when the topic *changes*, so a same-topic
restart sails past it. Clear `filled_at`, `status`, `consecutive_closes`, and
`end_reason` — the existing `reset` handler already does exactly this and its
comment documents the failure verbatim. Reuse that path.

An optional `topic_id` on `POST /api/session` is **not** a thin C8. The client
already holds `session.topic_id` and echoes it every turn as an opaque
server-issued string; echoing it once more to restart is the same pattern one
step earlier. C8 is the client learning what the catalog *contains* so it can
choose. Echoing is not choosing.

**Not here: the topic catalog.** Five topics, one learner — re-roll is enough.
Note 8 is agency, not stuckness.

## A2 — make the card true

- [ ] The deterministic floor, below.
- [ ] The verdict addition: never assert a cause, never add a criterion.
- [ ] Pin the partner's gender in the sketch.
- [x] The progress HUD.
- [ ] One more phone session by the same learner. **Gates A3 only.**

### The floor

The floor takes a different input on each path:

| Path | Input | Why |
|---|---|---|
| Text (`/api/turn/text`) | `user_reading` | The worker resolves typed pinyin to 汉字; it is the only component that can |
| Spoken (`/api/turn`) | the STT transcript | Already 汉字. `SpokenConversationResult` drops `user_reading` deliberately (`models.py:360`) — ~40 output tokens on the branch the reply waits behind |

Both inputs are already 汉字, so the normalization needed is punctuation and
whitespace stripping, not pinyin segmentation.

**The matcher.** Neither obvious reading of `expressible_with` works: all-tokens
fails `我叫小明`, because `self_name` is `[我, 叫, 姓]`; any-token fires on a bare
我, which appears in nearly every beginner utterance. So the floor matches on
**content tokens** — `expressible_with` minus a small closed list of ubiquitous
function words (我, 你, 的, 是) — at least one of which must appear. `self_name`
reduces to {叫, 姓} and `我叫小明` fires. `wellbeing` is `[最近, 怎么样]`, both
content, so 你最近怎么样 fires.

The stoplist is authored once and must be validated against every slot in all
five topics before A2 ships. A slot that reduces to the empty set gets no floor
and says so out loud rather than silently matching nothing.

`你呢？` is a **negative** fixture: the floor must not fire on it, the model path
must, and that disagreement is expected.

**Two properties keep the floor from breaking the existing design.** It runs
**one way** — it can only add credit the model withheld, never withhold credit
the model gave, so `expressible_with` remains a hint for the model exactly as
`SCENARIOS.md` specifies. And it is **gated on the learner's own words**, so it
can never cause the failure `SCENARIOS.md` calls worse: a slot credited because
the *partner* volunteered the fact.

**The request-slot rule.** A `request` slot is authored as *learner asked* **and**
*partner answered*, and `SCENARIOS.md` calls that rule *"on in every mode,
permanently."* Python can check the first half from the reading; it cannot check
the second without judging.

**The floor fires on the ask alone. The AND rule stays the authored rule for the
model.** The cost: a partner that counter-questions instead of answering can
leave the learner credited for a fact they never obtained. Worth paying, because
the partner is instructed to answer and `pressure_hint` withholds only the
answers to questions the learner has *not* asked — a deflection is our bug, and
the learner should not eat our bug.

**Where it runs.** On the turn path, writing into `slots_filled` **before
`termination.advance`**. Not on the verdict path: `workers/feedback.py`
recomputes from the client-held `filled_at`, so a floor in only one of the two
places would let the HUD show three of three while the verdict says a fact is
missing.

**The disagreement log.** Where the model and the floor disagree, log it beside
`termination.py`'s existing per-turn INFO line: server stderr, read by hand
(`fly logs`) on a single-user deploy. No store.

The floor ships **ungated**. A context gate is the validity track's V1
([`VALIDITY.md`](VALIDITY.md)), and it is blocked on evaluating a signal nobody
has checked yet.

### Gender (note 7)

None of the `greetings` slots need a third-person pronoun. 他 and 她 are both
`ta`, the prompt says *"pick from context,"* and the sketch never fixes the
partner's gender — so there is no context. Pin it in the flavour, or stop forcing
the choice. This is a reading-echo bug, not a tracker bug.

### The progress HUD (note 9)

[`SCENARIOS.md`](SCENARIOS.md) already specified the honest scaffold — *"a '2 of
3' indicator, or naming the outstanding goal on the scenario card"* — because a
HUD sits outside the story where an out-of-character partner sits inside it and
damages it. It was defaulted **off** on the grounds that *"the verdict card
already teaches the missed phrase, so failing is cheap and instructive."*

The first session falsified that clause. Failing is not cheap when the card
cannot be trusted.

**Ship the count, not the names.** The client already holds `state.filled_at`, so
it can compute the numerator; all it lacks is the denominator. **Add `n_slots` to
`ScenarioCard`** — "2 of 3", no per-turn wire change, and a count is still not
the slots. Naming the outstanding facts needs their descriptions on the wire,
which is a real disclosure decision and can follow if a count proves too thin.

**Turns too.** The same row carries `max_turns`, so the learner sees "3 of 7"
next to the slot count. The opening line does not spend a turn; the numerator is
the number of learner turns in the dialogue. A cap with no warning is how a
session ends in surprise.

This is the whole of note 9: progress the learner sees *during* the session,
which is when they asked for it — not a badge afterwards.

## A3 — only if a later session still drowns for words

Gated on evidence, not scheduled. Ship it only if the phone session after A2
shows the learner stuck for vocabulary rather than for trust — a help ledger over
a grader that still withholds credit is worse than none, because the learner used
help and still failed.

| Need | Ship |
|---|---|
| I cannot hear it | Show text — already renamed in A1 |
| I cannot understand it | Translate this line, **on tap** |
| I cannot produce it | The outstanding slot's `expressible_with` plus its English description |

**Words, not a winning sentence.** Handing over the line that fills the slot
turns the conversation into a prompted production drill — the pattern
`SCENARIOS.md` was written against, where the learner should get *"stuck at the
point where they need the words."* Recording the tap does not make retrieval
happen.

**Translation is a request beside the loop, like TTS** — not a field on every
reply. The partner is told to speak only Mandarin and the page is audio-first. A
per-turn gloss is a new per-turn job on the branch the learner waits behind; the
verdict worker's gloss is a one-shot at the end of a session and not the same
capability moved earlier.

**Count taps on a pass only.** A fail already has missing slots; adding *"and you
used help"* to it is piling on.

## Not on this track

- **The topic catalog.** C8 (#53).
- **Difficulty.** The partner is pinned to band 2 by a literal in `prompts.py`
  while `HSK_BAND_CEILING` is loaded and never read — C0 (#51).
- **Phase 7.** "Unassisted slots filled" does not need `db.py`. One learner, one
  phone: `localStorage` holds a clean-run flag fine.
- **Support counts feeding the band ceiling.** Heavy translate use might mean the
  partner is out of band, or that the learner was tired, or that the audio
  failed. Not a signal to wire into C3 before C0 reads the ceiling at all.
- **More scenarios per topic.** One scenario per topic is the variety ceiling and
  it is a content problem. Re-roll is enough until someone authors a second scene.
- **The gaming hole.** A learner can say the right words in the wrong place and
  the floor will credit it. That is [`VALIDITY.md`](VALIDITY.md).

## Open threads

**How much help is too much?** Do not decide it in verdict copy. A goal met with
help is a pass — they said the thing. Revisit after there are counts.

**Is the semantic tracker still the primary?** The disagreement log answers it.
If the floor catches most of what the model misses and the model rarely adds
anything the floor did not, the honest conclusion is that extraction should be
deterministic and the model should stop being asked.
