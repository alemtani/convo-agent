"""Pydantic models for the turn contract.

Phase 1 needs only the response shape: the user's transcribed speech and the
partner's reply, each rendered as a 汉字 + pinyin line. Both sides share one
`Utterance` model — the pair is the recurring unit across the design (DESIGN.md's
`partner_response` and every KB dialogue line), and a beginner reads pinyin for
their own words as much as the partner's. In Phase 3 the partner reply is produced
by the conversation worker and a separate `turn_annotation` is added alongside
these fields (not nested inside the utterance).
"""
from typing import Dict, List, Literal, Optional, Set

from pydantic import BaseModel, Field, PositiveInt, field_validator


class Utterance(BaseModel):
    """One line of dialogue: Chinese characters and their pinyin reading."""

    zh: str
    pinyin: str


class SyllableScore(BaseModel):
    """Per-syllable pronunciation score from Azure PA.

    Azure assesses zh-CN at the *grapheme* level — it leaves the romanized
    `syllable` field empty and reports the hanzi it scored, so `hanzi` carries
    that grapheme (one or more characters) and `pinyin` is its reading derived
    locally via `pinyin.to_pinyin`. `accuracy` is Azure's 0–100 accuracy, which
    for Mandarin folds in tone correctness.
    """

    hanzi: str
    pinyin: str
    accuracy: float


class PronunciationScore(BaseModel):
    """Azure Pronunciation Assessment of the user's spoken turn (two-pass).

    `overall` is Azure's utterance-level accuracy score; `syllables` breaks it
    down so the UI can color each character by how well it was pronounced.
    """

    overall: float
    syllables: List[SyllableScore]


class TurnTimings(BaseModel):
    """Per-stage wall-clock cost of one turn, in milliseconds (WS1 Stage 0).

    Every stage is optional because a stage that didn't run — or hasn't finished
    yet — must report nothing rather than zero: PA drops off a degraded turn,
    STT/Claude are absent from a text turn, and on a staged turn the table is
    still filling in when the early events flush.

    `total_ms` is measured across the whole orchestrator call, so it is *not* the
    sum of the parts — the gap is the un-instrumented work. It is only ever set
    on a turn that has finished (the `done` event, or a collected text turn);
    mid-turn events leave it `None` rather than reporting a total of a turn still
    in progress. Their arrival time is `TurnEvent.elapsed_ms`.

    Reported back to the client (and to the replay harness) rather than only
    logged, so both sides quote the same numbers.
    """

    stt_ms: Optional[float] = None
    pa_ms: Optional[float] = None
    claude_ms: Optional[float] = None
    grader_ms: Optional[float] = None
    total_ms: Optional[float] = None

    @classmethod
    def from_stages(cls, stages: dict) -> "TurnTimings":
        """Build from `timing.Timer.as_dict()` — stage names to `*_ms` fields."""
        return cls(**{f"{name}_ms": value for name, value in stages.items()})


class TurnUsage(BaseModel):
    """The Anthropic `usage` block for the turn's conversation call.

    The worker has always returned this and the orchestrator threw it away, so
    `cache_read_input_tokens` — the one number that says whether the frozen
    prefix is actually being reused — was invisible outside the live test. Every
    field is optional: the SDK omits the cache counters on some responses, and
    reading usage must never be able to fail a turn.

    `grader` is the second call the turn now buys (V2). It is reported
    separately rather than summed, because the two run on **different models at
    different prices** — Sonnet 5 for the reply, Opus 5 at `effort: high` with
    thinking on for the judgment — and a single token count across both would
    describe a price that nothing charges. Reporting only the converser's, as
    this did at first, hides the more expensive half on the branch whose cost was
    the whole reason for a separate model.
    """

    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cache_read_input_tokens: Optional[int] = None
    cache_creation_input_tokens: Optional[int] = None
    grader: Optional["TurnUsage"] = None

    @classmethod
    def from_sdk(cls, usage) -> "Optional[TurnUsage]":
        """Read an SDK usage object defensively; `None` in, `None` out."""
        if usage is None:
            return None
        return cls(
            **{
                field: getattr(usage, field, None)
                for field in cls.model_fields
                if field != "grader"
            }
        )


