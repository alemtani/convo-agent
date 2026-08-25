# Work streams

Three streams run in parallel. Each one has a spec, a set of eval cases it owns,
and a kickoff prompt at the foot of its spec.

| Stream | Question it answers | Spec |
| --- | --- | --- |
| A — Grading | Does the score mean what it says? | [grading.md](grading.md) |
| B — Latency | Does the turn feel like a conversation? | [latency.md](latency.md) |
| C — Reach | Can somebody else use it, in another language? | [reach.md](reach.md) |

## Why these three

The goal is a mobile app with auth, a second language (Spanish), the same five
topics, a grade that is trustworthy, and latency a person tolerates. Every one of
those lands in exactly one of the three streams above.

## Order

A gates C. A second language built on a grader nobody trusts doubles the
debugging surface, and the fix then has to land twice. B is independent of both
and can run start to finish beside them.

## Rules that hold across all three

**Evals come first.** A fix lands as a failing eval case, then the fix. This is
the same red-green rule the test suite already has; it now covers model behaviour
as well as code.

**KB content changes go through the skill.** Never edit `kb/zh/**` by hand. The
`kb-topic` skill is the only writer. A hand-edit is a lost test of the skill, and
the skill is the thing that has to work when a second language arrives.

**One stream, one branch, one PR.** Streams do not share a branch.

## What is not in a stream

Backup work, in rough priority order. Pull from here only when a stream is
blocked.

- Curriculum: issues #44–#53 (syllabus record, covered-vocab preference,
  selection weighting, `validate.py` as a gate, band/stage split, admin front
  door)
- Accessibility: #67 (A2), #68 (A3, gated)
- Resilience and multi-user seams: #63
- TTS polish and pause capture
- `annotate_pinyin.py` sandhi bug: #56
