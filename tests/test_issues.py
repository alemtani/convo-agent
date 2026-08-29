"""A7: feedback intake — the issue body, the refusals, and the rate limit.

Nothing here is a model call. Filing a bug and contesting a grade are the same
mechanism, so they are tested as one: a body built from client-held session
state, a GitHub client that is mocked at the HTTP boundary, and three refusals
that have to happen *before* anything reaches a public repo.

The body assertions are about the fenced JSON block, not the prose. That block
is the deliverable — a contested grade is a labelled disagreement, and it is
worth filing only if it can become an eval case without being retyped — so it
is asserted against the shape `evals/coherence/cases.py` actually loads.
"""
import json

import httpx
import pytest

from backend import issues, kb
from backend.models import DialogueTurn, FeedbackRequest, SessionState, Utterance

SCENARIO = kb.load_scenario("greetings")

DIALOGUE = [
    DialogueTurn(role="user", zh="你好，我叫小明。"),
    DialogueTurn(role="partner", zh="我叫小王。你最近怎么样？"),
    DialogueTurn(role="user", zh="我很好，你呢？"),
    DialogueTurn(role="partner", zh="我也很好。"),
]


def _request(**overrides) -> FeedbackRequest:
    fields = dict(
        kind="bug",
        topic_id="greetings",
        message="the counter never moved",
        sketch="The classmate is cheerful and unhurried.",
        opening_line=Utterance(zh="你好！", pinyin="nǐ hǎo!"),
        dialogue=DIALOGUE,
        state=SessionState(filled_at={"self_name": 1}, last_graded_turn=2),
    )
    fields.update(overrides)
    return FeedbackRequest(**fields)


def _fenced_json(body: str) -> dict:
    """Pull the one ```json block back out of the issue body."""
    _, _, rest = body.partition("```json\n")
    payload, _, _ = rest.partition("\n```")
    assert payload, "the issue body carries no fenced JSON block"
    return json.loads(payload)


# --- The body ---------------------------------------------------------------


def test_bug_body_carries_the_whole_session():
    """A bug report is the session, so the block is the session."""
    title, body = issues.render_issue(_request(), SCENARIO)

    assert "[bug]" in title
    assert "greetings" in title
    # The learner's own words, above the machine-readable half.
    assert "the counter never moved" in body
    assert body.index("the counter never moved") < body.index("```json")

    payload = _fenced_json(body)
    assert payload["kind"] == "bug"
    assert payload["topic_id"] == "greetings"
    assert payload["dialogue"] == [
        {"role": "user", "zh": "你好，我叫小明。"},
        {"role": "partner", "zh": "我叫小王。你最近怎么样？"},
        {"role": "user", "zh": "我很好，你呢？"},
        {"role": "partner", "zh": "我也很好。"},
    ]
    assert payload["state"]["filled_at"] == {"self_name": 1}
    # No claim: nobody contested anything.
    assert "claim" not in payload


def test_contest_body_is_an_eval_case_at_the_contested_turn():
    """The block replays as a case: history *before* the turn, the turn, the
    state as it stood — the shape `tests/fixtures/sessions/*.json` uses.

    Slicing is the whole point. A case carrying the turns that came *after* the
    disputed one would hand the grader the future when it is asked to re-judge
    the past.
    """
    request = _request(kind="contest", turn=2, slot_id="wellbeing",
                       message="你呢 asks it back — that filled it")
    _, body = issues.render_issue(request, SCENARIO)
    payload = _fenced_json(body)

    assert payload["kind"] == "contest"
    assert payload["learner_turn"] == "我很好，你呢？"
    assert payload["dialogue"] == [
        {"role": "user", "zh": "你好，我叫小明。"},
        {"role": "partner", "zh": "我叫小王。你最近怎么样？"},
    ]
    # `self_name` landed on turn 1, so it was already filled when turn 2 was
    # graded. A slot filled *at* the contested turn would not be.
    assert payload["state"]["filled_at"] == {"self_name": 1}
    assert payload["sketch"] == "The classmate is cheerful and unhurried."
    assert payload["opening_line"] == {"zh": "你好！", "pinyin": "nǐ hǎo!"}
    assert payload["id"] == "contest-greetings-t2"

    # The learner's claim, kept apart from the label. `gold.json` is written by
    # whoever reads the case; a learner is a party to the disagreement, so what
    # they assert is evidence, not a label.
    assert payload["claim"] == {
        "slots_established": ["wellbeing"],
        "rationale": "你呢 asks it back — that filled it",
    }
    assert "coherence" not in payload["claim"]


def test_contest_body_survives_a_slot_the_learner_could_not_name():
    """Mid-session the learner has no slot ids — the turn alone is the claim."""
    request = _request(kind="contest", turn=1, message="I said my name")
    _, body = issues.render_issue(request, SCENARIO)
    payload = _fenced_json(body)

    assert payload["learner_turn"] == "你好，我叫小明。"
    assert payload["dialogue"] == []
    assert payload["state"]["filled_at"] == {}
    assert payload["claim"]["slots_established"] == []