# --- The spoken turn, delivered in stages ---------------------------------
#
# `POST /api/turn` streams NDJSON rather than answering once at the end. The
# reason is the shape of the turn, not raw speed: STT resolves in a fraction of
# the time the conversation worker takes, so holding the transcript back until
# the reply is ready means the learner watches a loading bubble with no message
# of their own above it — the thread reads as if they never spoke. Each event is
# one line of JSON; `stage` discriminates.
#
# Everything that maps to an HTTP status (unknown topic, STT failure) is settled
# *before* the first byte, so a stream that starts is a stream that carries a
# transcript. Failures after that point can only be reported in-band, as an
# `error` event.
#
# Only `transcript` has a fixed position. `score` and `reply` are two concurrent
# branches raced against each other and are emitted in whichever order they
# resolve — usually score-then-reply (PA is the faster branch), but a slow Azure
# call legitimately inverts it. Clients must dispatch on `stage`, never on
# position. `done` is the one event that is always last, and `error` replaces it
# on a turn that failed after the status line was spent.


class TurnEvent(BaseModel):
    """Fields every staged event carries.

    `elapsed_ms` is the turn's age when this line was *flushed* — the arrival
    time, which is the number staged delivery is actually about. Two turns with
    identical stage durations feel completely different depending on when each
    line went out, and only this distinguishes them. It is the field the replay
    harness reads per event.

    `timings` is the stage table *as known at emit*, so it fills in as the turn
    progresses: `transcript` has `stt_ms` only, `score` adds `pa_ms`, `reply`
    adds `claude_ms`. A stage still running is absent rather than zero. Only
    `done` carries `total_ms` — on any earlier event a "total" would be a total
    of nothing, which is exactly the trap of reading a cumulative snapshot as a
    per-event cost.
    """

    elapsed_ms: Optional[float] = None
    timings: Optional[TurnTimings] = None


class TranscriptEvent(TurnEvent):
    """First event: what the learner said, as soon as STT resolves.

    `transcript.zh` is empty when nothing was recognized; the `reply` event then
    carries a re-prompt.
    """

    stage: Literal["transcript"] = "transcript"
    transcript: Utterance


class ScoreEvent(TurnEvent):
    """How the learner's turn was pronounced, as soon as Azure PA resolves.

    Its own event rather than a field on `reply` because PA and the conversation
    worker run concurrently and PA is much the faster of the two (Stage 0: 1.20s
    against 3.56s). The transcript is rendered unscored and gains its tone
    underlines when this lands — usually seconds before the reply does.

    `pronunciation` is `None` when PA failed. That is deliberately distinct from
    *no event at all*: a degraded turn says "not scored" explicitly instead of
    looking identical to a turn still being scored.

    `tone_errors` live here rather than on the annotation because both they and
    `pronunciation` are derived from the same PA result — the model never judges
    tone. Putting them on the annotation would re-gate the tone underlines on
    the conversation worker, which is the coupling this split exists to remove.
    """

    stage: Literal["score"] = "score"
    pronunciation: Optional[PronunciationScore] = None
    tone_errors: List["ToneError"] = []


