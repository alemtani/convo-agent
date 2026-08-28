# A8 — the grader's prompt, split by caller

**199 of 220 owed slots recovered, over 200 draws. 0 spurious.** Up from
A6.5's 190/220 on the prompt this replaces. Same corpus, same depth, same model.

The change: one grader prefix serving two callers became two, one per caller.
The live turn's prefix says *grade the final turn*; the review's says *judge
every turn* and carries the instruction that used to arrive as a note after the
cache breakpoint.

## The numbers


| session | owed | recovered | rate | every slot | spurious |
|---|---|---|---|---|---|
| family-my-family-oldest-turn | my_family | `my_family` 20/20 | 100% | 20/20 | — |
| family-size-question-mid-session | their_family_size | `their_family_size` 20/20 | 100% | 20/20 | — |
| food-drinks-question-mid-session | drinks | `drinks` 20/20 | 100% | 20/20 | — |
| food-two-questions-lost | recommendation, drinks | `drinks` 20/20, `recommendation` 20/20 | 100% | 20/20 | — |
| greetings-name-question-final-turn | partner_name | `partner_name` 20/20 | 100% | 20/20 | — |
| greetings-name-question-mid-session | partner_name | `partner_name` 11/20 | 55% | 11/20 | — |
| greetings-name-question-oldest-turn | partner_name | `partner_name` 8/20 | 40% | 8/20 | — |
| greetings-name-statement-oldest-turn | self_name | `self_name` 20/20 | 100% | 20/20 | — |
| greetings-nothing-to-recover | — | — | — | 20/20 | — |
| self-intro-ungraded-debt | partner_origin, partner_job | `partner_job` 20/20, `partner_origin` 20/20 | 100% | 20/20 | — |
| **total** | **220** | **199** | **90%** | **179/200** | **0** |

## Per-slot rates

The number A6 could not produce. Two slots both labelled *recoverable* can behave nothing alike, and only a per-slot rate with its sample count shows which one the loss is in.

| session | slot | recovered |
|---|---|---|
| family-my-family-oldest-turn | my_family | 20/20 |
| family-size-question-mid-session | their_family_size | 20/20 |
| food-drinks-question-mid-session | drinks | 20/20 |
| food-two-questions-lost | recommendation | 20/20 |
| food-two-questions-lost | drinks | 20/20 |
| greetings-name-question-final-turn | partner_name | 20/20 |
| greetings-name-question-mid-session | partner_name | 11/20 ⚠️ |
| greetings-name-question-oldest-turn | partner_name | 8/20 ⚠️ |
| greetings-name-statement-oldest-turn | self_name | 20/20 |
| self-intro-ungraded-debt | partner_origin | 20/20 |
| self-intro-ungraded-debt | partner_job | 20/20 |

## What moved

| session | A6.5 | A8 |
|---|---|---|
| **greetings-name-question-mid-session** | **0/20** | **11/20** |
| greetings-name-question-oldest-turn | 11/20 | 8/20 |
| self-intro-ungraded-debt · partner_origin | 19/20 | 20/20 |
| every other owed slot (8 of them) | 20/20 | 20/20 |
| **total** | **190/220 (86%)** | **199/220 (90%)** |

**The case that was never recovered is now recovered about half the time.** That
is the one A6 built its whole conclusion on and A6.5 measured at 0/20. Nothing
about the words of the prompt changed for it — the same three slot rules, in the
same order. What changed is that the instruction asking for earlier turns is now
*in the frozen prefix*, at the top of the request, instead of appended to the
last user message behind the cache breakpoint arguing with a block that had
already said "That pair is the whole of what you need" and "Grade the learner's
final turn."

**The oldest-turn case moved the other way, 11/20 to 8/20.** Three draws of
twenty. Reported rather than explained: at those counts the difference is not
distinguishable from noise, and inventing a mechanism for it is the mistake this
whole step exists to stop making.

**Spurious stayed at zero**, 200 draws, including twenty of a session with two
slots sitting there to be wrongly given.

## What it costs

| | before | after |
|---|---|---|
| turn prefix | 1079 tokens | **913** |
| review prefix | 1079 tokens | 1037 |
| review note (after the breakpoint) | 244 tokens | **36** |
| whole review call's instructions | 1323 tokens | **1073** |

