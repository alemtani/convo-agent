"""M2-D verdict-worker tests — structural invariants, no model wording asserted.

The worker's contract is unusual and it is the point of the design: it is handed
`goal_met` and `missing` and asked only to *explain* them. A judge asked "did
they succeed?" grades generously, and our partner replies and our grader come
from the same model family, so the decision is removed rather than prompted
against (`docs/SCENARIOS.md`, "Runtime: three tiers").

So the assertions here are: the outcome the caller computed survives into the
card unchanged, the model answer stays inside the band the learner can read, and
the failure shapes match the rest of the worker layer. Judgment quality is a
`live` concern, not a unit one.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import anthropic
import httpx
import pytest
from pydantic import ValidationError

from backend import config, kb, prompts
from backend.models import (
    GraderResult,
    DialogueTurn,
    ModelLine,
    SessionState,
    VerdictRequest,
    VerdictResult,
)
from backend.prompts import render_verdict_prompt
from backend.workers import feedback, grader

DIALOGUE = [
    DialogueTurn(role="user", zh="我叫小明"),
    DialogueTurn(role="partner", zh="你好！"),
]


def _recorded(explanation="You said your name, but never asked theirs.", lines=None):
    return VerdictResult(
        explanation=explanation,
        model_exchange=lines
        if lines is not None
        else [
            ModelLine(zh="你叫什么名字？", pinyin="nǐ jiào shénme míngzi?",
                      english="What is your name?"),
            ModelLine(zh="我叫小王。", pinyin="wǒ jiào xiǎo wáng.",
                      english="My name is Xiao Wang."),
            ModelLine(zh="认识你很高兴。", pinyin="rènshi nǐ hěn gāoxìng.",
                      english="Nice to meet you."),
        ],
    )


def _fake_client(parsed_output, *, stop_reason="end_turn"):
    msg = SimpleNamespace(parsed_output=parsed_output, stop_reason=stop_reason)
    parse = AsyncMock(return_value=msg)
    return SimpleNamespace(messages=SimpleNamespace(parse=parse)), parse


def _req(filled_at=None, end_reason="cap", notes=None):
    return VerdictRequest(
        topic_id="greetings",
        dialogue=DIALOGUE,
        state=SessionState(
            filled_at=filled_at if filled_at is not None else {"self_name": 1},
            status="complete",
            end_reason=end_reason,
        ),
        notes=notes or ["3rd tone on 你 flattened toward 2nd"],
    )


# --- The outcome is the server's, not the model's ------------------------


async def test_the_card_reports_the_recomputed_outcome_not_the_clients():
    """A client claiming success with nothing filled must not get a pass.

    This is the whole reason `goal_met` was taken away from the judge and then
    taken away from the client too: it is recomputed here from the KB.
    """
    client, _ = _fake_client(_recorded())
    card = await feedback.verdict(
        _req(filled_at={}).model_copy(
            update={"state": SessionState(filled_at={}, goal_met=True,
                                          status="complete")}
        ),
        client=client,
    )
    assert card.goal_met is False
    assert {m.id for m in card.missing} == {"self_name", "partner_name", "wellbeing"}


async def test_a_completed_goal_is_reported_as_met_with_nothing_missing():
    client, _ = _fake_client(_recorded(lines=[]))
    card = await feedback.verdict(
        _req(
            filled_at={"self_name": 1, "partner_name": 2, "wellbeing": 3},
            end_reason="goal",
        ),
        client=client,
    )
    assert card.goal_met is True
    assert card.missing == []
    assert card.end_reason == "goal"


async def test_missing_slots_carry_their_authored_english_description():
    """The card names what was missed in words the learner can read."""
    client, _ = _fake_client(_recorded())
    card = await feedback.verdict(_req(), client=client)
    descriptions = {m.id: m.description for m in card.missing}
    assert descriptions["partner_name"] == "Find out their name"


async def test_an_unknown_filled_id_cannot_break_the_lookup():
    """Stale client state must not 500 a completed session.

    The id→description lookup runs over authored slots, so an id the scenario
    never had is simply not one of them.
    """
    client, _ = _fake_client(_recorded())
    card = await feedback.verdict(_req(filled_at={"self_name": 1, "stale": 2}),
                                  client=client)
    assert {m.id for m in card.missing} == {"partner_name", "wellbeing"}


# --- `end_reason` is trusted, but only when it is consistent -------------


async def test_an_inconsistent_end_reason_is_dropped():
    """`end_reason` cannot be recomputed — it needs history we don't keep.

    So it is checked rather than trusted blindly: "goal" while slots are still
    missing is not a reason we can repeat back to the learner.
    """
    client, _ = _fake_client(_recorded())
    card = await feedback.verdict(_req(end_reason="goal"), client=client)
    assert card.end_reason is None


async def test_a_consistent_end_reason_survives():
    client, _ = _fake_client(_recorded())
    card = await feedback.verdict(_req(end_reason="closed"), client=client)
    assert card.end_reason == "closed"


# --- The model answer stays inside the band ------------------------------


async def test_the_model_exchange_is_carried_through():
    client, _ = _fake_client(_recorded())
    card = await feedback.verdict(_req(), client=client)
    assert len(card.model_exchange) == 3
    assert card.model_exchange[0].zh == "你叫什么名字？"


def test_every_character_of_the_model_answer_is_in_band():
    """A real assertion, not an eval: checkable against the KB and the ceiling.

    A "what you should have said" the learner cannot read teaches nothing, so
    this is the one thing about the generated Chinese we *can* pin without
    asserting wording. Run over the recorded fixture the way it would run over
    a live one.
    """
    allowed = feedback.in_band_characters("greetings")
    for line in _recorded().model_exchange:
        stray = {ch for ch in line.zh if _is_hanzi(ch)} - allowed
        assert not stray, f"out-of-band characters in the model answer: {stray}"


def _is_hanzi(ch: str) -> bool:
    return "一" <= ch <= "鿿"


def test_in_band_characters_covers_vocab_and_proper_names():
    allowed = feedback.in_band_characters("greetings")
    assert "你" in allowed and "叫" in allowed
    assert "明" in allowed, "proper names in the KB are readable too"


# --- Failure shapes match the rest of the worker layer -------------------


async def test_a_refusal_becomes_a_feedback_error():
    client, _ = _fake_client(_recorded(), stop_reason="refusal")
    with pytest.raises(feedback.FeedbackError):
        await feedback.verdict(_req(), client=client)


async def test_unparseable_output_becomes_a_feedback_error():
    client, _ = _fake_client(None)
    with pytest.raises(feedback.FeedbackError):
        await feedback.verdict(_req(), client=client)


async def test_a_timeout_becomes_a_feedback_error():
    client, parse = _fake_client(_recorded())
    parse.side_effect = anthropic.APITimeoutError(
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    )
    with pytest.raises(feedback.FeedbackError, match="timed out"):
        await feedback.verdict(_req(), client=client)


async def test_the_call_is_bounded_by_the_verdict_timeout():
    """Nothing on the hot path waits for this, but a learner does.

    The card renders in a pending state the moment the session ends, so an
    unbounded call is a spinner with no end — the timeout is what turns that
    into a "try again" card.
    """
    client, parse = _fake_client(_recorded())
    await feedback.verdict(_req(), client=client)
    assert parse.call_args.kwargs["timeout"] == config.VERDICT_TIMEOUT_S


async def test_the_call_is_not_cached():
    """One call per session; a cache write costs 1.25x and break-even is two
    reads (#32's cost note). There is no second read here to have."""
    client, parse = _fake_client(_recorded())
    await feedback.verdict(_req(), client=client)
    sent = parse.call_args.kwargs
    blocks = sent["system"] if isinstance(sent["system"], list) else []
    assert not any("cache_control" in b for b in blocks)


async def test_an_unknown_topic_raises_kb_error():
    client, _ = _fake_client(_recorded())
    with pytest.raises(kb.KbError):
        await feedback.verdict(
            _req().model_copy(update={"topic_id": "no-such-topic"}), client=client
        )


# --- The route -----------------------------------------------------------


from fastapi.testclient import TestClient  # noqa: E402

from backend.main import app  # noqa: E402

route_client = TestClient(app)

_COMPLETE = {
    "filled_at": {"self_name": 1},
    "status": "complete",
    "end_reason": "cap",
}


def _post(body=None, monkeypatch=None, card=None, boom=None):
    if monkeypatch is not None:
        async def fake_verdict(req, client=None):
            if boom is not None:
                raise boom
            return card

        monkeypatch.setattr(feedback, "verdict", fake_verdict)
    return route_client.post(
        "/api/verdict",
        json=body or {"topic_id": "greetings", "dialogue": [], "state": _COMPLETE},
    )


def test_verdict_route_returns_the_card(monkeypatch):
    card = feedback.VerdictCard.model_construct(
        goal_met=False, end_reason="cap", missing=[], explanation="Nearly.",
        model_exchange=[], turns_taken=3,
    )
    resp = _post(monkeypatch=monkeypatch, card=card)
    assert resp.status_code == 200
    assert resp.json()["explanation"] == "Nearly."


def test_verdict_on_an_unfinished_session_is_refused():
    """The unmet card contains the model exchange — the sentence the learner has
    not yet worked out. Serving it mid-session hands over the answer.
    """
    resp = _post({"topic_id": "greetings", "dialogue": [],
                  "state": {"filled_at": {}, "status": "active"}})
    assert resp.status_code == 409


def test_verdict_for_an_unknown_topic_is_404(monkeypatch):
    resp = _post(
        {"topic_id": "nope", "dialogue": [], "state": _COMPLETE},
        monkeypatch=monkeypatch,
        boom=kb.KbError("no such topic"),
    )
    assert resp.status_code == 404


def test_a_worker_failure_is_502(monkeypatch):
    resp = _post(monkeypatch=monkeypatch, boom=feedback.FeedbackError("refused"))
    assert resp.status_code == 502


def test_an_oversized_transcript_is_rejected():
    resp = _post({
        "topic_id": "greetings",
        "dialogue": [{"role": "user", "zh": "你好"}] * 60,
        "state": _COMPLETE,
    })
    assert resp.status_code == 422


# --- The card is readable by the learner it is written for ----------------


async def test_hanzi_in_the_explanation_gets_pinyin():
    """The card quotes the learner's phrases back; they must be able to read it.

    Added server-side rather than asked of the model — the same rule the input
    echo follows, and it cannot be forgotten on a bad generation.
    """
    client, _ = _fake_client(
        _recorded(explanation="You introduced yourself with 我叫小明, nicely done.")
    )
    card = await feedback.verdict(_req(), client=client)
    assert "我叫小明 (wǒ jiào xiǎo míng)" in card.explanation


async def test_an_explanation_the_model_already_glossed_is_left_alone():
    client, _ = _fake_client(
        _recorded(explanation="In 最近 (zuìjìn), the second syllable falls.")
    )
    card = await feedback.verdict(_req(), client=client)
    assert card.explanation == "In 最近 (zuìjìn), the second syllable falls."


async def test_the_model_exchange_is_not_annotated():
    """It already carries its own pinyin line — a second one would be noise."""
    client, _ = _fake_client(_recorded())
    card = await feedback.verdict(_req(), client=client)
    assert card.model_exchange[0].zh == "你叫什么名字？"


# --- Truncation and parse failures are 502s, never 500s -------------------


def test_verdict_runs_on_its_own_model_with_thinking_on(monkeypatch):
    """V2's model split (docs/VALIDITY.md), tested here first.

    The verdict worker is already a judgment role, already one call per
    session, already off the turn path — moving it to Opus 5 needs no
    architecture change. Opus 5 thinks by default, unlike the Sonnet 5 trap
    `conversation.py` documents (and this worker's own prior history with
    thinking disabled), so `max_tokens` must cover thinking *plus* output —
    the earlier 2048-token budget was sized for output alone.
    """
    monkeypatch.setattr(config, "VERDICT_MODEL", "claude-opus-5")
    monkeypatch.setattr(config, "VERDICT_EFFORT", "high")
    req = feedback.build_request(kb_block="KB", dialogue=[], prompt="P")
    assert req["model"] == "claude-opus-5"
    assert req["thinking"] == {"type": "adaptive"}
    assert req["output_config"] == {"effort": "high"}
    assert req["max_tokens"] >= 4096


async def test_a_truncated_response_becomes_a_feedback_error():
    """The SDK raises `ValidationError` from inside `messages.parse`.

    Uncaught it is a 500 and the card reads "Internal Server Error"; as a
    `FeedbackError` it is a 502 the client already degrades gracefully from,
    with a Try again button.
    """
    client, parse = _fake_client(_recorded())
    # The real thing: what the SDK raises when it validates a cut-off body.
    try:
        VerdictResult.model_validate_json('{"explanation":"You did ')
    except ValidationError as truncated:
        parse.side_effect = truncated

    with pytest.raises(feedback.FeedbackError, match="unparseable"):
        await feedback.verdict(_req(), client=client)


async def test_hitting_the_token_cap_becomes_a_feedback_error():
    client, _ = _fake_client(None, stop_reason="max_tokens")
    with pytest.raises(feedback.FeedbackError, match="too long"):
        await feedback.verdict(_req(), client=client)


# --- A1: the learner's own exit (#66) ---------------------------------------


async def test_stuck_survives_the_consistency_check():
    """It is a claim about the learner, not about the transcript.

    `goal` and `cap` have implications the server can check and find false.
    "I stopped because I was stuck" has none — the server keeps nothing that
    could contradict it — so it passes through the way `closed` does.
    """
    client, _ = _fake_client(_recorded())
    card = await feedback.verdict(_req(end_reason="stuck"), client=client)
    assert card.end_reason == "stuck"


async def test_the_stuck_brief_forbids_the_told_you_so():
    """The failure mode is the neighbouring `closed` copy's tone.

    `closed` is deliberately gently corrective — the learner left early and the
    card says so. A model reading `stuck` the same way would tell someone who
    asked for help that they should have pushed on, which is the reading A1
    exists to prevent.
    """
    prompt = prompts.render_verdict_prompt(
        goal_met=False, missing=[], turns_taken=4, end_reason="stuck"
    )
    assert "stopped and asked for feedback" in prompt
    assert "Do not treat it as giving up" in prompt
    # And the neighbours are untouched.
    closed = prompts.render_verdict_prompt(
        goal_met=False, missing=[], turns_taken=4, end_reason="closed"
    )
    assert "said goodbye twice" in closed


# --- V2: settling a debt before the card ----------------------------------


def _dialogue(pairs=2):
    turns = []
    for _ in range(pairs):
        turns.append(DialogueTurn(role="user", zh="我叫小明"))
        turns.append(DialogueTurn(role="partner", zh="你好"))
    return turns


async def test_a_session_with_no_debt_spends_no_grader_call(monkeypatch):
    """Every healthy session takes this path, so it must cost nothing."""
    called = False

    async def never(**kwargs):
        nonlocal called
        called = True
        raise AssertionError("graded a session that owed nothing")

    monkeypatch.setattr(grader, "grade", never)
    req = VerdictRequest(
        topic_id="greetings",
        dialogue=_dialogue(2),
        state=SessionState(status="complete", last_graded_turn=2),
    )
    state = await feedback.settle_outstanding_grades(
        req, scenario=kb.load_scenario("greetings")
    )
    assert state is req.state
    assert called is False


async def test_a_client_reporting_no_watermark_is_not_in_debt(monkeypatch):
    """`None` says nothing about its grades. Reading it as `0` would fire a
    recovery pass on every session an older client ever finished."""
    async def never(**kwargs):
        raise AssertionError("graded a session that reported no watermark")

    monkeypatch.setattr(grader, "grade", never)
    req = VerdictRequest(
        topic_id="greetings", dialogue=_dialogue(3),
        state=SessionState(status="complete"),
    )
    assert req.state.last_graded_turn is None
    state = await feedback.settle_outstanding_grades(
        req, scenario=kb.load_scenario("greetings")
    )
    assert state is req.state


async def test_an_outstanding_debt_is_settled_before_the_card(monkeypatch):
    """The card is computed from state, so an unsettled debt tells the learner
    they missed something they established — at the moment it is most visible."""
    async def late(**kwargs):
        assert kwargs["window"] == 2
        return (
            GraderResult(
                coherence="on_track",
                slots_filled=["wellbeing"],
                slots_filled_previously=["self_name", "partner_name"],
            ),
            None,
        )

    monkeypatch.setattr(grader, "grade", late)
    req = VerdictRequest(
        topic_id="greetings", dialogue=_dialogue(3),
        state=SessionState(status="complete", last_graded_turn=1),
    )
    state = await feedback.settle_outstanding_grades(
        req, scenario=kb.load_scenario("greetings")
    )
    assert set(state.filled_at) == {"self_name", "partner_name", "wellbeing"}


async def test_a_failed_final_pass_leaves_the_state_alone(monkeypatch):
    """A broken grader at the end of a broken session is not a reason to invent
    a grade."""
    async def boom(**kwargs):
        raise grader.GraderError("still down")

    monkeypatch.setattr(grader, "grade", boom)
    req = VerdictRequest(
        topic_id="greetings", dialogue=_dialogue(3),
        state=SessionState(status="complete", last_graded_turn=1),
    )
    state = await feedback.settle_outstanding_grades(
        req, scenario=kb.load_scenario("greetings")
    )
    assert state is req.state


def test_a_late_pass_that_completes_the_goal_supersedes_the_end_reason():
    """A learner who established everything did not leave unfinished, whatever
    button they pressed. `stuck` least of all (A1)."""
    reason = feedback._consistent_end_reason(
        SessionState(status="complete", end_reason="stuck"),
        scenario=kb.load_scenario("greetings"),
        missing=[],
        turns_taken=3,
    )
    assert reason == "goal"


async def test_the_recovery_pass_hands_the_grader_a_live_turns_shape(monkeypatch):
    """At verdict time `dialogue` holds everything, including the partner's
    final reply — but a live turn hands the grader the history *up to* the
    learner's turn plus that turn separately. Splitting at the end instead of at
    the last `user` entry showed the learner's turn twice, after a partner line
    it actually preceded."""
    captured = {}

    async def capture(**kwargs):
        captured.update(kwargs)
        return GraderResult(coherence="on_track"), None

    monkeypatch.setattr(grader, "grade", capture)
    dialogue = [
        DialogueTurn(role="user", zh="u1"),
        DialogueTurn(role="partner", zh="p1"),
        DialogueTurn(role="user", zh="u2"),
        DialogueTurn(role="partner", zh="p2"),
    ]
    req = VerdictRequest(
        topic_id="greetings", dialogue=dialogue,
        state=SessionState(status="complete", last_graded_turn=0),
    )
    await feedback.settle_outstanding_grades(
        req, scenario=kb.load_scenario("greetings")
    )

    assert [t.zh for t in captured["dialogue"]] == ["u1", "p1"]
    assert captured["user_text"] == "u2"
    # The partner's final reply is not shown, exactly as on a live turn.
    assert "p2" not in [t.zh for t in captured["dialogue"]]


async def test_the_recovery_pass_does_not_rewrite_how_the_session_ended(monkeypatch):
    """The session is over. `termination.advance` recomputes `status` and
    `end_reason` from scratch, so running it here would overwrite the real
    ending — `stuck`, `closed`, `ungraded` — with a fresh evaluation of a
    finished session. Only the credit is new."""
    async def late(**kwargs):
        return GraderResult(coherence="on_track", slots_filled=["self_name"]), None

    monkeypatch.setattr(grader, "grade", late)
    req = VerdictRequest(
        topic_id="greetings", dialogue=_dialogue(3),
        state=SessionState(
            status="complete", end_reason="stuck", last_graded_turn=1
        ),
    )
    state = await feedback.settle_outstanding_grades(
        req, scenario=kb.load_scenario("greetings")
    )

    assert state.end_reason == "stuck"
    assert state.status == "complete"
    assert "self_name" in state.filled_at


def test_a_card_over_unchecked_turns_never_blames_the_learner():
    """A turn nobody graded is not a turn the learner failed — and unlike a live
    turn there is no next one to correct it."""
    prompt = render_verdict_prompt(
        goal_met=False, missing=[], turns_taken=4, unchecked_turns=2
    )
    assert "could not be checked" in prompt
    assert "our fault and not theirs" in prompt


def test_a_healthy_card_says_nothing_about_grading():
    prompt = render_verdict_prompt(goal_met=True, missing=[], turns_taken=4)
    assert "could not be checked" not in prompt
