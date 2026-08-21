"""Phase 3a orchestrator tests — the text turn, worker mocked.

The orchestrator is plain Python (no LLM): it loads the topic KB, hands the
worker the session sketch + forgiveness default, and wraps the result. Tests stub
the worker so no tokens are spent and assert the wiring (KB loaded,
sketch/forgiveness passed, response shaped).

The *spoken* turn lives in `tests/test_turn_stream.py`, not here. It has no
collected form to assert against any more — `POST /api/turn` streams, so the
staged events are the contract and there is no second code path that could
disagree with them.
"""
import dataclasses
import pytest

from backend import config, kb, orchestrator
from backend.models import (
    ConversationTurnResponse,
    SessionStartResponse,
    SessionState,
    SketchResult,
    TextTurnRequest,
    TurnAnnotation,
    Utterance,
)
from backend.workers import conversation
from backend.workers import sketch as sketch_worker


async def test_run_text_turn_loads_kb_and_calls_worker(monkeypatch):
    captured = {}

    async def fake_respond(*, kb_block, sketch, dialogue, user_text, forgiveness_level,
                           want_reading=True, hint=None, client=None):
        captured.update(
            kb_block=kb_block, sketch=sketch, dialogue=dialogue,
            user_text=user_text, forgiveness_level=forgiveness_level,
        )
        return (
            Utterance(zh="你好！你叫什么名字？", pinyin="nǐ hǎo! nǐ jiào shénme míngzi?"),
            TurnAnnotation(coherence="on_track", topic_tags=["greetings"]),
            Utterance(zh="我叫小明", pinyin="wǒ jiào xiǎo míng"),
            object(),
        )

    monkeypatch.setattr(conversation, "respond", fake_respond)

    req = TextTurnRequest(
        topic_id="greetings",
        text="我叫小明",
        dialogue=[{"role": "user", "zh": "你好"}],
        sketch="SKETCH bytes from this session's POST /api/session",
    )
    resp = await orchestrator.run_text_turn(req)

    assert isinstance(resp, ConversationTurnResponse)
    assert resp.reply.zh == "你好！你叫什么名字？"
    assert resp.annotation.coherence == "on_track"

    # The orchestrator passes the client-held sketch straight through and owns
    # only the forgiveness default; it loads the real KB.
    assert captured["sketch"] == "SKETCH bytes from this session's POST /api/session"
    assert captured["forgiveness_level"] == config.FORGIVENESS_LEVEL_DEFAULT
    assert captured["user_text"] == "我叫小明"
    assert captured["kb_block"] == kb.load_kb_block("greetings")


async def test_run_text_turn_defaults_sketch_to_empty_before_a_session_starts(monkeypatch):
    """A turn sent before `POST /api/session` degrades to no flavour rather
    than failing — same permissiveness as `dialogue` defaulting to `[]`."""
    captured = {}

    async def fake_respond(*, sketch, **kwargs):
        captured["sketch"] = sketch
        return (
            Utterance(zh="你好", pinyin="nǐ hǎo"),
            TurnAnnotation(coherence="on_track"),
            Utterance(zh="你好", pinyin="nǐ hǎo"),
            object(),
        )

    monkeypatch.setattr(conversation, "respond", fake_respond)

    await orchestrator.run_text_turn(TextTurnRequest(topic_id="greetings", text="你好"))

    assert captured["sketch"] == ""


async def test_run_text_turn_transcript_is_the_workers_reading(monkeypatch):
    """The learner types pinyin; the bubble shows the 汉字 the worker read.

    This is the whole point of text mode for a beginner — local romanization can't
    do it (`to_pinyin` only goes hanzi→pinyin), and only the worker has the context
    to resolve `ta` into 他 vs 她.
    """
    monkeypatch.setattr(
        conversation,
        "respond",
        _worker_reply(reading=Utterance(zh="我叫小明", pinyin="wǒ jiào xiǎo míng")),
    )

    resp = await orchestrator.run_text_turn(
        TextTurnRequest(topic_id="greetings", text="wo jiao xiao ming")
    )

    assert resp.transcript == Utterance(zh="我叫小明", pinyin="wǒ jiào xiǎo míng")