class ReplyEvent(TurnEvent):
    """The partner's reply and the converser's annotation.

    Not necessarily the last event, which is why it is `reply` and not `final`:
    it races `score` and `state`, and whenever either is slower they land after
    it. `done` is the terminal event.

    Carries no scores — those went out on `score`. Deriving anything here from
    the PA result would make the reply wait on scoring.

    **It no longer carries `state`** (V2, `docs/VALIDITY.md`). State is derived
    from the *grader's* judgment, which is now a third branch of the fan-out and
    resolves independently of the reply. Holding the reply until the grade lands
    would spend the latency the fan-out exists to protect.

    The cost is real and is accepted rather than hidden: reply and state used to
    commit together, so a client that lost the connection between them could not
    end up with the turn recorded and its consequences lost. Now it can. It is
    survivable because the client resubmits state with every turn and the next
    grade reads the same history — where a held reply would be paid on every
    turn of every session.
    """

    stage: Literal["reply"] = "reply"
    reply: Utterance
    annotation: Optional["TurnAnnotation"] = None


class StateEvent(TurnEvent):
    """How far the learner has got, as soon as the grader resolves (V2).

    Its own event for the same reason `score` is one: it comes from a branch
    that races the reply, and whichever lands first should go out first. The
    grader is a short call on a small prefix, so it usually lands *before* the
    reply — a session that has ended can say so without waiting for the partner
    to finish a line nobody will read.

    `coherence` rides here rather than on the reply's annotation because it is
    the grader's judgment, not the converser's. The converser was asked for it
    for the whole of M2 and could never answer honestly: it knew what was being
    scored, so it took anything scoreable as relevant.
    """

    stage: Literal["state"] = "state"
    state: "SessionState"
    coherence: Optional[Literal["on_track", "drifting", "off_track"]] = None


class DoneEvent(TurnEvent):
    """Terminal event: the turn is over, with its complete accounting.

    Exists because no other event can play that role. `reply` and `score` arrive
    in either order, so "the turn is finished" would otherwise have to be
    inferred from the stream closing — indistinguishable from a dropped
    connection, a truncated proxy response, or a crashed worker. An explicit
    terminator makes the difference observable to the client.

    It is also the only honest place for `total_ms` and `usage`: both are
    whole-turn numbers, and quoting them on `reply` would have meant quoting
    them while a branch was still running.
    """

    stage: Literal["done"] = "done"
    usage: Optional[TurnUsage] = None


class TurnErrorEvent(TurnEvent):
    """A failure after the response committed to 200 — reported in-band.

    The worker refusing mid-stream can't become a 502: the status line is long
    gone. The client turns this into a failed reply bubble, keeping the
    transcript it already rendered.

    Terminal, in place of `done`: a turn ends in exactly one of the two, so a
    client that has seen neither knows the stream was cut rather than finished.
    """

    stage: Literal["error"] = "error"
    detail: str


# --- Phase 3a: the text-turn conversation contract ------------------------
#
# The server is a stateless proxy: the client holds the running transcript and
# resubmits it each turn. `TextTurnRequest` is what it sends; the conversation
# worker returns a `ConversationResult` (the structured-output schema Claude is
# constrained to), which the route surfaces as a `ConversationTurnResponse`.


class DialogueTurn(BaseModel):
    """One prior turn of the client-held transcript.

    `role` is the speaker from the learner's view: `user` is the learner,
    `partner` is the AI. The worker maps these onto Claude message roles
    (`partner`→assistant) — we keep our own vocabulary here so the wire contract
    doesn't leak the model's role names.
    """

    role: Literal["user", "partner"]
    zh: str


class ToneError(BaseModel):
    """One syllable whose tone was wrong, from audio (PA) or from typed digits.

    `said` is the tone the learner actually produced where we can know it — the
    typed path fills it from their tone digits, while Azure PA reports accuracy
    rather than a detected tone and so ships `tones.SAID_UNKNOWN`.

    `index` is the syllable's position among the utterance's hanzi. Display needs
    it because `syllable` alone can't be located in a turn that repeats a
    character (谢谢) — marking the wrong 谢 would be worse than marking none.
    """

    syllable: str
    expected: int
    said: int
    index: Optional[int] = None


