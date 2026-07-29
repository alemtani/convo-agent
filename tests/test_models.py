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
    TextTurnRequest,
    ToneError,
    TurnAnnotation,
    TurnResponse,
    TurnTimings,
    TurnUsage,
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
        # WS1 Stage 0 diagnostics; absent unless the orchestrator attached them.
        "timings": None,
        "usage": None,
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
        {"syllable": "你", "expected": 3, "said": 0, "index": None}
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
    assert err.model_dump() == {"syllable": "ma", "expected": 3, "said": 1, "index": None}


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
            "user_reading": {"zh": "我很好", "pinyin": "wǒ hěn hǎo"},
        }
    )
    assert result.partner_response.zh == "你今天怎么样？"
    assert result.turn_annotation.tone_errors[0].expected == 3
    assert result.turn_annotation.topic_tags == ["greetings"]
    # The learner's own turn, resolved from whatever they typed.
    assert result.user_reading.zh == "我很好"


def test_conversation_turn_response_shape():
    resp = ConversationTurnResponse(
        transcript=Utterance(zh="我叫小明", pinyin="wǒ jiào xiǎo míng"),
        reply=Utterance(zh="你好", pinyin="nǐ hǎo"),
        annotation=TurnAnnotation(coherence="on_track", topic_tags=["greetings"]),
    )
    assert resp.model_dump() == {
        "transcript": {"zh": "我叫小明", "pinyin": "wǒ jiào xiǎo míng"},
        "reply": {"zh": "你好", "pinyin": "nǐ hǎo"},
        "annotation": {
            "coherence": "on_track",
            "grammar_notes": [],
            "tone_errors": [],
            "topic_tags": ["greetings"],
            "should_give_feedback": False,
        },
        "timings": None,
        "usage": None,
    }


# --- WS3: text mode takes pinyin ------------------------------------------
#
# The learner is a beginner who can't necessarily type 汉字, so they type pinyin
# and the conversation worker reads it in context. Judging whether a romanized
# string is "valid Chinese" is exactly that worker's job, so the model layer stays
# permissive: it refuses an empty turn and nothing else.


def test_text_turn_request_strips_surrounding_whitespace():
    assert TextTurnRequest(topic_id="greetings", text="  ni3hao3 \n").text == "ni3hao3"


@pytest.mark.parametrize("text", ["", "   ", "\n\t"])
def test_text_turn_request_rejects_blank_text(text):
    with pytest.raises(ValidationError):
        TextTurnRequest(topic_id="greetings", text=text)


@pytest.mark.parametrize(
    "text",
    [
        "nihao",              # toneless pinyin — the common beginner case
        "ni3hao3",            # tone-numbered, so tones get checked
        "ni hao",             # spaced
        "wo jiao xiao ming",  # a name outside the topic vocab
        "你好",                # 汉字 still work for anyone who can type them
        "我叫Alex",
    ],
)
def test_text_turn_request_accepts_pinyin_and_hanzi(text):
    assert TextTurnRequest(topic_id="greetings", text=text).text == text


# --- WS1 Stage 0: turn diagnostics ----------------------------------------
#
# Timings and token usage ride back on the turn response so the client (and the
# replay harness) reads the same numbers the server logged, rather than each
# side measuring its own thing.


def test_turn_timings_defaults_every_stage_to_none():
    """A stage that didn't run reports nothing, never zero — `total` is the only
    number every turn necessarily has."""
    timings = TurnTimings(total_ms=1200.0)
    assert timings.model_dump() == {
        "stt_ms": None, "pa_ms": None, "claude_ms": None, "total_ms": 1200.0
    }


def test_turn_timings_from_stage_dict_maps_names_to_fields():
    timings = TurnTimings.from_stages({"stt": 900.0, "claude": 3100.0, "total": 4050.0})
    assert timings.stt_ms == 900.0
    assert timings.claude_ms == 3100.0
    assert timings.pa_ms is None       # PA degraded off this turn
    assert timings.total_ms == 4050.0


def test_turn_usage_reads_the_anthropic_usage_block():
    class FakeUsage:
        input_tokens = 42
        output_tokens = 108
        cache_read_input_tokens = 3000
        cache_creation_input_tokens = 0

    usage = TurnUsage.from_sdk(FakeUsage())

    assert usage.model_dump() == {
        "input_tokens": 42,
        "output_tokens": 108,
        "cache_read_input_tokens": 3000,
        "cache_creation_input_tokens": 0,
    }


def test_turn_usage_tolerates_a_usage_block_missing_cache_fields():
    """The cache fields are absent on some responses (and on the stub objects the
    orchestrator tests pass through). Reading usage must never break a turn."""
    class Sparse:
        input_tokens = 10
        output_tokens = 5

    usage = TurnUsage.from_sdk(Sparse())
    assert usage.input_tokens == 10
    assert usage.cache_read_input_tokens is None


def test_turn_usage_from_nothing_is_none():
    assert TurnUsage.from_sdk(None) is None
