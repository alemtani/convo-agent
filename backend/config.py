import json
import os
from dotenv import load_dotenv

# Load the project-root .env explicitly (one level up from backend/). Pinning the
# path — rather than letting find_dotenv search upward from this file — stops a
# stray nearer file like backend/.env from shadowing the real one. override=True
# so an empty/stale shell export can't win either. Absent in CI/prod, this is a
# no-op and real environment variables apply.
_ROOT_ENV = os.path.join(os.path.dirname(__file__), os.pardir, ".env")
load_dotenv(_ROOT_ENV, override=True)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
AZURE_SPEECH_KEY = os.getenv("AZURE_SPEECH_KEY", "")
AZURE_SPEECH_REGION = os.getenv("AZURE_SPEECH_REGION", "eastus")

# Shared passcode for the deployed app. Empty (the default) disables the gate,
# which is what you want locally — there is no public URL to protect and a login
# screen is pure friction. Setting it is therefore a *deploy* step, and because
# forgetting it is the exact failure the gate exists to prevent, the state is
# advertised rather than silent: `main` warns at startup and `/health` reports
# `auth: enabled|disabled`. See `backend/auth.py`.
APP_PASSCODE = os.getenv("APP_PASSCODE", "")

# How long a session cookie stays valid. Long, because the "user base" is one
# person's phone and re-entering a passcode is friction with no security payoff
# at this scale — the meaningful revocation lever is rotating `APP_PASSCODE`,
# which invalidates outstanding sessions regardless of this value.
SESSION_TTL_DAYS = float(os.getenv("SESSION_TTL_DAYS", "30"))

# Conversation worker (per-turn hot path). Sonnet 5 is the deliberate choice for
# the loop — cheap and deterministic enough for every-turn calls (DESIGN.md).
# Hold the model fixed per session: switching it mid-session busts the cache.
#
# Env-overridable so the replay harness can A/B a candidate (Haiku 4.5) against
# the incumbent without a code edit — the comparison is the whole point, and one
# measured on a patched working tree isn't reproducible.
CONVERSATION_MODEL = os.getenv("CONVERSATION_MODEL", "claude-sonnet-5")

# Effort for the per-turn loop. Unset means `high`, the API default, which buys
# deliberation a one-sentence in-band reply has no use for. Env-overridable for
# the same reason as the model: it is a dial whose right setting is a measured
# question, not a settled one.
CONVERSATION_EFFORT = os.getenv("CONVERSATION_EFFORT", "low")

# How forgiving the partner is of learner errors (0=strict … 1=very patient).
# Baked as a literal into the frozen system prompt — never per-turn — so the
# cached prefix stays byte-identical. Default 0.8 = patient (DESIGN.md).
FORGIVENESS_LEVEL_DEFAULT = 0.8

# Per-syllable PA accuracy below this is surfaced as a tone error (Phase 3b).
# Aligned with the frontend's existing `tone-bad` cutoff in index.html.
TONE_ERROR_THRESHOLD = 60.0

# Nothing on the turn may wait forever. Staging raises the stakes: the spoken
# path holds an *open HTTP response* for the whole turn, so a stalled upstream
# call no longer just delays a reply — it parks a connection, a worker thread,
# and the request's audio buffer indefinitely, with the client showing a pending
# bubble that will never resolve. A deadline turns each of those into a failure
# the turn already knows how to report.
#
# Sized for the learner, not for the worst case the network can produce. Against
# Stage 0 (STT 1.32s, PA 1.20s, Claude 3.56s) these leave ~4x, ~4x and ~3x
# headroom — enough that a normal turn never trips one, tight enough that a
# wedged call fails while the learner is still willing to retry. Staring at a
# pending bubble for 45s is a worse outcome than a clear failure at 10s.
#
# The trade this makes: these are no longer only a safety net. A deadline this
# close to the measured p50 can cut a call that *would* have succeeded, so each
# one is a real tunable — hence env-overridable. We have no p95 for any of the
# three yet; the replay harness (next PR) produces it, and these numbers should
# be revisited against it rather than defended on intuition.
#
# They are not equally risky, which is why they differ:
#   PA      degrades to `pronunciation: null` and the turn completes — cheapest
#           failure of the three, so the tightest budget costs the least.
#   STT     kills the whole turn with a 502. Scales with utterance length, which
#           push-to-talk keeps short; revisit if recordings get longer.
#   Claude  is the reply. Cutting it loses a turn that might have landed a
#           second later, and the tokens are spent either way.
#
# Claude's is now 15s, revisited against the replay p95 the way #22 asked for
# rather than defended on intuition. Two things moved it up from 10s. The
# measured p95 is 4.8–5.2s, so 10s was ~2x p95 — closer to the body of the
# distribution than a safety net should sit. And with the SDK retry removed
# below, 10s became a *real* cut for the first time: turns that used to be
# rescued by an invisible second attempt now simply die. Two in ~50 did.
#
# 15s is ~3x p95 and still well inside the "clear failure beats a bubble that
# never resolves" bound the original number was reaching for.
# Partner-reply synthesis (M4). Off the turn's critical path — `/api/tts` is its
# own endpoint, keyed on text — so a slow one delays a bubble's audio, not the
# reply itself. Still bounded: it holds a request open like anything else.
#
# Xiaoxiao is the zh-CN voice the PA live fixture already synthesizes with, so
# the learner hears one voice across the app rather than two.
TTS_VOICE = os.getenv("TTS_VOICE", "zh-CN-XiaoxiaoNeural")

