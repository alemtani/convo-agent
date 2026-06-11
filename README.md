# Convo Agent

A Mandarin conversation practice agent powered by Claude and Azure Speech Services.

## Architecture

- **FastAPI** backend serving a REST API
- **Anthropic Claude API** for conversational AI
- **Azure Speech Services** for speech-to-text, text-to-speech, and pronunciation assessment
- **SQLite** (via aiosqlite) for async data persistence
- Frontend expected at `http://localhost:3000` (CORS pre-configured)

## Setup

1. **Clone the repo**

   ```bash
   git clone git@github.com:alemtani/convo-agent.git
   cd convo-agent
   ```

2. **Create a virtual environment and install dependencies**

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r backend/requirements.txt
   ```

3. **Configure environment variables**

   ```bash
   cp .env.example .env
   ```

   Fill in your keys in `.env`:

   ```
   ANTHROPIC_API_KEY=sk-ant-...
   AZURE_SPEECH_KEY=your-azure-speech-key
   AZURE_SPEECH_REGION=eastus
   ```

## Running the dev server

```bash
source .venv/bin/activate
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

The health check is available at `GET /health`.

## Current status

The project is in early scaffold stage:

- FastAPI server with CORS and health check endpoint
- Configuration system loading API keys from `.env`
- Dependencies installed for Claude, Azure Speech, and async SQLite
- Conversation, speech processing, and pronunciation assessment endpoints are not yet implemented
