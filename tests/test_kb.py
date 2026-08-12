"""Phase 3a `kb.py` tests — pure local I/O, real red-green TDD.

The loader's one hard invariant is determinism: `load_kb_block` is the big token
chunk frozen behind the prompt-cache breakpoint, so it must be **byte-identical**
across calls within a session (DESIGN.md's cached-prefix rule). These tests use
the committed `kb/zh/greetings` topic plus a tmp topic for the parser edges.

M2 adds the `scenario:` block (`docs/SCENARIOS.md`): authored slots, and one
derived turn cap. The parser's job is *structure* — the semantic guardrails
(`n_slots > 1`, dependency cycles, a starving `max_turns` override) belong to
`validate.py` at authoring time, so a degenerate-but-well-formed scenario must
still parse. Otherwise the validator could never report on it.
"""
import textwrap

import pytest

from backend import kb

SCENARIO_MD = textwrap.dedent(
    """\
    ---
    id: shopping
    display_name: "Shopping (买东西)"
    target_vocab: [买, 要, 水果, 三, 个, 多少, 钱]
    scenario:
      situation: "You're at a fruit stall. The vendor greets you."
      goal: "Buy three pieces of fruit, and find out what they cost."
      slots:
        - id: item
          kind: inform
          description: "Say you want fruit"
          expressible_with: [水果, 要, 买]
        - id: quantity
          kind: inform
          description: "Say how many — three"
          expressible_with: [三, 个]
        - id: price
          kind: request
          description: "Find out what they cost"
          expressible_with: [多少, 钱]
          depends_on: [item]
    ---

    # Shopping
    """
)


def _write_topic(tmp_path, topic_md, topic_id="tmp-topic"):
    """Materialize a minimal four-file topic; returns (topic_id, root)."""
    d = tmp_path / topic_id
    d.mkdir()
    (d / "topic.md").write_text(topic_md, encoding="utf-8")
    (d / "vocab.md").write_text("| 你 | nǐ | you | 1 |\n", encoding="utf-8")
    (d / "grammar.md").write_text("Adjective predicates.\n", encoding="utf-8")
    (d / "dialogues.md").write_text("> 你好\n", encoding="utf-8")
    return topic_id, str(tmp_path)


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


# --- load_vocab_block: the scenario-free half, for the sketch worker (M2-B) -


def test_load_vocab_block_matches_kb_block_minus_the_scenario_section():
    """`load_kb_block` = `load_vocab_block` + the scenario section, byte for
    byte — the split must not change what the conversation worker freezes."""
    assert kb.load_kb_block("greetings") == kb.load_vocab_block("greetings") + "\n\n" + kb.render_scenario_block(
        kb.load_topic("greetings").scenario
    )


def test_load_vocab_block_never_contains_the_scenario_section():
    """The sketch worker's whole KB block, by construction — slots must never
    reach a model call (`docs/SCENARIOS.md`, `backend/workers/sketch.py`)."""
    block = kb.load_vocab_block("greetings")
    assert "SCENARIO" not in block
    assert "partner_name" not in block   # a greetings slot id


def test_load_vocab_block_is_byte_identical_across_calls():
    assert kb.load_vocab_block("greetings") == kb.load_vocab_block("greetings")


# --- scenario parsing (M2) ------------------------------------------------


def test_parse_scenario_extracts_slots_in_authored_order():
    scenario = kb.parse_topic_frontmatter(SCENARIO_MD).scenario
    assert scenario.situation == "You're at a fruit stall. The vendor greets you."
    assert scenario.goal == "Buy three pieces of fruit, and find out what they cost."
    assert [s.id for s in scenario.slots] == ["item", "quantity", "price"]
    assert [s.kind for s in scenario.slots] == ["inform", "inform", "request"]
    assert scenario.slots[0].description == "Say you want fruit"
    assert scenario.slots[2].expressible_with == ("多少", "钱")
    assert scenario.slots[2].depends_on == ("item",)


def test_parse_scenario_defaults_optional_slot_fields():
    # `depends_on` is optional; an absent one is empty, never None.
    scenario = kb.parse_topic_frontmatter(SCENARIO_MD).scenario
    assert scenario.slots[0].depends_on == ()
    assert scenario.max_turns_reason is None
    assert scenario.authored_max_turns is None


def test_parse_scenario_counts_slots_and_request_slots():
    scenario = kb.parse_topic_frontmatter(SCENARIO_MD).scenario
    assert scenario.n_slots == 3
    assert scenario.n_request_slots == 1


def test_topic_without_a_scenario_block_still_parses():
    # Topics land before their scenarios do (#29), so `scenario` is optional.
    topic = kb.parse_topic_frontmatter(
        "---\nid: x\ndisplay_name: X\ntarget_vocab: [你]\n---\n"
    )
    assert topic.scenario is None


def test_parse_scenario_rejects_an_unknown_slot_kind():
    md = SCENARIO_MD.replace("kind: request", "kind: demand")
    with pytest.raises(kb.KbError, match="kind"):
        kb.parse_topic_frontmatter(md)


def test_parse_scenario_rejects_a_slot_missing_a_required_field():
    md = SCENARIO_MD.replace('      description: "Say you want fruit"\n', "")
    with pytest.raises(kb.KbError, match="description"):
        kb.parse_topic_frontmatter(md)