# Signed percentage handed to SSML `<prosody rate>`. Negative is slower. The
# default neural pace is native-speed and a band-1–2 learner cannot segment it;
# -10% is enough to hear word boundaries without sounding drugged. A dial, not a
# constant — the right value is a measured question once real sessions run.
TTS_RATE_PCT = int(os.getenv("TTS_RATE_PCT", "-10"))

# More generous than STT's: the learner is not staring at a pending bubble
# waiting for this. The reply text is already on screen (or one 👁 away), and a
# missed synthesis degrades to a revealed line rather than a lost turn.
TTS_TIMEOUT_S = float(os.getenv("TTS_TIMEOUT_S", "10"))

# Lines kept in the server-side synthesis cache. At ~25 KB per one-sentence
# reply, 64 entries is a couple of megabytes — small against the cost it avoids,
# which is re-billing Azure for a line the learner just asked to hear again.
TTS_CACHE_MAX_ENTRIES = int(os.getenv("TTS_CACHE_MAX_ENTRIES", "64"))

# Our own loggers. uvicorn leaves the root logger at WARNING, so without this
# every `logger.info` under `backend/` is dropped — including the per-turn
# timings line and the session state machine.
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# The STT deadline is not one number, because recognition time tracks the
# length of the recording. Push-to-talk holds the button through the learner's
# own pauses, so a hesitant turn is a *longer* WAV with more segments in it —
# and a flat budget timed out exactly the learner who needed the time, while
# comfortably covering the fluent turns it did not need to. So: a floor for the
# short ones, plus a per-second allowance, capped so a wedged session still
# cannot hold a request open indefinitely.
#
# The client cuts a hold at STT_TIMEOUT_MAX_S and sends what was said, so a
# long press cannot grow a blob this budget cannot finish. The cap then only
# fires for a wedged Azure call, which the turn already reports as a failure
# they can retry.
STT_TIMEOUT_S = float(os.getenv("STT_TIMEOUT_S", "5"))
STT_TIMEOUT_PER_AUDIO_S = float(os.getenv("STT_TIMEOUT_PER_AUDIO_S", "1.0"))
STT_TIMEOUT_MAX_S = float(os.getenv("STT_TIMEOUT_MAX_S", "30"))
PA_TIMEOUT_S = float(os.getenv("PA_TIMEOUT_S", "5"))
CLAUDE_TIMEOUT_S = float(os.getenv("CLAUDE_TIMEOUT_S", "15"))

# ...and the deadline has to be the *whole* budget, which means no retries under
# it. The SDK retries twice by default and a timeout is a retryable error, so
# `CLAUDE_TIMEOUT_S` was really "10s, three times" — a wedged call could hold the
# turn ~30s plus backoff, past every number the deadline was sized against.
# Replay caught one at 13.9s: a 10s timeout, then a 3.9s retry that succeeded.
#
# Zero, not one, because of who is waiting. A retry restarts the whole budget
# while the learner watches a pending bubble, and it re-spends the tokens of the
# attempt we just abandoned. At this timescale the retry that costs least is the
# learner saying it again — the failure is visible and immediate, and they were
# going to repeat themselves anyway. Env-overridable in case a flaky network
# makes one attempt the wrong trade.
CLAUDE_MAX_RETRIES = int(os.getenv("CLAUDE_MAX_RETRIES", "0"))

