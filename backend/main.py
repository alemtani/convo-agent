import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="Convo Agent", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), os.pardir, "frontend")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/hello")
async def hello():
    return {"message": "hello world"}


# Static page is mounted last so explicit API routes above take precedence;
# html=True serves frontend/index.html at "/".
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
