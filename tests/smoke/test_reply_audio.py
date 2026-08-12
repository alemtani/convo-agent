"""Frontend smoke tests: audio-only partner replies (M4).

`test_audio.py` covers the playback seam — that a context exists, unlocks, and
survives. This file covers what M4 built on it: the reply arrives as sound, its
text is withheld, and both escape hatches work.

The escape hatches are the point. Autoplay is the headline, but a learner who
cannot parse the line is the ordinary case, not the edge case, and 🔊 and 👁 are
the difference between a session continuing and a session ending. So most of
what follows is about them — replay that doesn't re-synthesize, reveal that
survives the global toggle, and a failed synthesis that falls back to text
instead of leaving a bubble with nothing in it.

What a headless browser cannot answer is whether a sound reached a speaker; iOS
autoplay in particular has no desktop equivalent. `window.__audio._plays()`
counts buffers actually started, which is the last step the page controls, and
the real device check stays manual (issue #33's acceptance criterion).
"""

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.smoke

REPLY_ZH = "你好！很高兴认识你。"     # matches conftest's canned typed reply


def load(page):
    """Load the page in typing mode.

    Most of what this file asserts is about the *reply*, which is identical
    either way, and a typed turn reaches it without a two-second microphone.
    `test_the_spoken_turn_speaks_its_reply` covers the mic path once.
    """
    page.add_init_script("localStorage.setItem('convo.mode', 'text')")
    page.goto("/")


def _send_text(page, text="ni hao"):
    """Run one typed turn to completion and return the partner bubble."""
    page.fill("#text-input", text)
    page.click("#send")
    bubble = page.locator(".bubble.partner").last
    expect(bubble.locator(".controls")).to_be_visible()
    return bubble


def test_reply_arrives_without_its_text(page):
    """The default mode: you hear the line, you don't read it."""
    load(page)
    bubble = _send_text(page)

    expect(bubble).to_have_class("bubble partner unread")
    expect(bubble.locator(".zh")).to_have_count(0)
    expect(bubble.locator(".pinyin")).to_have_count(0)
    # Both hatches are up from the first frame, not added on failure.
    expect(bubble.locator("button.replay")).to_be_visible()
    expect(bubble.locator("button.reveal")).to_be_visible()


def test_the_reply_plays_itself(page):
    """Autoplay from a fetch callback — no gesture on the stack, as on a phone."""
    load(page)
    _send_text(page)

    page.wait_for_function("window.__audio._plays() === 1")
    assert page.evaluate("window.__stub.requests.filter((p) => p === '/api/tts').length") == 1


def test_replay_costs_no_second_synthesis(page):
    """The acceptance criterion. The decoded buffer is kept; 🔊 never re-fetches."""
    load(page)
    bubble = _send_text(page)
    page.wait_for_function("window.__audio._plays() === 1")

    bubble.locator("button.replay").click()
    bubble.locator("button.replay").click()

    page.wait_for_function("window.__audio._plays() === 3")
    assert page.evaluate("window.__stub.requests.filter((p) => p === '/api/tts').length") == 1


def test_reveal_shows_the_text_in_place(page):
    """One bubble, repainted — not a second one appended below it."""
    load(page)
    bubble = _send_text(page)

    bubble.locator("button.reveal").click()

    expect(page.locator(".bubble.partner")).to_have_count(1)
    expect(bubble.locator(".zh")).to_have_text(REPLY_ZH)
    expect(bubble.locator(".pinyin")).to_be_visible()


def test_reveal_toggles_back(page):
    load(page)
    bubble = _send_text(page)

    bubble.locator("button.reveal").click()
    expect(bubble.locator(".zh")).to_have_count(1)
    bubble.locator("button.reveal").click()
    expect(bubble.locator(".zh")).to_have_count(0)


def test_show_text_mode_is_retroactive(page):
    """Turning text on is for reviewing what was already said."""
    load(page)
    bubble = _send_text(page)
    expect(bubble.locator(".zh")).to_have_count(0)

    page.click("#show-text")

    expect(bubble.locator(".zh")).to_have_text(REPLY_ZH)


