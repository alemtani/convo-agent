# Earlier-turn recall — what the end-of-session review gives back

A6 shipped `feedback.review_session`: one grader call over the whole
conversation before the verdict card, add-only, judging every turn again with
the rest of the session in view. This directory measures how much of the credit
the live grades missed actually comes back.

**A6 could not answer that.** Its measurement was four hand-built waves of five
draws on one topic, and it reported a real-looking split — an unambiguous
你叫什么名字？ two turns back returned in about one draw in five, while the same
turn's 我叫小明 returned 5/5. At five draws that split is one draw wide. A1 made
exactly this mistake and A3 paid for it: three consecutive passes off a
recording is one draw asserted three times, and at a true rate of 0.5 it goes
green about one time in eight.

So A6.5 is a measurement before it is a fix.

## What a case is

Not a turn. A **finished session**, in the shape `/api/verdict` receives it: the
opening line, every turn in `user`/`partner` pairs, and the `SessionState` the
*live* grades produced — under-credited on purpose, because that is the input
the review actually gets.

Labels live in `cases/gold.json`, apart from the transcripts, and account for
every slot in the scenario:

| field | meaning |
|---|---|
| `recoverable` | the session establishes it; the submitted state does not carry it. What the review owes. |
| `already_credited` | the live grades got it. Restated from the state so a labeller has to look at it. |
| `never_established` | no turn establishes it. Credit reported here is **spurious**. |

A slot in none of the three is one nobody judged, and
`tests/test_review_eval.py` fails the corpus for it — a recall rate computed
over an incomplete labelling reads as evidence while being arithmetic over a
hole.

## What it measures

`replay.py` drives `feedback.review_session` — the shipped call, not a request
the runner assembles — and records the **diff**: which slot ids entered
`filled_at` that the client did not submit. That is the credit a learner would
have seen on their card.

Two directions, and they are different bugs:

- **missed** — an owed slot the review did not report. Credit earned and not
  given. This is the number A8 exists to move.
- **spurious** — a slot no turn established, reported anyway. The pass is
  add-only, so this cannot be taken back before the learner reads the card. It
  must stay at zero.

`recall.py` turns observations into rates, always with the sample count.
`report.py` renders them. The numbers are in [`RESULTS.md`](RESULTS.md).

## Running it

```bash
python -m evals.review.replay --repeat 20            # free, off cassettes
python -m evals.review.replay --record --samples 20 --repeat 20   # live; costs money
python -m evals.review.replay --case greetings-name-question-two-back
```

Twenty draws, not five, is the whole point of the directory. Recording is the
only thing that spends, it is committed, and freshness is the scheduled job's
problem (`.github/workflows/rerecord.yml`).
