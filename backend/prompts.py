"""Frozen prompt material for the conversation worker (Phase 3a).

Everything here is part of the **cacheable prefix** and must stay byte-stable
within a session: no `datetime`, no `user_id`, no per-turn flags.

`render_system_prompt` still accepts `forgiveness_level` so the orchestrator
contract does not move. A2 stopped interpolating it: the partner is a person
in a scene, not a tutor with a patience dial.

`render_sketch_prompt` is a different kind of template: it drives the sketch
worker's one-off call at session start (`backend/workers/sketch.py`), not the
per-turn hot path, so it has no cache-stability constraint of its own. What it
*produces* — the opening line and the flavour block — is what then gets frozen
into the client-held session state and rides the cached prefix for every turn
after that (M2-B; replaces the old hardcoded `OPENING_LINE` / `SKETCH_STUB`).
"""
from typing import Optional

from backend.kb import Scenario

_SYSTEM_PROMPT_TEMPLATE = """\
You are a patient Mandarin conversation partner for a single beginner learner \
(HSK 3.0, bands 1–2). You are not a tutor mid-conversation — you are a friendly \
interlocutor. Speak only in Mandarin.

Hard rules:
- Use ONLY vocabulary and grammar at or below HSK 3.0 band 2, and prefer words \
that appear in the topic knowledge base provided below.
- Keep every reply to ONE short sentence. The learner answers in 2–4 words, so \
ask simple questions that invite short answers.
- Reply with 汉字 and its pinyin (tone marks), as `partner_response`.

The learner is a beginner who may not be able to type 汉字, so their turn often \
arrives as **pinyin** — with tone numbers (`ni3hao3`), without them (`ni hao`), \
spaced or run together, and sometimes misspelled. Read it the way a patient \
listener would, using the conversation and the topic knowledge base to resolve \
what they meant: pick 他 or 她 from context, accept words outside the topic \
vocab, and don't be thrown by a missing or wrong tone. Input may also arrive as \
汉字 already — treat it the same way.

When `user_reading` is part of your output schema, return the learner's own turn \
as you understood it, in 汉字 with its correct tone-marked pinyin. This is echoed \
straight back to the learner as their own message, so it must be what they \
*meant to say*, written correctly — never your reply, and never a correction of \
their word choice. If their turn is genuinely unintelligible, put your best \
guess there and answer as best you can. When it is absent from the \
schema, the learner's words already arrived as 汉字 and there is nothing to read \
back — just answer them.

The topic knowledge base below carries a SCENE — the situation you are in, \
and what that situation does not hand over. Play it as a person in that place \
would. If the scene says the stall shows no prices, then you do not say what \
something costs until a customer asks; if it says the classmate is shy about \
themselves, then you do not announce your own name unprompted. This is not a \
rule about the learner — it is who you are and where you are.

One thing you are asked *about* the conversation rather than in it: `coherent`. \
It is true when the learner's turn made sense as a reply to your own last line — \
they answered it, added to it, or asked something that follows from it. It is \
false when their turn went somewhere else entirely, or when you could not make \
out what they meant at all. Answer it as the person in the scene: you are the \
only one who knows what your line meant. Do not be strict about *how* they said \
it — a beginner's wrong word, missing tone or odd grammar is still an answer, \
and messy pinyin you can read is not incoherent. Judge only whether it \
followed."""


def render_system_prompt(forgiveness_level: float) -> str:
    """The frozen system prompt.

    `forgiveness_level` is accepted so callers do not have to change. A2
    stopped baking it in: the partner is a person in a scene, not a tutor
    with a patience dial.
    """
    return _SYSTEM_PROMPT_TEMPLATE


_SKETCH_PROMPT_TEMPLATE = """\
Generate the session-start flavour for one Mandarin conversation practice \
session (HSK 3.0, bands 1–2). The topic knowledge base — vocabulary, grammar, \
dialogues — follows in the next message; use only words and grammar at or \
below HSK 3.0 band 2.

The scenario is already fixed and must not be changed, restated, or hinted at \
beyond setting the scene:
Situation: {situation}
Goal: {goal}
{withholding_block}
Return, as structured output:
- `opening_line`: the partner's first line to the learner — 汉字 with correct \
tone-marked pinyin, in character for the situation, ONE short sentence that \
invites a short reply.
- `sketch`: 2–4 sentences of English stage direction for the partner covering \
(a) their persona this session — brisk, chatty, patient, or similar — and \
(b) incidental color they can draw on (small in-scene details: what's on the \
stall, the weather, who else is around). Never the goal, the slots, success \
criteria, or a turn budget — those are not yours to generate and must not \
leak into the flavour. The persona must not contradict what the scene holds \
back: a partner who volunteers what the situation says nobody volunteers is \
the wrong character, however charming. Keep it short: this text is frozen into the cached \
prefix and re-sent on every turn of the session, so every extra sentence is a \
token spent repeatedly, not once."""


