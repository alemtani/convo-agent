"""Fixtures for the Playwright frontend smoke suite.

The suite is deterministic by construction and therefore runs in CI, unlike the
`live` marker: no API keys, no model output, no wall-clock waits. Three pieces
make that true.

* **The page is served, not opened from disk.** `getUserMedia` and
  `audioWorklet.addModule()` both need a secure context and a real origin, and
  `file://` is neither — so a threaded `http.server` serves `frontend/` on
  127.0.0.1, which browsers count as secure.
* **The microphone is a file.** Chrome's
  `--use-file-for-fake-audio-capture` makes `getUserMedia` replay a WAV we
  generate here, so "did frames flow?" has a fixed answer instead of depending
  on whatever hardware the runner has.
* **The server is a `fetch` stub, not the backend.** `/api/turn*` answers from
  canned JSON installed by an init script. In *manual* mode a response is held
  until the test releases it, which is what lets the pending-bubble and
  optimistic-echo assertions look at the in-flight state without racing a timer.

The stub replaces `window.fetch` rather than using Playwright routing on
purpose: route handlers run on the same thread as the sync test, so a handler
that blocks to hold a response open would deadlock the test that wants to
inspect the page meanwhile.
"""

import base64
import functools
import http.server
import importlib.util
import io
import json
import math
import struct
import threading
import wave
from pathlib import Path

import pytest

# Collected only when Playwright is installed. The default `pytest -q` run
# deselects this suite by marker, but deselection happens after collection —
# and collection imports the module — so an environment without the smoke
# extras (a plain checkout, the Stop hook, CI's backend job) would fail at
# import time instead of quietly skipping.
#
# Globbed rather than listed by name. A hardcoded list makes every *new* smoke
# file a CI break that is invisible locally: the suite passes on a dev machine,
# where Playwright *is* importable, and only the backend job — which installs
# just backend/requirements.txt — ever sees the ImportError. That is a trap
# baited for the next person to add a file here, and it caught this PR. The
# condition is "this directory needs Playwright", which is a property of the
# directory, so ask the directory.
collect_ignore = (
    []
    if importlib.util.find_spec("playwright")
    else [path.name for path in Path(__file__).parent.glob("test_*.py")]
)

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    """SimpleHTTPRequestHandler without the per-request stderr logging."""

    def log_message(self, *args):
        pass


@pytest.fixture(scope="session")
def frontend_server():
    """Serve `frontend/` on 127.0.0.1 for the session; yields the base URL."""
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0),
        functools.partial(_QuietHandler, directory=str(FRONTEND_DIR)),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture(scope="session")
def fake_audio_wav(tmp_path_factory):
    """A 2 s 440 Hz mono tone for Chrome's fake capture device.

    Chrome wants 16-bit PCM. Content barely matters — the assertions are about
    frames arriving at all — but a fixed file keeps sample counts stable.
    """
    path = tmp_path_factory.mktemp("audio") / "mic.wav"
    rate, seconds = 48000, 2
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(
            b"".join(
                struct.pack("<h", int(0.4 * 32767 * math.sin(2 * math.pi * 440 * i / rate)))
                for i in range(rate * seconds)
            )
        )
    return path


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args, fake_audio_wav):
    """Give Chromium a deterministic microphone instead of real hardware."""
    return {
        **browser_type_launch_args,
        "args": [
            *browser_type_launch_args.get("args", []),
            "--use-fake-ui-for-media-stream",
            "--use-fake-device-for-media-stream",
            f"--use-file-for-fake-audio-capture={fake_audio_wav}",
            "--autoplay-policy=no-user-gesture-required",
        ],
    }


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args, frontend_server):
    return {
        **browser_context_args,
        "base_url": frontend_server,
        "permissions": ["microphone"],
        # Pinned, not inherited. The viewport decides whether the thread scrolls
        # at all, and `reduced_motion` decides whether a smooth scroll animates —
        # a developer machine with "reduce motion" enabled turns every follow
        # scroll instant and hides the very races these tests exist to catch.
        "viewport": {"width": 420, "height": 760},
        "reduced_motion": "no-preference",
    }


