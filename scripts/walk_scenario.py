#!/usr/bin/env python3
"""Walk a scenario end to end and report whether it is winnable.

Issue #29's acceptance bar: every authored scenario must be *completable* —
start a session, play the learner, and reach `status: complete` with
`goal_met: true`. `validate.py` cannot answer that. It checks a scenario is
well-formed and in scope; it cannot check that a real partner, given this KB,
will actually hand over the facts a `request` slot asks for.

That is the failure this catches: a scenario that validates and still cannot be
won, because the partner has no natural way to say the thing, or the tracker
never credits the slot. Both are content bugs, and both are invisible until a
session runs.

This hits real Claude and costs money, so it is a **dev-time tool run by hand**,
not part of the test suite — the same line `scripts/replay.py` sits on. It is
also non-deterministic by construction (an LLM plays the learner), which is
exactly what `CLAUDE.md` keeps out of CI.

Usage (server running on :8000):

    .venv/bin/python scripts/walk_scenario.py                 # every topic
    .venv/bin/python scripts/walk_scenario.py --topic family
    .venv/bin/python scripts/walk_scenario.py --runs 3        # flaky-slot hunt

The learner it plays is deliberately *competent*, not average: it knows the
slots and asks for them directly. A scenario this learner cannot win is broken
for certain. One this learner wins may still be too hard for a beginner — that
question needs a real session on a phone, which is why a KB change still earns a
tunnel check.
"""
import argparse
import asyncio
import json
import os
import sys

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from anthropic import AsyncAnthropic  # noqa: E402

from backend import config, kb  # noqa: E402

DEFAULT_BASE_URL = "http://127.0.0.1:8000"

# The learner is told the slots because the point is to test the *scenario*,
# not the learner. A learner who never asks the question cannot distinguish
# "the partner won't answer" from "nobody asked" — and only the first is a bug.
LEARNER_PROMPT = """\
You are playing a beginner Mandarin learner in a practice conversation. Reply \
with ONE short Mandarin line and nothing else — no translation, no pinyin, no \
quotes, no explanation.

The situation: {situation}
Your goal: {goal}

Facts you still need to establish, in your own words:
{remaining}

Rules:
- One short sentence. Beginner Mandarin, HSK bands 1-2.
- Go after a fact you still need. Do not chat aimlessly.
- If every fact is established, say a natural goodbye.
"""


async def learner_line(client, *, situation, goal, remaining, dialogue):
    """One learner utterance, from a model that knows what it still needs."""
    history = "\n".join(f"{t['role']}: {t['zh']}" for t in dialogue[-10:])
    resp = await client.messages.create(
        model=config.CONVERSATION_MODEL,
        max_tokens=64,
        system=LEARNER_PROMPT.format(
            situation=situation,
            goal=goal,
            remaining="\n".join(f"- {r}" for r in remaining) or "- (all done)",
        ),
        messages=[{"role": "user", "content": history or "(the conversation has not started)"}],
    )
    return "".join(block.text for block in resp.content if block.type == "text").strip()


async def walk(http, anthropic_client, topic_id, *, verbose):
    """One session, start to finish. Returns the final state dict."""
    resp = await http.post("/api/session")
    resp.raise_for_status()
    session = resp.json()

    # `/api/session` picks its own topic, so a walk of a *named* topic overrides
    # it on the turns. The sketch is then flavour from a different scene, which
    # costs nothing here: this asks whether the slots are winnable, not whether
    # the persona is coherent.
    topic_id = topic_id or session["topic_id"]
    scenario = kb.load_scenario(topic_id)
    if scenario is None:
        raise SystemExit(f"{topic_id} has no scenario")

    descriptions = {slot.id: slot.description for slot in scenario.slots}
    dialogue = [{"role": "partner", "zh": session["opening_line"]["zh"]}]
    state = {"filled_at": {}, "status": "active", "goal_met": False}
    card = session["scenario_card"]

    turn = 0
    while state["status"] == "active":
        turn += 1
        remaining = [d for sid, d in descriptions.items() if sid not in state["filled_at"]]
        text = await learner_line(
            anthropic_client,
            situation=card["situation"],
            goal=card["goal"],
            remaining=remaining,
            dialogue=dialogue,
        )
        turn_resp = await http.post(
            "/api/turn/text",
            json={
                "topic_id": topic_id,
                "text": text,
                "dialogue": dialogue,
                "sketch": session["sketch"],
                "state": state,
            },
        )
        turn_resp.raise_for_status()
        body = turn_resp.json()
        state = body["state"]
        dialogue.append({"role": "user", "zh": body["transcript"]["zh"]})
        dialogue.append({"role": "partner", "zh": body["reply"]["zh"]})
        if verbose:
            print(f"  {turn:>2}. learner: {body['transcript']['zh']}")
            print(f"      partner: {body['reply']['zh']}")
            print(f"      filled: {sorted(state['filled_at'])}")

        # The server caps the session; this only stops a runaway loop if the
        # cap ever fails to fire, so it is generous on purpose.
        if turn > (scenario.max_turns or 12) + 5:
            state["end_reason"] = "runaway"
            break

    return state


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topic", help="one topic id (default: every topic with a scenario)")
    parser.add_argument("--runs", type=int, default=1, help="walks per topic")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--passcode", default=os.environ.get("APP_PASSCODE", ""))
    parser.add_argument("-v", "--verbose", action="store_true", help="print every turn")
    args = parser.parse_args()

    if not config.ANTHROPIC_API_KEY:
        raise SystemExit("ANTHROPIC_API_KEY is not configured")

    topics = [args.topic] if args.topic else [
        t for t in kb.list_topic_ids() if kb.load_scenario(t) is not None
    ]
    anthropic_client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)

    failures = []
    async with httpx.AsyncClient(base_url=args.base_url, timeout=120.0) as http:
        if args.passcode:
            (await http.post("/api/auth", json={"passcode": args.passcode})).raise_for_status()

        for topic_id in topics:
            for run in range(args.runs):
                label = f"{topic_id}" + (f" (run {run + 1})" if args.runs > 1 else "")
                print(f"\n=== {label} ===")
                state = await walk(http, anthropic_client, topic_id, verbose=args.verbose)
                won = state["status"] == "complete" and state.get("goal_met")
                missing = sorted(
                    s.id for s in kb.load_scenario(topic_id).slots
                    if s.id not in state["filled_at"]
                )
                print(
                    f"{'PASS' if won else 'FAIL'} {label}: "
                    f"status={state['status']} goal_met={state.get('goal_met')} "
                    f"end_reason={state.get('end_reason')} missing={missing}"
                )
                if not won:
                    failures.append((label, missing))

    print("\n" + "=" * 60)
    if failures:
        print(f"{len(failures)} walk(s) did not reach the goal:")
        for label, missing in failures:
            print(f"  - {label}: never filled {missing}")
        # A scenario that cannot be won is a content bug, so this is a
        # non-zero exit — it is meant to be usable as a gate by hand.
        raise SystemExit(1)
    print(f"all {len(topics)} scenario(s) reached the goal")


if __name__ == "__main__":
    asyncio.run(main())