async def test_run_text_turn_passes_the_stripped_text_to_the_worker(monkeypatch):
    captured = {}

    async def fake_respond(*, user_text, **kwargs):
        captured["user_text"] = user_text
        return (
            Utterance(zh="你好", pinyin="nǐ hǎo"),
            TurnAnnotation(coherence="on_track"),
            Utterance(zh="你好", pinyin="nǐ hǎo"),
            object(),
        )

    monkeypatch.setattr(conversation, "respond", fake_respond)

    await orchestrator.run_text_turn(
        TextTurnRequest(topic_id="greetings", text="  ni3hao3  ")
    )

    assert captured["user_text"] == "ni3hao3"


async def test_run_text_turn_derives_tone_errors_from_typed_digits(monkeypatch):
    """Text mode's payoff: `said` is the tone the learner actually believed.

    The PA path can only ship `tones.SAID_UNKNOWN` (Azure reports accuracy, not a
    produced tone). Typing states the belief outright, so the misconception is
    nameable — 你 is tone 3 and they wrote tone 2.
    """
    monkeypatch.setattr(
        conversation,
        "respond",
        _worker_reply(reading=Utterance(zh="你好", pinyin="nǐ hǎo")),
    )

    resp = await orchestrator.run_text_turn(
        TextTurnRequest(topic_id="greetings", text="ni2hao3")
    )

    assert [e.model_dump() for e in resp.annotation.tone_errors] == [
        {"syllable": "你", "expected": 3, "said": 2, "index": 0}
    ]


async def test_run_text_turn_has_no_tone_errors_without_tone_digits(monkeypatch):
    # Tone digits are optional; typing toneless pinyin is a normal turn, not an
    # error-laden one. The orchestrator must not invent tone errors from nothing.
    monkeypatch.setattr(
        conversation,
        "respond",
        _worker_reply(reading=Utterance(zh="你好", pinyin="nǐ hǎo")),
    )

    resp = await orchestrator.run_text_turn(
        TextTurnRequest(topic_id="greetings", text="nihao")
    )

    assert resp.annotation.tone_errors == []


async def test_run_text_turn_propagates_unknown_topic(monkeypatch):
    async def fake_respond(**kwargs):
        raise AssertionError("worker should not be called for an unknown topic")

    monkeypatch.setattr(conversation, "respond", fake_respond)

    with pytest.raises(kb.KbError):
        await orchestrator.run_text_turn(TextTurnRequest(topic_id="nope", text="你好"))


# --- WS1 Stage 0: per-stage timings + Anthropic usage ----------------------
#
# Text mode has only one timed stage, so what these pin is the relationship
# between `claude_ms` and `total_ms` — the gap is the server's own overhead, and
# it's the comparison that says whether a slow spoken turn is Claude or the
# speech stages. Timings are wall-clock, so the assertions are about *which*
# stages are reported and how they relate, never about a millisecond count.
# The concurrent PA-vs-Claude timings are asserted on the stream.


class _FakeUsage:
    input_tokens = 120
    output_tokens = 90
    cache_read_input_tokens = 4200
    cache_creation_input_tokens = 0


def _worker_reply(annotation=None, reading=None):
    async def fake_respond(*, kb_block, sketch, dialogue, user_text, forgiveness_level,
                           want_reading=True, hint=None, client=None):
        return (
            Utterance(zh="你好！你叫什么名字？", pinyin="nǐ hǎo! nǐ jiào shénme míngzi?"),
            annotation or TurnAnnotation(coherence="on_track", topic_tags=["greetings"]),
            reading or Utterance(zh="你好", pinyin="nǐ hǎo"),
            object(),
        )
    return fake_respond


async def test_run_text_turn_reports_claude_and_total_only(monkeypatch):
    """Text mode has no STT and no PA, so the difference between `claude_ms` and
    `total_ms` is the server's own overhead — the comparison that says whether a
    slow spoken turn is Claude or the speech stages."""

    async def fake_respond(**kwargs):
        return (
            Utterance(zh="你好", pinyin="nǐ hǎo"),
            TurnAnnotation(coherence="on_track"),
            Utterance(zh="你好", pinyin="nǐ hǎo"),
            _FakeUsage(),
        )

    monkeypatch.setattr(conversation, "respond", fake_respond)

    resp = await orchestrator.run_text_turn(
        TextTurnRequest(topic_id="greetings", text="你好")
    )

    assert resp.timings.claude_ms is not None
    assert resp.timings.total_ms >= resp.timings.claude_ms
    assert resp.timings.stt_ms is None and resp.timings.pa_ms is None
    assert resp.usage.cache_read_input_tokens == 4200


