# Turn eval — measure the partner, not only the judge

`evals/coherence/` calls `grader.grade` directly. That is the right
instrument for A3, because it holds the partner still. It also means no
eval in the repo ran the partner, which is how A2 cut a third of the
system prompt with nothing to replay.

This package drives `orchestrator.run_text_turn`. One cassette-backed
client threads into both workers, so a run covers the reply, the grade
computed against that reply, and the state it advances to.

The headline check is over-volunteering: a `request` slot handed over
before the learner asked is a point they can no longer earn. See
[`withholding.py`](withholding.py) and [`RESULTS.md`](RESULTS.md).

```bash
python -m evals.turn.replay --repeat 3            # session cases; free
python -m evals.turn.replay --record --samples 3  # live; costs money
python -m evals.turn.replay --repeat 3 --cases-dir evals/turn/cases
python -m evals.turn.replay --record --samples 3 --cases-dir evals/turn/cases
```

Probes live in [`cases/`](cases/) and stay apart from
`tests/fixtures/sessions/`. A fabricated turn filed among recordings is
a lie a later reader cannot detect.
