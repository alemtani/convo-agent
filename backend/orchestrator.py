"""Turn coordination — the stateless seam between the route and the worker.

A plain Python coordinator (not an LLM): per turn it loads the topic KB, hands
the conversation worker the session sketch + forgiveness default, and wraps the
worker's reply + annotation into the response. It owns *which* sketch / model /
forgiveness apply; the worker stays a pure request builder.

Phase 3a drives this from text (`POST /api/turn/text`). Phase 3b adds the real
speech loop, in two halves: `prepare_audio_turn` runs everything that can still
fail with an HTTP status (KB load, STT), and `stream_audio_turn` races PA against
the conversation worker, emitting each stage as it resolves.
"""
import asyncio
import logging
import random
from typing import AsyncIterator, List, Optional, Tuple, Union

from anthropic import AsyncAnthropic

from backend import config, kb, termination, timing, tones, typed_pinyin
from backend.models import (
    ConversationTurnResponse,
    DialogueTurn,
    DoneEvent,
    ReplyEvent,
    ScenarioCard,
    ScoreEvent,
    SessionStartResponse,
    SessionState,
    StateEvent,
    TextTurnRequest,
    TranscriptEvent,
    TurnAnnotation,
    TurnErrorEvent,
    TurnTimings,
    TurnUsage,
    Utterance,
)
from backend.pinyin import to_pinyin
from backend.speech import pronunciation, stt
from backend.workers import conversation
from backend.workers import grader as grader_worker
from backend.workers import sketch as sketch_worker

logger = logging.getLogger(__name__)

# One line of the NDJSON body `POST /api/turn` streams, discriminated by `stage`.
StagedEvent = Union[
    TranscriptEvent, ScoreEvent, ReplyEvent, StateEvent, DoneEvent, TurnErrorEvent
]

# Shown when nothing is recognized — an explicit "please say it again" so the
# learner knows to retry, not a bare greeting that reads like a fresh turn. Keeps
# the turn alive without spending a worker call on empty input; in-band (≤ band 2).
RETRY_REPLY = Utterance(zh="请再说一次。", pinyin=to_pinyin("请再说一次"))


async def run_text_turn(
    req: TextTurnRequest, client: Optional[AsyncAnthropic] = None
) -> ConversationTurnResponse:
    """Coordinate one text turn: load KB, run the worker, shape the response.

    The mirror of the spoken turn minus the speech stages, and still collected
    rather than staged: with only one worker call there is nothing to stage —
    the reply *is* the turn. `req.text` is usually
    *pinyin* — a beginner can't necessarily type 汉字 — so the transcript comes
    from the worker's reading of that input rather than from local romanization:
    it is the one component that can resolve `ta` into 他 or 她 from context.

    Tone errors are still computed here, never by the model. Where the audio path
    derives them from PA accuracy, text mode derives them from the tone digits the
    learner typed, which yields a real `said` instead of a sentinel (see
    `typed_pinyin`). Typing without tone digits simply produces none.

    Raises `kb.KbError` for an unknown topic and `conversation.ConversationError`
    on a refusal / unparseable reply — the route maps these to 404 / 502.
    """
    timer = timing.Timer()
    kb_block = kb.load_converser_block(req.topic_id)
    scenario = kb.load_scenario(req.topic_id)
    turn = _turn_index(req.dialogue)

    with timer.stage("claude"):
        reply, annotation, reading, usage = await conversation.respond(
            kb_block=kb_block,
            sketch=req.sketch,
            dialogue=req.dialogue,
            user_text=req.text,
            forgiveness_level=config.FORGIVENESS_LEVEL_DEFAULT,
            hint=termination.closing_hint(scenario=scenario, turn=turn),
            client=client,
        )

    # Serial, unlike the spoken path — and the one place text mode differs.
    # The grader needs the learner's turn as 汉字, and in text mode only the
    # converser can produce it: the learner typed pinyin. Re-resolving it here
    # would be a second, independent reading of `wo jiao xiao ming`, and when
    # the two disagreed the learner's own bubble and their grade would describe
    # different sentences. This is the mic-free harness, not the learner's path,
    # so a serialized second call is the cheaper of the two costs.
    with timer.stage("grader"):
        grade, grader_usage = await _grade_or_degrade(
            scenario=scenario, dialogue=req.dialogue, user_text=reading.zh,
            opening_line=req.opening_line.zh if req.opening_line else None,
            window=termination.grading_window(req.state, turn=turn),
            client=client,
        )

    tone_errors = typed_pinyin.tone_errors_from_typed(req.text, reading.zh)
    state = _advance_or_echo(
        req.state, scenario=scenario, grade=grade,
        learner_closed=annotation.learner_said_goodbye, turn=turn,
    )
    annotation = TurnAnnotation.from_worker(annotation, tone_errors)

    return ConversationTurnResponse(
        transcript=reading,
        reply=reply,
        annotation=annotation,
        timings=_report(timer, usage, mode="text", grader_usage=grader_usage),
        usage=_turn_usage(usage, grader_usage),
        state=state,
    )


