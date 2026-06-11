# CLAUDE.md

## Project purpose

Convo Agent is a Mandarin conversation practice tool. It uses Claude as the conversational AI engine and Azure Speech Services for speech-to-text, text-to-speech, and pronunciation assessment.

## Tech stack

- Python 3.9+
- FastAPI + Uvicorn
- Anthropic SDK (Claude API)
- Azure Cognitive Services Speech SDK
- aiosqlite (async SQLite)
- Pydantic for data validation
- python-dotenv for environment config

## Key files and directories

```
backend/
  main.py            # FastAPI app, endpoints, CORS config
  config.py          # Loads env vars (API keys, Azure region)
  requirements.txt   # Python dependencies
  __init__.py
.env.example         # Template for required environment variables
.gitignore
```

## How to run

```bash
source .venv/bin/activate
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

No test suite exists yet.

## Development conventions

- Async/await throughout (FastAPI async handlers, aiosqlite)
- Environment-based configuration — no hardcoded secrets
- CORS allows `http://localhost:3000` for local frontend development
- Git commit messages use conventional commits (e.g. `feat: scaffold backend`)

## Design reference

See `docs/DESIGN.md` for the full architecture spec, data flow, data models, MVP scope, build order, and technical risk assessment.