# Canned turns. `transcript` is 汉字 + tone-marked pinyin (the worker's reading
# of typed pinyin, or Azure's transcription); `reply` is the partner's answer.
# `timings`/`usage` are carried so the per-turn timings line actually renders —
# without them `renderTimings` returns early and the tests would silently stop
# covering where that line lands relative to the bubbles.
TIMINGS = {"stt_ms": 300.0, "pa_ms": 250.0, "claude_ms": 900.0, "total_ms": 1250.0}
USAGE = {"input_tokens": 4200, "output_tokens": 60, "cache_read_input_tokens": 4096}

# M2-B: `POST /api/session`'s canned reply — the opening line + scenario card
# a fresh thread renders before any turn happens. Real content doesn't matter
# to these tests, only that the shape parses; `test_thread.py`'s `seed()`
# pre-seeds this same shape into localStorage so the render is synchronous
# (no fetch race) wherever a test cares about exact bubble counts.
SESSION_START = {
    "scenario_card": {
        "situation": "You meet a classmate on campus in the morning.",
        "goal": "Introduce yourself and find out their name.",
    },
    "opening_line": {"zh": "你好！你叫什么名字？", "pinyin": "nǐ hǎo! nǐ jiào shénme míngzi?"},
    "sketch": "The classmate is warm and a little shy.",
}

TURN_TEXT = {
    "transcript": {"zh": "你好", "pinyin": "nǐ hǎo"},
    "reply": {"zh": "你好！很高兴认识你。", "pinyin": "nǐ hǎo! hěn gāoxìng rènshi nǐ."},
    "annotation": {"tone_errors": []},
    "timings": {**TIMINGS, "stt_ms": None, "pa_ms": None},   # no speech on a typed turn
    "usage": USAGE,
}

# The spoken turn answers in *stages* — one JSON object per line — so its canned
# form is a list, not an object. A single-object stub would let the page's
# `ndjson()` parse one event with `stage: undefined`, match no branch, and leave
# both bubbles pending forever while every count-based assertion still passed.
# That is exactly the failure this suite caught when the route started streaming.
TURN_AUDIO = [
    {
        "stage": "transcript",
        "transcript": {"zh": "你好", "pinyin": "nǐ hǎo"},
        "timings": {**TIMINGS, "pa_ms": None, "claude_ms": None, "total_ms": None},
        "elapsed_ms": 310.0,
    },
    {
        "stage": "score",
        "pronunciation": {
            "overall": 88.0,
            "syllables": [
                {"hanzi": "你", "pinyin": "nǐ", "accuracy": 92.0},
                {"hanzi": "好", "pinyin": "hǎo", "accuracy": 84.0},
            ],
        },
        "tone_errors": [],
        "timings": {**TIMINGS, "claude_ms": None, "total_ms": None},
        "elapsed_ms": 560.0,
    },
    {
        "stage": "reply",
        "reply": {"zh": "你好！", "pinyin": "nǐ hǎo!"},
        "annotation": {"tone_errors": []},
        "timings": {**TIMINGS, "total_ms": None},
        "elapsed_ms": 1240.0,
    },
    {"stage": "done", "timings": TIMINGS, "usage": USAGE, "elapsed_ms": 1250.0},
]