def _turn_index(dialogue) -> int:
    """The 1-based index of the turn being taken, from the submitted history.

    Derived rather than counted: the server is stateless, so a counter would be
    one more thing the client could desync. The client pushes learner and
    partner strictly in pairs, and the opening line is deliberately never part
    of `dialogue` — so it costs the learner none of their budget
    (`docs/SCENARIOS.md`, "Definition of a turn").
    """
    return len(dialogue) // 2 + 1


def _pick_scenario_topic() -> kb.Topic:
    """Choose which topic a new session opens on.

    Uniform random over every topic with an authored scenario — the only
    selection policy that needs no state. `DESIGN.md`'s proficiency-weighted
    selection (pinned focus, covered-set weighting) is a Phase 7-8 concern
    that reads per-user learning state this endpoint has none of yet; this is
    the seam it slots into later; not built now, not needed with one topic on
    disk.

    Raises `kb.KbError` if no topic has a scenario at all (nothing sessionable).
    """
    candidates = [
        topic
        for topic in (kb.load_topic(topic_id) for topic_id in kb.list_topic_ids())
        if topic.scenario is not None
    ]
    if not candidates:
        raise kb.KbError("no topic has an authored scenario")
    return random.choice(candidates)


def _load_scenario_topic(topic_id: str) -> kb.Topic:
    """Load a caller-echoed topic, refusing one that cannot host a session.

    Both failures are the same `kb.KbError` the draw raises, and both reach the
    learner as the route's existing 404: an id we never issued, and an id whose
    topic has since lost its scenario (the KB is authored, so that can happen
    between one session and the next).
    """
    topic = kb.load_topic(topic_id)
    if topic.scenario is None:
        raise kb.KbError(f"topic has no authored scenario: {topic_id}")
    return topic


async def start_session(
    *, topic_id: Optional[str] = None, client: Optional[AsyncAnthropic] = None
) -> SessionStartResponse:
    """Coordinate one session start: pick a topic, generate flavour, pin the
    scenario card.

    The topic is normally chosen here — the frontend has no business knowing
    which topics exist (that's the KB's business), so `POST /api/session` needs
    no request body. `topic_id` rides back on the response instead, and the
    client echoes it on every turn after (`TextTurnRequest.topic_id`, the
    `topic_id` form field on `POST /api/turn`) the same way it echoes `sketch`
    — an opaque value handed to it, not a lookup it performs on its own.

    A caller *may* pass a topic id to replay the same scenario (A1's "Try this
    again", `docs/ACCESSIBILITY.md`); the draw is simply skipped. In practice
    that is an id this endpoint issued, but nothing here enforces it — the
    server keeps no record of what it handed out, and `GET /api/topics` lists
    the catalog anyway. What the client still does not do is *choose*: it hands
    back a string it was given. That distinction is the design, not a check.

    Keyword-only on purpose. Every caller and every test double passes
    `client=` by name, and a positional `topic_id` in front of it would let a
    mocked client bind silently to the topic slot.

    One `sketch` worker call per session (M2-B) — the opening line and the
    persona/color flavour that used to be the hardcoded `prompts.OPENING_LINE`
    / `SKETCH_STUB`. The client freezes the response and resubmits `sketch`
    byte-identical on every turn for the rest of the session, so this is the
    one point in the session where a cache *write* happens rather than a read
    (`docs/SCENARIOS.md`, "Caching"). `scenario_card` is the authored
    `situation`/`goal` straight from `topic.md`; slots are never surfaced.

    Raises `kb.KbError` if no topic has an authored scenario, `sketch.
    SketchError` on a refusal / unparseable reply.
    """
    topic = _load_scenario_topic(topic_id) if topic_id else _pick_scenario_topic()

    result = await sketch_worker.generate(topic.id, topic.scenario, client=client)
    return SessionStartResponse(
        topic_id=topic.id,
        display_name=topic.display_name,
        scenario_card=ScenarioCard(
            situation=topic.scenario.situation, goal=topic.scenario.goal
        ),
        opening_line=result.opening_line,
        sketch=result.sketch,
    )


