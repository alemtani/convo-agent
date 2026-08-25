# Stream B — Make it fast

**The question:** does a turn feel like talking to somebody?

A measured turn on 2026-08-24, ~17:20 PT: **10.87s**. Reported stages: STT 1.01s,
PA 0.97s, Claude 3.30s. PA and the conversation worker run concurrently
(`orchestrator.stream_audio_turn`), so those three account for about 4.3s of it.

Six and a half seconds are unaccounted for.

## Where they went

Two suspects, and the instrumentation cannot currently separate them.

### The grader is the third branch and it is not in that breakdown

`stream_audio_turn` fans out three tasks: PA, the conversation worker, and the
grader. `TurnTimings` has a `grader_ms` field. It was not in the numbers above.

The grader runs on `claude-opus-5` with adaptive thinking on and
`GRADER_EFFORT=medium`, `max_tokens=4096` (`config.py:195-217`). Thinking on a
4096-token budget is seconds, not milliseconds. `GRADER_TIMEOUT_S=15` says the
design already expects it to be slow.

The learner waits for it. That is deliberate and it is correct: the controls stay
shut until the grade lands, because on a finishing turn the grade decides whether
the session is over. Unblocking the UI on `reply` would show a live microphone on
a session that has already ended.

So the grader is not to be moved off the wait. It is to be made fast.

### The client half is not measured at all

`total_ms` covers the orchestrator call. It starts when the server has the audio.
It does not cover: the phone finishing the recording, encoding it, uploading it
over a mobile network through a Cloudflare tunnel, or the browser rendering the
reply.

On a phone on cellular, an audio upload is not free. Nobody knows how much of the
6.5s is this, because nothing measures it.

## What gets built

### B0 — Measure the whole thing (first)

Nothing else in this stream is worth doing before the number is honest.

Client-side marks, reported alongside the server's `TurnTimings`:

- mic stop → request sent (encode)
- request sent → first byte (upload + server queue)
- first byte → `transcript` event
- `transcript` → `reply` event
- `reply` → `done` event
- `done` → rendered

Surface `grader_ms` in the same view as `stt_ms`, `pa_ms` and `claude_ms`. Its
absence from the reported breakdown is why this stream opened with a mystery
instead of a target.

The existing HUD is the place for this. It ships behind the same flag.

### B1 — Optimise the grader

The branch the turn's wall clock runs on. In order of expected return:

1. **Shrink its input.** Stream A's A3 cuts it from the whole transcript to the
   partner's last line plus the learner's turn. Fewer input tokens, less to think
   about. This is the biggest lever and it lands in the other stream — coordinate.
2. **Measure effort against accuracy.** `GRADER_EFFORT` is `medium`. Try `low`.
   Try Sonnet 5. The cassette suite from A0 makes this a measurement rather than
   a guess: same cases, same labels, compare accuracy and latency across model and
   effort settings.
3. **Cap `max_tokens` to what thinking actually uses.** 4096 is headroom, not a
   measurement. Record what real grades consume.

The rule: a change here needs an accuracy number beside the latency number. A
fast grader that is wrong is Stream A's problem all over again.

### B2 — Stream the verdict

The end-of-session card is one blocking call on `claude-sonnet-5` with adaptive
thinking and `max_tokens=4096` (`workers/feedback.py:219-230`). The learner
watches a spinner for the whole of it.

Stream it. The card has parts — the outcome, the missed slots, the exchange that
would have worked — and they can land as they resolve, the way the turn already
lands its stages. `stream_audio_turn` is the pattern to copy.

### B3 — Hold a budget

Once B0 reports honestly, set targets and fail the eval report when they are
missed:

| Mark | Target |
| --- | --- |
| Learner's own words on screen | < 1.5s |
| Partner's reply on screen | < 3.5s |
| Controls live again | < 5s |

These are placeholders until B0 produces a real distribution. Replace them with
p50 and p95 from measured turns, not from one session.

## Not in this stream

Unblocking the UI on `reply` instead of `done`. Considered and rejected: the
grade decides whether the session has ended, and a live mic on a finished session
is worse than a slow one.

## Done when

- Every segment of a turn, client and server, has a number.
- `grader_ms` is visible wherever the other stages are.
- The verdict card renders progressively.
- p50 reply-on-screen is under the budget, on a phone, over a tunnel.

## Kickoff prompt

```
Read docs/streams/latency.md. Start Stream B at B0: honest end-to-end
instrumentation.

A 10.87s turn reported STT 1.01s, PA 0.97s, Claude 3.30s — about 4.3s of it,
since PA and the worker run concurrently. Find the other 6.5s. Add client-side
marks (mic stop → request sent → first byte → each stage event → rendered) and
report them beside the server's TurnTimings. Surface grader_ms in the HUD with
the other stages; it is the third fan-out branch and it was missing from the
breakdown.

Measure before changing anything. Write the failing tests first. Branch from
main, conventional commits, open a PR that shows the before-and-after breakdown
of a real turn on a phone through a tunnel.
```