@pytest.fixture(scope="session")
def fake_speech_b64():
    """A tiny decodable audio clip for the `/api/tts` stub, base64 encoded.

    A WAV, not an MP3, even though the real endpoint returns MP3: browsers sniff
    the container in `decodeAudioData` and ignore the content type, and a WAV is
    the one format we can produce here without shipping an encoder. What the
    page does with the bytes — decode, cache, play — is identical either way,
    and that is the whole of what these tests can observe.

    Fabricating a fake MP3 would decode to a rejected promise, which the page
    correctly treats as "synthesis failed" — every audio assertion would then be
    testing the fallback path while appearing to test playback.
    """
    buf = io.BytesIO()
    rate = 24000
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(
            b"".join(
                struct.pack("<h", int(0.3 * 32767 * math.sin(2 * math.pi * 440 * i / rate)))
                for i in range(rate // 4)
            )
        )
    return base64.b64encode(buf.getvalue()).decode("ascii")


_STUB_JS = """
(canned) => {
  const state = {
    responses: canned,      // path -> JSON body, or array of NDJSON events
    status: {},             // path -> HTTP status override
    manual: false,          // hold responses/lines until released
    waiting: [],            // queued steps, in order
    requests: [],
  };
  // `release()` flushes everything queued — the whole response, or a stream's
  // remaining lines at once. `releaseNext()` advances exactly one step, which is
  // what lets a test look at the thread *between* two stages of one turn.
  state.release = () => { state.waiting.splice(0).forEach((f) => f()); };
  state.releaseNext = () => { const f = state.waiting.shift(); if (f) f(); };
  window.__stub = state;

  window.fetch = (input, init) => {
    const path = new URL(input, location.href).pathname;
    state.requests.push(path);
    const status = state.status[path] || 200;
    const canned = state.responses[path];

    // A staged turn: one JSON object per line, delivered incrementally. `fetch`
    // resolves on *headers* — as the real one does — and the body arrives after,
    // so a page that awaits the whole body instead of reading the stream shows
    // up here as a hang rather than passing quietly.
    if (status === 200 && Array.isArray(canned)) {
      let controller;
      const encoder = new TextEncoder();
      const stream = new ReadableStream({ start: (c) => { controller = c; } });
      const emit = (line) => controller.enqueue(encoder.encode(JSON.stringify(line) + "\\n"));
      const steps = canned.map((line) => () => emit(line));
      steps.push(() => controller.close());
      if (state.manual) state.waiting.push(...steps);
      else steps.forEach((step) => step());
      return Promise.resolve(new Response(stream, {
        status,
        headers: { "Content-Type": "application/x-ndjson" },
      }));
    }

    // Binary: `{__audio: "<base64>"}` answers with real decodable bytes, which
    // is what /api/tts returns. Anything less and decodeAudioData rejects, and
    // the page's honest fallback (reveal the text) would mask every playback
    // assertion as a pass.
    const audio = status === 200 && canned && canned.__audio;
    if (audio) {
      const bin = Uint8Array.from(atob(audio), (c) => c.charCodeAt(0));
      const make = () => new Response(bin, {
        status,
        headers: { "Content-Type": "audio/mpeg" },
      });
      if (!state.manual) return Promise.resolve(make());
      return new Promise((resolve) => state.waiting.push(() => resolve(make())));
    }

    const body = status === 200 ? JSON.stringify(canned || {}) : "stubbed failure";
    const make = () => new Response(body, {
      status,
      headers: { "Content-Type": status === 200 ? "application/json" : "text/plain" },
    });
    if (!state.manual) return Promise.resolve(make());
    return new Promise((resolve) => state.waiting.push(() => resolve(make())));
  };
}
"""


@pytest.fixture
def page(page, fake_speech_b64):
    """A page whose `/api/turn*` and `/api/tts` calls answer from canned data."""
    # `/health` is canned explicitly rather than left to the stub's `{}` default:
    # the page probes it on load to decide whether to raise the passcode gate,
    # and these tests are about the thread, not the gate. Saying `disabled` out
    # loud keeps that a stated precondition instead of an accident of the
    # default — a gate that appeared here would fail every test with a blank
    # overlay and no obvious cause.
    canned = {
        "/api/turn": TURN_AUDIO,
        "/api/turn/text": TURN_TEXT,
        "/api/session": SESSION_START,
        "/api/tts": {"__audio": fake_speech_b64},
        "/health": {"status": "ok", "auth": "disabled"},
    }
    page.add_init_script(f"({_STUB_JS})({json.dumps(canned)})")
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    yield page
    assert not errors, f"uncaught page errors: {errors}"
