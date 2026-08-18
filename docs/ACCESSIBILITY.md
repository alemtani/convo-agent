# Accessibility — giving the learner a next move

Companion to [`DESIGN.md`](DESIGN.md) and [`SCENARIOS.md`](SCENARIOS.md). Those
documents specify the turn loop and how a session is graded. This one specifies
**what the learner does when they are stuck**, which the running app has no
answer for.

Status: **not built.** This is the plan, written from the first real session
run by the learner it was built for (2026-08-16). Nothing here has shipped.

---

## The evidence

The app was demoed, then used. The demo went fine. The session did not. The
learner's own words: *"I was just so stumped. It felt like I was graded on
saying exactly the right words rather than my intention."*

Ten notes came out of that session. They are not ten problems.

| # | Note | Where it lands |
|---|---|---|
| 1 | Closing should be judged, not hardcoded to 再见 | Already true — see below |
| 2 | When stumped, end the session now and read the feedback | A1 — bail out |
| 3 | Translate the partner's line inline | A3 — support ledger |
| 4 | 👁/🙈 should say "Show text" / "Hide text" | A3 — support ledger |
| 5 | Asking for a hint should mean something | A3 — support ledger |
| 6 | The scenario doesn't change on refresh | A1 — agency |
| 7 | Gender pronouns are handled badly | A2 — reader fidelity |
| 8 | Redo this scenario; pick a scenario | A1 — agency |
| 9 | There is no sense of progress | A4 — progression |
| 10 | `zui jian` was not credited; `zuijian` was | A2 — reader fidelity |

## The diagnosis

**When the learner is stuck, the app has two moves: guess, or say 再见 twice.**

Both end the session badly, and neither teaches the words that were missing at
the moment they were missing. Everything the learner can reach mid-session is
either hidden behind an emoji (👁), unavailable (translation), or terminal
(goodbye).

That is what "accessible" means here, and it is one sentence:

> There is always a next move, and taking it costs something legible — instead
> of costing nothing, or costing the session.

