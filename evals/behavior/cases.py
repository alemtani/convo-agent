"""The worker calls the behavioral evals assert on — defined once, read twice.

`tests/test_worker_behavior.py` asserts against these; `python -m
evals.behavior.record` records them. One definition, because a recorder that
assembled its own request would key on a call the test never makes, and the
first sign of it would be a red build with no recording to fix it.

Every case is a coroutine of one argument — the client — so the same function
body is what gets recorded and what gets replayed. Nothing here asserts; the
asserts live with the tests, next to the reason each one exists.

**Production shapes only.** `dialogue` is what the client actually sends:
`[]` on turn 1, strict `user`/`partner` pairs after it, and the opening line in
its own field — never a leading partner turn. The live versions of these cases
submitted a history no client builds, so they graded a `messages` array the app
never assembles.
"""
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Tuple

from backend import config, kb, orchestrator, termination
from backend.models import SessionState
from backend.workers import conversation, grader, sketch

TOPIC_ID = "greetings"

# A stand-in for a session's frozen flavour block (`SessionStartResponse.sketch`
# in real use). Any byte-stable string exercises the same request assembly; the
# sketch worker has its own case below.
SKETCH_STUB = "A short first-meeting exchange."

OPENING_LINE = "早上好！"

# Eval recording is not the learner-facing turn. The live bound is short so a
# phone is not left staring at a dead mic; a cassette that times out is a hole
# in the gate, so this runner waits longer than the app does.
_TIMEOUT_S = 60.0


@dataclass(frozen=True)
class BehaviorCase:
    """One worker call, named. `run(client)` returns whatever it returns."""

    id: str
    run: Callable[[Any], Awaitable[Any]]


async def _converse(client, *, dialogue, user_text, want_reading=True):
    """One partner turn, through the block the app actually freezes.

    `load_converser_block`, not `load_kb_block`: V2 blinded the partner to the
    goal and the slots, and a prefix built the old way is not the prefix that
    ships.
    """
    return await conversation.respond(
        kb_block=kb.load_converser_block(TOPIC_ID),
        sketch=SKETCH_STUB,
        dialogue=dialogue,
        user_text=user_text,
        forgiveness_level=config.FORGIVENESS_LEVEL_DEFAULT,
        want_reading=want_reading,
        client=client,
    )


# The orchestrator's own rule, imported rather than restated — the same
# reason `evals.coherence.replay` imports it. A second copy of "which turn is
# this?" is a second thing that can drift, and the grader's window is derived
# from it.
_turn_index = orchestrator._turn_index


async def _grade(client, *, dialogue, user_text):
    """One grade, with the window the orchestrator would have derived."""
    turn = _turn_index(dialogue)
    return await grader.grade(
        scenario=kb.load_scenario(TOPIC_ID),
        dialogue=list(dialogue),
        user_text=user_text,
        opening_line=OPENING_LINE,
        window=termination.grading_window(SessionState(), turn=turn),
        timeout=_TIMEOUT_S,
        client=client,
    )


async def converser_reply(client):
    return await _converse(client, dialogue=[], user_text="你好")


async def sketch_result(client):
    return await sketch.generate(
        TOPIC_ID, kb.load_scenario(TOPIC_ID), client=client
    )


async def grade_asked_for_a_name(client):
    return await _grade(client, dialogue=[], user_text="早上好，你叫什么名字？")


async def grade_elliptical_question(client):
    return await _grade(
        client,
        dialogue=[
            {"role": "user", "zh": "我叫亚当。"},
            {"role": "partner", "zh": "认识你很高兴！你最近怎么样？"},
        ],
        user_text="我很好，你呢？",
    )


async def grade_a_bare_greeting(client):
    return await _grade(client, dialogue=[], user_text="你好。")


CASES: Tuple[BehaviorCase, ...] = (
    BehaviorCase("converser-reply", converser_reply),
    BehaviorCase("sketch-result", sketch_result),
    BehaviorCase("grade-asked-for-a-name", grade_asked_for_a_name),
    BehaviorCase("grade-elliptical-question", grade_elliptical_question),
    BehaviorCase("grade-a-bare-greeting", grade_a_bare_greeting),
)

BY_ID = {case.id: case for case in CASES}
