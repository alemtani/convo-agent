"""Pydantic models for the turn contract.

Phase 1 needs only the response shape: the user's transcribed speech and the
partner's reply, each rendered as a 汉字 + pinyin line. Both sides share one
`Utterance` model — the pair is the recurring unit across the design (DESIGN.md's
`partner_response` and every KB dialogue line), and a beginner reads pinyin for
their own words as much as the partner's. In Phase 3 the partner reply is produced
by the conversation worker and a separate `turn_annotation` is added alongside
these fields (not nested inside the utterance).
"""
from typing import List, Optional

from pydantic import BaseModel


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
    is the partner's turn. In Phase 1 `reply` is a hardcoded constant; Phase 3
    replaces it with the conversation worker's output. `pronunciation` holds the
    Phase 2 tone scores; it is `None` when nothing was recognized or PA failed
    (the turn degrades to transcript-only rather than failing).
    """

    transcript: Utterance
    reply: Utterance
    pronunciation: Optional[PronunciationScore] = None