The last clause matters. The learner asked for translation (#3) and in the same
breath said a hint is *"a sign of weakness"* (#5). Both are right. Hiding help
does not make help meaningful. **Recording it does.**

---

## Two failures of fidelity, found in one turn

Before any of that: the session graded a turn wrong, and then explained the
wrong grade with a rule nobody wrote.

The learner asked 你最近怎么样 — *how have you been lately* — which is the
`wellbeing` slot in `greetings`, authored as *"Find out how they have been
lately."* The verdict said:

> *"you actually asked ni zui jin zen me yang at one point, but then your reply
> didn't confirm you understood her answer…"*

**No such requirement exists.** Read `kb/zh/greetings/topic.md`: the slot is
`kind: request`, and `prompts.py` fills a request slot when *"the learner drove
it, and your reply answers it."* Comprehension is not a criterion. Neither the
KB nor `termination.py` has any notion of confirming that you understood.

Two distinct bugs stack here, and they need separating.

### 1. The tracker is too strict

[`SCENARIOS.md`](SCENARIOS.md) predicted this exact failure under "Known
risks": *"A learner who reaches the goal by an unanticipated but valid route
might not trip the slot… If real use shows the extractor being too strict, the
fix is extractor prompting, not more vocabulary in the seed."*

Real use has now shown it. Note #10 is the same failure with a different
trigger: `zui jian` spaced was not credited where `zuijian` run together was —
even though `prompts.py` explicitly tells the worker to accept pinyin *"spaced
or run together."* Note #7 is a third: 他 and 她 are both `ta`, the prompt says
to *"pick from context,"* and nothing in the scene ever fixes the partner's
gender for the worker to pick from.

The alignment code is not at fault. `typed_pinyin.py` skips separators when it
walks typed pinyin against the expected syllables. The worker read the turn and
still withheld the slot. This is prompt and eval work, not a patch.

### 2. The verdict rationalizes a tracker miss

This is the new one, and it is worse.

`workers/feedback.py` is built so the model cannot re-grade: the outcome is
computed in Python and stated to the worker as fact. `prompts.py` says it
outright — *"Do not re-grade it, soften it, or argue with it. If you are told
the learner did not establish a fact, they did not establish it, no matter how
well the conversation reads."*

That instruction does its job and then keeps going. Handed a transcript where
the learner plainly asked, and a verdict saying they did not, the model does the
only thing left: **it invents a criterion that reconciles them.** The guardrail
against generous grading became a generator of fake rules.

The learner is then taught a rule that does not exist, cannot be satisfied, and
will not appear in any KB. That is worse than a missed slot. A missed slot is a
bad grade; a fabricated rule is bad instruction.

**The fix is not more prompt.** The worker needs a way to say *"the outcome and
the transcript disagree"* rather than being required to explain the outcome no
matter what. Two candidate shapes, to be decided in A2:

- Let the verdict report a `disagreement` flag. It still does not change the
  outcome — it surfaces the conflict to the learner and to our logs.
- Or narrow the worker's brief: explain **what the learner did**, and name the
  missing facts, without asserting *why* each one is missing.

The second is smaller and probably right. The worker was never told *why* a slot
is unfilled, because Python does not know either — so it should not be writing
that sentence at all.

### Note #1 is already done

`learner_closed` is a model judgment (*"a goodbye, 再见 and the like"*), not a
string match. `termination.py` needs two in a row (`CLOSES_TO_END`), and a close
carrying real content resets the counter — so a topic that *teaches* 再见 does
not end itself. Nothing to hardcode away. Keep it.

---

## The core idea: a support ledger

> Help is **free in the moment and recorded in the verdict.**

Three tiers, available on every turn, each logged against the turn it was used
on:

| Tier | What the learner gets | Cost today |
|---|---|---|
| 1 — Show text | The 汉字 + pinyin already behind 👁 | Free and invisible |
| 2 — Translate | An English gloss of the partner's line | Does not exist |
| 3 — Give me the words | The phrase that would establish the outstanding slot: 汉字, pinyin, gloss | Does not exist |

Nothing is locked. Nothing is punished mid-session. The verdict gains one
sentence: *"You met the goal. You used help twice, both times on asking the
price."*

This resolves the contradiction in notes #3 and #5. The learner wanted help
available and wanted help to mean something. Under a ledger it means something
without ever being withheld — the outcome stops being pass/fail and becomes
pass/fail **plus how much scaffolding you leaned on**, which is the honest
measure and the only one that can improve.

Tier 3 is the direct answer to being stumped. It converts a dead end into a turn
the learner actually speaks, using words that are in band by construction. Tier 2
is cheaper than it looks: the verdict worker already writes English glosses for
in-band lines, so this is an existing capability moved earlier in the session.

Tier 1 gets the rename from note #4 at the same time. "Show text" and "Hide
text", in words. The emoji were never the affordance they looked like.

### What this costs

The reply gains an English gloss and the turn response gains a support count.
The frontend gains two buttons and the localStorage record to survive a reload.
`termination.py` stays pure — support is counted, never a termination condition.

### The risk worth naming

Translation on tap can turn a Mandarin session into English reading. Two
mitigations, both cheap: translation is **per line, on demand**, never a global
toggle; and the count is visible in the verdict, so the learner sees their own
drift. If the ledger shows tier 2 on every single turn, that is a finding about
band ceiling — not a reason to take the button away.

---

## Progression falls out of the ledger

Note #9 asks for what Duolingo does right: the sense that you are getting
somewhere. The instinct is to reach for streaks and XP. The app does not need
them. It needs **one number that moves**, and the ledger produces one for free:

> **Unassisted slots filled.**

- Same scenario, second attempt, less help → visible improvement.
- **Clean run** = goal met, zero support used. That is the badge, and it is
  earned against a fixed scenario, so it is real rather than decorative.
- Across topics, the covered-set and proficiency store (`db.py`, `profile.py`,
  Phase 7 — still unbuilt) turn it into *"3 of 5 topics cleared clean."*

This is why the ledger ships before the progression work. Progression needs
something to count, and today nothing is counted.

It also preserves the thing the learner said was good: *"it's good this is a
harsh reality check."* A clean run is hard. Nothing here makes the goal easier —
it makes the road to it passable, and prices the help.

---

## Agency over what you practice

Notes #6 and #8 are one gap: the learner cannot choose, and cannot re-roll.

**#6 is working as designed, and the design is wrong.** The session lives in
`localStorage` and is restored on load — deliberately, because a phone that locks
mid-conversation must not lose the thread. What is missing is a way to
*decline* the restore. There is a reset control, but nothing next to the
scenario card that says "a different one."

**#8 is nearly free on the server.** `GET /api/topics` already exists (#54).
`POST /api/session` takes no body only because the frontend was given no business
knowing the catalog. Adding an optional `topic_id` is additive.

One constraint to be honest about: **there is one scenario per topic.** Redoing
the exact scenario is therefore the easy half, and it is the half that matters
for a clean run. Genuine *variety* means authoring more scenarios per topic,
which is content work on the curriculum track, not a feature here.

This is a deliberate down payment on **C8 (#53)**, not a competitor to it. C8 is
the curriculum-aware topic list — covered, weak, stale, weighted. What ships here
is the dumb version: a list, and a tap. When the profile store lands, C8 replaces
the ordering without touching the surface.

---

## Staging

Four chunks. A1 and A2 are independent and can share a session.

| | Chunk | Notes covered | Shape |
|---|---|---|---|
| **A1** | Bail out and agency | 2, 4, 6, 8 | Frontend, plus an optional `topic_id` on session start |
| **A2** | Reader fidelity | 7, 10, and the rationalized verdict | Prompt work and `live` evals |
| **A3** | Support ledger | 3, 5, and the #4 rename | Reply fields, two controls, verdict change |
| **A4** | Progression | 9 | Needs A3 to count and Phase 7 to persist |

**A1 — bail out and agency.** *"I'm stuck, end it"* is a new `end_reason` into
the existing verdict path; everything the verdict needs is already client-held.
This is the smallest change that removes the worst dead end, and it should not
wait for the rest.

**A2 — reader fidelity.** Before scaffolding the learner, make sure the thing
being scaffolded reads them correctly. A `live` eval set of sloppy pinyin —
spaced, unspaced, mistoned, misspelled — asserting the expected reading and the
expected `slots_filled`. Plus the verdict-brief narrowing above, and a scene fact
that fixes the partner's gender so 他/她 has context to resolve against.

**A3 — support ledger.** The big one. Do not start it before A2: a ledger built
on a tracker that withholds credit will record help the learner did not need.

**A4 — progression.** Pulls Phase 7 (`db.py`, `profile.py`) forward, and meets
the curriculum track coming the other way.

Every chunk changes what the learner experiences, so every chunk earns a tunnel
and a real phone check before its PR — including A2, which touches no frontend
file. The suite asserts the request we build. It cannot see that the partner now
reads spaced pinyin correctly.

---

## What this does not do

- **It does not lower the goal.** Slots stay authored, binary, and checked in
  Python. The verdict stays a computed outcome.
- **It does not add gamification.** No streaks, no XP, no daily goal. One
  number, derived from work the learner actually did.
- **It does not fix difficulty.** The partner is pinned to band 2 because
  `prompts.py` hardcodes it — `HSK_BAND_CEILING` is loaded and never read
  (**C0, #51**). That is the real difficulty knob and it belongs to the
  curriculum track. Accessibility is about the learner who is stuck at the right
  level, not about picking the level.

## Open threads

- **How much help is too much?** The ledger records; it does not judge. Whether
  a goal met with tier-3 help on every slot should read as a pass is a product
  decision, and it should be made after watching real counts, not before.
- **Should support feed the band ceiling?** Heavy tier-2 use is evidence the
  partner is out of band. That is a signal C3 (#46) would want, and a reason to
  keep the ledger durable rather than per-session.
- **One scenario per topic is the variety ceiling.** Re-rolling gives a new
  opening line and flavour, not a new obstacle. At some point that stops feeling
  like a fresh session.