async def prepare_audio_turn(
    audio_bytes: bytes, *, topic_id: str = "greetings"
) -> Tuple[Utterance, str, timing.Timer]:
    """Everything about a spoken turn that can still fail with an HTTP status.

    Split out from the stream so the route settles 404 (unknown topic) and 502
    (STT / Azure config) *before* committing to a 200 — once the first event is
    on the wire the status line is spent, and anything later can only be an
    in-band error event. KB load goes first because it's local and free: a bad
    topic shouldn't cost an Azure call.

    The `Timer` is started here rather than in the stream and handed back, so
    `total_ms` covers the whole turn including STT — splitting the coordinator in
    two must not split the measurement, or every staged turn would under-report
    by the one stage that sits in front of everything.

    Raises `kb.KbError` and `stt.SttError`.
    """
    timer = timing.Timer()
    kb_block = kb.load_converser_block(topic_id)
    with timer.stage("stt"):
        recognized = await stt.transcribe(audio_bytes)
    return Utterance(zh=recognized, pinyin=to_pinyin(recognized)), kb_block, timer


async def stream_audio_turn(
    audio_bytes: bytes,
    *,
    transcript: Utterance,
    kb_block: str,
    timer: Optional[timing.Timer] = None,
    dialogue: Optional[List[DialogueTurn]] = None,
    sketch: str = "",
    scenario: Optional[kb.Scenario] = None,
    state: Optional[SessionState] = None,
    opening_line: Optional[str] = None,
    client: Optional[AsyncAnthropic] = None,
) -> AsyncIterator[StagedEvent]:
    """Coordinate one spoken turn, emitting each stage as it resolves.

    Two-pass speech (DESIGN.md Risk 1): `prepare_audio_turn` has already run STT;
    PA assesses the same audio against that transcript, and since both PA and the
    conversation worker depend only on the transcript they run concurrently to
    keep the hot path short. The worker stays text-only — `tone_errors` are
    computed here from the PA score, not by the model.

    The transcript is yielded first and alone. It is the one thing that exists
    early, and it's the learner's own words: holding it back until the worker
    answers leaves them watching a loading bubble with nothing of theirs above
    it.

    Every event carries `elapsed_ms` measured at the moment it is emitted, so the
    replay harness can report when each stage *arrived*, not just how long it
    ran. Two turns with identical stage durations feel completely different
    depending on when each event flushed, and only the arrival time says which.

    The turn always ends in exactly one terminal event — `done` or `error`. The
    stream closing is not a completion signal: it looks the same as a dropped
    connection.

    A PA failure degrades to scores-off; empty recognition short-circuits to a
    re-prompt without spending a worker call; a worker failure becomes a
    `TurnErrorEvent` rather than an exception, because the response has already
    committed to 200.
    """
    timer = timer or timing.Timer()
    state = state if state is not None else SessionState()
    turn = _turn_index(dialogue or [])

    yield TranscriptEvent(transcript=transcript, **_at_emit(timer))

    if not transcript.zh:
        # Nothing to assess and nothing to answer, so neither branch is started:
        # silence must not cost an Azure PA call or a Claude turn.
        #
        # The state is echoed back *unchanged* rather than omitted or defaulted:
        # no worker ran, so nothing was established, and a fresh state here would
        # wipe every filled slot on a single unrecognized mumble — with no server
        # copy to restore it from.
        yield ReplyEvent(reply=RETRY_REPLY, **_at_emit(timer))
        # The state still goes out, unchanged. "Nothing was established" and
        # "no event arrived" are the same thing to a client that gets neither,
        # and only one of them is safe to treat as the session standing still.
        yield StateEvent(state=state, **_at_emit(timer))
        # Still a measured turn: it cost an STT round trip, and dropping the
        # short-circuit from the numbers would bias the p50 downward.
        yield DoneEvent(timings=_report(timer, None, mode="audio"),
                        elapsed_ms=timer.total_ms)
        return

    async def assess_branch():
        with timer.stage("pa"):
            return await _assess_or_degrade(audio_bytes, transcript.zh)

    async def respond_branch():
        with timer.stage("claude"):
            return await conversation.respond(
                kb_block=kb_block,
                sketch=sketch,
                dialogue=dialogue or [],
                user_text=transcript.zh,
                forgiveness_level=config.FORGIVENESS_LEVEL_DEFAULT,
                hint=termination.closing_hint(scenario=scenario, turn=turn),
                # STT already gave us the learner's 汉字, so the worker's reading
                # of them would be an echo we drop — and the reply is the branch
                # the learner waits behind. Don't buy tokens we throw away.
                want_reading=False,
                client=client,
            )

    async def grade_branch():
        with timer.stage("grader"):
            return await _grade_or_degrade(
                scenario=scenario,
                dialogue=dialogue or [],
                # STT already produced the learner's 汉字, so this branch needs
                # nothing from the converser and starts alongside it. That is
                # only true on the spoken path; text mode has to serialize.
                user_text=transcript.zh,
                opening_line=opening_line,
                window=termination.grading_window(state, turn=turn),
                client=client,
            )

    # Each branch times itself rather than the gather timing the group: the whole
    # question WS1 Stage 2 hangs on is which branch is the slower one, and a
    # single number for all three cannot answer it.
    #
    # They are also *emitted* separately, as each resolves, rather than gathered
    # into one event. Stage 0 measured PA at 1.20s against Claude's 3.56s, so the
    # scores exist ~2.4s before the reply does; publishing them together throws
    # that away — the same "hold it until everything's done" mistake the
    # transcript event exists to fix. Whichever branch lands first goes out
    # first, so neither stage can gate the other in either direction.
    pa_task = asyncio.ensure_future(assess_branch())
    worker_task = asyncio.ensure_future(respond_branch())
    grade_task = asyncio.ensure_future(grade_branch())
    pending = {pa_task, worker_task, grade_task}
    usage = None
    reply_sent = False
    have_grade, pending_grade = False, None
    grader_usage = pending_grader_usage = None

    try:
        while pending:
            done, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED
            )
            # `done` is a set, so when both branches land in the same round its
            # iteration order is arbitrary — drain in pipeline order instead, or
            # the event sequence is nondeterministic exactly when the two stages
            # are closest together.
            for task in (pa_task, worker_task, grade_task):
                if task not in done:
                    continue
                if task is pa_task:
                    score = task.result()
                    yield ScoreEvent(
                        pronunciation=score,
                        # Derived here, never by the model. They ride `score`
                        # rather than `final` because they come from the same
                        # PA result — putting them on the annotation would
                        # re-gate the tone underlines on the worker.
                        tone_errors=(
                            tones.tone_errors_from_score(
                                score, threshold=config.TONE_ERROR_THRESHOLD
                            )
                            if score is not None
                            else []
                        ),
                        **_at_emit(timer),
                    )
                elif task is worker_task:
                    reply, annotation, _reading, usage = task.result()
                    reply_sent = True

                    yield ReplyEvent(
                        reply=reply,
                        # Tone errors ride `score`, so the wire annotation is
                        # completed here with none: the two are derived from the
                        # PA result and must not re-gate the reply on scoring.
                        annotation=TurnAnnotation.from_worker(annotation, []),
                        **_at_emit(timer),
                    )
                    if have_grade:
                        # It resolved first and waited for this. Release it now,
                        # in the order the client needs rather than the order the
                        # branches finished.
                        yield StateEvent(
                            state=_advance_or_echo(
                                state, scenario=scenario, grade=pending_grade,
                                learner_closed=annotation.learner_said_goodbye,
                                turn=turn,
                            ),
                            coherence=(
                                pending_grade.coherence if pending_grade else None
                            ),
                            **_at_emit(timer),
                        )
                else:
                    grade, grader_usage = task.result()
                    # **Held until the reply lands.** The client commits the turn
                    # to `dialogue` on `reply` and adopts state on `state`; if a
                    # grade goes out and the converser then fails, the learner
                    # keeps a credited slot — or a completed session — for a turn
                    # that never entered the history the next grade will read.
                    # The grader usually wins the race, so this is the common
                    # order, not the exceptional one.
                    if not reply_sent:
                        pending_grade, have_grade = grade, True
                        pending_grader_usage = grader_usage
                        continue
                    yield StateEvent(
                        # A failed grade credits no slot and leaves the
                        # watermark where it was, so the next turn's window
                        # settles this one. The close still applies — it is the
                        # converser's observation, not the grader's. The event
                        # goes out either way: "nothing was established" and "no
                        # event arrived" look identical to a client that gets
                        # neither, and only one is safe to treat as progress.
                        state=_advance_or_echo(
                            state, scenario=scenario, grade=grade,
                            learner_closed=annotation.learner_said_goodbye,
                            turn=turn,
                        ),
                        coherence=grade.coherence if grade else None,
                        **_at_emit(timer),
                    )

        # Both branches have settled, so this is the first point at which the
        # stage table and the token usage are complete.
        yield DoneEvent(
            timings=TurnTimings.from_stages(timer.as_dict()),
            usage=_turn_usage(usage, grader_usage or pending_grader_usage),
            elapsed_ms=timer.total_ms,
        )
    except conversation.ConversationError as exc:
        logger.warning("conversation worker failed mid-turn: %s", exc)
        yield TurnErrorEvent(detail=str(exc), **_at_emit(timer))
    except Exception:
        # **The turn already committed to 200**, so its status line is spent and
        # anything escaping here truncates the body: the client sees neither
        # `done` nor `error` and cannot tell a finished turn from a dropped
        # connection. `ConversationError` above is the *expected* failure with a
        # message worth showing; this is everything else — a raw `APIError`, a
        # rate limit, a bug in this function — reported in-band rather than
        # silently. `CancelledError` derives from `BaseException`, so a client
        # disconnect still propagates and is not swallowed here.
        logger.exception("turn failed after the response committed to 200")
        yield TurnErrorEvent(detail="turn failed", **_at_emit(timer))
    finally:
        # A failure in either branch leaves the other still running; without this
        # the losing branch outlives the request as an orphaned task.
        for task in (pa_task, worker_task, grade_task):
            if not task.done():
                task.cancel()
        # One log line per turn, after both branches have settled — so it carries
        # the full picture (including `cache_read`) even though the events that
        # went out earlier could only report the stages finished at their moment.
        _report(timer, usage, mode="audio",
                grader_usage=grader_usage or pending_grader_usage)