# The verdict call (M2-D) sits beside the loop, not in it — no turn waits on it.
# But a *learner* does: the card renders in a pending state the moment the
# session ends, so this is sized to what a person will sit in front of, not to
# what the API might eventually manage. Longer than a turn because the call is
# uncached and reads the whole transcript; far short of the SDK's 10-minute
# default, which would be a spinner with no end.
VERDICT_TIMEOUT_S = float(os.getenv("VERDICT_TIMEOUT_S", "20"))

# The verdict explains a computed outcome; it does not judge. Opus 5 plus
# adaptive thinking was a V2 staging test (#74) before the grader existed.
# The grader is now the judgment role, and this call is one paragraph of
# English plus a four-line in-band exchange that a learner sits in front of.
# Sonnet 5, same family as the converser and the sketch. Separate knob from
# `CONVERSATION_MODEL` so a measured swap still does not need a code edit.
VERDICT_MODEL = os.getenv("VERDICT_MODEL", "claude-sonnet-5")

# Same posture as `CONVERSATION_EFFORT`: unset means the API default (`high`),
# which buys deliberation this card has no use for. Env-overridable for the
# same reason as the model.
VERDICT_EFFORT = os.getenv("VERDICT_EFFORT", "low")


def _load_band_ceiling(default: int = 2) -> int:
    """The learner's HSK ceiling is *owned by the KB authoring workflow*
    (kb/zh/_hsk/ceiling.json) and merely consumed here — the service depends on
    the tooling's value, not the reverse. Universal across topics; raising it
    unlocks higher-band vocab everywhere. Moves to the per-user profile later."""
    path = os.path.join(os.path.dirname(__file__), os.pardir,
                        "kb", "zh", "_hsk", "ceiling.json")
    try:
        with open(path, encoding="utf-8") as f:
            return int(json.load(f)["band_ceiling"])
    except (OSError, KeyError, ValueError, TypeError):
        return default


HSK_BAND_CEILING = _load_band_ceiling()


# --- V2: the grader (docs/VALIDITY.md) ------------------------------------
#
# The scoring judgment, split off the converser and onto its own goal-blind
# call. It joins the turn's fan-out rather than waiting on the reply, so the
# learner never waits behind it — but the *session* does, since termination is
# computed from what it returns.
GRADER_MODEL = os.getenv("GRADER_MODEL", "claude-opus-5")

# Judgment is where capability pays. Opus 5 is $5/$25 per Mtok against Sonnet 5's
# $3/$15 — 1.67x, on a call that runs once per turn against a short prefix.
# Caches are per-model, so the grader gets its own entry either way; Opus 5
# caches from 512 tokens where Sonnet 5 needs 1024, which suits the smaller
# grader prefix.
#
# `medium`, not `high`. This call is off the *reply* path but not off the turn:
# the learner cannot speak again until the grade lands, because they are waiting
# to find out whether they got their point across. So the grader is the branch
# the turn's wall clock now runs on, and effort here is latency the learner sits
# in — not a free upgrade paid for in tokens alone.
GRADER_EFFORT = os.getenv("GRADER_EFFORT", "medium")

# **Thinking is on here, and that is the reason for a separate token budget.**
# The conversation, sketch and verdict workers all set `thinking: disabled`
# because `max_tokens` caps thinking *plus* output: a budget sized for the JSON
# alone gets eaten by reasoning and the call dies with `stop_reason: max_tokens`
# and nothing parsed. A grader wants the deliberation — it *is* the judgment —
# so it gets headroom instead of a prohibition. On Opus 5 thinking is on by
# default and disabling it is rejected above `high` effort, so this worker has
# to decide it explicitly rather than inherit the hot path's posture.
GRADER_MAX_TOKENS = int(os.getenv("GRADER_MAX_TOKENS", "4096"))

# The learner waits on this: the controls stay shut until the grade lands, so
# this timeout is the longest they can be left looking at an answered reply and
# a dead microphone. A failure is survivable — the reply stands, the credit is
# owed and settled by the next turn — which is what makes a bound of this size
# affordable rather than reckless.
GRADER_TIMEOUT_S = float(os.getenv("GRADER_TIMEOUT_S", "15"))

# The recovery pass runs *in front of* the verdict call, so its budget and the
# verdict's add up in the one place a learner is already waiting on a spinner.
# It is also best-effort by construction — a failure leaves the state alone and
# the card is still written — so it gets a tighter bound than a live turn's
# grade, which is the one whose result the session depends on.
VERDICT_RECOVERY_TIMEOUT_S = float(os.getenv("VERDICT_RECOVERY_TIMEOUT_S", "8"))