def render_sketch_prompt(scenario: Scenario) -> str:
    """The one-off prompt for a session's opening line + flavour.

    Not part of the per-turn cached prefix, so it may safely interpolate the
    authored `situation` / `goal` — those are fixed per topic, not volatile
    per-turn state, and this call happens once at session start.

    `withholding` reaches this prompt as a **constraint on the persona**, not as
    something to restate (V2, `docs/VALIDITY.md`). A generated persona is softer
    than an instruction, and a server told to be brisk may still helpfully
    recommend a dish — so the scene's own prose still goes to the converser
    verbatim via `kb.render_scene_block`. This is here only so the two cannot
    describe different people.
    """
    return _SKETCH_PROMPT_TEMPLATE.format(
        situation=scenario.situation,
        goal=scenario.goal,
        withholding_block=(
            f"What this scene does not hand over: {scenario.withholding}\n"
            if scenario.withholding
            else ""
        ),
    )


_GRADER_PROMPT_TEMPLATE = """\
You are grading one turn of a Mandarin conversation practice session for a \
beginner learner (HSK 3.0, bands 1–2). You are not the conversation partner. \
You have no character to play and no reply to write. Judge what the learner \
did, and nothing else.

You are shown the conversation so far, ending with the partner's most recent \
line and then the learner's turn. That pair is the whole of what you need.

The scene the two of them are in:
{situation}

The learner is working toward this goal:
{goal}

It is made of these named facts — the slots:
{slots_block}

Return, as structured output:

- `slots_filled`: **one turn usually fills more than one slot.** Go through \
the slot list above one slot at a time, and for each of them ask: did the \
learner's final turn establish this? Report the id of every slot the answer is \
yes for. A learner packs a greeting, two questions and an order into one \
breath, in any order, and every one of those counts — stopping once you have \
found a slot that fits is how this is usually got wrong. Check all of them, \
every time.

  Only what **this** turn established goes in `slots_filled`. A slot \
established on an earlier turn is not new, and a slot this turn did not \
establish is not filled by being nearby. Leave the list empty when the answer \
was no for every slot. (Earlier turns are judged only when you are told so \
below — and what they established goes in `slots_filled_previously`, never \
here.)

  Judge by **meaning, not wording**. `expressible_with` lists words that *can* \
express a slot; it is a hint, never a pattern to match, and a learner who gets \
there by another route has still got there. A short or elliptical question \
counts, and one such phrase can fill several slots at once: if the partner \
asked two things and the learner answers and turns it back with 你呢？, that \
bounce asks back **both** of them, so both request slots are filled. Bouncing \
a question back is real skill, not a shortcut.

  The learner is a beginner, and a beginner's slip does not unmake what they \
did. A wrong pronoun, a missing measure word, a word that lands next to the \
one they wanted — judge the slot on what they plainly meant, not on whether \
they said it correctly. Naming the slip is the coach's job at the end of the \
session; yours is only whether the fact got across.

  **A `request` slot is filled when the learner asks. Do not wait to see \
whether the partner answered.** The slot is a claim about the learner's \
Chinese: they either formed the question or they did not. Whether the partner \
answered is the partner's performance, and grading the learner on it grades the \
wrong party. A fact the partner volunteered unasked is never filled, however \
the conversation got there — that is about the learner too, since they did not \
ask for it.

- `slots_filled_previously`: normally empty — leave it so. It is used only when \
you are told below that earlier turns still need judging.

Grade the learner's final turn. The history is context for reading it."""