def _turn_usage(converser_usage, grader_usage):
    """The turn's cost, with the two calls kept apart.

    Not summed. The reply is Sonnet 5 and the judgment is Opus 5 at `effort:
    high` with thinking on, so one number across both would describe a price
    nothing charges — and reporting only the converser's hides the more expensive
    half, on the branch whose cost was the reason for a separate model.
    """
    turn = TurnUsage.from_sdk(converser_usage)
    grade = TurnUsage.from_sdk(grader_usage)
    if turn is None:
        # No converser usage but a graded turn still cost something; say so
        # rather than dropping it.
        return TurnUsage(grader=grade) if grade else None
    turn.grader = grade
    return turn


def _at_emit(timer: timing.Timer) -> dict:
    """The two arrival fields every mid-turn event carries.

    `timer.stages` rather than `as_dict()` on purpose: `as_dict()` injects
    `total`, and a total on an event emitted while a branch is still running is
    a total of an unfinished turn. Mid-turn, the honest whole-turn number is
    `elapsed_ms` — how old the turn was when this line flushed.
    """
    return {
        "timings": TurnTimings.from_stages(timer.stages),
        "elapsed_ms": timer.total_ms,
    }


async def _grade_or_degrade(
    *, scenario, dialogue, user_text, opening_line=None, window=1, client=None
):
    """Run the grader, degrading a failure to `None` rather than failing the turn.

    The same posture as `_assess_or_degrade`, for the same reason: the reply
    stands either way. A learner whose grader timed out gets their partner's
    answer and an unchanged session — they do not get a 502 in the middle of a
    conversation.

    `None` is not a grade. It is the absence of one, and `_advance_or_echo` is
    the only thing allowed to interpret it. Returns `(grade, usage)` so the turn
    can report what the judgment cost as well as what the reply did.
    """
    if scenario is None:
        # No scenario, nothing to grade — and no call to spend on finding out.
        return None, None
    try:
        return await grader_worker.grade(
            scenario=scenario, dialogue=dialogue, user_text=user_text,
            opening_line=opening_line, window=window, client=client,
        )
    except grader_worker.GraderError as exc:
        logger.warning("grader failed: %s", exc)
        return None, None