def test_title_names_the_contested_turn():
    title, _ = issues.render_issue(_request(kind="contest", turn=2), SCENARIO)
    assert "[contest]" in title
    assert "turn 2" in title


# --- The refusals -----------------------------------------------------------


def test_oversized_transcript_is_refused():
    """A public repo is not a paste bin.

    The cap is on characters, not turns: `max_length` on the list already bounds
    the count, and 40 turns of a megabyte each would pass it.
    """
    with pytest.raises(ValueError):
        _request(dialogue=[DialogueTurn(role="user", zh="你" * 40_000)])


def test_too_many_turns_is_refused():
    long_session = [DialogueTurn(role="user", zh="你好")] * 80
    with pytest.raises(ValueError):
        _request(dialogue=long_session)


def test_empty_message_is_refused():
    """The prose *is* the report. Without it there is nothing to act on."""
    with pytest.raises(ValueError):
        _request(message="   ")


def test_contest_without_a_turn_is_refused():
    """A contest is scoped to one turn by construction."""
    with pytest.raises(ValueError):
        _request(kind="contest", turn=None)


def test_contest_naming_an_unknown_slot_is_refused():
    request = _request(kind="contest", turn=2, slot_id="not-a-slot")
    with pytest.raises(issues.InvalidContest):
        issues.check_contest(request, SCENARIO)


def test_contest_past_the_end_of_the_transcript_is_refused():
    request = _request(kind="contest", turn=9)
    with pytest.raises(issues.InvalidContest):
        issues.check_contest(request, SCENARIO)


def test_contest_against_a_topic_with_no_scenario_is_refused():
    """No scenario, no slots — nothing to have graded wrong."""
    request = _request(kind="contest", turn=1, slot_id="wellbeing")
    with pytest.raises(issues.InvalidContest):
        issues.check_contest(request, None)


def test_a_bug_needs_no_scenario():
    """A bug report about a topic that never got a scenario is still a report."""
    issues.check_contest(_request(), None)


# --- The rate limit ---------------------------------------------------------


def test_rate_limit_refuses_past_the_window():
    window = issues.Window(limit=2, seconds=60)
    assert window.take(now=1000.0)
    assert window.take(now=1001.0)
    assert not window.take(now=1002.0)


def test_rate_limit_recovers_when_the_window_rolls_off():
    window = issues.Window(limit=1, seconds=60)
    assert window.take(now=1000.0)
    assert not window.take(now=1030.0)
    assert window.take(now=1061.0)


def test_rate_limit_reports_when_to_come_back():
    window = issues.Window(limit=1, seconds=60)
    window.take(now=1000.0)
    assert window.retry_after(now=1020.0) == 40


# --- The GitHub call --------------------------------------------------------


def _mock_github(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_create_issue_builds_the_request_github_expects(monkeypatch):
    monkeypatch.setattr(issues.config, "GITHUB_ISSUE_TOKEN", "ghp_secret")
    monkeypatch.setattr(issues.config, "GITHUB_ISSUE_REPO", "alemtani/convo-agent")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["accept"] = request.headers.get("accept")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            201,
            json={"html_url": "https://github.com/alemtani/convo-agent/issues/7",
                  "number": 7},
        )

    async with _mock_github(handler) as client:
        result = await issues.create_issue("t", "b", labels=("feedback",), client=client)

    assert seen["url"] == "https://api.github.com/repos/alemtani/convo-agent/issues"
    assert seen["auth"] == "Bearer ghp_secret"
    assert seen["accept"] == "application/vnd.github+json"
    assert seen["body"] == {"title": "t", "body": "b", "labels": ["feedback"]}
    assert result.number == 7
    assert result.url.endswith("/issues/7")


async def test_create_issue_without_a_token_never_calls_out(monkeypatch):
    monkeypatch.setattr(issues.config, "GITHUB_ISSUE_TOKEN", "")

    def handler(request):                              # pragma: no cover
        raise AssertionError("called GitHub with no token")

    async with _mock_github(handler) as client:
        with pytest.raises(issues.NotConfigured):
            await issues.create_issue("t", "b", client=client)


async def test_github_failure_is_reported_without_the_token(monkeypatch):
    """A 401 from GitHub must not echo the credential into a client-facing
    detail. The route surfaces `str(exc)`, so this is the last place to check.
    """
    monkeypatch.setattr(issues.config, "GITHUB_ISSUE_TOKEN", "ghp_secret")
    monkeypatch.setattr(issues.config, "GITHUB_ISSUE_REPO", "alemtani/convo-agent")

    def handler(request):
        return httpx.Response(401, json={"message": "Bad credentials"})

    async with _mock_github(handler) as client:
        with pytest.raises(issues.IssueError) as exc:
            await issues.create_issue("t", "b", client=client)

    assert "ghp_secret" not in str(exc.value)
    assert "401" in str(exc.value)