# `ConverserAnnotation`'s docstring is deliberately short, and the reasoning for
# its shape lives out here in comments. Pydantic emits a model's docstring as the
# JSON-schema `description`, and `messages.parse` renders that schema into the
# request — so anything written inside the class is text the conversation worker
# reads. A docstring explaining which rubric fields moved to the grader, and why,
# would teach the partner the rubric in the act of documenting its removal.
#
# What moved (V2, `docs/VALIDITY.md`): `slots_filled`, `learner_closed` and
# `coherence` were folded in here to avoid a second call. The cost was
# epistemic — a partner that can see the checkbox behind a question stops being
# a person in a scene and becomes a proctor who wants you to pass. They are
# `GraderResult`'s now and the converser is given no place to say them.
#
# What stayed: `grammar_notes`, a verdict input about the learner's Chinese
# rather than about the rubric, produced by the call that already read their
# sentence in order to answer it.
#
# `tone_errors` is absent for a different reason: tone is never the model's
# judgment. The server fills it from Azure PA accuracy or from typed tone digits.
# It used to be in the schema with the prompt insisting it stay empty, so every
# turn spent output tokens rendering `"tone_errors":[]` and then had it
# overwritten. `TurnAnnotation` is the wire shape that carries it.
class ConverserAnnotation(BaseModel):
    """Notes on one learner turn, alongside the partner's reply."""

    grammar_notes: List[str] = []
    topic_tags: List[str] = []
    should_give_feedback: bool = False
    learner_said_goodbye: bool = False


class GraderResult(BaseModel):
    """What the *grader* judges — the scoring half, on its own call (V2).

    Produced by a goal-blind converser's counterpart: a call that holds no
    character, writes no reply, and has no reason to be generous. It reads the
    previous partner turn plus the learner's turn — exactly the pair that answers
    both questions it is asked (`docs/VALIDITY.md`, "What the grader reads").

    `slots_filled` is narrow — *which of these named facts did this turn
    establish?* — structured extraction, not judgment. What the ids then *mean*
    is decided in `termination.py`, in Python.

    **A `request` slot is credited on the learner's ask alone.** The authored
    rule reads *asked* AND *partner answered*, and the grader deliberately does
    not wait for the second half: the slot is a claim about the learner's
    Chinese, and whether the partner answered is the partner's performance.
    Grading the learner on it grades the wrong party. That is a stronger reason
    than the one in `docs/SCENARIOS.md`, which credits on the ask only because
    Python cannot check a reply — and it is why the grader needs one turn rather
    than a lag or a session-end pass.

    `slots_filled` is what the *current* turn established;
    `slots_filled_previously` is what any **owed** turns established — turns
    whose grade never landed, which this call is settling. They are attributed
    rather than unioned because `termination.advance` resets the close counter on
    a turn that carried content, and a slot credited late is not content this
    turn carried. Empty by construction whenever the window is one turn, which is
    every healthy turn.

    Both default to crediting nothing, so a partial grade cannot advance a
    session by accident. `coherence` deliberately has **no default**: it is the
    judgment, and a grader that omitted it should fail validation and degrade to
    an unchanged state rather than have an opinion invented for it.

    **`learner_closed` is not here.** Noticing that someone is leaving needs no
    rubric, so it is the converser's observation (`ConverserAnnotation`), which
    means a close is applied on time even through a total grader outage.
    """

    coherence: Literal["on_track", "drifting", "off_track"]
    slots_filled: List[str] = []
    slots_filled_previously: List[str] = []


