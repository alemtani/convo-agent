"""Pydantic models for the turn contract.

Phase 1 needs only the response shape: the user's transcribed speech plus the
partner's reply. The reply is a nested `PartnerReply` (汉字 + pinyin) because that
pair is the recurring unit across the design — DESIGN.md's `partner_response`
structured output and every KB dialogue line — so Phase 3's conversation worker
maps onto it without reshaping the HTTP response.
"""
from pydantic import BaseModel


class PartnerReply(BaseModel):
    """The partner's turn: Chinese characters and their pinyin reading."""

    zh: str
    pinyin: str


class TurnResponse(BaseModel):
    """Response body for `POST /api/turn`.

    `transcript` is the user's turn (Azure STT output); `reply` is the partner's
    turn. In Phase 1 `reply` is a hardcoded constant; Phase 3 replaces it with the
    conversation worker's output and adds annotation fields here.
    """

    transcript: str
    reply: PartnerReply
