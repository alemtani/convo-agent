# B1 — a failed experiment

**The hypothesis:** the grader is the branch the turn's wall clock runs on, so
turning its dials — `GRADER_MODEL`, `GRADER_EFFORT`, `GRADER_MAX_TOKENS` —
makes the turn fast.

**The answer: no.** None of the dials moves the number enough to matter, and one
of them cannot move it at all. **No config change shipped.** Do not re-run this;
read the table.

84 live grades: 7 labelled cases × 3 repeats × 4 settings, through
`workers.grader.grade` itself. Recorded to cassettes, so the accuracy half
replays free.

| setting | runs | exact | spurious | missed | p50 | p95 | max out |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `claude-opus-5/medium` (shipped) | 21 | 16/21 (76%) | 4 | 1 | 3.50s | 6.85s | 679 |
| `claude-opus-5/low` | 21 | 15/21 (71%) | 4 | 2 | 2.78s | 3.70s | 286 |
| `claude-sonnet-5/medium` | 19 | 16/19 (84%) | 3 | 0 | 3.23s | 4.90s | 421 |
| `claude-sonnet-5/low` | 21 | 17/21 (81%) | 3 | 1 | 2.75s | 4.52s | 259 |

## Why each dial failed

**Effort buys 0.75s of p50, and the accuracy column cannot tell you whether that
was worth it.** n=21 per arm and the arms differ by one or two runs. That is not
a measurement of a difference, it is the corpus being small. Nothing here
justifies moving off `opus-5/medium`.

**Sonnet is not the cheap win.** `sonnet-5/medium` blew `GRADER_TIMEOUT_S` twice
on `elliptical-ni-ne` — its 84% is scored over 19 runs, not 21. A timeout is not
a slower grade, it is **an uncredited turn**: the learner is told they
established nothing. That makes it the worst arm in the table, not the best.

**`GRADER_MAX_TOKENS` is not a latency dial at all.** It is a cap, not a
reservation — lowering 4096 to the measured 1536 would not save a token or a
millisecond, and would only make truncation likelier on a hard grade. The stream
doc called 4096 "headroom, not a measurement". The measurement's answer is that
the headroom is free. **Leave it.**

## What the run did establish

Two numbers worth more than the table, both of which point away from this step:

**There is a ~2.2s floor.** The fastest grade in 84 tries was 2.17s, for a
24-token answer. No setting goes under it.

**Latency tracks output, not input.** Grades under 100 output tokens ran a
median 2.86s (n=57); grades over 200 ran 5.32s (n=8). The grader is slow when it
*deliberates*, not when it *reads*.

**Which is a warning about A5.** The grader's per-turn input is already **16–63
tokens** after the cache breakpoint — the other ~2,080 are the cached rubric.
There is very little there to cut. Stream A names shrinking the input as B1's
"biggest lever"; on this corpus it is not obviously a lever at all. The caveat
that keeps it honest: these cases are 0–4 turns, so this understates what A5
cuts from a ten-turn session — but ten turns of beginner Mandarin is a couple of
hundred tokens against a 2.2s floor.

## The instrument stays

`sweep.py` / `score.py` / `report.py` outlive the negative result: they are the
only thing in the repo that reports grader accuracy and grader latency in one
table, which is what any future change to this call has to clear. Re-run free:

```bash
python -m evals.grader.sweep --repeat 3          # off cassettes; latency reads "not measured"
python -m evals.grader.sweep --record --samples 3 # live; the only run that times anything
```

## One thing for Stream A

The gaming turn (`nonsequitur-slot-fill`) was credited `recommendation` at
**every one of the four settings, in all 12 runs.** V0 recorded that failure
against the converser's tracker; the goal-blind grader has not fixed it. It is
not a model or effort problem, so no dial in this sweep can reach it.
