"""Phase 1 model tests: the turn response contract.

Pure Pydantic validation — no Azure, no Claude. Asserts the nested shape
(`TurnResponse{transcript, reply: PartnerReply{zh, pinyin}}`) that the route
returns and that Phase 3's conversation worker will later map onto.
"""
import pytest
from pydantic import ValidationError

from backend.models import PartnerReply, TurnResponse


def test_turn_response_serializes_nested_shape():
    resp = TurnResponse(
        transcript="你好老师",
        reply=PartnerReply(zh="你好", pinyin="nǐ hǎo"),
    )
    assert resp.model_dump() == {
        "transcript": "你好老师",
        "reply": {"zh": "你好", "pinyin": "nǐ hǎo"},
    }


def test_turn_response_parses_from_json_dict():
    resp = TurnResponse.model_validate(
        {"transcript": "", "reply": {"zh": "你好", "pinyin": "nǐ hǎo"}}
    )
    assert resp.transcript == ""
    assert resp.reply.zh == "你好"


def test_partner_reply_requires_both_fields():
    with pytest.raises(ValidationError):
        PartnerReply(zh="你好")  # missing pinyin
