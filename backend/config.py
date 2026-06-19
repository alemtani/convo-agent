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
CONVERSATION_MODEL = "claude-sonnet-4-6"

# How forgiving the partner is of learner errors (0=strict … 1=very patient).
# Baked as a literal into the frozen system prompt — never per-turn — so the
# cached prefix stays byte-identical. Default 0.8 = patient (DESIGN.md).
FORGIVENESS_LEVEL_DEFAULT = 0.8

# Per-syllable PA accuracy below this is surfaced as a tone error (Phase 3b).
# Aligned with the frontend's existing `tone-bad` cutoff in index.html.
TONE_ERROR_THRESHOLD = 60.0


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
