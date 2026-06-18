"""Phase 1 model tests: the turn response contract.

Pure Pydantic validation — no Azure, no Claude. Asserts the symmetric shape
(`TurnResponse{transcript: Utterance, reply: Utterance}`, each a 汉字 + pinyin
line) that the route returns and that Phase 3's worker will later map onto.
"""
import pytest
from pydantic import ValidationError

from backend.models import (
    PronunciationScore,
    SyllableScore,
    TurnResponse,
    Utterance,
)


def test_turn_response_serializes_symmetric_shape():
    resp = TurnResponse(
        transcript=Utterance(zh="你好老师", pinyin="nǐ hǎo lǎo shī"),
        reply=Utterance(zh="你好", pinyin="nǐ hǎo"),
    )
    # `pronunciation` defaults to None (Phase 2 tone scores are optional — a turn
    # with no recognized speech, or a PA failure, omits them).
    assert resp.model_dump() == {
        "transcript": {"zh": "你好老师", "pinyin": "nǐ hǎo lǎo shī"},
        "reply": {"zh": "你好", "pinyin": "nǐ hǎo"},
        "pronunciation": None,
    }


def test_turn_response_carries_pronunciation_scores():
    resp = TurnResponse(
        transcript=Utterance(zh="老师", pinyin="lǎo shī"),
        reply=Utterance(zh="你好", pinyin="nǐ hǎo"),
        pronunciation=PronunciationScore(
            overall=80.0,
            syllables=[
                SyllableScore(hanzi="老", pinyin="lǎo", accuracy=97.0),
                SyllableScore(hanzi="师", pinyin="shī", accuracy=67.0),
            ],
        ),
    )
    dumped = resp.model_dump()
    assert dumped["pronunciation"]["overall"] == 80.0
    assert dumped["pronunciation"]["syllables"][1] == {
        "hanzi": "师",
        "pinyin": "shī",
        "accuracy": 67.0,
    }


def test_syllable_score_requires_all_fields():
    with pytest.raises(ValidationError):
        SyllableScore(hanzi="老", accuracy=97.0)  # missing pinyin


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
