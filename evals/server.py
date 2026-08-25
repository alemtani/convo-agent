"""The eval entrypoint: the real app, with every Anthropic call on cassettes.

    uvicorn evals.server:app          # replays; a miss is an error
    CASSETTE_RECORD=1 uvicorn evals.server:app     # spends money, records

`app` is `backend.main.app` itself — the same routes, the same orchestrator, the
same workers. The only difference is that `cassette.install()` ran first, so the
module-global client each worker falls back to is a cassette rather than a real
`AsyncAnthropic`. That makes an end-to-end run against a live server free, which
is what `POST /api/turn/text` could not be before.

**Why an entrypoint and not a flag.** A `CASSETTES=1` check inside `backend/`
would put eval code in the production import path and make one misconfigured
variable on fly.io enough to serve a learner canned replies. Launching a
different module cannot happen by accident on a deploy, and `backend.main`
carries no line that knows this file exists. The env vars below are read *here*,
never in `backend/`, so they mean nothing to the production process.

**Azure stays real.** This layer wraps `messages.parse`. STT, PA and TTS are a
different SDK and a different shape, so "free end-to-end" means the text
harness — `POST /api/turn/text` — and not the audio path. `POST /api/turn` still
spends Azure money on every call.
"""
import logging
import os

from evals import cassette

logger = logging.getLogger(__name__)


def _flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


client = cassette.install(
    store=cassette.CassetteStore(
        os.getenv("CASSETTE_DIR") or cassette.CassetteStore.default_root()
    ),
    record=_flag("CASSETTE_RECORD"),
    samples=int(os.getenv("CASSETTE_SAMPLES", "1")),
    refresh=_flag("CASSETTE_REFRESH"),
)

# Imported *after* install so no worker can have built a real client first.
from backend.main import app  # noqa: E402

logger.warning(
    "cassette server: Anthropic calls %s from %s (Azure is still live)",
    "RECORDING to" if client.record else "replaying",
    client.store.root,
)

__all__ = ["app", "client"]