Counted with `messages.count_tokens` on `claude-opus-5`, `greetings`.

The turn prefix is 15% shorter and still **above Opus 5's 512-token minimum
cacheable prefix** — worth stating, because a cut that took it under would have
stopped the prefix caching at all, silently, and the only symptom would have
been the bill.

**The review's prefix is deliberately not cached**, and that corrects the A8
plan. The plan expected a second cache entry to be the price of the split. It is
a saving instead: the review is *one call per session*, so a cached prefix is
written at 1.25x and read zero times, and break-even is two reads — the same
arithmetic `workers/feedback.py` already applies to the verdict call.

## Step 3: what should not ship on every call — measured, and declined

The A8 plan's third step asks whether the rubric should be fetched rather than
pinned. It should not, and the arithmetic is short enough to write down.

The only deferrable content is the rubric block — scene, goal, slots — at **211
tokens**, 23% of the turn prefix. Everything else is instruction the model needs
before it reads anything.

Deferred tool loading (`tool_search`) is the API-native mechanism, and it does
not fit this call. It works by marking tools `defer_loading: true` alongside a
non-deferred search tool; the grader's only "tool" is the `GraderResult` schema
that `messages.parse` renders. Making the rubric fetchable means adding a real
tool and an agentic round-trip — two API calls where there is one — on the path
the learner waits behind, since the grade rides the turn's fan-out.

What that buys: 211 tokens of **cache read**, at 0.1x, per turn. About one
hundredth of a cent. On the review call, which is uncached, it is a tenth of a
cent per session. Against a second round trip on a latency-critical path.

It does not pay, and it would not have paid at 612 words either. The instinct —
stop shipping every instruction on every call — is answered here by *not sending
the review's instructions on turn calls at all*, which is free, needs no new
mechanism, and is what the split already does.

## What A8 did not fix

`greetings-name-question-mid-session` is 11/20, not 20/20. A6.5's hypothesis —
that the review collapses `self_name` and `partner_name` into one already-
credited piece of business when the learner's own name comes first — is still
the best account of what is left, and it is an authoring question
(`description`, `expressible_with`) before it is a prompt one. Prompt weight was
worth about half of that case and nothing else in the corpus.

## A gate this step had to fix on the way

The first re-record went red on `computer-work-ni-ne`: 3/5 exact against a 4/5
floor, read as a regression the cut had caused. It was not. Measured at twenty
draws it is **16/20 on the new prompt and 17/20 on the old** — one draw apart,
and a gate that false-fails about a quarter of the time at the rate that case
actually has. A5's "0 missed of 55" and A3's "3/3" were the same coin landing
the other way.

So the dense gate moved to twenty draws with a floor of fourteen
(`tests/test_coherence_eval.py`), and the weekly job tops those three cases up
to depth after its refresh. Same lesson as A6.5, found again, in the one place
the stream had not applied it yet.

---

# A6.5 — earlier-turn recall, measured

*Kept as history. The numbers below are the baseline on the prompt A8 replaced;
`observations.json` and the A8 section above hold the current run. Nothing here
is regenerated — a baseline that moves with the code is not a baseline.*

**Baseline: 190 of 220 owed slots recovered, over 200 draws. 0 spurious.**
`claude-opus-5`, ten finished sessions across four topics, twenty draws each,
replayed through `feedback.review_session` off committed cassettes.

Generated by `python -m evals.review.replay --repeat 20`; the table below is
`evals/review/report.py` over `observations.json`.

## What this corrects

A6 reported that the session review "is worth roughly one slot in five on
earlier turns today". **That is not what a real corpus says.** Nine of the
eleven owed slots come back at 19/20 or 20/20, across four topics, and the
overall rate is 86%. A6's number came from four hand-built waves of five draws
on one transcript, and the one transcript it used happens to be the corpus's
only real failure.

The pass also **never invented credit**: 0 spurious recoveries in 200 draws,
including twenty draws of a session where the learner asks nothing and two slots
are there to be wrongly given. The add-only rule is safe in practice, not only
on paper.

## The numbers

