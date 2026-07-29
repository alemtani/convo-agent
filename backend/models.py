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

    Every stage is optional because a stage that didn't run must report nothing
    rather than zero: PA drops off a degraded turn, and STT/Claude are absent
    from a text turn. `total_ms` is measured across the whole orchestrator call,
    so it is *not* the sum of the parts — the gap is the un-instrumented work.

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


class TurnResponse(BaseModel):
    """Response body for `POST /api/turn`.

    `transcript` is the user's turn (Azure STT output + derived pinyin); `reply`
    is the partner's turn. In Phase 1 `reply` is a hardcoded constant; Phase 3b
    replaces it with the conversation worker's output. `pronunciation` holds the
    Phase 2 tone scores; it is `None` when nothing was recognized or PA failed
    (the turn degrades to transcript-only rather than failing). `annotation` is
    the Phase 3b turn annotation — its `tone_errors` are filled deterministically
    from `pronunciation`; `None` on a transcript-only / short-circuited turn.
    """

    transcript: Utterance
    reply: Utterance
    pronunciation: Optional[PronunciationScore] = None
    annotation: Optional["TurnAnnotation"] = None
    timings: Optional[TurnTimings] = None
    usage: Optional[TurnUsage] = None


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


class TurnAnnotation(BaseModel):
    """The worker's read on one learner turn — logged silently, surfaced later.

    `coherence` is whether the turn stayed on the conversation's arc;
    `grammar_notes`/`tone_errors`/`topic_tags` accumulate the per-turn signal the
    (Phase 4) feedback worker consumes. `should_give_feedback` is the worker's
    hint that enough has accrued to interrupt for a coaching round.
    """

    coherence: Literal["on_track", "drifting", "off_track"]
    grammar_notes: List[str] = []
    tone_errors: List[ToneError] = []
    topic_tags: List[str] = []
    should_give_feedback: bool = False


class ConversationResult(BaseModel):
    """Structured output the conversation worker constrains Claude to.

    Mirrors `DESIGN.md`'s per-turn JSON: the partner's reply plus the turn
    annotation. The model is forced to this shape via `messages.parse`, so the
    worker never parses free text.

    `user_reading` is what the worker understood the learner to have *said* — the
    turn rendered as 汉字 + correct pinyin. It carries text mode: a beginner types
    `wo jiao xiao ming` and the worker resolves it in context (including 他/她 and
    words outside the topic vocab). On the audio path the input is already hanzi
    from STT, so it echoes that back and the orchestrator ignores it — one schema
    keeps a single cacheable request shape for both paths.
    """

    partner_response: Utterance
    turn_annotation: TurnAnnotation
    user_reading: Utterance


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
