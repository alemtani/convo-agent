# Stream B — Make it fast

**The question:** does a turn feel like talking to somebody?

A measured turn on 2026-08-24, ~17:20 PT: **10.87s**. Reported stages: STT 1.01s,
PA 0.97s, Claude 3.30s.

## How to read that number

**STT is the only serial stage.** PA needs the transcript as its reference and
the conversation and grader branches need the text, so all three start once STT
returns and run concurrently (`orchestrator.stream_audio_turn`). The server floor
for a turn is therefore `stt + max(branches)`, not the sum of the parts.

So the region between STT finishing and the turn ending is about **9.9s wide**,
and the longest branch anybody can see inside it is 3.3s.

**10.87 is the round trip, not the server total.** `frontend/index.html:1674`
prints `roundTripMs` as the headline when it has one, which includes uploading a
~160 KB WAV from the phone. `total_ms` is printed beside it as "server N". Those
two numbers split the problem in half and the split is the whole question: time
inside the fan-out is a model problem, time outside it is a client problem.

## Why the granularity is missing

`frontend/index.html:1683` renders a hardcoded stage list:
`[stt_ms, pa_ms, claude_ms]`.

**`grader_ms` is not in it.** The field exists on `TurnTimings`, the orchestrator
populates it, and the HUD drops it on the floor. The grader is the third fan-out
branch — `claude-opus-5`, adaptive thinking on, `GRADER_EFFORT=medium`,
`max_tokens=4096`, `GRADER_TIMEOUT_S=15` (`config.py:195-225`) — and it has been
invisible in every breakdown read so far.

That is why a turn could look like it had six unexplained seconds in it. The
instrument was not reporting the branch most likely to be holding them.

## The learner waits for the grade, and should

The controls stay shut until the grade lands, because on a finishing turn the
grade decides whether the session is over. Unblocking the UI on `reply` would
show a live microphone on a session that has already ended.

So the grader does not come off the wait. It gets made fast.

## What gets built

### B0 — Surface the granularity (done)

Nothing else here is worth doing before the numbers are complete. This is a
read-the-instrument task before it is a fix-anything task.

1. **Add `grader_ms` to the HUD stage list.** One line. It may answer most of the
   question on its own.
2. **Make the round-trip / server split unmissable.** Both numbers are already
   computed and printed; they should be legible enough that nobody reads the
   headline as server time again.
3. **Then add client marks** for whatever the split leaves unexplained: mic stop
   → request sent (encode), request sent → first byte (upload), each stage event,
   `done` → rendered.

`upload` is request sent → first byte. Today's server does not flush headers
until STT returns, so that interval is upload plus STT, not upload alone. When
the stream starts earlier, `upload` and `transcript` split on their own.

Ships behind the same flag the HUD already uses.

### B1 — Optimise the grader

The branch the turn's wall clock most likely runs on. In order of expected
return:

1. **Shrink its input.** Stream A's A5 cuts it from the whole transcript to the
   partner's last line plus the learner's turn. Fewer input tokens, less to think
   about. This is the biggest lever and it lands in the other stream —
   coordinate, do not duplicate.
2. **Measure effort and model against accuracy.** `GRADER_EFFORT` is `medium`.
   Try `low`. Try Sonnet 5. Stream A's cassette suite makes this a measurement
   rather than a guess: same cases, same labels, compare accuracy and latency
   across settings.
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

**This is the one change that threatens the eval gate.** Every Anthropic call
today is a non-streaming `messages.parse`, which is what keeps Stream A's
cassette layer small. Streaming introduces the case a cassette layer handles
worst. B2 must prove the layer still works before it lands, or it turns the gate
off without anybody noticing.

### B3 — Hold a budget

Once B0 reports honestly, set targets and fail the eval report when they are
missed:

| Mark | Target |
| --- | --- |
| Learner's own words on screen | < 1.5s |
| Partner's reply on screen | < 3.5s |
| Controls live again | < 5s |

Placeholders until B0 produces a real distribution. Replace them with p50 and p95
from measured turns, not from one session.

## Not in this stream

Unblocking the UI on `reply` instead of `done`. Considered and rejected: the
grade decides whether the session has ended, and a live mic on a finished session
is worse than a slow one.

## Done when

- Every branch of a turn, client and server, has a number on screen.
- `grader_ms` is visible wherever the other stages are.
- The verdict card renders progressively, with the cassette gate still green.
- p50 reply-on-screen is under the budget, on a phone, over a tunnel.

## Kickoff prompt

```
Read docs/streams/latency.md. Start Stream B at B0: surface the granularity.

A 10.87s turn reported STT 1.01s, PA 0.97s, Claude 3.30s. STT is the only serial
stage — PA, the conversation worker and the grader all run concurrently after it
— so the region after STT is ~9.9s wide and the longest branch anybody can see
in it is 3.3s.

The reason is instrumentation, not mystery. frontend/index.html:1683 renders a
hardcoded [stt_ms, pa_ms, claude_ms] and drops grader_ms, which is the third
fan-out branch and runs Opus 5 with thinking on. Add it. Then make the
round-trip vs "server" split (index.html:1674) legible, since that separates
WAV upload from server time.

Measure before changing anything. Write the failing tests first. Branch from
main, conventional commits, open a PR showing a real turn's full breakdown from
a phone through a tunnel.
```
