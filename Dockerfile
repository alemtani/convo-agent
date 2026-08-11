FROM python:3.11-slim

# The Azure Speech SDK links against these at load time even for file-based
# recognition (no mic, no playback) — omitting them fails at import, not at
# the call site, so the error shows up nowhere near the STT/PA code that
# looks broken.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libssl3 \
      libasound2 \
      ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/ backend/
COPY frontend/ frontend/
# Baked in at build time, not mounted: kb/ is git-versioned markdown, and the
# DB (Phase 4+) stores a pointer into it, never the content (DESIGN.md).
COPY kb/ kb/

EXPOSE 8000

# --proxy-headers so uvicorn trusts Fly's X-Forwarded-Proto: request.url.scheme
# reads "https" behind the edge, which is what makes the session cookie Secure
# (backend/main.py) instead of silently downgrading on every deploy.
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
