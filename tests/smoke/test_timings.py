"""The per-turn timings HUD has to report every branch, client and server.

B0 (`docs/streams/latency.md`): a 10.87s turn that only showed STT, PA and
Claude looked like it had six unexplained seconds in it. The grader was on
the wire and the HUD dropped it. The headline was an unlabeled number, so it
read as server time when it was the round trip (upload included).

These tests pin the instrument, not a latency budget. They drive the same
stubbed turn the rest of the smoke suite uses, so they stay deterministic.
"""

import re

import pytest
from playwright.sync_api import expect

from tests.smoke.test_thread import _speak, seed

pytestmark = pytest.mark.smoke


def timings_text(page):
    el = page.locator("#thread .timings")
    expect(el).to_have_count(1)
    return el.inner_text()


def set_grader_ms(page, path, ms):
    """Put `grader_ms` on the canned turn the HUD actually reads (`done`)."""
    page.evaluate(
        """([path, ms]) => {
          const body = window.__stub.responses[path];
          if (Array.isArray(body)) {
            for (const event of body) {
              if (event.timings) {
                event.timings.grader_ms = event.stage === "done" ? ms : null;
              }
            }
          } else if (body && body.timings) {
            body.timings.grader_ms = ms;
          }
        }""",
        [path, ms],
    )


def has_mark(text, label):
    """A labeled duration, not the label as a substring of another word.

    `grade` must not match `grader`, and a bare `round` must not match
    `round trip`.
    """
    return re.search(rf"(?i)(?:^|[\s·]){re.escape(label)}\s+\d+\.\d+s", text) is not None


def test_spoken_timings_name_the_round_trip_and_the_server(page):
    """The headline is the wait the learner felt, labeled as such.

    An unlabeled bold number next to `server 4.31s` is how a 10.87s round trip
    got quoted as the server total. Both halves of the split have to carry
    their name.
    """
    seed(page)
    _speak(page)
    text = timings_text(page)

    assert has_mark(text, "round trip"), text
    assert has_mark(text, "server"), text
    headline = page.locator("#thread .timings b").inner_text()
    assert headline.lower().startswith("round trip"), headline


def test_spoken_timings_include_the_grader_branch(page):
    """The third fan-out branch is on the wire; the HUD has to show it.

    Opus 5 with thinking on is the branch most likely to hold the turn, and
    dropping it is what made the post-STT region look empty.
    """
    seed(page)
    set_grader_ms(page, "/api/turn", 8800.0)
    _speak(page)
    text = timings_text(page)

    assert has_mark(text, "grader"), text
    assert "grader 8.80s" in text


def test_spoken_timings_omit_a_grader_that_did_not_run(page):
    """A missing stage reports nothing, not zero — same rule as PA and Claude."""
    seed(page)
    set_grader_ms(page, "/api/turn", None)
    _speak(page)
    text = timings_text(page)

    assert "grader" not in text.lower(), text


def test_spoken_timings_include_client_marks(page):
    """Whatever the round-trip / server split does not explain still needs a number.

    encode: mic stop → request sent. upload: request sent → first byte.
    Each staged event, when it arrived. paint: `done` → the HUD is in the DOM.
    """
    seed(page)
    _speak(page)
    text = timings_text(page)

    for label in ("encode", "upload", "transcript", "score", "reply", "state", "paint"):
        assert has_mark(text, label), f"missing {label!r} in {text!r}"


def test_text_timings_name_the_split_and_the_grader(page):
    """Text mode has no WAV, but the same two questions still apply.

    The headline is still the round trip. The grader still runs (serially,
    after the converser). encode is a spoken-path mark and must not appear.
    """
    seed(page, mode="text")
    set_grader_ms(page, "/api/turn/text", 2100.0)
    page.fill("#text-input", "ni hao")
    page.click("#send")
    text = timings_text(page)

    assert has_mark(text, "round trip"), text
    assert has_mark(text, "server"), text
    assert "grader 2.10s" in text
    assert not has_mark(text, "encode"), text
    headline = page.locator("#thread .timings b").inner_text()
    assert headline.lower().startswith("round trip"), headline