def test_the_global_toggle_overrules_a_revealed_bubble(page):
    """"All" means all — a per-bubble reveal does not outlive it.

    The first cut let the override survive, so pressing "hide all" could leave
    one bubble still showing its text with nothing on screen explaining why. An
    invisible exception is worse than losing the reveal, which is one tap to
    redo.
    """
    load(page)
    bubble = _send_text(page)
    bubble.locator("button.reveal").click()
    expect(bubble.locator(".zh")).to_have_count(1)

    page.click("#show-text")        # show all — already visible, stays visible
    expect(bubble.locator(".zh")).to_have_text(REPLY_ZH)

    page.click("#show-text")        # hide all — the override goes with it
    expect(bubble.locator(".zh")).to_have_count(0)


def test_the_global_button_names_its_scope(page):
    """It sits beside a bubble carrying the same two emoji.

    Bare, a global 👁 next to a bubble whose text is already visible reads as
    the two controls disagreeing — which is exactly how it was reported.
    """
    load(page)

    expect(page.locator("#show-text")).to_have_text("👁 All")
    page.click("#show-text")
    expect(page.locator("#show-text")).to_have_text("🙈 All")


def test_show_text_preference_survives_a_reload(page):
    load(page)
    _send_text(page)
    page.click("#show-text")

    page.reload()

    expect(page.locator(".bubble.partner .zh")).to_have_text(REPLY_ZH)


def test_a_restored_reply_is_hidden_and_silent(page):
    """Reloading mid-session must not replay the whole conversation at you."""
    load(page)
    _send_text(page)
    page.wait_for_function("window.__audio._plays() === 1")

    page.reload()
    bubble = page.locator(".bubble.partner").last
    expect(bubble.locator("button.replay")).to_be_visible()

    expect(bubble.locator(".zh")).to_have_count(0)
    assert page.evaluate("window.__audio._plays()") == 0
    assert page.evaluate("window.__stub.requests.filter((p) => p === '/api/tts').length") == 0


def test_a_failed_synthesis_reveals_the_text(page):
    """The dead end this whole feature has to avoid: no audio *and* no words."""
    load(page)
    page.evaluate("window.__stub.status['/api/tts'] = 502")

    bubble = _send_text(page)

    expect(bubble.locator(".zh")).to_have_text(REPLY_ZH)
    expect(bubble.locator("button.replay")).to_be_visible()
    assert page.evaluate("window.__audio._plays()") == 0


def test_a_blocked_playback_is_a_failure_not_a_silence(page):
    """A device that refuses to make a sound must not look like success.

    `playBuffer` resolves false rather than throwing when the context is not
    running, so passing its result through left the learner with no audio, no
    error and no text — the dead end, reached silently. Found on a real iPhone,
    where nothing on screen said anything had gone wrong.
    """
    load(page)
    _send_text(page)                     # primes the cache; no network below
    page.evaluate("window.__audio._close()")

    outcome = page.evaluate(
        "window.__audio.speak('%s').then(() => 'played', (e) => e.blocked ? 'blocked' : 'other')"
        % REPLY_ZH
    )

    assert outcome == "blocked"


def test_a_blocked_playback_reveals_the_text(page):
    """Same recovery as a failed synthesis: words beat silence."""
    load(page)
    bubble = _send_text(page)
    page.evaluate("window.__audio._close()")

    # 🔊 from script, so no gesture unlocks the context mid-test.
    page.evaluate("document.querySelector('.bubble.partner button.replay').click()")

    expect(bubble.locator(".zh")).to_have_text(REPLY_ZH)
    expect(page.locator("#status")).to_contain_text("silent switch")


