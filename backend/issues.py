"""File a learner's report as a GitHub issue (A7, `docs/streams/grading.md`).

Two affordances, one mechanism. **File a bug** sends the session and a sentence
about what went wrong. **Contest a grade** is the same POST scoped to one turn:
which turn, which slot the learner believes they filled, and why. They share a
route, a body, and a rate limit, because the only difference between them is how
much of the session the report is about.

Why this is in Stream A rather than in a general feedback backlog: the output is
an **eval case**. A filed bug is handled by writing the failing case, fixing it,
and closing the issue; a contested grade is a *labelled disagreement*, which is
the highest-value thing that can land in `gold.json`. So the issue body is built
to be replayed, not merely read — a fenced JSON block in the shape
`evals/coherence/cases.py` loads, under the learner's own prose. Whoever picks
the issue up copies the block into `tests/fixtures/sessions/<id>.json` and writes
the gold label; nothing is retyped, which is the difference between a report that
becomes a test and one that becomes a to-do.

The learner's claim is kept *out* of the label. They are a party to the
disagreement, so what they assert is evidence — `claim`, beside the case — and
`gold.json` is still written by whoever reads it. That is the same separation
`evals/coherence/cases.py` documents for its own labels.

Two things are load-bearing about the boundary:

- **The token never reaches the client.** It lives in the environment beside the
  Anthropic and Azure keys, and the client learns only the issue URL it got.
- **The rate limit is part of the feature.** With `APP_PASSCODE` unset this
  endpoint is unauthenticated *and* it writes to a public repo. A limit added
  later would be a limit added after the first spam run.
"""
import json
import logging
import time
from collections import deque
from typing import Deque, Optional, Sequence, Tuple

import httpx

from backend import config
from backend.kb import Scenario
from backend.models import FeedbackRequest, FeedbackResponse, SessionState

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"


class IssueError(Exception):
    """The report could not be filed. The learner's session is unaffected."""


class NotConfigured(IssueError):
    """No token or no repo — the feature is off on this deploy."""


class RateLimited(IssueError):
    """The window is spent. Carries the seconds until it is not."""

    def __init__(self, retry_after: int):
        super().__init__(
            f"too many reports for now — try again in {retry_after} seconds"
        )
        self.retry_after = retry_after


class InvalidContest(IssueError):
    """A contest that names a turn or a slot this scenario does not have."""


class Window:
    """A fixed-window counter over the last `seconds`.

    Deliberately in-process and global rather than per-IP. The resource being
    protected is a public issue tracker, and a per-IP budget is one proxy away
    from unlimited; a single-user practice app has no reason to let *anyone*
    file five reports an hour. In-process is honest at one replica, which is what
    this deploy is — and it fails closed on restart only in the direction that
    costs nothing.
    """

    def __init__(self, limit: int, seconds: float):
        self.limit = limit
        self.seconds = seconds
        self._hits: Deque[float] = deque()

    def _evict(self, now: float) -> None:
        while self._hits and now - self._hits[0] >= self.seconds:
            self._hits.popleft()

    def take(self, now: Optional[float] = None) -> bool:
        """Record one use and say whether it was allowed."""
        now = time.monotonic() if now is None else now
        self._evict(now)
        if len(self._hits) >= self.limit:
            return False
        self._hits.append(now)
        return True

    def retry_after(self, now: Optional[float] = None) -> int:
        """Whole seconds until the oldest hit rolls off. At least 1."""
        now = time.monotonic() if now is None else now
        self._evict(now)
        if not self._hits:
            return 0
        return max(1, int(self.seconds - (now - self._hits[0])))


_limiter: Optional[Window] = None


def limiter() -> Window:
    """The process-wide window, built on first use.

    Built lazily rather than at import so the configured limit is read after
    `.env` is loaded and after any test has moved it.
    """
    global _limiter
    if _limiter is None:
        _limiter = Window(config.FEEDBACK_RATE_LIMIT, config.FEEDBACK_RATE_WINDOW_S)
    return _limiter


def reset_limiter() -> None:
    """Drop the window. For tests, and for nothing else."""
    global _limiter
    _limiter = None


def is_configured() -> bool:
    """True when a token *and* a destination repo are set."""
    return bool(config.GITHUB_ISSUE_TOKEN and config.GITHUB_ISSUE_REPO)


# --- What a report is about -------------------------------------------------


