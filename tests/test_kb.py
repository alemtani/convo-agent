"""Phase 3a `kb.py` tests — pure local I/O, real red-green TDD.

The loader's one hard invariant is determinism: `load_kb_block` is the big token
chunk frozen behind the prompt-cache breakpoint, so it must be **byte-identical**
across calls within a session (DESIGN.md's cached-prefix rule). These tests use
the committed `kb/zh/greetings` topic plus a tmp topic for the parser edges.
"""
import textwrap

import pytest

from backend import kb


def test_parse_topic_frontmatter_extracts_fields():
    md = textwrap.dedent(
        """\
        ---
        id: greetings
        display_name: "Greetings (你好)"
        target_vocab: [你, 好, 老师]
        proper_names: [小明, 小王]
        related: [self-intro, family]
        ---

        # Greetings

        Body text the parser ignores.
        """
    )
    topic = kb.parse_topic_frontmatter(md)
    assert topic.id == "greetings"
    # The quotes around the display name are stripped.
    assert topic.display_name == "Greetings (你好)"
    # Bracket lists parse to a list of CJK tokens, not one joined string.
    assert topic.target_vocab == ["你", "好", "老师"]
    assert topic.proper_names == ["小明", "小王"]
    assert topic.related == ["self-intro", "family"]


def test_parse_topic_frontmatter_handles_empty_lists():
    md = "---\nid: x\ndisplay_name: X\ntarget_vocab: []\nproper_names: []\nrelated: []\n---\n"
    topic = kb.parse_topic_frontmatter(md)
    assert topic.target_vocab == []
    assert topic.proper_names == []


def test_parse_topic_frontmatter_requires_fences():
    with pytest.raises(kb.KbError):
        kb.parse_topic_frontmatter("# no frontmatter here\n")


def test_load_topic_reads_committed_greetings():
    topic = kb.load_topic("greetings")
    assert topic.id == "greetings"
    assert "你" in topic.target_vocab
    assert "老师" in topic.target_vocab


def test_load_kb_block_includes_vocab_grammar_dialogues():
    block = kb.load_kb_block("greetings")
    # Content from each of the three source files is present.
    assert "lǎoshī" in block          # vocab.md
    assert "Adjective predicates" in block or "很" in block  # grammar.md
    assert "认识你很高兴" in block      # dialogues.md


def test_load_kb_block_is_byte_identical_across_calls():
    # The cached-prefix invariant: same topic -> same bytes, every time.
    assert kb.load_kb_block("greetings") == kb.load_kb_block("greetings")


def test_load_kb_block_orders_sections_vocab_grammar_dialogues():
    block = kb.load_kb_block("greetings")
    # Fixed section order so the bytes never reshuffle between turns/processes.
    assert block.index("lǎoshī") < block.index("Adjective") < block.index(
        "Meeting for the first time"
    )


def test_unknown_topic_raises_kberror():
    with pytest.raises(kb.KbError):
        kb.load_kb_block("does-not-exist")
    with pytest.raises(kb.KbError):
        kb.load_topic("does-not-exist")
