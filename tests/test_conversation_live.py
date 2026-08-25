"""Phase 3a live evals — the project's first token-spending tests.

Excluded from the default run (`pytest.ini addopts=-m "not live"`); invoke with
`pytest -m live` and a real `ANTHROPIC_API_KEY`. These assert structural
invariants and the cache-hit smoke test, never exact model wording (DESIGN.md
risk #3): a silent prefix invalidator would drop `cache_read_input_tokens` to 0.
"""
import pytest

from anthropic import AsyncAnthropic

from backend import config, kb
from backend.models import ConversationResult, Utterance
from backend.workers import conversation

pytestmark = pytest.mark.live

# A stand-in for a session's frozen flavour block (`SessionStartResponse.sketch`
# in real use) — these tests are about the KB block caching, not the sketch
# worker itself, so any byte-stable string exercises the same cache mechanics.
SKETCH_STUB = "A short first-meeting exchange."


def _client():
    if not config.ANTHROPIC_API_KEY:
        pytest.skip("ANTHROPIC_API_KEY not configured")
    return AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)


async def test_live_cache_read_on_second_turn():
    """Turn 1 writes the cache; turn 2 (same prefix) reads it.

    The cached-prefix invariant, proven end-to-end: if the system prompt + KB +
    sketch are byte-identical across turns, the second call serves them from
    cache. A non-zero `cache_read_input_tokens` is the proof.
    """
    client = _client()
    kb_block = kb.load_kb_block("greetings")

    _r1, _a1, _g1, _rd1, usage1 = await conversation.respond(
        kb_block=kb_block, sketch=SKETCH_STUB, dialogue=[], user_text="你好",
        forgiveness_level=config.FORGIVENESS_LEVEL_DEFAULT, client=client,
    )
    # The KB block must clear Sonnet 4.6's 2048-token minimum cacheable prefix, or
    # nothing ever caches. Turn 1 may *write* the cache (cold) or *read* an entry a
    # prior run left within the 5-min TTL (warm) — either proves the prefix is
    # cacheable; only a flat zero means it's too small. (Don't require creation: a
    # warm cache legitimately reports cache_creation == 0.)
    assert (
        usage1.cache_creation_input_tokens > 0
        or usage1.cache_read_input_tokens > 0
    ), "prefix did not cache — too small?"

    _r2, _a2, _g2, _rd2, usage2 = await conversation.respond(
        kb_block=kb_block, sketch=SKETCH_STUB,
        dialogue=[
            {"role": "user", "zh": "你好"},
            {"role": "partner", "zh": "你好！你叫什么名字？"},
        ],
        user_text="我叫小明",
        forgiveness_level=config.FORGIVENESS_LEVEL_DEFAULT, client=client,
    )
    assert usage2.cache_read_input_tokens > 0, "cache miss — a silent invalidator?"


async def test_live_reply_is_valid_structured_output():
    """Structural eval: valid schema, a non-empty reply, well-formed annotation.

    Length is intentionally not asserted — brevity is shaped by the prompt, and a
    hard char ceiling on a live reply is brittle. We assert structure only.
    """
    client = _client()
    reply, annotation, grade, _reading, _usage = await conversation.respond(
        kb_block=kb.load_kb_block("greetings"), sketch=SKETCH_STUB, dialogue=[],
        user_text="你好", forgiveness_level=config.FORGIVENESS_LEVEL_DEFAULT,
        client=client,
    )

    assert isinstance(reply, Utterance)
    assert reply.zh and reply.pinyin
    assert annotation.coherence in {"on_track", "drifting", "off_track"}
    assert annotation.learner_said_goodbye in (True, False)
    # Tone is never the model's to judge, so the field it would go in is not
    # even in the schema any more — the server adds it downstream.
    assert not hasattr(annotation, "tone_errors")


