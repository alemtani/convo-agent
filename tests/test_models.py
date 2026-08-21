"""Model tests: the turn contracts, as they go on the wire.

Pure Pydantic validation — no Azure, no Claude. The spoken turn is delivered as
staged events (`transcript` → `score`/`reply` → `done`, or `error`), each a line
of NDJSON discriminated by `stage`; the text turn is still a single
`ConversationTurnResponse`. Both are built from 汉字 + pinyin `Utterance` pairs.
"""
import pytest
from pydantic import ValidationError

from backend.models import (
    ConversationResult,
    ConversationTurnResponse,
    DialogueTurn,
    DoneEvent,
    PronunciationScore,
    ReplyEvent,
    ScoreEvent,
    SessionState,
    SyllableScore,
    TextTurnRequest,
    ToneError,
    TranscriptEvent,
    SpokenConversationResult,
    TurnAnnotation,
    TurnErrorEvent,
    TurnTimings,
    TurnUsage,
    Utterance,
    VerdictCard,
    WorkerAnnotation,
)


def test_transcript_event_serializes_with_its_stage_tag():
    """`stage` is what a client dispatches on, so it must survive the dump.

    Only `transcript` has a fixed position in the stream; everything after it
    arrives in whichever order the branches resolve, which makes the tag the
    only reliable discriminator.
    """
    event = TranscriptEvent(transcript=Utterance(zh="你好老师", pinyin="nǐ hǎo lǎo shī"))
    assert event.model_dump() == {
        "stage": "transcript",
        "transcript": {"zh": "你好老师", "pinyin": "nǐ hǎo lǎo shī"},
        # Arrival + the stage table so far; absent unless the orchestrator
        # attached them.
        "elapsed_ms": None,
        "timings": None,
    }


def test_reply_event_carries_the_annotation():
    event = ReplyEvent(
        reply=Utterance(zh="你好", pinyin="nǐ hǎo"),
        annotation=TurnAnnotation(
            coherence="on_track",
            tone_errors=[ToneError(syllable="你", expected=3, said=0)],
        ),
    )
    dumped = event.model_dump()
    assert dumped["stage"] == "reply"
    assert dumped["annotation"]["tone_errors"] == [
        {"syllable": "你", "expected": 3, "said": 0, "index": None}
    ]


def test_score_event_carries_pronunciation_scores():
    event = ScoreEvent(
        pronunciation=PronunciationScore(
            overall=80.0,
            syllables=[
                SyllableScore(hanzi="老", pinyin="lǎo", accuracy=97.0),
                SyllableScore(hanzi="师", pinyin="shī", accuracy=67.0),
            ],
        ),
        tone_errors=[ToneError(syllable="师", expected=1, said=0)],
    )
    dumped = event.model_dump()
    assert dumped["stage"] == "score"
    assert dumped["pronunciation"]["overall"] == 80.0
    assert dumped["pronunciation"]["syllables"][1] == {
        "hanzi": "师",
        "pinyin": "shī",
        "accuracy": 67.0,
    }
    # Tone errors ride here, never on the reply: both they and `pronunciation`
    # come from the same PA result.
    assert dumped["tone_errors"][0]["syllable"] == "师"


def test_score_event_distinguishes_unscored_from_still_scoring():
    """`pronunciation: null` is a statement, not an omission.

    A degraded turn still emits the event, because silence is indistinguishable
    from a turn whose PA hasn't come back yet.
    """
    dumped = ScoreEvent().model_dump()
    assert dumped["stage"] == "score"
    assert dumped["pronunciation"] is None
    assert dumped["tone_errors"] == []


def test_only_the_done_event_reports_a_total():
    """Mid-turn events carry the stage table so far; `total_ms` needs the end.

    A total quoted while a branch is still running is a total of an unfinished
    turn — the trap of reading a cumulative snapshot as a per-event cost.
    """
    mid = ScoreEvent(elapsed_ms=560.0, timings=TurnTimings(stt_ms=310.0, pa_ms=250.0))
    assert mid.timings.total_ms is None
    assert mid.elapsed_ms == 560.0

    done = DoneEvent(
        elapsed_ms=1250.0,
        timings=TurnTimings(stt_ms=310.0, pa_ms=250.0, claude_ms=900.0, total_ms=1250.0),
        usage=TurnUsage(cache_read_input_tokens=4096),
    )
    assert done.stage == "done"
    assert done.timings.total_ms == 1250.0
    assert done.usage.cache_read_input_tokens == 4096