def test_parse_scenario_rejects_an_unknown_key():
    md = SCENARIO_MD.replace("  goal:", "  difficulty: hard\n  goal:")
    with pytest.raises(kb.KbError, match="difficulty"):
        kb.parse_topic_frontmatter(md)


def test_parse_scenario_accepts_degenerate_shapes_for_the_validator():
    # One inform slot breaks *both* authoring guardrails, but it is well-formed:
    # the parser must hand it over so `validate.py` can report on it.
    md = textwrap.dedent(
        """\
        ---
        id: x
        display_name: X
        target_vocab: [你好]
        scenario:
          situation: "You meet someone."
          goal: "Greet them."
          slots:
            - id: greeting
              kind: inform
              description: "Greet the vendor"
              expressible_with: [你好]
        ---
        """
    )
    scenario = kb.parse_topic_frontmatter(md).scenario
    assert scenario.n_slots == 1
    assert scenario.n_request_slots == 0


# --- the derived turn cap -------------------------------------------------


@pytest.mark.parametrize(
    "n_slots, n_request, expected",
    [
        (3, 1, 6),   # fruit stall            (SCENARIOS.md)
        (2, 2, 6),   # weather                (SCENARIOS.md)
        (3, 2, 7),   # directions             (SCENARIOS.md)
        (4, 1, 7),   # ordering food          (SCENARIOS.md)
        (2, 1, 5),   # the tightest budget the guardrail permits
        (3, 2, 7),   # greetings, as migrated in this PR
    ],
)
def test_derive_max_turns_table(n_slots, n_request, expected):
    assert kb.derive_max_turns(n_slots, n_request) == expected


def test_derive_max_turns_reads_the_pacing_coefficients():
    # Retuning pacing is one edit to kb/zh/pacing.json — never a code change and
    # never eight topic edits, so nothing here may be hardcoded.
    pacing = {"slot_weight": 2, "request_slot_weight": 3, "base": 1}
    assert kb.derive_max_turns(3, 1, pacing=pacing) == 2 * 3 + 3 * 1 + 1


def test_scenario_max_turns_is_derived_when_not_authored():
    scenario = kb.parse_topic_frontmatter(SCENARIO_MD).scenario
    assert scenario.max_turns == 6


def test_authored_max_turns_override_wins_and_keeps_its_reason():
    md = SCENARIO_MD.replace(
        "  slots:",
        '  max_turns: 9\n  max_turns_reason: "Vendor haggles; extraction takes longer."\n  slots:',
    )
    scenario = kb.parse_topic_frontmatter(md).scenario
    assert scenario.max_turns == 9
    assert scenario.authored_max_turns == 9
    assert scenario.max_turns_reason.startswith("Vendor haggles")


def test_an_override_without_a_reason_parses_so_the_validator_can_reject_it():
    md = SCENARIO_MD.replace("  slots:", "  max_turns: 4\n  slots:")
    scenario = kb.parse_topic_frontmatter(md).scenario
    assert scenario.max_turns == 4
    assert scenario.max_turns_reason is None


# --- the scenario inside the cached block ---------------------------------


def test_load_kb_block_includes_the_scenario_section(tmp_path):
    topic_id, root = _write_topic(tmp_path, SCENARIO_MD)
    block = kb.load_kb_block(topic_id, root)
    assert "# SCENARIO" in block
    assert "You're at a fruit stall" in block
    for fragment in ("item", "[inform]", "price", "[request]", "Find out what they cost"):
        assert fragment in block


def test_load_kb_block_scenario_section_comes_last(tmp_path):
    topic_id, root = _write_topic(tmp_path, SCENARIO_MD)
    block = kb.load_kb_block(topic_id, root)
    assert block.index("VOCABULARY") < block.index("GRAMMAR") < block.index(
        "DIALOGUES"
    ) < block.index("SCENARIO")


def test_load_kb_block_with_a_scenario_is_byte_identical_across_calls(tmp_path):
    topic_id, root = _write_topic(tmp_path, SCENARIO_MD)
    first = kb.load_kb_block(topic_id, root)
    kb.load_kb_block.cache_clear()
    # Byte-identical even on a cold cache — the section is a pure function of the
    # authored bytes, so it survives a process restart mid-session.
    assert kb.load_kb_block(topic_id, root) == first


def test_load_kb_block_omits_the_section_when_a_topic_has_no_scenario(tmp_path):
    md = "---\nid: bare\ndisplay_name: Bare\ntarget_vocab: [你]\n---\n"
    topic_id, root = _write_topic(tmp_path, md, topic_id="bare")
    assert "SCENARIO" not in kb.load_kb_block(topic_id, root)


def test_load_kb_block_omits_the_turn_cap(tmp_path):
    # `max_turns` bounds the session; it is not the partner's business. Telling
    # the model its budget invites turn-counting instead of pursuing the goal
    # (SCENARIOS.md, "Pressure" — state drives steering, not a counter).
    topic_id, root = _write_topic(tmp_path, SCENARIO_MD)
    assert "max_turns" not in kb.load_kb_block(topic_id, root)


def test_committed_greetings_carries_a_valid_scenario():
    scenario = kb.load_topic("greetings").scenario
    assert scenario is not None
    # The migrated reference example must satisfy the authoring guardrails.
    assert scenario.n_slots > 1
    assert scenario.n_request_slots >= 1
    assert scenario.max_turns == kb.derive_max_turns(
        scenario.n_slots, scenario.n_request_slots
    )
