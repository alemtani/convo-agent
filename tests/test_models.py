"""Phase 1 model tests: the turn response contract.

Pure Pydantic validation — no Azure, no Claude. Asserts the symmetric shape
(`TurnResponse{transcript: Utterance, reply: Utterance}`, each a 汉字 + pinyin
line) that the route returns and that Phase 3's worker will later map onto.
"""
import pytest
from pydantic import ValidationError

from backend.models import TurnResponse, Utterance


def test_turn_response_serializes_symmetric_shape():
    resp = TurnResponse(
        transcript=Utterance(zh="你好老师", pinyin="nǐ hǎo lǎo shī"),
        reply=Utterance(zh="你好", pinyin="nǐ hǎo"),
    )
    assert resp.model_dump() == {
        "transcript": {"zh": "你好老师", "pinyin": "nǐ hǎo lǎo shī"},
        "reply": {"zh": "你好", "pinyin": "nǐ hǎo"},
    }


def test_turn_response_parses_from_json_dict():
    resp = TurnResponse.model_validate(
        {
            "transcript": {"zh": "", "pinyin": ""},
            "reply": {"zh": "你好", "pinyin": "nǐ hǎo"},
        }
    )
    assert resp.transcript.zh == ""
    assert resp.reply.zh == "你好"


def test_utterance_requires_both_fields():
    with pytest.raises(ValidationError):
        Utterance(zh="你好")  # missing pinyin
