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

```bash
python -m evals.coherence.replay --record --samples 3   # tops up missing samples
```

A key with a full cassette costs nothing even under `--record`. Change a prompt
and its key changes, so only the affected cases are re-recorded.

## Staleness

Cassettes drift from the live model, and that is handled by
[`.github/workflows/rerecord.yml`](../../.github/workflows/rerecord.yml) — a
scheduled job that re-records with `--refresh` and fails if the diff is not
empty. Never by a live call on a PR: a build that is green 90% of the time is
worse than one that is honestly stale.