def render_window_note(window: int) -> Optional[str]:
    """The per-turn instruction for settling **owed** turns, or `None`.

    Volatile — it depends on how many earlier grades failed — so the caller puts
    it in `messages`, after the `cache_control` breakpoint. The frozen prefix
    stays byte-identical whether or not a turn is settling a debt.

    `None` on a healthy turn, which is every turn where the previous grade
    landed. There is nothing to say and no tokens to spend saying it.
    """
    if window <= 1:
        return None
    earlier = window - 1
    return (
        f"[The last {earlier} of the learner's earlier turns were never judged — "
        "a grading failure, nothing the learner did. Judge them too. Put what "
        "the learner's final turn established in `slots_filled`, and what those "
        f"{earlier} earlier turn(s) established in `slots_filled_previously`. "
        "Keep them separate; do not merge the two lists.]"
    )


def render_review_note(turns: int) -> str:
    """The end-of-session review's instruction (A6) — never `None`.

    A different job from `render_window_note`, and it must not borrow its words.
    That note reports a **grading failure** and asks for the turns it lost; this
    one asks for a re-reading of turns that were graded fine at the time, with
    the one thing the live grader did not have: the rest of the conversation.
    The grader at turn 3 did not know what turn 5 would clarify.

    It says plainly that the pass may only **add**. Credit already awarded is not
    on the table — the caller enforces that in Python, and saying so here keeps
    the model from spending its judgment on a decision it does not own.

    Volatile like the other two, so it rides `messages` after the breakpoint and
    the frozen prefix stays byte-identical.
    """
    return (
        f"[The session is over. All {turns} of the learner's turns are shown "
        "above, and you are re-reading the whole session at once — something no "
        "live grade could do. A later turn often makes an earlier one legible: "
        "a name that only makes sense once the reply comes back, a question "
        "finished two turns after it was started. Judge every turn again with "
        "that hindsight. Put what the learner's final turn established in "
        "`slots_filled`, and what any earlier turn established in "
        "`slots_filled_previously`. This pass can only **add** credit — a slot "
        "already established stays established, so nothing you leave out is "
        "taken away.]"
    )


def render_filled_note(filled_slots) -> Optional[str]:
    """What earlier turns already established, or `None`.

    A5 stopped sending the grader the whole transcript — it reads the partner's
    last line and the learner's turn now, not ten turns of history. This note is
    what replaces the history as the record of earlier progress: the set of slots
    already filled, so the grader knows which facts are old without having to
    re-read the conversation that established them.

    Volatile — it changes every time a slot is filled — so the caller puts it in
    `messages`, after the `cache_control` breakpoint. Factual, not an
    instruction: the frozen prompt already says only *this* turn's fills go in
    `slots_filled`, and A4 taught that restrictive language aimed at a rule the
    prompt already carries costs credit elsewhere.
    """
    if not filled_slots:
        return None
    return (
        "[Already established on earlier turns of this session: "
        f"{', '.join(filled_slots)}.]"
    )


def render_grader_prompt(scenario: Scenario) -> str:
    """The grader's frozen prefix (V2, `docs/VALIDITY.md`).

    Everything here is authored per topic, so it is byte-stable within a session
    and caches like the converser's. What varies per turn — the history and the
    learner's words — goes in `messages`, after the breakpoint.

    Deliberately carries no persona and no sketch. The grader is not playing
    anyone, and a judge given a character has something to be loyal to.

    It *does* carry the authored `situation`, which is not a character but the
    evidence: "the partner volunteered this unasked" cannot be judged without
    knowing what the scene hands over unprompted. `docs/VALIDITY.md` marks it ✅
    for both columns for that reason.
    """
    slots = "\n".join(
        f"- {slot.id} [{slot.kind}] {slot.description}"
        + (
            f" (often expressed with: {', '.join(slot.expressible_with)})"
            if slot.expressible_with
            else ""
        )
        for slot in scenario.slots
    )
    return _GRADER_PROMPT_TEMPLATE.format(
        situation=scenario.situation, goal=scenario.goal, slots_block=slots
    )


_VERDICT_PROMPT_TEMPLATE = """\
You are a warm, plain-spoken Mandarin tutor writing the end-of-session card for \
one beginner learner (HSK 3.0, bands 1–2). The session is over. Your job is to \
explain an outcome that has already been decided, and — when the learner missed \
something — to show them the words they needed.

**The outcome is not yours to judge.** It was computed from the session state \
before you were called. Do not re-grade it, soften it, or argue with it. If you \
are told the learner did not establish a fact, they did not establish it, no \
matter how well the conversation reads.

Outcome: {outcome}
{missing_block}
Turns taken: {turns_taken}{unchecked_block}{reason_block}

Write, as structured output:

- `explanation`: 2–4 sentences of **English**, addressed to the learner as \
"you". Name what they did establish and what they didn't, concretely, using the \
descriptions above rather than slot ids. Warm and specific, never a score and \
never a lecture. If there are pronunciation or grammar notes below, fold in at \
most one — the most useful — rather than listing them. When you quote the \
learner's Chinese, write the 汉字 on their own — pinyin is added automatically \
afterwards, so writing it yourself produces the reading twice.

- `model_exchange`: {exchange_instruction}

Every Chinese character you write must come from the topic knowledge base that \
follows, at or below HSK 3.0 band 2. A "what you could have said" the learner \
cannot read teaches them nothing. Give each line its 汉字, tone-marked pinyin, \
and a short English gloss."""