def learner_turn_indices(dialogue: Sequence) -> list:
    """Positions of the learner's own turns, in order.

    Counted by role rather than assuming `user, partner, user, …`. The client
    does append them in pairs today, but a transcript that ends on a turn whose
    reply never arrived is exactly the kind of session someone files a report
    about, and arithmetic that assumes the pair would point at the wrong turn.
    """
    return [i for i, turn in enumerate(dialogue) if turn.role == "user"]


def check_contest(req: FeedbackRequest, scenario: Optional[Scenario]) -> None:
    """Refuse a contest that does not point at anything.

    A bug report passes unconditionally: a topic that landed before its scenario
    did (#29) can still be reported broken.

    A contest cannot. It says a specific turn deserved credit for a specific
    slot, and both halves are checkable here — so a nonsense claim is refused
    while the learner is still looking at the screen, rather than filed into a
    public repo for a maintainer to discover.
    """
    if req.kind != "contest":
        return
    if scenario is None:
        raise InvalidContest(
            f"{req.topic_id} has no scenario, so it has no grade to contest"
        )
    turns = learner_turn_indices(req.dialogue)
    if not 1 <= (req.turn or 0) <= len(turns):
        raise InvalidContest(
            f"turn {req.turn} is not in this session — it has {len(turns)} "
            "learner turns"
        )
    if req.slot_id and req.slot_id not in {slot.id for slot in scenario.slots}:
        raise InvalidContest(f"{req.topic_id} has no slot {req.slot_id!r}")


def state_at_turn(state: SessionState, turn: int) -> dict:
    """Rebuild the state as it stood when `turn` was graded.

    `filled_at` maps a slot to the turn that established it, so the state a turn
    was graded under is every slot filled *before* it. The slot under dispute is
    excluded by construction, which is the point: a case that hands the grader
    the credit it is being asked to award has already answered the question.

    `consecutive_closes` is not reconstructible from `filled_at` and is reported
    as 0 rather than guessed — the cases in `tests/fixtures/sessions/` carry it
    the same way.
    """
    return {
        "filled_at": {
            slot: at for slot, at in state.filled_at.items() if at < turn
        },
        "consecutive_closes": 0,
    }


def case_payload(req: FeedbackRequest, scenario: Optional[Scenario]) -> dict:
    """The machine-readable half of the issue.

    For a contest this is a replayable case in `evals/coherence/cases.py`'s
    shape: the history *before* the disputed turn, the turn itself, and the
    state it was graded under. Slicing matters — a case carrying the turns that
    came after would hand the grader the future when it is asked to re-judge the
    past, and it would pass for the wrong reason.

    For a bug it is the whole session, unsliced, because the report is about the
    session rather than about one grade.
    """
    dialogue = [{"role": t.role, "zh": t.zh} for t in req.dialogue]
    payload = {
        "kind": req.kind,
        "topic_id": req.topic_id,
        "sketch": req.sketch,
        "opening_line": (
            req.opening_line.model_dump() if req.opening_line else None
        ),
        "notes": req.message,
    }
    if req.kind != "contest" or req.turn is None:
        payload["dialogue"] = dialogue
        payload["state"] = req.state.model_dump(mode="json")
        return payload

    index = learner_turn_indices(req.dialogue)[req.turn - 1]
    payload["id"] = f"contest-{req.topic_id}-t{req.turn}"
    payload["dialogue"] = dialogue[:index]
    payload["learner_turn"] = req.dialogue[index].zh
    payload["state"] = state_at_turn(req.state, req.turn)
    payload["contested_turn"] = req.turn
    # The learner's assertion, not a label. `coherence` is absent on purpose:
    # whether the turn followed from the partner's line is the reader's call,
    # and a `gold.json` entry pre-filled by one side of a disagreement would
    # manufacture the consent this split exists to withhold.
    payload["claim"] = {
        "slots_established": [req.slot_id] if req.slot_id else [],
        "rationale": req.message,
    }
    return payload


