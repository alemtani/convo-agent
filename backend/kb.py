"""Load a topic knowledge base from git-versioned markdown.

A topic is `kb/zh/<id>/{topic,vocab,grammar,dialogues}.md`. The orchestrator
injects `load_kb_block(topic_id)` — vocab + grammar + dialogues concatenated —
as the big token chunk frozen behind the prompt-cache breakpoint, so that block
**must be byte-identical across every call within a session** (DESIGN.md's
cached-prefix rule). We therefore read files in a fixed order with fixed headers
and never interpolate anything volatile.

`topic.md` frontmatter is a tiny, fixed `key: value` / `key: [a, b]` shape plus
one nested `scenario:` block (see `kb/zh/greetings/topic.md`), so it is
hand-parsed rather than pulling in a YAML dependency the service doesn't
otherwise need. The parser is deliberately strict about *shape* and silent about
*meaning*: `kb/zh/_tools/validate.py` imports it and owns every semantic rule, so
a degenerate-but-well-formed scenario has to parse or the validator could never
report on it.
"""
import functools
import json
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

KB_ROOT = os.path.join(os.path.dirname(__file__), os.pardir, "kb", "zh")
PACING_PATH = os.path.join(KB_ROOT, "pacing.json")

# Fixed assembly order — the contract that keeps the block byte-stable. The
# header before each file makes the concatenation legible to the model without
# adding any per-call variation.
_KB_SECTIONS = (
    ("vocab.md", "# VOCABULARY"),
    ("grammar.md", "# GRAMMAR"),
    ("dialogues.md", "# DIALOGUES"),
)


class KbError(Exception):
    """A topic is missing, or its markdown can't be parsed."""


# The scenario block's fixed shape. Indents are exact, not minimums — a strict
# reader is the point of hand-parsing, and it keeps every topic's bytes uniform.
_SCENARIO_INDENT = 2
_SLOT_ITEM_INDENT = 4
_SLOT_KEY_INDENT = 6
_SLOT_KINDS = ("inform", "request")
_SCENARIO_KEYS = ("situation", "goal", "slots", "max_turns", "max_turns_reason")
_SLOT_KEYS = ("id", "kind", "description", "expressible_with", "depends_on")
_SLOT_REQUIRED = ("id", "kind", "description")


@dataclass(frozen=True)
class Slot:
    """One binary success criterion. `filled ⊇ required` is the goal, in Python.

    `expressible_with` is a hint to the extractor and a handle for the validator
    — never a string matcher. The extractor decides *semantically* whether the
    fact was established (`docs/SCENARIOS.md`, "Seed format").
    """

    id: str
    kind: str  # "inform" (learner conveys) | "request" (learner extracts)
    description: str
    expressible_with: Tuple[str, ...] = ()
    depends_on: Tuple[str, ...] = ()


@dataclass(frozen=True)
class Scenario:
    """The authored goal: what the learner is trying to accomplish, as state."""

    situation: str  # English, learner-visible
    goal: str  # English, learner-visible
    slots: Tuple[Slot, ...]
    max_turns: int  # effective cap: the authored override, else derived
    authored_max_turns: Optional[int] = None
    max_turns_reason: Optional[str] = None

    @property
    def n_slots(self) -> int:
        return len(self.slots)

    @property
    def n_request_slots(self) -> int:
        return sum(1 for s in self.slots if s.kind == "request")


@dataclass(frozen=True)
class Topic:
    """Parsed `topic.md` frontmatter — drives selection/scope, not the prompt."""

    id: str
    display_name: str
    target_vocab: List[str]
    proper_names: List[str]
    related: List[str]
    # Optional: topics can land before their scenario does (#29), and the
    # runtime requirement lands with the scenario loop itself (#30).
    scenario: Optional[Scenario] = None


@dataclass(frozen=True)
class TopicSummary:
    """One catalog row — what `GET /api/topics` shows before a session starts.

    Deliberately not `Topic`: the catalog is a *browsing* view. It carries the
    blurb a learner picks by and nothing a session needs, so listing topics
    never parses a scenario or a vocab list it will not use.
    """

    id: str
    display_name: str
    summary: str


