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

# Conversation worker (per-turn hot path). Sonnet 4.6 is the deliberate choice
# for the loop — cheap and deterministic enough for every-turn calls (DESIGN.md).
# Hold the model fixed per session: switching it mid-session busts the cache.
CONVERSATION_MODEL = "claude-sonnet-5"

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
# Sized off the Stage 0 measurements (STT 1.32s, PA 1.20s, Claude 3.56s) with
# roughly an order of magnitude of headroom: the job here is to bound the worst
# case, not to police a slow-but-working call. Env-overridable so a flaky network
# doesn't need a code change.
STT_TIMEOUT_S = float(os.getenv("STT_TIMEOUT_S", "15"))
PA_TIMEOUT_S = float(os.getenv("PA_TIMEOUT_S", "15"))
CLAUDE_TIMEOUT_S = float(os.getenv("CLAUDE_TIMEOUT_S", "45"))


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
