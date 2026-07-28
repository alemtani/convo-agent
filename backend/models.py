"""Pydantic models for the turn contract.

Phase 1 needs only the response shape: the user's transcribed speech and the
partner's reply, each rendered as a 汉字 + pinyin line. Both sides share one
`Utterance` model — the pair is the recurring unit across the design (DESIGN.md's
`partner_response` and every KB dialogue line), and a beginner reads pinyin for
their own words as much as the partner's. In Phase 3 the partner reply is produced
by the conversation worker and a separate `turn_annotation` is added alongside
these fields (not nested inside the utterance).
"""
import re
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
    """A single mispronounced syllable, from the evaluation path (Azure PA).

    Text-only Phase 3a turns carry no audio, so this list is empty; it is wired
    through now so Phase 3b can populate it from per-syllable tone scores.
    """

    syllable: str
    expected: int
    said: int


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
    """

    partner_response: Utterance
    turn_annotation: TurnAnnotation


#: Han characters — CJK Unified Ideographs plus Extension A. Text mode is
#: hanzi-only, and this is how we tell 汉字 from romanization.
_HANZI = re.compile(r"[㐀-䶿一-鿿]")


class TextTurnRequest(BaseModel):
    """Request body for `POST /api/turn/text` (text mode).

    `topic_id` selects the KB whose vocab/grammar/dialogues seed the cached
    prefix; `dialogue` is the client-held transcript so far; `text` is the
    learner's latest utterance. The audio path reuses the same orchestrator seam
    with `text` sourced from Azure STT instead.
    """

    topic_id: str
    text: str
    dialogue: List[DialogueTurn] = []

    @field_validator("text")
    @classmethod
    def _must_be_hanzi(cls, v: str) -> str:
        """Normalize whitespace and require at least one 汉字.

        Text mode is hanzi-only by design — no pinyin→hanzi converter exists, and
        `pinyin.to_pinyin` passes Latin through unchanged ("nihao" → "nihao"), so
        romanization would echo as its own pinyin line *and* reach the worker as
        typed. Rejecting it here keeps that out of the whole pipeline. Mixed input
        ("我叫Alex") is fine; only *zero* hanzi is refused.
        """
        v = v.strip()
        if not _HANZI.search(v):
            raise ValueError(
                "text must contain 汉字 — type Chinese characters using your "
                "keyboard's pinyin IME; romanized pinyin is not supported"
            )
        return v


class ConversationTurnResponse(BaseModel):
    """Response body for `POST /api/turn/text`: the learner's turn + the reply.

    `transcript` echoes the learner's typed hanzi with server-derived pinyin — it
    mirrors `TurnResponse.transcript` so the client renders a typed turn through
    exactly the same path as a spoken one. There is no `pronunciation`: tone
    scores need audio, so they belong to the speech path alone.
    """

    transcript: Utterance
    reply: Utterance
    annotation: TurnAnnotation
