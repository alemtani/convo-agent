"""Recorded turns, and the gold labels held apart from them (V0).

`docs/VALIDITY.md` asks whether `coherence` can carry a gate. Answering that
needs two things the repo did not have: recorded turns to judge, and an
independent opinion of what each turn deserves.

**The labels live in their own file on purpose.** A case is a transcript and
nothing else — no verdict, no hint of one. Whoever labels reads the transcript
and writes `gold.json`, and the writer of the cases is not obliged to be the
labeller. That separation is the same one the whole track is about: the party
that judges should not also be the party with a stake in the answer.
"""
import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# Two tags since A4, where three collapsed into two: `on_track` → `coherent`,
# and both `drifting` and `off_track` → `incoherent`. The three tags were built
# to *measure* — to find out whether the signal separated gaming from earned
# credit at all. It does, so it is a gate now, and a gate has one consequence:
# `drifting` and `off_track` mean the same thing to it. A legitimate topic
# change is caught by that collapse. It is a real cost, taken deliberately.
COHERENCE_TAGS = ("coherent", "incoherent")

GOLD_FILENAME = "gold.json"

# Any `gold*.json` is a label set, never a case. A second labeller's opinion
# lands beside the first as `gold.second-opinion.json`, and a case set that
# swallowed it would replay someone's judgment as if it were a transcript.
GOLD_PREFIX = "gold"


class CaseError(Exception):
    """A case set that cannot be trusted: malformed, mislabelled, or unpaired."""


def _opening_line(raw) -> Optional[dict]:
    """Normalise the wire shape to `{zh, pinyin}` or `None`.

    A bare string is accepted as `zh` so a fixture written by hand is not
    rejected for missing pinyin the grader never reads.
    """
    if not raw:
        return None
    if isinstance(raw, str):
        zh = raw.strip()
        return {"zh": zh, "pinyin": ""} if zh else None
    zh = (raw.get("zh") or "").strip()
    if not zh:
        return None
    return {"zh": zh, "pinyin": raw.get("pinyin") or ""}


@dataclass(frozen=True)
class Case:
    """One recorded learner turn, in enough context to replay it.

    Mirrors what `/api/turn/text` submits — the client holds the transcript, so
    a replay is the same shape as a turn: topic, sketch, prior dialogue, the
    words the learner just said, and the opening line a turn-1 grade is judged
    against. The opening line is never in `dialogue` (`docs/SCENARIOS.md`,
    "Definition of a turn"), so without it the grader sees the learner's first
    words and nothing they answered.
    """

    id: str
    topic_id: str
    sketch: str
    dialogue: Tuple[dict, ...]
    learner_turn: str
    notes: str = ""
    state: dict = field(default_factory=dict)
    # Utterance dict `{zh, pinyin}` as the client resubmits it, or `None` when
    # the case is not turn 1. The grader only reads `zh`.
    opening_line: Optional[dict] = None


@dataclass(frozen=True)
class Gold:
    """What this turn deserved, in the judgment of a human (or a second agent).

    `coherence` is the tag a fair reader would give. `credit_ok` is the separate
    and more important question: **may this turn earn slot credit?** They are not
    the same field. A turn can wander and still establish a fact, and the whole
    risk the gate carries is that it reads the first and decides the second.

    Since A4 the tag is the **partner's** judgment, so it is scored from
    `evals/turn` — the runner that runs a partner — rather than from the
    grader-only runner next door, which no longer produces one.
    """

    case_id: str
    coherence: str
    credit_ok: bool
    slots_established: Tuple[str, ...] = ()
    rationale: str = ""


def load_cases(directory: str) -> List[Case]:
    """Read every case in `directory`, sorted by id. The gold file is not a case."""
    cases = []
    for filename in sorted(os.listdir(directory)):
        if not filename.endswith(".json") or filename.startswith(GOLD_PREFIX):
            continue
        path = os.path.join(directory, filename)
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
        stem = filename[: -len(".json")]
        if payload.get("id") != stem:
            raise CaseError(
                f"{filename}: case id {payload.get('id')!r} does not match its filename "
                f"{stem!r} — the id is how a gold label finds it"
            )
        cases.append(
            Case(
                id=stem,
                topic_id=payload["topic_id"],
                sketch=payload.get("sketch", ""),
                dialogue=tuple(payload.get("dialogue", [])),
                learner_turn=payload["learner_turn"],
                notes=payload.get("notes", ""),
                state=payload.get("state", {}),
                opening_line=_opening_line(payload.get("opening_line")),
            )
        )
    return cases


def load_gold(path: str) -> Dict[str, Gold]:
    """Read the label file: case id → `Gold`.

    Both fields are required. A label file that omits `credit_ok` has answered
    the easy question and skipped the one V1 depends on, so it is an error
    rather than a default — a default here would silently manufacture consent.
    """
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    gold = {}
    for case_id, entry in payload.items():
        tag = entry.get("coherence")
        if tag not in COHERENCE_TAGS:
            raise CaseError(
                f"{case_id}: coherence {tag!r} is not one of {COHERENCE_TAGS}"
            )
        if "credit_ok" not in entry:
            raise CaseError(
                f"{case_id}: no credit_ok — a label must say whether the turn may "
                "earn credit, which is the question a gate actually decides"
            )
        # A real boolean, not anything truthy. `bool("false")` is `True`, so
        # coercing here would hand a labeller who wrote a string the silent yes
        # this check exists to prevent.
        if not isinstance(entry["credit_ok"], bool):
            raise CaseError(
                f"{case_id}: credit_ok is {entry['credit_ok']!r} — it must be a "
                "JSON boolean; anything else coerces to a yes nobody wrote"
            )
        gold[case_id] = Gold(
            case_id=case_id,
            coherence=tag,
            credit_ok=entry["credit_ok"],
            slots_established=tuple(entry.get("slots_established", [])),
            rationale=entry.get("rationale", ""),
        )
    return gold


def paired(cases: List[Case], gold: Dict[str, Gold]) -> List[Tuple[Case, Gold]]:
    """Zip cases to labels, refusing any set where the two disagree about membership.

    An unlabelled case would quietly leave a hole in the matrix; a label with no
    case is a transcript someone deleted and a judgment left behind. Neither is
    a measurement anyone should read.
    """
    case_ids = {case.id for case in cases}
    missing = sorted(case_ids - set(gold))
    if missing:
        raise CaseError(f"cases with no gold label: {', '.join(missing)}")
    orphaned = sorted(set(gold) - case_ids)
    if orphaned:
        raise CaseError(f"gold labels with no case: {', '.join(orphaned)}")
    return [(case, gold[case.id]) for case in cases]