async def test_turn_usage_is_none_when_the_worker_returns_no_usage(monkeypatch):
    # The stub workers elsewhere in this suite hand back a bare `object()`; a
    # response with no readable usage must still be a valid turn.
    monkeypatch.setattr(conversation, "respond", _worker_reply())

    resp = await orchestrator.run_text_turn(
        TextTurnRequest(topic_id="greetings", text="你好")
    )

    assert resp.usage.input_tokens is None


# --- M2-B: session start (topic selection, sketch worker, scenario card) ---

_FAKE_SCENARIO = kb.Scenario(
    situation="fake situation", goal="fake goal",
    slots=(kb.Slot(id="s", kind="inform", description="d"),
           kb.Slot(id="r", kind="request", description="d2")),
    max_turns=6,
)


def _fake_topic(topic_id, *, has_scenario=True):
    return kb.Topic(
        id=topic_id, display_name=topic_id, target_vocab=[], proper_names=[],
        related=[], scenario=_FAKE_SCENARIO if has_scenario else None,
    )


async def test_start_session_calls_the_sketch_worker_and_pins_the_scenario_card(
    monkeypatch,
):
    captured = {}

    real_scenario = kb.load_topic("greetings").scenario

    async def fake_generate(topic_id, scenario, *, client=None):
        captured["topic_id"] = topic_id
        captured["scenario"] = scenario
        return SketchResult(
            opening_line=Utterance(zh="你好！你叫什么名字？", pinyin="nǐ hǎo! nǐ jiào shénme míngzi?"),
            sketch="The partner is warm and unhurried.",
        )

    monkeypatch.setattr(sketch_worker, "generate", fake_generate)
    # Narrow the candidate scan to one topic so selection is deterministic —
    # `_pick_scenario_topic` picks uniformly at random over everything on disk,
    # and this test is about what start_session *does with* the topic it got.
    # See the `_pick_scenario_topic` tests below for the selection logic itself.
    monkeypatch.setattr(kb, "list_topic_ids", lambda root=kb.KB_ROOT: ["greetings"])

    resp = await orchestrator.start_session()

    assert isinstance(resp, SessionStartResponse)
    assert resp.topic_id == "greetings"
    assert captured["topic_id"] == "greetings"
    # The already-loaded scenario is passed straight through — `generate` must
    # not have to re-load the topic to get it (a duplicate disk read).
    assert captured["scenario"] == real_scenario
    assert resp.scenario_card.situation == real_scenario.situation
    assert resp.scenario_card.goal == real_scenario.goal
    assert resp.opening_line.zh == "你好！你叫什么名字？"
    assert resp.sketch == "The partner is warm and unhurried."


async def test_start_session_never_shows_slots_on_the_scenario_card(monkeypatch):
    """`ScenarioCard` is `situation` + `goal` only — slots stay server-side
    (`docs/SCENARIOS.md`)."""

    async def fake_generate(topic_id, scenario, *, client=None):
        return SketchResult(
            opening_line=Utterance(zh="你好", pinyin="nǐ hǎo"), sketch="flavour"
        )

    monkeypatch.setattr(sketch_worker, "generate", fake_generate)

    resp = await orchestrator.start_session()

    assert set(type(resp.scenario_card).model_fields) == {"situation", "goal"}


async def test_start_session_does_not_load_the_topic_twice(monkeypatch):
    """The duplicate `kb.load_topic` call this used to make (once here, once
    inside the sketch worker) cost a real disk read + frontmatter parse per
    session start; `generate` now takes the already-loaded scenario instead."""
    calls = []
    real_load_topic = kb.load_topic

    def counting_load_topic(topic_id, root=kb.KB_ROOT):
        calls.append(topic_id)
        return real_load_topic(topic_id, root)

    async def fake_generate(topic_id, scenario, *, client=None):
        return SketchResult(
            opening_line=Utterance(zh="你好", pinyin="nǐ hǎo"), sketch="flavour"
        )

    monkeypatch.setattr(kb, "load_topic", counting_load_topic)
    monkeypatch.setattr(sketch_worker, "generate", fake_generate)
    # One candidate, so the scan loads exactly one topic and any second load
    # would be the duplicate this test guards against.
    monkeypatch.setattr(kb, "list_topic_ids", lambda root=kb.KB_ROOT: ["greetings"])

    await orchestrator.start_session()

    # `_pick_scenario_topic`'s candidate scan is the one and only place that
    # loads a topic: every topic on disk once, and none of them twice. Asserting
    # the *shape* of the scan rather than a literal id list keeps the test about
    # the double-load bug instead of about how many topics the KB happens to
    # ship.
    assert calls == kb.list_topic_ids()
    assert len(calls) == len(set(calls))


