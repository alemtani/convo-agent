"""Behavioral evals of the conversation worker, off cassettes.

Structural invariants over model output. These used to live in
`test_conversation_live.py` behind `@pytest.mark.live`. That suite unpacked
five values from `conversation.respond` after the grader split returned four,
so every test failed at the first call — and nothing noticed, because an
excluded suite is a suite nobody runs.

Cassettes make the same asserts free, deterministic, and part of the default
gate. Contact tests (cache_read_input_tokens > 0) stay in the live file: a
recorded 850 is not proof the API cached our prefix.
"""
import pytest

from backend import config, kb
from backend.models import ConverserAnnotation, Utterance
from backend.workers import conversation
from tests.helpers import cassette_draw_count

pytestmark = pytest.mark.cassette

# A stand-in for a session's frozen flavour block. These tests are about the
# reply shape, not the sketch worker, so any byte-stable string will do.
SKETCH_STUB = "A short first-meeting exchange."


async def test_reply_is_valid_structured_output(cassette_client):
    """Valid schema, a non-empty reply, well-formed annotation.

    Length is not asserted — brevity is shaped by the prompt, and a hard char
    ceiling on a model reply is brittle. Structure only.
    """
    n = cassette_draw_count(cassette_client)
    for _ in range(n):
        reply, annotation, reading, _usage = await conversation.respond(
            kb_block=kb.load_converser_block("greetings"),
            sketch=SKETCH_STUB,
            dialogue=[],
            user_text="你好",
            forgiveness_level=config.FORGIVENESS_LEVEL_DEFAULT,
            client=cassette_client,
        )

        assert isinstance(reply, Utterance)
        assert reply.zh and reply.pinyin
        assert reading is not None and reading.zh
        assert isinstance(annotation, ConverserAnnotation)
        assert isinstance(annotation.topic_tags, list)
        assert isinstance(annotation.grammar_notes, list)
        assert isinstance(annotation.should_give_feedback, bool)
        assert isinstance(annotation.learner_said_goodbye, bool)
        # Tone is never the model's to judge. Coherence and slots moved to the
        # grader at V2; a test that still reads them off the annotation is
        # how this file's predecessor rotted.
        assert "tone_errors" not in ConverserAnnotation.model_fields
        assert "coherence" not in ConverserAnnotation.model_fields
        assert "slots_filled" not in ConverserAnnotation.model_fields