def _advance_or_echo(state, *, scenario, grade, learner_closed, turn):
    """Advance the session, whether or not a grade landed.

    A missing grade is **not** a graded turn that established nothing. It leaves
    the watermark where it was, so the next turn's window covers this one and the
    credit the learner earned arrives late rather than never. Slots are the only
    thing owed.

    `learner_closed` comes from the converser, which cannot fail independently of
    the reply — no reply, no turn, no state event — so a close is applied on time
    even through a total grader outage, and `consecutive_closes` stays exact
    while slots are outstanding.
    """
    return termination.advance(
        state,
        scenario=scenario,
        slots_filled=grade.slots_filled if grade else [],
        slots_filled_previously=grade.slots_filled_previously if grade else [],
        learner_closed=learner_closed,
        turn=turn,
        graded=grade is not None,
    )


async def _assess_or_degrade(audio_bytes, reference_text):
    """Run PA, degrading a failure to no-scores rather than failing the turn."""
    try:
        return await pronunciation.assess(audio_bytes, reference_text)
    except pronunciation.PaError as exc:
        logger.warning("pronunciation assessment failed: %s", exc)
        return None


def _report(timer: timing.Timer, usage, *, mode: str, grader_usage=None) -> TurnTimings:
    """Log the turn's cost and return it as the wire model (WS1 Stage 0).

    One line per turn, at INFO so it shows up under a plain `uvicorn` run without
    a flag. The critical path is `stt + max(pa, claude)`, so the stages are
    printed side by side — the gap between their sum and `total` is the server's
    own overhead, and `cache_read` says whether the frozen prefix is being
    reused at all.
    """
    stages = timer.as_dict()
    logger.info(
        "turn timings mode=%s %s cache_read=%s cache_write=%s in=%s out=%s"
        " grader_in=%s grader_out=%s grader_cache_read=%s",
        mode,
        " ".join(f"{name}={stages[name]:.0f}ms"
                 for name in timing.STAGE_ORDER if name in stages),
        getattr(usage, "cache_read_input_tokens", None),
        getattr(usage, "cache_creation_input_tokens", None),
        getattr(usage, "input_tokens", None),
        getattr(usage, "output_tokens", None),
        getattr(grader_usage, "input_tokens", None),
        getattr(grader_usage, "output_tokens", None),
        getattr(grader_usage, "cache_read_input_tokens", None),
    )
    return TurnTimings.from_stages(stages)