| session | owed | recovered | rate | every slot | spurious |
|---|---|---|---|---|---|
| family-my-family-oldest-turn | my_family | `my_family` 20/20 | 100% | 20/20 | — |
| family-size-question-mid-session | their_family_size | `their_family_size` 20/20 | 100% | 20/20 | — |
| food-drinks-question-mid-session | drinks | `drinks` 20/20 | 100% | 20/20 | — |
| food-two-questions-lost | recommendation, drinks | `drinks` 20/20, `recommendation` 20/20 | 100% | 20/20 | — |
| greetings-name-question-final-turn | partner_name | `partner_name` 20/20 | 100% | 20/20 | — |
| greetings-name-question-mid-session | partner_name | — | 0% | 0/20 | — |
| greetings-name-question-oldest-turn | partner_name | `partner_name` 11/20 | 55% | 11/20 | — |
| greetings-name-statement-oldest-turn | self_name | `self_name` 20/20 | 100% | 20/20 | — |
| greetings-nothing-to-recover | — | — | — | 20/20 | — |
| self-intro-ungraded-debt | partner_origin, partner_job | `partner_job` 20/20, `partner_origin` 19/20 | 98% | 19/20 | — |
| **total** | **220** | **190** | **86%** | **170/200** | **0** |

## Per-slot rates

The number A6 could not produce. Two slots both labelled *recoverable* can behave nothing alike, and only a per-slot rate with its sample count shows which one the loss is in.

| session | slot | recovered |
|---|---|---|
| family-my-family-oldest-turn | my_family | 20/20 |
| family-size-question-mid-session | their_family_size | 20/20 |
| food-drinks-question-mid-session | drinks | 20/20 |
| food-two-questions-lost | recommendation | 20/20 |
| food-two-questions-lost | drinks | 20/20 |
| greetings-name-question-final-turn | partner_name | 20/20 |
| greetings-name-question-mid-session | partner_name | 0/20 ⚠️ |
| greetings-name-question-oldest-turn | partner_name | 11/20 ⚠️ |
| greetings-name-statement-oldest-turn | self_name | 20/20 |
| self-intro-ungraded-debt | partner_origin | 19/20 ⚠️ |
| self-intro-ungraded-debt | partner_job | 20/20 |

## The one failure, and what it is not

All of the loss is `greetings/partner_name` — 你叫什么名字？ — and the corpus
moves that exact question through three positions to find out why.

| where the question sits | recovered |
|---|---|
| the final turn | **20/20** |
| the oldest turn | 11/20 |
| mid-session | **0/20** |

Two explanations are ruled out by the corpus itself:

- **Not the wording.** The same seven characters recover 20/20 when they are the
  final turn. If 你叫什么名字？ were hard to read as a `request` slot, that case
  would fail too.
- **Not the position.** `family-size-question-mid-session` and
  `food-drinks-question-mid-session` are the same shape — a three-turn session
  whose *middle* turn carries the only owed slot — and both recover 20/20. A
  serial-position story predicts they fail with greetings, and they do not.

What is left is the interaction. In the 0/20 case the learner says 我叫小明 on
turn 1, the partner answers 你好，小明！, and *then* the learner asks the name
back — with `self_name` already listed as established in the filled-slot note.
Reading the recorded grades makes it concrete: every draw reports `self_name`
and `wellbeing` and simply never mentions `partner_name`. The review sweeps the
turn before it and the turn after it. It reads the name exchange as one piece of
business that is already credited, and the second name slot inside it
disappears.

That is a hypothesis, not a finding, and it is the one A8 has to test. It is
also a hypothesis about **a pair of sibling slots**, not about prompt length —
so the honest thing to say to A8 is that its premise is now open: the prefix may
weigh 612 words, but a corpus at 86% with one localised failure is not the
picture "the instruction that matters is diluted" predicts.

## What is asserted, and what is only reported

`tests/test_review_eval.py` gates two things off these cassettes and spends
nothing to do it:

- **Spurious credit, at every draw.** The pass is add-only, so a slot it invents
  reaches a card the learner reads as truth and cannot be taken back. That is
  not a rate.
- **Recall, as a floor at 80%.** A regression guard set under the 86%
  measurement, not at it — the corpus is re-recorded weekly and a floor set
  exactly at the number fails on noise. Raising it is A8's job.

The per-position table above is reported, not asserted. It is a diagnosis and it
will stop being true the moment someone fixes it, which is the point.
