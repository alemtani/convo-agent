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
STT_TIMEOUT_S = float(os.getenv("STT_TIMEOUT_S", "5"))
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