class TurnAnnotation(ConverserAnnotation):
    """The converser's read on one learner turn — logged silently, surfaced later.

    `grammar_notes`/`tone_errors`/`topic_tags` accumulate the per-turn signal the
    (Phase 4) feedback worker consumes. `should_give_feedback` is the worker's
    hint that enough has accrued to interrupt for a coaching round.

    Extends the model-facing shape with the one field the server owns.

    It carries **no `coherence`**, and that is a wire fact rather than an
    oversight: coherence is the grader's judgment now, and this annotation ships
    on the `reply` event, which fires the moment the converser lands. There is no
    grade to merge in yet. The grader's output rides `state` instead, so each
    event carries what its own branch produced.
    """

    tone_errors: List[ToneError] = []

    @classmethod
    def from_worker(
        cls, annotation: ConverserAnnotation, tone_errors: List[ToneError]
    ) -> "TurnAnnotation":
        """Wire annotation = what the model said + what the server measured.

        Reads only the model-facing fields, so handing this an annotation that
        already carries `tone_errors` replaces them rather than colliding. That
        is the rule this type exists to enforce: the server owns the field, and
        whatever was there before is not evidence.
        """
        return cls(
            **{
                name: getattr(annotation, name)
                for name in ConverserAnnotation.model_fields
            },
            tone_errors=tone_errors,
        )


# Same rule as `ConverserAnnotation` above, and this is the class that proves it:
# every word of a model's docstring is rendered into the request as the schema
# `description`, so design notes written here are prompt.
#
# `user_reading` carries text mode. A beginner types `wo jiao xiao ming` and the
# worker resolves it in context — 他 or 她 from context, words outside the topic
# vocab — and it is the only component that can. The short instruction below is
# addressed to the model on purpose; the rest of the reasoning is out here.
#
# **There is no `grade` field**, and its absence is the point rather than an
# omission. A `GraderResult` nested here would be rendered into the request by
# `messages.parse` — field names, and the rubric docstring with it — handing the
# partner the criteria in its cached prefix by exactly the route the system
# prompt was stripped to close (V2, `docs/VALIDITY.md`).
class ConversationResult(BaseModel):
    """The partner's reply, notes on the turn, and the learner's own words as
    you understood them."""

    partner_response: Utterance
    turn_annotation: ConverserAnnotation
    user_reading: Utterance


# The spoken path's schema. STT already produced the learner's 汉字, so the
# worker's reading of them is an echo the orchestrator drops — and asking for it
# cost ~40 output tokens on the one branch the reply waits behind, measured at
# ~0.8s of the turn. That is why this is a second schema rather than one shared
# shape.
#
# The cost of the split is one extra prompt-cache entry per session: the output
# schema is rendered *into* the cached prefix, so a variant changes
# `cache_creation_input_tokens` for byte-identical system blocks. One extra write
# per session, not per turn — and each path still reads its own prefix on every
# turn after the first. That same rendering is why neither schema may carry the
# rubric; see `ConversationResult`.
class SpokenConversationResult(BaseModel):
    """The partner's reply, and notes on the turn."""

    partner_response: Utterance
    turn_annotation: ConverserAnnotation


class SessionState(BaseModel):
    """How far the learner has got, and whether the session is over (M2-C).

    The server is a stateless proxy, so this travels the same road as `sketch`
    and `dialogue`: the server computes it, the client holds it, and the client
    resubmits it on the next turn. Nothing here is persisted server-side.

    `filled_at` maps a slot id to the turn that established it. A dict rather
    than a set plus a parallel order field, because the verdict worker explains
    *both* halves — which facts are missing (a set comparison) and when each
    landed (`docs/SCENARIOS.md`, worked example 1). `filled` is derived from it
    and is deliberately not a wire field: two sources of truth for the same
    thing is how they drift apart.

    The client can of course lie about its own progress. This is a single-user
    practice app with no score to game, and lying only costs the learner their
    verdict — so `goal_met` is recomputed server-side wherever it matters
    (`workers/feedback.py`) rather than defended with a server session table,
    which is the thing the stateless-proxy rule exists to avoid.

    There is no `wrapping` status. The final turn is a *derived* phase
    (`turn >= max_turns`), not stored — a third status would be one more thing
    the client could desync.
    """

    filled_at: Dict[str, PositiveInt] = Field(default_factory=dict, max_length=32)
    consecutive_closes: int = Field(default=0, ge=0)
    # The highest turn a grade has landed for. The window a grader must judge is
    # `turn - last_graded_turn`, so a turn whose grade never arrived is settled
    # by the next one.
    #
    # A watermark rather than a count of ungraded turns, because a count has to
    # be *incremented by the client* when a grade does not arrive — and not
    # receiving the `state` event is the entire failure mode. A watermark only
    # goes stale, and the arithmetic covers the gap on its own.
    #
    # `None`, not `0`, when absent. A client that does not report a watermark is
    # saying nothing about its grades, and `0` would say the opposite — that
    # every turn so far is owed. That reading would fire a recovery pass on every
    # session a client too old to send the field ever finished.
    last_graded_turn: Optional[int] = Field(default=None, ge=0)
    status: Literal["active", "complete"] = "active"
    goal_met: bool = False
    end_reason: Optional[Literal["goal", "cap", "closed", "stuck", "ungraded"]] = None
    # Which topic this state belongs to, stamped by the client at write time so
    # a restored store can be checked against the session it was written under
    # (#29 puts more than one topic on disk). Absent on a fresh state.
    topic_id: Optional[str] = None

    @property
    def filled(self) -> Set[str]:
        return set(self.filled_at)


