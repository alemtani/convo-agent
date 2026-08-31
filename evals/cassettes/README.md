# Cassettes

Recorded Anthropic responses, committed so the eval suite costs nothing to run.
One file per key; the key is
`sha256(model + system + tools + messages + params)` over the request the
workers assemble. The code is in [`../cassette/`](../cassette/).

```json
{
  "key": "e3b0c442…",
  "model": "claude-opus-5",
  "summary": "claude-opus-5 → GraderResult: 请问，什么菜最好吃？",
  "recorded_at": "2026-08-24T00:00:00+00:00",
  "samples": [
    {"stop_reason": "end_turn", "parsed_output": {…}, "usage": {…}}
  ]
}
```

**The request is not stored.** It is rebuilt from the code on every run, so a
copy of the KB block in every file would only make the directory unreadable in
review. `summary` is what identifies a cassette to a human reading a diff.

**`samples` is a list because one draw is not a measurement.** Record N per key
(`--samples N`) and assert against the distribution. A replay walks the samples
in order and wraps around, so `--repeat` sees the spread and CI still gets the
same answers every time.

## Recording

A key with a full cassette costs nothing even under `--record`. Change a prompt
and its key changes, so only the affected cases are re-recorded — in the PR that
changed the prompt, where someone is already reading the diff.

Five runners share this directory, and none reaches another's keys: grader-only
keys from the coherence runner, partner and withholding-judge keys from the two
turn corpora, whole-session keys from the review runner, and the behavioral
cases.

```bash
# tops up missing samples; --refresh replaces a cassette outright
python -m evals.coherence.replay --record --samples 5 --repeat 5
python -m evals.turn.replay      --record --samples 5 --repeat 5
python -m evals.turn.replay      --record --samples 5 --repeat 5 \
    --cases-dir evals/turn/cases --out evals/turn/observations.probes.json
python -m evals.review.replay    --record --samples 20 --repeat 20
python -m evals.behavior.record  --record --samples 5
```

**Depth needs both flags.** `--samples` is the ceiling — how many draws a key
may hold — and `--repeat` is how many times the run visits each case. A key
holds only as many samples as it was visited, so recording at `--samples 5
--repeat 3` leaves three, and a gate that judges a rate over five then fails its
own depth check. The two move together. `evals.behavior.record` is the
exception: it loops `--samples` times directly and takes no `--repeat`.

Two corpora are deeper than the rest, because what they measure is a rate:

- the three dense grader cases (`tests/test_coherence_eval.py`, `DENSE_SAMPLES`)
  at twenty draws — the five-draw gate false-failed about a quarter of the time
  on a case that had not regressed. Top them up *after* any `--refresh` run,
  which would otherwise truncate them back:

  ```bash
  python -m evals.coherence.replay --record --samples 20 --repeat 20 \
      --case milk-and-biscuits --case computer-work-ni-ne --case clip-and-tea
  ```

- the whole review corpus at twenty, for the same reason: five draws is the
  sample count A6 could not read a fix out of.

## Sweeping

A key nothing reaches any more is a recording no prompt in the tree can produce.
Pruning is a manual step, run when a re-record leaves orphans behind — and it is
deliberately not a flag on a runner. One store, five runners, none of which
reaches the others' keys: a runner that deleted what *it* did not touch would
take the rest of the corpus with it. Each writes down what it reached
(`--used-out`) and the sweep takes the union.

So a sweep is only safe from a pass where **every** runner ran with
`--used-out`, and every recording step came first:

```bash
python -m evals.cassette.sweep --used /tmp/coherence.json /tmp/turn.json \
    /tmp/turn-probes.json /tmp/review.json /tmp/behavior.json
```

A missing manifest is an error rather than an empty set: a runner that crashed
must not read as "reached nothing".

## Staleness

Cassettes drift from the live model. That drift is not caught on a schedule and
not caught on a PR — a build that is green 90% of the time is worse than one
that is honestly stale. It is caught when someone re-records, which is what a
prompt change already requires.

The live half is different and *is* scheduled:
[`.github/workflows/live.yml`](../../.github/workflows/live.yml) runs
`pytest -m live` weekly. Those are the calls a recording cannot stand in for —
a real `cache_read_input_tokens`, and Azure.