# --- `_pick_scenario_topic`: the selection policy itself --------------------


def test_pick_scenario_topic_excludes_topics_with_no_scenario(monkeypatch):
    monkeypatch.setattr(kb, "list_topic_ids", lambda root=kb.KB_ROOT: ["a", "b"])
    monkeypatch.setattr(
        kb, "load_topic",
        lambda tid, root=kb.KB_ROOT: _fake_topic(tid, has_scenario=(tid == "b")),
    )

    topic = orchestrator._pick_scenario_topic()

    assert topic.id == "b"


def test_pick_scenario_topic_chooses_among_all_scenario_topics(monkeypatch):
    """Uniform over every candidate — asserted on what `random.choice` is
    handed, not by sampling repeatedly, so this stays deterministic."""
    captured = {}

    def fake_choice(seq):
        captured["ids"] = sorted(t.id for t in seq)
        return seq[0]

    monkeypatch.setattr(orchestrator.random, "choice", fake_choice)
    monkeypatch.setattr(kb, "list_topic_ids", lambda root=kb.KB_ROOT: ["a", "b", "c"])
    monkeypatch.setattr(
        kb, "load_topic",
        lambda tid, root=kb.KB_ROOT: _fake_topic(tid, has_scenario=(tid != "c")),
    )

    orchestrator._pick_scenario_topic()

    assert captured["ids"] == ["a", "b"]


def test_pick_scenario_topic_raises_when_none_have_a_scenario(monkeypatch):
    monkeypatch.setattr(kb, "list_topic_ids", lambda root=kb.KB_ROOT: ["a"])
    monkeypatch.setattr(
        kb, "load_topic", lambda tid, root=kb.KB_ROOT: _fake_topic(tid, has_scenario=False)
    )

    with pytest.raises(kb.KbError, match="no topic has an authored scenario"):
        orchestrator._pick_scenario_topic()


def test_pick_scenario_topic_raises_when_the_kb_is_empty(monkeypatch):
    monkeypatch.setattr(kb, "list_topic_ids", lambda root=kb.KB_ROOT: [])

    with pytest.raises(kb.KbError):
        orchestrator._pick_scenario_topic()


# --- M2-C: session state on the text turn --------------------------------


def _tracker_worker(monkeypatch, slots_filled=(), learner_closed=False, capture=None):
    """Stub the worker with a fixed tracker result; record the kwargs it got."""

    async def fake_respond(*, kb_block, sketch, dialogue, user_text,
                           forgiveness_level, want_reading=True, hint=None,
                           client=None):
        if capture is not None:
            capture.update(hint=hint, dialogue=dialogue)
        return (
            Utterance(zh="好。", pinyin="hǎo."),
            TurnAnnotation(
                coherence="on_track",
                slots_filled=list(slots_filled),
                learner_closed=learner_closed,
            ),
            Utterance(zh="我叫小明", pinyin="wǒ jiào xiǎo míng"),
            object(),
        )

    monkeypatch.setattr(conversation, "respond", fake_respond)


def _req(dialogue=None, state=None, text="我叫小明"):
    return TextTurnRequest(
        topic_id="greetings",
        text=text,
        dialogue=dialogue or [],
        state=state or SessionState(),
    )


async def test_a_turn_returns_the_advanced_state(monkeypatch):
    _tracker_worker(monkeypatch, slots_filled=["self_name"])
    resp = await orchestrator.run_text_turn(_req())
    assert resp.state.filled_at == {"self_name": 1}
    assert resp.state.status == "active"


async def test_state_accumulates_across_turns(monkeypatch):
    """The server is stateless: progress survives only because it round-trips."""
    _tracker_worker(monkeypatch, slots_filled=["partner_name"])
    resp = await orchestrator.run_text_turn(
        _req(
            dialogue=[{"role": "user", "zh": "我叫小明"}, {"role": "partner", "zh": "你好"}],
            state=SessionState(filled_at={"self_name": 1}),
        )
    )
    assert resp.state.filled_at == {"self_name": 1, "partner_name": 2}