class ScenarioCard(BaseModel):
    """The learner-visible half of an authored scenario (`docs/SCENARIOS.md`).

    `situation` and `goal`, English, straight from `topic.md`'s `scenario:`
    block — pinned at the top of the thread. Slots are the machine-checkable
    form of the same goal and are never shown here.
    """

    situation: str
    goal: str


class SketchResult(BaseModel):
    """Structured output the sketch worker constrains Claude to.

    One call at session start (`backend/workers/sketch.py`): `opening_line`
    replaces the old hardcoded `prompts.OPENING_LINE`, and `sketch` — persona +
    incidental color, never the goal or slots — replaces `prompts.SKETCH_STUB`.
    Both freeze into the client-held session state and ride the conversation
    worker's cached prefix for the rest of the session.
    """

    opening_line: Utterance
    sketch: str


class SessionStartRequest(BaseModel):
    """Request body for `POST /api/session` — optional, and only ever an echo.

    Omitted (the ordinary case), the server draws a topic and the client is
    told which one it got. Supplied, it is any catalog topic id with an
    authored scenario — in practice the one this endpoint handed back on an
    earlier `SessionStartResponse`, which is what A1's "Try this again" replays
    (`docs/ACCESSIBILITY.md`). Nothing *enforces* that it was issued: the
    server is a stateless proxy and records no ids, and `GET /api/topics`
    already lists the catalog. An id with no scenario is a 404, which is the
    only check there is.

    That is echoing, not choosing, and the distinction is the whole reason this
    is not a thin C8 (#53): the client hands back an opaque string it was
    given. Learning what the catalog *contains* so it can pick is a different
    feature and still isn't here.
    """

    topic_id: Optional[str] = None


class SessionStartResponse(BaseModel):
    """Response body for `POST /api/session`.

    The server picks the topic unless it is handed one it issued earlier
    (`SessionStartRequest`); the frontend has no business knowing which topics
    exist, so it has nothing of its own to send. `topic_id` rides back here,
    and the client echoes it — an opaque value it was handed, not one it looked
    up — on every turn (`TextTurnRequest.topic_id`, the `topic_id` form field
    on `POST /api/turn`), the same way it already echoes `sketch`.

    This is the one point in a session where flavour is *generated* rather
    than reused: the client freezes `sketch` here and resubmits it
    byte-identical on every turn (`TextTurnRequest.sketch`) — the server
    never stores or regenerates it, keeping the stateless-proxy property.
    `opening_line` is rendered once as the first partner bubble and does not
    consume the turn budget.
    """

    topic_id: str
    # The learner's name for what the server drew. `topic_id` is a slug and
    # was a fine label while there was one topic; with the catalog on disk
    # (#29) the client has to show *which* scene this is, and it must not
    # need a second request to `GET /api/topics` to find out.
    display_name: str
    scenario_card: ScenarioCard
    opening_line: Utterance
    sketch: str


