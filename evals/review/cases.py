"""Finished sessions, and the gold labels held apart from them (A6.5).

A **review case** is not a turn. It is a whole session as the client held it at
the moment the verdict is asked for: the opening line, every turn, and the
`SessionState` the *live* grades actually produced — including the credit those
grades missed. `feedback.review_session` then re-reads it with hindsight, and
the question this corpus answers is how much of the missed credit comes back.

The labels live in their own file, for the same reason the grader's do
(`evals/coherence/cases.py`): a case is a transcript and a state, with no
verdict attached. Whoever labels reads the session and writes `gold.json`.

Three fields, and the third is what makes the measurement honest:

- `recoverable` — slots the session plainly establishes that the submitted
  state does not carry. Almost always an earlier turn, which is the case A6
  found weak; on a session whose last grade also failed it includes the final
  turn, and the learner is owed both the same way.
- `already_credited` — what the live grades did get, restated from the state so
  a labeller has to look at it. A slot here is not recoverable; it is not on
  the table at all.
- `never_established` — slots no turn in the session establishes. Credit
  reported for one of these is **spurious**: the review inventing a fact to be
  generous, which is worse than the miss it was fixing.

Together they must account for every slot in the scenario. A slot in none of
them is one nobody judged, and a recall number computed over an incomplete
labelling reads as evidence while being arithmetic over a hole.
"""
import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

GOLD_FILENAME = "gold.json"

# Any `gold*.json` is a label set, never a case — the same rule the grader's
# corpus uses, so a second opinion can land beside the first.
GOLD_PREFIX = "gold"


class ReviewCaseError(Exception):
    """A case set that cannot be trusted: malformed, mislabelled, or unpaired."""


def _opening_line(raw) -> Optional[dict]:
    """Normalise the wire shape to `{zh, pinyin}` or `None`.

    A bare string is accepted as `zh`: the review reads only the 汉字, and a
    fixture written by hand should not be rejected for pinyin nobody looks at.
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
class ReviewCase:
    """One finished session, in the shape `/api/verdict` receives it.

    `dialogue` is every turn, learner first, in strict `user`/`partner` pairs —
    the opening line is its own field and is never part of it
    (`docs/SCENARIOS.md`, "Definition of a turn"). `state` is what the live
    grades produced, `filled_at` and `last_graded_turn` included: the review's
    input is the *under-credited* state, not the correct one.
    """

    id: str
    topic_id: str
    dialogue: Tuple[dict, ...]
    state: dict = field(default_factory=dict)
    notes: str = ""
    opening_line: Optional[dict] = None

    @property
    def turns_taken(self) -> int:
        return len(self.dialogue) // 2

    @property
    def filled(self) -> frozenset:
        return frozenset(self.state.get("filled_at", {}))


@dataclass(frozen=True)
class ReviewGold:
    """What this session's review owes the learner, in a human's judgment."""

    case_id: str
    recoverable: Tuple[str, ...] = ()
    already_credited: Tuple[str, ...] = ()
    never_established: Tuple[str, ...] = ()
    rationale: str = ""


def load_cases(directory: str) -> List[ReviewCase]:
    """Read every case in `directory`, sorted by id. A gold file is not a case."""
    cases = []
    for filename in sorted(os.listdir(directory)):
        if not filename.endswith(".json") or filename.startswith(GOLD_PREFIX):
            continue
        path = os.path.join(directory, filename)
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
        stem = filename[: -len(".json")]
        if payload.get("id") != stem:
            raise ReviewCaseError(
                f"{filename}: case id {payload.get('id')!r} does not match its "
                f"filename {stem!r} — the id is how a gold label finds it"
            )
        cases.append(
            ReviewCase(
                id=stem,
                topic_id=payload["topic_id"],
                dialogue=tuple(payload.get("dialogue", [])),
                state=payload.get("state", {}),
                notes=payload.get("notes", ""),
                opening_line=_opening_line(payload.get("opening_line")),
            )
        )
    return cases


def load_gold(path: str) -> Dict[str, ReviewGold]:
    """Read the label file: case id → `ReviewGold`.

    `recoverable` is required and may be empty — an empty list is a real label
    (this session has nothing to recover, so any credit the review reports is
    spurious), and it has to be written down rather than defaulted. A label
    that omits the field has not answered the question this corpus asks.
    """
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    gold = {}
    for case_id, entry in payload.items():
        if "recoverable" not in entry:
            raise ReviewCaseError(
                f"{case_id}: no recoverable — a label must say which slots an "
                "earlier turn established and the state does not carry, even "
                "when that list is empty"
            )
        for name in ("recoverable", "already_credited", "never_established"):
            if name in entry and not isinstance(entry[name], list):
                raise ReviewCaseError(f"{case_id}: {name} must be a JSON list")
        overlap = set(entry["recoverable"]) & set(entry.get("already_credited", []))
        if overlap:
            raise ReviewCaseError(
                f"{case_id}: {', '.join(sorted(overlap))} is both recoverable and "
                "already credited — a slot the learner already has cannot be "
                "recovered, and scoring it would count credit twice"
            )
        gold[case_id] = ReviewGold(
            case_id=case_id,
            recoverable=tuple(entry["recoverable"]),
            already_credited=tuple(entry.get("already_credited", [])),
            never_established=tuple(entry.get("never_established", [])),
            rationale=entry.get("rationale", ""),
        )
    return gold


def paired(
    cases: List[ReviewCase], gold: Dict[str, ReviewGold]
) -> List[Tuple[ReviewCase, ReviewGold]]:
    """Zip cases to labels, refusing a set where the two disagree about membership."""
    case_ids = {case.id for case in cases}
    missing = sorted(case_ids - set(gold))
    if missing:
        raise ReviewCaseError(f"cases with no gold label: {', '.join(missing)}")
    orphaned = sorted(set(gold) - case_ids)
    if orphaned:
        raise ReviewCaseError(f"gold labels with no case: {', '.join(orphaned)}")
    return [(case, gold[case.id]) for case in cases]