def test_a_suspended_context_recovers_at_play_time(page):
    """The iPhone bug, as close as Chromium can get to it.

    iOS suspends this context when `getUserMedia` reconfigures the audio
    session, and the suspension lands *after* the tap that caused it — so the
    reply arrives, seconds later, with a suspended context and no gesture on the
    stack. Playback has to recover on its own rather than wait for a tap that
    is not coming.
    """
    load(page)
    _send_text(page)
    page.wait_for_function("window.__audio._plays() === 1")
    page.evaluate("window.__audio._suspend()")

    played = page.evaluate("window.__audio.speak('%s').then(() => true, () => false)" % REPLY_ZH)

    assert played is True
    assert page.evaluate("window.__audio.state()") == "running"


def test_an_unspeakable_reply_renders_as_text_only(page):
    """A reply past the endpoint's character cap has no audio form at all.

    Rare — a partner reply is a sentence — but it is a different failure from
    Azure being down, and it was reaching the learner dressed as that one:
    "audio unavailable", with a 🔊 that could never work no matter how often it
    was pressed.
    """
    load(page)
    page.evaluate("window.__stub.status['/api/tts'] = 422")

    bubble = _send_text(page)

    expect(bubble.locator(".zh")).to_have_text(REPLY_ZH)
    expect(page.locator("#status")).to_contain_text("too long to speak")
    # Present but inert: a bubble missing the buttons every other bubble has
    # reads as a rendering bug, not as an explanation.
    expect(bubble.locator("button.replay")).to_be_disabled()
    expect(bubble.locator("button.reveal")).to_be_disabled()


def test_an_unspeakable_reply_cannot_be_hidden(page):
    """Hiding it would leave a bubble with no audio *and* no words."""
    load(page)
    page.evaluate("window.__stub.status['/api/tts'] = 422")
    bubble = _send_text(page)

    page.click("#show-text")        # show all
    page.click("#show-text")        # hide all — everything else goes quiet

    expect(bubble.locator(".zh")).to_have_text(REPLY_ZH)


def test_an_upstream_failure_keeps_its_controls_live(page):
    """The contrast case: Azure being down is transient, so 🔊 stays worth a tap."""
    load(page)
    page.evaluate("window.__stub.status['/api/tts'] = 502")

    bubble = _send_text(page)

    expect(page.locator("#status")).to_contain_text("audio unavailable")
    expect(bubble.locator("button.replay")).to_be_enabled()
    expect(bubble.locator("button.reveal")).to_be_enabled()


def test_a_failed_synthesis_leaves_the_thread_usable(page):
    """A silent turn is a setback, not the end of the session."""
    load(page)
    page.evaluate("window.__stub.status['/api/tts'] = 502")
    _send_text(page)

    page.evaluate("delete window.__stub.status['/api/tts']")
    _send_text(page, "zaijian")

    expect(page.locator(".bubble.partner")).to_have_count(2)
    page.wait_for_function("window.__audio._plays() === 1")


def test_a_new_conversation_drops_the_cached_audio(page):
    """The speech cache is the one structure here that grows without a bound."""
    load(page)
    _send_text(page)
    page.wait_for_function("window.__audio._cached() === 1")

    page.click("#reset")

    assert page.evaluate("window.__audio._cached()") == 0
    expect(page.locator(".bubble.partner")).to_have_count(0)


def test_the_spoken_turn_speaks_its_reply(page):
    """The same path from the mic side, where the reply arrives mid-stream.

    Both waits are on observable state, never on a clock. Releasing after a
    fixed sleep made this the one flaky test in the suite: under full-suite load
    the release beat the first frame, so no audio was captured and no turn was
    ever sent — and it passed in isolation every time.
    """
    page.goto("/")
    page.hover("#talk")
    page.mouse.down()
    page.wait_for_function("window.__convo.framesCaptured > 0")
    page.mouse.up()
    page.wait_for_function("window.__convo.lastSampleCount !== null")

    expect(page.locator(".bubble.partner")).to_have_count(1)
    expect(page.locator(".bubble.partner .zh")).to_have_count(0)
    page.wait_for_function("window.__audio._plays() === 1")