async def test_filling_the_last_slot_completes_the_session(monkeypatch):
    _tracker_worker(monkeypatch, slots_filled=["wellbeing"])
    resp = await orchestrator.run_text_turn(
        _req(state=SessionState(filled_at={"self_name": 1, "partner_name": 2}))
    )
    assert resp.state.status == "complete"
    assert resp.state.goal_met is True
    assert resp.state.end_reason == "goal"


async def test_the_turn_index_comes_from_the_submitted_history(monkeypatch):
    """No server counter — the client's transcript *is* the turn number.

    The opening line is deliberately not part of `dialogue`, so it costs the
    learner nothing (`docs/SCENARIOS.md`, "Definition of a turn").
    """
    _tracker_worker(monkeypatch, slots_filled=["self_name"])
    dialogue = []
    for expected_turn in (1, 2, 3):
        resp = await orchestrator.run_text_turn(_req(dialogue=list(dialogue)))
        assert resp.state.filled_at == {"self_name": expected_turn}
        dialogue += [{"role": "user", "zh": "我叫小明"}, {"role": "partner", "zh": "好。"}]


async def test_the_hint_names_the_outstanding_slot(monkeypatch):
    captured = {}
    _tracker_worker(monkeypatch, capture=captured)
    await orchestrator.run_text_turn(_req(state=SessionState(filled_at={"self_name": 1})))
    assert "partner_name" in captured["hint"]


async def test_no_hint_on_the_first_turn_of_a_fresh_session(monkeypatch):
    """Nothing is established yet, so the first missing slot is simply the goal.

    A hint still goes out — the scene should already be unresolved — but it must
    never be the closing one.
    """
    captured = {}
    _tracker_worker(monkeypatch, capture=captured)
    await orchestrator.run_text_turn(_req())
    assert "final turn" not in (captured["hint"] or "")


async def test_a_topic_without_a_scenario_still_turns(monkeypatch):
    """#29 lands topics before scenarios; those sessions just run unbounded."""
    _tracker_worker(monkeypatch, capture=(captured := {}))
    monkeypatch.setattr(kb, "load_scenario", lambda *a, **k: None)
    resp = await orchestrator.run_text_turn(_req())
    assert resp.state.status == "active"
    assert resp.state.filled_at == {}
    assert captured["hint"] is None


# --- M2-E: the session names its topic (#29) --------------------------------


async def test_start_session_returns_the_topic_display_name(monkeypatch):
    """With more than one topic on disk, `topic_id` is no longer a label.

    The learner is told what they drew, and the client must not have to fetch
    the catalog to find out — it already has the session.
    """
    async def fake_generate(topic_id, scenario, client=None):
        return SketchResult(
            opening_line=Utterance(zh="你好", pinyin="nǐ hǎo"), sketch="S"
        )

    monkeypatch.setattr(sketch_worker, "generate", fake_generate)
    resp = await orchestrator.start_session()
    assert resp.display_name == kb.load_topic(resp.topic_id).display_name
    assert resp.display_name


# --- A1: a caller-supplied topic (#66) --------------------------------------


async def test_start_session_uses_a_given_topic_instead_of_drawing_one(monkeypatch):
    """"Try this again" must land on the same scenario, not a random one."""
    captured = {}

    async def fake_generate(topic_id, scenario, client=None):
        captured["topic_id"] = topic_id
        return SketchResult(
            opening_line=Utterance(zh="你好！", pinyin="nǐ hǎo!"),
            sketch="The partner is warm and unhurried.",
        )

    monkeypatch.setattr(sketch_worker, "generate", fake_generate)

    def explode():
        raise AssertionError("a supplied topic must not go through the draw")

    monkeypatch.setattr(orchestrator, "_pick_scenario_topic", explode)

    resp = await orchestrator.start_session(topic_id="greetings")

    assert resp.topic_id == "greetings"
    assert captured["topic_id"] == "greetings"


async def test_start_session_rejects_a_topic_with_no_scenario(monkeypatch):
    """Straight into the route's existing 404. A topic with no authored
    scenario has nothing to be a session about."""
    scenarioless = dataclasses.replace(kb.load_topic("greetings"), scenario=None)
    monkeypatch.setattr(
        kb, "load_topic", lambda topic_id, root=kb.KB_ROOT: scenarioless
    )

    with pytest.raises(kb.KbError):
        await orchestrator.start_session(topic_id="greetings")
