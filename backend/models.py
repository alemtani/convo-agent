"""Pydantic models for the turn contract.

Phase 1 needs only the response shape: the user's transcribed speech and the
partner's reply, each rendered as a 汉字 + pinyin line. Both sides share one
`Utterance` model — the pair is the recurring unit across the design (DESIGN.md's
`partner_response` and every KB dialogue line), and a beginner reads pinyin for
their own words as much as the partner's. In Phase 3 the partner reply is produced
by the conversation worker and a separate `turn_annotation` is added alongside
these fields (not nested inside the utterance).
"""
from typing import List, Literal, Optional

from pydantic import BaseModel, field_validator


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
    """

    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cache_read_input_tokens: Optional[int] = None
    cache_creation_input_tokens: Optional[int] = None

    @classmethod
    def from_sdk(cls, usage) -> "Optional[TurnUsage]":
        """Read an SDK usage object defensively; `None` in, `None` out."""
        if usage is None:
            return None
        return cls(
            **{
                field: getattr(usage, field, None)
                for field in cls.model_fields
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
    """The partner's reply and the worker's annotation.

    Not necessarily the last event, which is why it is `reply` and not `final`:
    it races `score`, and whenever PA is the slower branch the scores land
    *after* it. `done` is the terminal event.

    Carries no scores — those went out on `score`. Deriving anything here from
    the PA result would make the reply wait on scoring.
    """

    stage: Literal["reply"] = "reply"
    reply: Utterance
    annotation: Optional["TurnAnnotation"] = None


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


class WorkerAnnotation(BaseModel):
    """The annotation *as the model produces it* — no `tone_errors`.

    Tone is never the model's judgment: the server fills it deterministically,
    from Azure PA accuracy on the spoken path or from the tone digits the learner
    typed. The field used to be in the schema with the prompt insisting it stay
    empty, so every turn spent output tokens rendering `"tone_errors":[]` and
    then had it overwritten. Leaving it out is the same contract enforced by
    construction instead of by instruction.

    `TurnAnnotation` is still what goes on the wire — see `from_worker`.
    """

    coherence: Literal["on_track", "drifting", "off_track"]
    grammar_notes: List[str] = []
    topic_tags: List[str] = []
    should_give_feedback: bool = False


class TurnAnnotation(WorkerAnnotation):
    """The worker's read on one learner turn — logged silently, surfaced later.

    `coherence` is whether the turn stayed on the conversation's arc;
    `grammar_notes`/`tone_errors`/`topic_tags` accumulate the per-turn signal the
    (Phase 4) feedback worker consumes. `should_give_feedback` is the worker's
    hint that enough has accrued to interrupt for a coaching round.

    Extends the model-facing shape with the one field the server owns.
    """

    tone_errors: List[ToneError] = []

    @classmethod
    def from_worker(
        cls, annotation: WorkerAnnotation, tone_errors: List[ToneError]
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
                for name in WorkerAnnotation.model_fields
            },
            tone_errors=tone_errors,
        )


class ConversationResult(BaseModel):
    """Structured output the conversation worker constrains Claude to (text).

    Mirrors `DESIGN.md`'s per-turn JSON: the partner's reply plus the turn
    annotation. The model is forced to this shape via `messages.parse`, so the
    worker never parses free text.

    `user_reading` is what the worker understood the learner to have *said* — the
    turn rendered as 汉字 + correct pinyin. It carries text mode: a beginner types
    `wo jiao xiao ming` and the worker resolves it in context (including 他/她 and
    words outside the topic vocab), and it is the only component that can.
    """

    partner_response: Utterance
    turn_annotation: WorkerAnnotation
    user_reading: Utterance


class SpokenConversationResult(BaseModel):
    """The same turn without `user_reading` — the spoken path's schema.

    On the audio path the learner's words already arrived as 汉字 from STT, so
    the worker's reading of them is an echo the orchestrator drops on the floor.
    Asking for it anyway cost ~40 output tokens on the one branch the reply waits
    behind — measured at ~0.8s of the turn, which is why this is a second schema
    rather than one shared shape.

    The cost of the split is one extra prompt-cache entry per session: the
    output schema is rendered *into* the cached prefix (a variant changes
    `cache_creation_input_tokens` for byte-identical system blocks), so the two
    paths cache separately. That is one extra write per session, not per turn,
    and each path still reads its own prefix on every turn after the first.
    """

    partner_response: Utterance
    turn_annotation: WorkerAnnotation


class TextTurnRequest(BaseModel):
    """Request body for `POST /api/turn/text` (text mode).

    `topic_id` selects the KB whose vocab/grammar/dialogues seed the cached
    prefix; `dialogue` is the client-held transcript so far; `text` is the
    learner's latest utterance — **pinyin** (`ni hao`, or `ni3hao3` when they want
    tones checked), or 汉字 if they'd rather type those. The worker reads either
    and reports back which characters it understood. The audio path reuses the
    same orchestrator seam with `text` sourced from Azure STT instead.
    """

    topic_id: str
    text: str
    dialogue: List[DialogueTurn] = []

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