class TopicListing(BaseModel):
    """One row of `GET /api/topics` — a topic a learner can recognise.

    Blurb only. Vocab, grammar and the scenario slots stay server-side: the
    client never needs them, and `situation`/`goal` reach it through
    `SessionStartResponse.scenario_card` once a session actually starts.
    """

    id: str
    display_name: str
    summary: str


class TopicListResponse(BaseModel):
    """Response body for `GET /api/topics`.

    An object rather than a bare array so the catalog can grow fields —
    covered state, weights, a pin (`docs/CURRICULUM.md`) — without a
    breaking change to the shape the client parses.
    """

    topics: List[TopicListing]


class ModelLine(BaseModel):
    """One line of the verdict's "what you could have said" exchange.

    English gloss included: a band-1 learner reading a corrected exchange needs
    to know what it *means*, or the demonstration teaches only mimicry.
    """

    zh: str
    pinyin: str
    english: str


class VerdictResult(BaseModel):
    """Structured output the verdict worker constrains Claude to (M2-D).

    Deliberately *not* the wire shape. The worker returns only the two things a
    model is actually good at — a short English explanation, and an in-band
    Chinese exchange — while `goal_met` and `missing` are recomputed by the
    server and assembled into `VerdictCard` around this. The judge is never
    asked whether the learner succeeded, so it cannot be lenient about it
    (`docs/SCENARIOS.md`, "Runtime: three tiers").
    """

    explanation: str
    model_exchange: List[ModelLine] = []


class MissingSlot(BaseModel):
    """A goal fact the learner never established, in learner-readable form."""

    id: str
    description: str


class VerdictCard(BaseModel):
    """Response body for `POST /api/verdict`: the end-of-session card.

    `goal_met` and `missing` are the server's, recomputed from the KB and the
    session's filled set — never the model's, and never the client's. What the
    worker contributes is `explanation` and, when the goal was missed, a 3–4
    line `model_exchange` the learner could have said instead.
    """

    goal_met: bool
    end_reason: Optional[Literal["goal", "cap", "closed", "stuck", "ungraded"]] = None
    missing: List[MissingSlot] = []
    explanation: str
    model_exchange: List[ModelLine] = []
    turns_taken: int = 0


class VerdictRequest(BaseModel):
    """Request body for `POST /api/verdict`.

    Bounded on purpose, like `TtsRequest`: behind one shared passcode this is an
    uncached Sonnet call over caller-supplied text, which makes it the most
    expensive thing in the app to abuse. A real session cannot approach these
    limits — the turn cap tops out well under 20 turns.

    `notes` is the per-turn tone/grammar signal the client accumulated, rolled
    up rather than re-derived: the annotations already happened, once, on turns
    that are over.
    """

    topic_id: str
    dialogue: List[DialogueTurn] = Field(default_factory=list, max_length=40)
    state: SessionState = Field(default_factory=SessionState)
    notes: List[str] = Field(default_factory=list, max_length=60)
    # Only read by the recovery pass, and only when the debt reaches back to the
    # first turn — where the opening line is the sole thing the learner's words
    # answer, and it is never in `dialogue`. Optional everywhere else.
    opening_line: Optional[Utterance] = None

    @field_validator("notes")
    @classmethod
    def _bound_note_length(cls, notes: List[str]) -> List[str]:
        if any(len(note) > 300 for note in notes):
            raise ValueError("a note is longer than 300 characters")
        return notes


class PasscodeRequest(BaseModel):
    """Request body for `POST /api/auth`: the shared passcode, nothing else.

    No length or shape validation on purpose — a rejected-before-comparison
    passcode is a length oracle, and there is only one correct value to compare
    against anyway (`auth.check_passcode`, constant time).
    """

    passcode: str


