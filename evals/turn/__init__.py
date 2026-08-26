"""The turn runner: an eval that runs the partner, not only the judge.

Everything under `evals/coherence/` calls `grader.grade` directly, so it
measures the judge and never the thing being judged. This package drives
`orchestrator.run_text_turn` instead — the seam that threads one client into
both workers — so a single cassette-backed run covers the reply, the grade and
the state advance the way a real turn does.
"""
