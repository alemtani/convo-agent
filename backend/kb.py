"""Load a topic knowledge base from git-versioned markdown.

A topic is `kb/zh/<id>/{topic,vocab,grammar,dialogues}.md`. The orchestrator
injects `load_kb_block(topic_id)` — vocab + grammar + dialogues concatenated —
as the big token chunk frozen behind the prompt-cache breakpoint, so that block
**must be byte-identical across every call within a session** (DESIGN.md's
cached-prefix rule). We therefore read files in a fixed order with fixed headers
and never interpolate anything volatile.

`topic.md` frontmatter is a tiny, fixed `key: value` / `key: [a, b]` shape (see
`kb/zh/greetings/topic.md`), so it is hand-parsed rather than pulling in a YAML
dependency the service doesn't otherwise need.
"""
import os
from dataclasses import dataclass
from typing import List

KB_ROOT = os.path.join(os.path.dirname(__file__), os.pardir, "kb", "zh")

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


@dataclass(frozen=True)
class Topic:
    """Parsed `topic.md` frontmatter — drives selection/scope, not the prompt."""

    id: str
    display_name: str
    target_vocab: List[str]
    proper_names: List[str]
    related: List[str]


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


def parse_topic_frontmatter(md: str) -> Topic:
    """Extract the leading `--- ... ---` frontmatter block into a `Topic`."""
    if not md.lstrip().startswith("---"):
        raise KbError("topic.md is missing its frontmatter fences")
    body = md.lstrip()[3:]  # drop the opening ---
    end = body.find("\n---")
    if end == -1:
        raise KbError("topic.md frontmatter is not closed with ---")
    fields = {}
    for line in body[:end].splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, raw = line.split(":", 1)
        key, raw = key.strip(), raw.strip()
        fields[key] = _parse_list(raw) if raw.startswith("[") else _strip_scalar(raw)

    try:
        return Topic(
            id=fields["id"],
            display_name=fields["display_name"],
            target_vocab=fields.get("target_vocab", []) or [],
            proper_names=fields.get("proper_names", []) or [],
            related=fields.get("related", []) or [],
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


def load_kb_block(topic_id: str, root: str = KB_ROOT) -> str:
    """Concatenate vocab + grammar + dialogues into one deterministic block.

    This is the cached payload: byte-identical across calls for a given topic,
    with a fixed section order and headers and no interpolated/volatile content.
    """
    topic_dir = _topic_dir(topic_id, root)
    parts = []
    for filename, header in _KB_SECTIONS:
        parts.append(f"{header}\n\n{_read(os.path.join(topic_dir, filename))}")
    return "\n\n".join(parts)