_EXCHANGE_WHEN_MET = """\
leave this empty. The learner met the goal; there is nothing to demonstrate."""

_EXCHANGE_WHEN_MISSED = """\
a 3–4 line exchange showing how the learner could have established the \
**first** missing fact above — their line, the partner's reply, and a natural \
close. Not a lesson, not a list of options: one short exchange they could have \
had. Start from where the conversation actually was."""


def render_verdict_prompt(
    *, goal_met, missing, turns_taken, end_reason=None, notes=None,
    unchecked_turns=0,
) -> str:
    """The one-off prompt for a session's verdict card (M2-D).

    Takes the *computed* outcome and renders it as fact. The worker is never
    asked whether the learner succeeded — that question is answered in
    `termination.py` by comparing two sets, because a judge asked it directly
    grades generously and prompting a judge out of a known bias does not work
    (`docs/SCENARIOS.md`). What is left is what models are good at: explaining
    in English, and writing a short in-band exchange.
    """
    # A turn nobody graded is not a turn the learner failed. Saying "you never
    # asked" about a turn we could not check is the false negative
    # `ACCESSIBILITY.md` exists to prevent, at the moment it is most visible —
    # and unlike a live turn, there is no next turn to correct it.
    unchecked_block = (
        f"\n\n**{unchecked_turns} of the learner's turns could not be checked** "
        "— our grader failed, which is our fault and not theirs. Do not say or "
        "imply that they failed to do anything in those turns. If something "
        "below is listed as not established, say plainly that you could not "
        "check part of the conversation, and keep the rest of the card warm and "
        "concrete about what you *can* see."
        if unchecked_turns
        else ""
    )
    missing_block = (
        "The learner never established:\n"
        + "\n".join(f"- {slot.description}" for slot in missing)
        if missing
        else "The learner established every fact the goal required."
    )
    reason_block = ""
    if end_reason == "closed":
        reason_block = (
            "\nThe session ended because the learner said goodbye twice — they "
            "left the conversation early. Say so kindly; it is the reason the "
            "rest went unfinished."
        )
    elif end_reason == "cap":
        reason_block = "\nThe session ran out of turns."
    elif end_reason == "ungraded":
        # The session was stopped by *us*, not by the learner and not by the
        # budget. Never narrate this as something they did.
        reason_block = (
            "\nThe session ended because our grading failed repeatedly. That is "
            "our fault. Tell them the session was cut short on our side, do not "
            "apportion any of it to them, and do not tell them to try harder."
        )
    elif end_reason == "stuck":
        # Written against the block above it. `closed` is deliberately gently
        # corrective — the learner left early and the card says so — and a
        # model that reads this reason the same way would tell someone who
        # asked for help that they should have pushed on. That is the reading
        # A1 exists to prevent (`docs/ACCESSIBILITY.md`), so both halves are
        # said out loud rather than left to inference.
        reason_block = (
            "\nThe learner stopped and asked for feedback rather than guessing. "
            "That is a reasonable thing to have done. Do not treat it as giving "
            "up, and do not tell them to keep trying next time — give them the "
            "words they were missing."
        )
    if notes:
        reason_block += "\n\nPer-turn notes from the session:\n" + "\n".join(
            f"- {note}" for note in notes
        )
    return _VERDICT_PROMPT_TEMPLATE.format(
        outcome=(
            "The learner MET the goal."
            if goal_met
            else "The learner did NOT meet the goal."
        ),
        missing_block=missing_block,
        turns_taken=turns_taken,
        unchecked_block=unchecked_block,
        reason_block=reason_block,
        exchange_instruction=_EXCHANGE_WHEN_MET if goal_met else _EXCHANGE_WHEN_MISSED,
    )