def test_syllable_score_requires_all_fields():
    with pytest.raises(ValidationError):
        SyllableScore(hanzi="老", accuracy=97.0)  # missing pinyin


def test_events_parse_from_json_dicts():
    """The client and the replay harness both read these back off the wire."""
    transcript = TranscriptEvent.model_validate(
        {"stage": "transcript", "transcript": {"zh": "", "pinyin": ""}}
    )
    assert transcript.transcript.zh == ""

    error = TurnErrorEvent.model_validate(
        {"stage": "error", "detail": "worker refused the turn", "elapsed_ms": 900.0}
    )
    assert error.stage == "error"
    assert "refused" in error.detail


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
                "topic_tags": ["greetings"],
                "should_give_feedback": False,
            },
            "user_reading": {"zh": "我很好", "pinyin": "wǒ hěn hǎo"},
        }
    )
    assert result.partner_response.zh == "你今天怎么样？"
    assert result.turn_annotation.topic_tags == ["greetings"]
    # The learner's own turn, resolved from whatever they typed.
    assert result.user_reading.zh == "我很好"


def test_the_model_is_never_asked_for_tone_errors():
    """Tone is the server's judgment, from PA accuracy or typed digits. The
    field used to be in the schema with the prompt insisting it stay empty —
    output tokens spent every turn to render `[]` and have it overwritten."""
    assert "tone_errors" not in ConversationResult.model_json_schema()["$defs"][
        "WorkerAnnotation"
    ]["properties"]


def test_the_spoken_schema_drops_the_reading_the_audio_path_throws_away():
    """STT already produced the learner's 汉字, so the worker's reading of them
    is an echo. It is the reply branch the learner waits behind, so the tokens
    come off the one stage that gates the turn."""
    assert "user_reading" in ConversationResult.model_fields
    assert "user_reading" not in SpokenConversationResult.model_fields
    # Same reply and same annotation either way — only the echo is gone.
    assert "partner_response" in SpokenConversationResult.model_fields
    assert "turn_annotation" in SpokenConversationResult.model_fields


def test_wire_annotation_takes_tone_errors_from_the_server_not_the_model():
    annotation = TurnAnnotation.from_worker(
        WorkerAnnotation(coherence="on_track", topic_tags=["greetings"]),
        [ToneError(syllable="ma", expected=3, said=1)],
    )
    assert annotation.coherence == "on_track"
    assert annotation.topic_tags == ["greetings"]
    assert annotation.tone_errors[0].expected == 3


def test_wire_annotation_replaces_tone_errors_rather_than_colliding():
    """Handed an annotation that already carries scores, the server's are the
    ones that survive — the field is not the model's to fill."""
    stale = TurnAnnotation(
        coherence="on_track", tone_errors=[ToneError(syllable="你", expected=3, said=1)]
    )
    assert TurnAnnotation.from_worker(stale, []).tone_errors == []


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
            # M2-C's tracker rides the annotation both paths already carry.
            "slots_filled": [],
            "learner_closed": False,
        },
        "timings": None,
        "usage": None,
        "state": None,
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


# --- A1: the learner's own exit (#66) ---------------------------------------


def test_stuck_is_a_session_end_reason():
    """The bail-out ends a session, so it needs a value on both models.

    `SessionState` carries it off the client; `VerdictCard` carries it back with
    the card. A reason accepted on one and rejected on the other would 422 the
    verdict of the one exit built for a learner who is already stuck.
    """
    assert SessionState(end_reason="stuck").end_reason == "stuck"
    assert VerdictCard(
        goal_met=False, end_reason="stuck", explanation="…"
    ).end_reason == "stuck"


def test_an_invented_end_reason_is_still_rejected():
    with pytest.raises(ValidationError):
        SessionState(end_reason="bored")
