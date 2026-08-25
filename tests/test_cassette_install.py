"""Interception: the seam that makes an eval against the *real server* free.

`evals/cassette/` covers every caller that passes `client=`. A running server is
not one of them — `main.py` threads no client, so each worker falls back to its
module-global `_client` and a real `POST /api/turn/text` spends real money.

`cassette.install()` seeds those globals. That is production module state,
mutated at runtime by eval code, so the tests here are about the blast radius as
much as the feature:

- it reaches **every** worker, and refuses loudly if a worker's seam moved;
- a turn through the real FastAPI app records once and replays for nothing;
- an ordinary `uvicorn backend.main:app` process is untouched — asserted in a
  subprocess, because the only honest way to test "importing the app installs
  nothing" is in an interpreter no test has already dirtied.
"""
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.models import (
    ConversationResult,
    ConverserAnnotation,
    GraderResult,
    Utterance,
)
from backend.workers import conversation, feedback, grader, sketch
from evals import cassette

WORKERS = (conversation, grader, sketch, feedback)

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def restore_globals():
    """Never let an installed client leak into another test's turn."""
    before = [module._client for module in WORKERS]
    yield
    for module, client in zip(WORKERS, before):
        module._client = client


class _FakeLive:
    """A stand-in for `AsyncAnthropic` at the recording end of the layer.

    Answers whatever schema the request asks for, and counts the calls — the
    count is the assertion that a replay reached the network zero times.
    """

    def __init__(self):
        self.calls = 0
        self.messages = SimpleNamespace(parse=self._parse)

    async def _parse(self, **request):
        self.calls += 1
        schema = request["output_format"]
        if schema is GraderResult:
            parsed = GraderResult(coherence="on_track", slots_filled=[])
        elif schema is ConversationResult:
            parsed = ConversationResult(
                partner_response=Utterance(zh="您好！", pinyin="nín hǎo!"),
                turn_annotation=ConverserAnnotation(),
                user_reading=Utterance(zh="我要一杯茶", pinyin="wǒ yào yì bēi chá"),
            )
        else:  # pragma: no cover - a new worker schema should say so out loud
            raise AssertionError(f"unexpected schema {schema}")
        return SimpleNamespace(
            stop_reason="end_turn",
            parsed_output=parsed,
            usage=SimpleNamespace(
                input_tokens=100,
                output_tokens=10,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
            ),
        )


# --- The seam ------------------------------------------------------------


def test_install_seeds_every_worker(tmp_path):
    client = cassette.install(store=cassette.CassetteStore(tmp_path))

    for module in WORKERS:
        assert module._client is client
        # The fallback the workers actually take is `_get_client()`, so it is
        # what has to return the cassette — setting the global is the means.
        assert module._get_client() is client


def test_install_returns_the_client_it_installed(tmp_path):
    mine = cassette.CassetteClient(cassette.CassetteStore(tmp_path))
    assert cassette.install(mine) is mine


def test_install_refuses_a_module_whose_seam_moved(tmp_path):
    # If a worker stops holding a module-global `_client`, interception stops
    # working — and the failure mode is a bill, not a red build. So it is a
    # loud error rather than a skipped module.
    moved = SimpleNamespace(__name__="backend.workers.moved")
    with pytest.raises(cassette.CassetteError, match="_client"):
        cassette.install(
            store=cassette.CassetteStore(tmp_path), modules=(conversation, moved)
        )


def test_uninstall_puts_the_globals_back(tmp_path):
    for module in WORKERS:
        module._client = None
    cassette.install(store=cassette.CassetteStore(tmp_path))

    cassette.uninstall()

    for module in WORKERS:
        assert module._client is None


# --- A whole turn through the real app -----------------------------------


def _turn():
    return {
        "topic_id": "food-ordering",
        "text": "wo yao yi bei cha",
        "dialogue": [],
        "opening_line": {"zh": "您好", "pinyin": "nín hǎo"},
    }


def test_a_real_turn_records_once_and_then_replays_for_nothing(tmp_path):
    store = cassette.CassetteStore(tmp_path)
    live = _FakeLive()
    cassette.install(cassette.CassetteClient(store, record=True, live=live))

    with TestClient(app) as http:
        first = http.post("/api/turn/text", json=_turn())
    assert first.status_code == 200, first.text
    # Two calls on a text turn: the partner, then the grader.
    assert live.calls == 2

    replayed = _FakeLive()
    cassette.install(cassette.CassetteClient(store, live=replayed))
    with TestClient(app) as http:
        second = http.post("/api/turn/text", json=_turn())

    assert second.status_code == 200, second.text
    assert second.json()["reply"] == first.json()["reply"]
    assert replayed.calls == 0


def test_a_turn_with_no_recording_fails_loudly_rather_than_spending(tmp_path):
    cassette.install(store=cassette.CassetteStore(tmp_path))

    with TestClient(app) as http:
        with pytest.raises(cassette.CassetteMiss):
            http.post("/api/turn/text", json=_turn())


# --- The production entrypoint is untouched ------------------------------


def _in_a_fresh_interpreter(source: str) -> str:
    done = subprocess.run(
        [sys.executable, "-c", source],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def test_importing_the_production_app_installs_nothing():
    # The differentiator is which entrypoint was launched — never a flag inside
    # `backend/`. `uvicorn backend.main:app` must not be installable into.
    out = _in_a_fresh_interpreter(
        "import backend.main\n"
        "from backend.workers import conversation, feedback, grader, sketch\n"
        "print([m._client for m in (conversation, grader, sketch, feedback)])\n"
    )
    assert out == "[None, None, None, None]"


def test_the_eval_entrypoint_serves_the_same_app_with_the_layer_installed():
    out = _in_a_fresh_interpreter(
        "import evals.server, backend.main\n"
        "from backend.workers import conversation, feedback, grader, sketch\n"
        "from evals.cassette import CassetteClient\n"
        "same = evals.server.app is backend.main.app\n"
        "installed = all(\n"
        "    isinstance(m._client, CassetteClient)\n"
        "    for m in (conversation, grader, sketch, feedback)\n"
        ")\n"
        "print(same, installed)\n"
    )
    assert out == "True True"
