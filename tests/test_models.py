"""Phase 1 model tests: the turn response contract.

Pure Pydantic validation — no Azure, no Claude. Asserts the symmetric shape
(`TurnResponse{transcript: Utterance, reply: Utterance}`, each a 汉字 + pinyin
line) that the route returns and that Phase 3's worker will later map onto.
"""
import pytest
from pydantic import ValidationError

from backend.models import (
    ConversationResult,
    ConversationTurnResponse,
    DialogueTurn,
    PronunciationScore,
    SyllableScore,
    ToneError,
    TurnAnnotation,
    TurnResponse,
    Utterance,
)


def test_turn_response_serializes_symmetric_shape():
    resp = TurnResponse(
        transcript=Utterance(zh="你好老师", pinyin="nǐ hǎo lǎo shī"),
        reply=Utterance(zh="你好", pinyin="nǐ hǎo"),
    )
    # `pronunciation` and `annotation` default to None (Phase 2 tone scores and
    # the Phase 3b turn annotation are optional — a transcript-only turn omits them).
    assert resp.model_dump() == {
        "transcript": {"zh": "你好老师", "pinyin": "nǐ hǎo lǎo shī"},
        "reply": {"zh": "你好", "pinyin": "nǐ hǎo"},
        "pronunciation": None,
        "annotation": None,
    }


def test_turn_response_carries_annotation_with_tone_errors():
    # Phase 3b: the audio turn surfaces the worker's annotation, with tone_errors
    # populated deterministically from PA (not by the model).
    resp = TurnResponse(
        transcript=Utterance(zh="你好", pinyin="nǐ hǎo"),
        reply=Utterance(zh="你好", pinyin="nǐ hǎo"),
        annotation=TurnAnnotation(
            coherence="on_track",
            tone_errors=[ToneError(syllable="你", expected=3, said=0)],
        ),
    )
    dumped = resp.model_dump()
    assert dumped["annotation"]["tone_errors"] == [
        {"syllable": "你", "expected": 3, "said": 0}
    ]


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


# --- Phase 3a: the text-turn contract -------------------------------------


def test_dialogue_turn_rejects_unknown_role():
    DialogueTurn(role="user", zh="你好")
    DialogueTurn(role="partner", zh="你好")
    with pytest.raises(ValidationError):
        DialogueTurn(role="assistant", zh="你好")  # only user/partner allowed


def test_turn_annotation_defaults_are_empty():
    ann = TurnAnnotation(coherence="on_track")
    assert ann.grammar_notes == []
    assert ann.tone_errors == []
    assert ann.topic_tags == []
    assert ann.should_give_feedback is False


def test_turn_annotation_rejects_unknown_coherence():
    with pytest.raises(ValidationError):
        TurnAnnotation(coherence="vibes")


def test_tone_error_shape():
    err = ToneError(syllable="ma", expected=3, said=1)
    assert err.model_dump() == {"syllable": "ma", "expected": 3, "said": 1}


def test_conversation_result_nests_reply_and_annotation():
    result = ConversationResult.model_validate(
        {
            "partner_response": {"zh": "你今天怎么样？", "pinyin": "nǐ jīntiān zěnmeyàng?"},
            "turn_annotation": {
                "coherence": "on_track",
                "grammar_notes": [],
                "tone_errors": [{"syllable": "ma", "expected": 3, "said": 1}],
                "topic_tags": ["greetings"],
                "should_give_feedback": False,
            },
        }
    )
    assert result.partner_response.zh == "你今天怎么样？"
    assert result.turn_annotation.tone_errors[0].expected == 3
    assert result.turn_annotation.topic_tags == ["greetings"]


def test_conversation_turn_response_shape():
    resp = ConversationTurnResponse(
        reply=Utterance(zh="你好", pinyin="nǐ hǎo"),
        annotation=TurnAnnotation(coherence="on_track", topic_tags=["greetings"]),
    )
    assert resp.model_dump() == {
        "reply": {"zh": "你好", "pinyin": "nǐ hǎo"},
        "annotation": {
            "coherence": "on_track",
            "grammar_notes": [],
            "tone_errors": [],
            "topic_tags": ["greetings"],
            "should_give_feedback": False,
        },
    }