async def test_live_each_output_shape_caches_on_its_own_prefix():
    """The output schema rides *inside* the cached span.

    Measured, not assumed: byte-identical system blocks report different
    `cache_creation_input_tokens` per schema, so the spoken and text shapes keep
    separate cache entries. That costs one extra write per session — fine — but
    only if each shape still *reads* on its own second turn. If a shape never
    reads, the output cut has traded ~40 output tokens for a full prefix write
    every turn, which is a much worse deal than it looks.
    """
    client = _client()
    kb_block = kb.load_kb_block("greetings")

    async def turn(want_reading):
        _reply, _ann, _grade, _reading, usage = await conversation.respond(
            kb_block=kb_block, sketch=SKETCH_STUB, dialogue=[], user_text="你好",
            forgiveness_level=config.FORGIVENESS_LEVEL_DEFAULT,
            want_reading=want_reading, client=client,
        )
        return usage

    # Warm both, then read both. Interleaved on purpose: if the two shapes
    # shared one entry, the alternation would show up as a write every turn.
    await turn(True)
    await turn(False)
    warm_text, warm_spoken = await turn(True), await turn(False)

    assert warm_text.cache_read_input_tokens > 0, "text shape stopped caching"
    assert warm_spoken.cache_read_input_tokens > 0, "spoken shape stopped caching"
    assert (
        warm_text.cache_read_input_tokens != warm_spoken.cache_read_input_tokens
    ), "expected distinct prefixes — has the schema left the cached span?"


# --- M2-C: the tracker's tolerance for how a learner actually talks --------


async def test_live_a_request_slot_fills_when_asked_and_answered():
    """The bug behind a session on a phone that would not end.

    The learner asked 你叫什么名字 on turn 1 and the partner answered with its
    name in the same reply. `partner_name` was never credited — not that turn,
    not any later one — so the goal could not complete and the session ran to
    its cap. Replaying the real transcript reproduced it every time on the old
    prompt and never on this one.

    The fix is extractor prompting, which is what `SCENARIOS.md` prescribes for
    "authored slots make scenarios rigid": the seed fixes *which* facts count,
    and the extractor judges semantically whether one was established. So this
    has to run against the real model — a recorded fixture would only prove we
    can write a fixture.
    """
    _reply, annotation, grade, _reading, _usage = await conversation.respond(
        kb_block=kb.load_kb_block("greetings"),
        sketch=SKETCH_STUB,
        dialogue=[{"role": "partner", "zh": "早上好！"}],
        user_text="早上好，你叫什么名字？",
        forgiveness_level=config.FORGIVENESS_LEVEL_DEFAULT,
        want_reading=False,
        client=_client(),
    )

    # Asked, and the partner's reply is where the name comes from — so the fact
    # is established on this turn, whatever words carried it.
    assert "partner_name" in annotation.slots_filled, (
        "the learner asked for the name and the partner answered; "
        f"tracker reported {annotation.slots_filled}"
    )


async def test_live_an_elliptical_question_fills_its_slot():
    """你呢 counts — turning the question back is skill, not a shortcut.

    Never actually observed failing (30 runs, both prompts), so this guards an
    invariant rather than fixing a bug: the loosening above must not later be
    tightened in a way that starts demanding the canonical 你最近怎么样.
    """
    _reply, annotation, grade, _reading, _usage = await conversation.respond(
        kb_block=kb.load_kb_block("greetings"),
        sketch=SKETCH_STUB,
        dialogue=[
            {"role": "user", "zh": "我叫亚当。"},
            {"role": "partner", "zh": "认识你很高兴！你最近怎么样？"},
        ],
        user_text="我很好，你呢？",
        forgiveness_level=config.FORGIVENESS_LEVEL_DEFAULT,
        want_reading=False,
        client=_client(),
    )

    assert "wellbeing" in annotation.slots_filled, (
        "turning the question back with 你呢 is how a real learner asks this; "
        f"tracker reported {annotation.slots_filled}"
    )


async def test_live_a_volunteered_fact_is_still_never_credited():
    """The mitigation must not become leniency.

    Loosening the extractor toward meaning is exactly the change that could
    start crediting facts the *partner* gave away — the mirror-image failure
    `SCENARIOS.md` calls the worse one, because it turns every session into a
    pass. The learner here asks nothing.
    """
    _reply, annotation, grade, _reading, _usage = await conversation.respond(
        kb_block=kb.load_kb_block("greetings"),
        sketch=SKETCH_STUB,
        dialogue=[{"role": "partner", "zh": "你好！"}],
        user_text="你好。",
        forgiveness_level=config.FORGIVENESS_LEVEL_DEFAULT,
        want_reading=False,
        client=_client(),
    )

    assert "partner_name" not in annotation.slots_filled
    assert "wellbeing" not in annotation.slots_filled
