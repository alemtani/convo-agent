"""Phase 0 route tests: health, hello-world API, and static page serving.

Workers/Azure are not involved here — these are pure HTTP-surface assertions
via FastAPI's TestClient.
"""
from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)


def test_health_ok():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_hello_returns_hello_world():
    resp = client.get("/api/hello")
    assert resp.status_code == 200
    assert resp.json() == {"message": "hello world"}


def test_root_serves_static_page():
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    # The page is the visible surface; it must reference the turn API it calls
    # (Phase 1 deepened the page from the hello round-trip to push-to-talk).
    assert "/api/turn" in resp.text