class TextTurnRequest(BaseModel):
    """Request body for `POST /api/turn/text` (text mode).

    `topic_id` selects the KB whose vocab/grammar/dialogues seed the cached
    prefix; `dialogue` is the client-held transcript so far; `text` is the
    learner's latest utterance — **pinyin** (`ni hao`, or `ni3hao3` when they want
    tones checked), or 汉字 if they'd rather type those. The worker reads either
    and reports back which characters it understood. The audio path reuses the
    same orchestrator seam with `text` sourced from Azure STT instead.

    `sketch` is this session's frozen flavour block from `POST /api/session`
    (`SessionStartResponse.sketch`) — the client resubmits it byte-identical on
    every turn, same as `dialogue`. Defaults to empty for a turn sent before a
    session has been started, which degrades to no flavour rather than failing.

    `state` is this session's progress (`SessionState`) — computed by the server,
    held by the client, resubmitted here, on the same terms as `sketch`. It
    defaults to a fresh state, so a turn sent before any state exists still
    works.
    """

    topic_id: str
    text: str
    dialogue: List[DialogueTurn] = []
    state: SessionState = Field(default_factory=SessionState)
    sketch: str = ""
    # The partner's first line, client-held like `sketch`. It is deliberately not
    # in `dialogue` — it costs the learner none of their turn budget
    # (`docs/SCENARIOS.md`, "Definition of a turn") — but the grader has to see
    # it, because on turn 1 it is the *only* thing the learner's words are a
    # response to. Without it, coherence on the turn most likely to be answering
    # a greeting is judged against nothing at all. Optional: a session started
    # before this field existed simply grades turn 1 without it.
    opening_line: Optional[Utterance] = None

    @field_validator("text")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        """Strip surrounding whitespace and reject an empty turn.

        Deliberately permissive about *what* is typed: a beginner types pinyin,
        and deciding whether a romanized string is "valid" is exactly the judgment
        the conversation worker makes in context. The only thing we refuse here is
        nothing at all.
        """
        v = v.strip()
        if not v:
            raise ValueError("text must not be empty")
        return v


# A partner reply is a sentence. The cap is what keeps an unbounded body from
# being an unbounded Azure bill — synthesis is billed per character, and behind a
# single shared passcode this is the cheapest endpoint to abuse: no audio to
# upload, no conversation to hold, just text and a charge.
TTS_MAX_CHARS = 200


class TtsRequest(BaseModel):
    """Request body for `POST /api/tts`: one line to speak.

    Only the text — no voice, no rate. Those are server config, so the client
    cannot ask for an expensive voice, and the cache key stays a property of the
    deployment rather than of whoever is calling.
    """

    text: str

    @field_validator("text")
    @classmethod
    def _speakable(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("text must not be empty")
        if len(v) > TTS_MAX_CHARS:
            raise ValueError(f"text must be at most {TTS_MAX_CHARS} characters")
        return v


class ConversationTurnResponse(BaseModel):
    """Response body for `POST /api/turn/text`: the learner's turn + the reply.

    `transcript` is the worker's *reading* of the learner's input — the 汉字 it
    understood, with correct tone-marked pinyin. For a pinyin typist that is the
    payoff: they write `wo jiao xiao ming` and get 我叫小明 back, rendered through
    exactly the same bubble path as a spoken turn. There is no `pronunciation`
    (that needs audio), but `annotation.tone_errors` *can* be populated here when
    the learner typed tone digits — see `typed_pinyin`.
    """

    transcript: Utterance
    reply: Utterance
    annotation: TurnAnnotation
    timings: Optional[TurnTimings] = None
    usage: Optional[TurnUsage] = None
    # The spoken path's equivalent rides `ReplyEvent`; text mode has one
    # response, so it rides that.
    state: Optional[SessionState] = None