def render_issue(
    req: FeedbackRequest, scenario: Optional[Scenario]
) -> Tuple[str, str]:
    """Title and body. Prose on top, the case underneath.

    Order is deliberate. A person opening the issue should read what the learner
    said first; the JSON is for whoever picks it up, and it is folded away so it
    does not bury the sentence that explains why it exists.
    """
    scope = f"turn {req.turn}" if req.kind == "contest" and req.turn else "session"
    title = f"[{req.kind}] {req.topic_id}: {scope} — {_headline(req.message)}"

    lines = [
        f"_Filed from the app ({req.kind}). {req.topic_id}, {scope}._",
        "",
        "### What went wrong",
        "",
        req.message,
        "",
    ]
    if req.kind == "contest":
        claimed = f"`{req.slot_id}`" if req.slot_id else "_not named_"
        lines += [
            "### The contested grade",
            "",
            f"- Turn: **{req.turn}**",
            f"- Slot the learner claims they filled: {claimed}",
            f"- Slots this scenario has: "
            + (", ".join(f"`{s.id}`" for s in scenario.slots) if scenario else "—"),
            "",
        ]
    lines += [
        _transcript_block(req),
        "",
        "### Case",
        "",
        "Drop this into `tests/fixtures/sessions/<id>.json` and label it in "
        "`gold.json`. The learner's `claim` is evidence, not the label — the "
        "labeller is not a party to the disagreement "
        "(`evals/coherence/cases.py`).",
        "",
        "```json",
        json.dumps(case_payload(req, scenario), ensure_ascii=False, indent=2),
        "```",
    ]
    return title, "\n".join(lines)


def _headline(message: str) -> str:
    """First line of the report, short enough to be a title."""
    first = message.strip().splitlines()[0]
    return first if len(first) <= 60 else first[:57].rstrip() + "…"


def _transcript_block(req: FeedbackRequest) -> str:
    """The conversation, readable, with the learner's turns numbered.

    Numbered because a contest names a turn, and an issue that says "turn 2"
    over an unnumbered transcript makes the reader count.
    """
    lines = ["<details><summary>Transcript</summary>", ""]
    if req.opening_line:
        lines.append(f"- _partner (opening)_: {req.opening_line.zh}")
    learner = 0
    for entry in req.dialogue:
        if entry.role == "user":
            learner += 1
            lines.append(f"- **learner (turn {learner})**: {entry.zh}")
        else:
            lines.append(f"- _partner_: {entry.zh}")
    lines += [
        "",
        f"Slots filled: `{json.dumps(req.state.filled_at, ensure_ascii=False)}` · "
        f"status: `{req.state.status}` · end_reason: `{req.state.end_reason}`",
        "",
        "</details>",
    ]
    return "\n".join(lines)


# --- The GitHub boundary ----------------------------------------------------


async def create_issue(
    title: str,
    body: str,
    *,
    labels: Sequence[str] = (),
    client: Optional[httpx.AsyncClient] = None,
) -> FeedbackResponse:
    """POST one issue. Raises `IssueError` for anything that isn't a 201.

    The token is read here and nowhere else, and no failure message repeats it:
    the route hands `str(exc)` to the client, so this is the last place that can
    keep a credential out of a browser.
    """
    if not is_configured():
        raise NotConfigured(
            "feedback intake is not configured on this server "
            "(GITHUB_ISSUE_TOKEN / GITHUB_ISSUE_REPO)"
        )
    url = f"{GITHUB_API}/repos/{config.GITHUB_ISSUE_REPO}/issues"
    payload = {"title": title, "body": body, "labels": list(labels)}
    headers = {
        "Authorization": f"Bearer {config.GITHUB_ISSUE_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    owned = client is None
    client = client or httpx.AsyncClient(timeout=config.GITHUB_TIMEOUT_S)
    try:
        response = await client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        # `repr` of an httpx error can carry the request URL but never a header,
        # so this is safe to surface — and a learner who just typed a paragraph
        # deserves to know it did not land.
        raise IssueError(f"could not reach GitHub: {exc}") from exc
    finally:
        if owned:
            await client.aclose()

    if response.status_code != 201:
        # The status, not the body: GitHub's error bodies are safe today, but
        # this string is rendered in the client and the token is one field away.
        raise IssueError(f"GitHub refused the report ({response.status_code})")
    data = response.json()
    return FeedbackResponse(url=data["html_url"], number=data["number"])


async def file_report(
    req: FeedbackRequest,
    scenario: Optional[Scenario],
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> FeedbackResponse:
    """Validate, rate-limit, render, file. The whole route, minus HTTP.

    Order matters: the contest is checked before the window is spent, so a
    learner who names the wrong slot does not burn a slot in their own budget.
    """
    check_contest(req, scenario)
    if not is_configured():
        raise NotConfigured(
            "feedback intake is not configured on this server "
            "(GITHUB_ISSUE_TOKEN / GITHUB_ISSUE_REPO)"
        )
    window = limiter()
    if not window.take():
        raise RateLimited(window.retry_after())
    title, body = render_issue(req, scenario)
    result = await create_issue(
        title, body, labels=config.GITHUB_ISSUE_LABELS, client=client
    )
    logger.info("filed %s report as issue #%s", req.kind, result.number)
    return result