def _catalog_rows(root: str) -> Dict[str, Tuple[str, str]]:
    """`id -> (display_name, summary)` from the `index.md` markdown table.

    The catalog lives in `index.md` because it is written for a human reading
    the repo, and a second machine-readable copy would drift from it. Parsing
    is deliberately loose — an unparseable row is skipped, never raised. A bad
    blurb must not take down topic listing, and `validate.py` already owns
    telling an author their row is wrong.
    """
    try:
        text = _read(os.path.join(root, "index.md"))
    except KbError:
        return {}

    rows: Dict[str, Tuple[str, str]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 4:
            continue
        # Column 1 is a markdown link whose text is the topic id.
        match = re.match(r"\[([^\]]+)\]\(", cells[0])
        if not match:
            continue  # the header row and its `|---|` separator land here
        rows[match.group(1).strip()] = (cells[1], cells[3])
    return rows


def list_topics(root: str = KB_ROOT) -> List[TopicSummary]:
    """Every topic on disk, with its catalog blurb. Sorted by id.

    Directories are the source of truth, not the catalog: `/api/session` draws
    from `list_topic_ids`, so anything startable has to be listable. A stale
    row for a deleted topic is dropped; a topic with no row still lists, named
    from its own frontmatter with an empty summary.
    """
    rows = _catalog_rows(root)
    topics = []
    for topic_id in list_topic_ids(root):
        display_name, summary = rows.get(topic_id, ("", ""))
        if not display_name:
            display_name = load_topic(topic_id, root).display_name
        topics.append(TopicSummary(topic_id, display_name, summary))
    return topics


def _parse_list(raw: str) -> List[str]:
    """Parse an inline `[a, b, c]` list of (CJK) tokens. Empty `[]` -> []."""
    inner = raw.strip().lstrip("[").rstrip("]").strip()
    if not inner:
        return []
    return [item.strip() for item in inner.split(",") if item.strip()]


def _strip_scalar(raw: str) -> str:
    """Unquote a scalar value (`"X"` or `X`)."""
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


@functools.lru_cache(maxsize=1)
def _load_pacing(path: str = PACING_PATH) -> Dict[str, int]:
    """Read the turn-cap coefficients. Owned by the authoring workflow."""
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        return {k: int(raw[k]) for k in ("slot_weight", "request_slot_weight", "base")}
    except (OSError, KeyError, ValueError, TypeError) as exc:
        raise KbError(f"cannot read pacing coefficients from {path}: {exc}") from exc


def derive_max_turns(
    n_slots: int, n_request_slots: int, pacing: Optional[Dict[str, int]] = None
) -> int:
    """The one threshold: `slots + request_slots + 2` under the default pacing.

    A **policy**, not arithmetic — see `docs/SCENARIOS.md`. There is deliberately
    no floor: a strong learner can pack every slot into one utterance, so the
    physical minimum is 1 for every scenario and a minimum would flag a correct
    pass as a bug.
    """
    p = pacing or _load_pacing()
    return p["slot_weight"] * n_slots + p["request_slot_weight"] * n_request_slots + p["base"]


def _split_key(line: str, where: str) -> Tuple[str, str]:
    if ":" not in line:
        raise KbError(f"{where}: expected `key: value`, got {line.strip()!r}")
    key, raw = line.split(":", 1)
    return key.strip(), raw.strip()


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _parse_slot(entries: List[Tuple[str, str]]) -> Slot:
    fields = {}
    for key, raw in entries:
        if key not in _SLOT_KEYS:
            raise KbError(f"scenario slot: unknown key {key!r}")
        fields[key] = _parse_list(raw) if raw.startswith("[") else _strip_scalar(raw)
    missing = [k for k in _SLOT_REQUIRED if not fields.get(k)]
    if missing:
        raise KbError(f"scenario slot {fields.get('id')!r} missing: {', '.join(missing)}")
    if fields["kind"] not in _SLOT_KINDS:
        raise KbError(
            f"scenario slot {fields['id']!r} has kind {fields['kind']!r}; "
            f"expected one of {', '.join(_SLOT_KINDS)}"
        )
    return Slot(
        id=fields["id"],
        kind=fields["kind"],
        description=fields["description"],
        expressible_with=tuple(fields.get("expressible_with", ()) or ()),
        depends_on=tuple(fields.get("depends_on", ()) or ()),
    )


def _parse_scenario(lines: List[str]) -> Scenario:
    """Parse the indented `scenario:` block.

    Structure only. Duplicate slot ids, dependency cycles, an override that
    starves the goal and the two guardrail rules all parse cleanly here and are
    rejected by `validate.py` — that split is what lets the validator's fixtures
    exist at all.
    """
    fields, slot_entries, in_slots = {}, [], False
    for line in lines:
        if not line.strip():
            continue
        indent = _indent(line)
        if indent == _SCENARIO_INDENT:
            in_slots = False
            key, raw = _split_key(line, "scenario")
            if key not in _SCENARIO_KEYS:
                raise KbError(f"scenario: unknown key {key!r}")
            if key == "slots":
                in_slots = True
                continue
            fields[key] = _strip_scalar(raw)
        elif in_slots and indent == _SLOT_ITEM_INDENT and line.lstrip().startswith("- "):
            slot_entries.append([_split_key(line.lstrip()[2:], "scenario slot")])
        elif in_slots and indent == _SLOT_KEY_INDENT and slot_entries:
            slot_entries[-1].append(_split_key(line, "scenario slot"))
        else:
            raise KbError(f"scenario: unexpected indentation in {line.strip()!r}")

    for key in ("situation", "goal"):
        if not fields.get(key):
            raise KbError(f"scenario: missing `{key}`")
    if not slot_entries:
        raise KbError("scenario: missing `slots`")

    slots = tuple(_parse_slot(e) for e in slot_entries)
    authored = fields.get("max_turns")
    if authored is not None:
        try:
            authored = int(authored)
        except ValueError as exc:
            raise KbError(f"scenario: max_turns must be an integer, got {authored!r}") from exc
    n_request = sum(1 for s in slots if s.kind == "request")
    return Scenario(
        situation=fields["situation"],
        goal=fields["goal"],
        slots=slots,
        max_turns=authored if authored is not None else derive_max_turns(len(slots), n_request),
        authored_max_turns=authored,
        max_turns_reason=fields.get("max_turns_reason") or None,
    )


def parse_topic_frontmatter(md: str) -> Topic:
    """Extract the leading `--- ... ---` frontmatter block into a `Topic`."""
    if not md.lstrip().startswith("---"):
        raise KbError("topic.md is missing its frontmatter fences")
    body = md.lstrip()[3:]  # drop the opening ---
    end = body.find("\n---")
    if end == -1:
        raise KbError("topic.md frontmatter is not closed with ---")
    lines = body[:end].splitlines()

    fields, scenario, i = {}, None, 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if not line.strip():
            continue
        key, raw = _split_key(line, "topic.md frontmatter")
        if key == "scenario":
            if raw:
                raise KbError("scenario: must be an indented block, not an inline value")
            # Consume the whole indented block, then hand it to the sub-parser.
            block = []
            while i < len(lines) and (not lines[i].strip() or _indent(lines[i]) > 0):
                block.append(lines[i])
                i += 1
            scenario = _parse_scenario(block)
            continue
        fields[key] = _parse_list(raw) if raw.startswith("[") else _strip_scalar(raw)

    try:
        return Topic(
            id=fields["id"],
            display_name=fields["display_name"],
            target_vocab=fields.get("target_vocab", []) or [],
            proper_names=fields.get("proper_names", []) or [],
            related=fields.get("related", []) or [],
            scenario=scenario,
        )
    except KeyError as exc:
        raise KbError(f"topic.md frontmatter missing required field: {exc}") from exc


def _topic_dir(topic_id: str, root: str) -> str:
    path = os.path.join(root, topic_id)
    if not os.path.isdir(path):
        raise KbError(f"unknown topic: {topic_id!r}")
    return path


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError as exc:
        raise KbError(f"cannot read {path}: {exc}") from exc


def load_topic(topic_id: str, root: str = KB_ROOT) -> Topic:
    """Read and parse `<topic_id>/topic.md`."""
    return parse_topic_frontmatter(_read(os.path.join(_topic_dir(topic_id, root), "topic.md")))


def list_topic_ids(root: str = KB_ROOT) -> List[str]:
    """Every topic directory under `root` — a subdirectory with a `topic.md`.

    A directory check rather than a hardcoded list, so a new topic (#29)
    appears here the moment its `topic.md` lands, with no registry to update.
    It also naturally excludes `root`'s non-topic entries (`_hsk/`, `_tools/`,
    `pacing.json`, `index.md`) since none of those has a `topic.md` inside it.
    Sorted for a deterministic order — callers that need randomness (session
    topic selection) draw from this rather than relying on directory order,
    which the filesystem doesn't guarantee.
    """
    if not os.path.isdir(root):
        return []
    return sorted(
        name
        for name in os.listdir(root)
        if os.path.isfile(os.path.join(root, name, "topic.md"))
    )


@functools.lru_cache(maxsize=None)
def load_scenario(topic_id: str, root: str = KB_ROOT) -> Optional[Scenario]:
    """The parsed `scenario:` block, or `None` for a topic that has none.

    Memoized for the same reason `load_kb_block` is: M2-C reads this on **every**
    turn (`termination.advance` needs the slot ids and the turn cap), and
    `load_topic` re-reads and re-parses `topic.md` on each call — a blocking file
    read inside an async handler, which is exactly what the caching on its
    siblings exists to keep off the hot path.

    `None` is not an error: topics can land before their scenario does (#29), and
    those sessions simply run unbounded, as they did before M2-C.
    """
    return load_topic(topic_id, root).scenario


@functools.lru_cache(maxsize=None)
def load_vocab_block(topic_id: str, root: str = KB_ROOT) -> str:
    """Concatenate vocab + grammar + dialogues only — no scenario section.

    The scenario-free half of `load_kb_block`, memoized the same way. Exists
    for the sketch worker (`backend/workers/sketch.py`): it needs the topic's
    vocabulary to write an in-band opening line, but never the authored slots
    — `docs/SCENARIOS.md` states flatly that scenario criteria "never pass
    through a model," and the conversation worker's cached prefix is the only
    place slots belong. Passing this instead of `load_kb_block` is what keeps
    that true.
    """
    topic_dir = _topic_dir(topic_id, root)
    parts = []
    for filename, header in _KB_SECTIONS:
        parts.append(f"{header}\n\n{_read(os.path.join(topic_dir, filename))}")
    return "\n\n".join(parts)


@functools.lru_cache(maxsize=None)
def load_kb_block(topic_id: str, root: str = KB_ROOT) -> str:
    """Concatenate vocab + grammar + dialogues + scenario into one block.

    This is the cached payload: byte-identical across calls for a given topic,
    with a fixed section order and headers and no interpolated/volatile content.

    Memoized per `(topic_id, root)` for the life of the process — the KB is
    git-versioned and authored dev-time only (no runtime writer), so this lifts
    the blocking file reads off the async hot path after the first turn.
    `KbError` is not cached (a bad topic re-raises every call); edits to the
    markdown on disk require a process restart or `load_kb_block.cache_clear()`.
    """
    parts = [load_vocab_block(topic_id, root)]
    topic = parse_topic_frontmatter(_read(os.path.join(_topic_dir(topic_id, root), "topic.md")))
    if topic.scenario is not None:
        parts.append(render_scenario_block(topic.scenario))
    return "\n\n".join(parts)


def render_scenario_block(scenario: Scenario) -> str:
    """Render the authored scenario for the cached prefix.

    A pure function of authored bytes, so it stays byte-identical across turns
    and across process restarts — the cached-prefix rule again.

    `max_turns` is deliberately absent. The cap bounds the *session*, and telling
    the partner its budget invites turn-counting; steering comes from outstanding
    slots instead (`docs/SCENARIOS.md`, "Pressure").
    """
    lines = [
        "# SCENARIO",
        "",
        f"Situation: {scenario.situation}",
        f"Goal: {scenario.goal}",
        "",
        "Slots the learner must complete:",
    ]
    lines += [f"- {s.id} [{s.kind}] {s.description}" for s in scenario.slots]
    return "\n".join(lines) + "\n"
