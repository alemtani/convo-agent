"""B1: what the grader costs, and what it costs to make it cheaper.

`docs/streams/latency.md` B1. The grader is a fan-out branch the learner waits
on — the controls stay shut until the grade lands, because on a finishing turn
the grade decides whether the session is over. So its latency is the learner's
latency, and every dial on it (`GRADER_MODEL`, `GRADER_EFFORT`,
`GRADER_MAX_TOKENS`) is a latency dial as well as a quality one.

This package turns those dials one at a time over the labelled corpus
`evals/coherence/` already holds, and reports accuracy beside latency. The rule
the stream doc sets, and the reason both numbers live in one table: **a fast
grader that is wrong is Stream A's problem all over again.**
"""
