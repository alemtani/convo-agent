"""Phase 3a `kb.py` tests — pure local I/O, real red-green TDD.

The loader's one hard invariant is determinism: `load_kb_block` is the big token
chunk frozen behind the prompt-cache breakpoint, so it must be **byte-identical**
across calls within a session (DESIGN.md's cached-prefix rule). These tests use
the committed `kb/zh/greetings` topic plus a tmp topic for the parser edges.

M2 adds the `scenario:` block (`docs/SCENARIOS.md`): authored slots, and one
derived turn cap. The parser's job is *structure* — the semantic guardrails
(`n_slots > 1`, a starving `max_turns` override) belong to `validate.py` at
authoring time, so a degenerate-but-well-formed scenario must still parse.
Otherwise the validator could never report on it.
"""
import dataclasses
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


def test_list_topic_ids_finds_the_committed_topics():
    ids = kb.list_topic_ids()
    assert "greetings" in ids
    # Non-topic entries under the root (`_hsk/`, `_tools/`, `pacing.json`,
    # `index.md`) must not appear — none of them has a `topic.md`.
    assert "_hsk" not in ids
    assert "_tools" not in ids


def test_list_topic_ids_is_sorted():
    ids = kb.list_topic_ids()
    assert ids == sorted(ids)


def test_list_topic_ids_empty_root_returns_empty(tmp_path):
    assert kb.list_topic_ids(str(tmp_path)) == []


def test_list_topic_ids_missing_root_returns_empty(tmp_path):
    assert kb.list_topic_ids(str(tmp_path / "does-not-exist")) == []


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
    assert not hasattr(scenario.slots[2], "depends_on")


def test_parse_scenario_defaults_optional_slot_fields():
    scenario = kb.parse_topic_frontmatter(SCENARIO_MD).scenario
    assert scenario.max_turns_reason is None
    assert scenario.authored_max_turns is None


def test_depends_on_is_an_unknown_slot_key():
    """A2 cut. The field earned a cycle check and a log line, and never
    blocked a credit. Leftover YAML must fail loudly so authors do not
    keep writing it."""
    md = SCENARIO_MD.replace(
        "      expressible_with: [多少, 钱]\n",
        "      expressible_with: [多少, 钱]\n      depends_on: [item]\n",
    )
    with pytest.raises(kb.KbError, match="unknown key"):
        kb.parse_topic_frontmatter(md)


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


# --- M2-C: the scenario on the per-turn path ------------------------------


def test_load_scenario_returns_the_parsed_scenario():
    scenario = kb.load_scenario("greetings")
    assert [s.id for s in scenario.slots] == ["self_name", "partner_name", "wellbeing"]
    assert scenario.max_turns == 7


def test_load_scenario_is_memoized_like_the_kb_block():
    """It is now read on every turn, so it must not re-parse `topic.md` each time.

    `load_kb_block` is cached for exactly this reason; termination needs the
    parsed `Scenario` rather than the rendered string, and an uncached loader
    would put a blocking file read back on the async hot path.
    """
    assert kb.load_scenario("greetings") is kb.load_scenario("greetings")


def test_load_scenario_is_none_for_a_topic_without_one(tmp_path):
    """Topics can land before their scenario does (#29) — that is not an error."""
    topic_dir = tmp_path / "bare"
    topic_dir.mkdir()
    (topic_dir / "topic.md").write_text(
        "---\nid: bare\ndisplay_name: \"Bare\"\ntarget_vocab: [你, 好]\n---\n\n# Bare\n",
        encoding="utf-8",
    )
    assert kb.load_scenario("bare", root=str(tmp_path)) is None


def test_load_scenario_raises_for_an_unknown_topic():
    with pytest.raises(kb.KbError):
        kb.load_scenario("no-such-topic")


# --- M2-E: the topic catalog (#29) ------------------------------------------


def test_list_topics_returns_id_display_name_and_summary():
    """`index.md` is the human-facing catalog; the API reads it, not a registry."""
    topics = kb.list_topics()
    by_id = {t.id: t for t in topics}
    assert "greetings" in by_id
    assert by_id["greetings"].display_name == "Greetings (你好)"
    assert "name" in by_id["greetings"].summary.lower()


def test_list_topics_covers_every_topic_directory():
    """A topic on disk with no catalog row is an authoring bug, not a 404.

    `list_topic_ids` is what `/api/session` draws from, so a topic missing from
    the catalog would be startable and undiscoverable at the same time.
    """
    assert [t.id for t in kb.list_topics()] == kb.list_topic_ids()


def test_list_topics_is_sorted_by_id():
    ids = [t.id for t in kb.list_topics()]
    assert ids == sorted(ids)


def test_list_topics_ignores_rows_without_a_topic_dir(tmp_path):
    """A stale catalog row must not invent a topic the server cannot load."""
    (tmp_path / "real").mkdir()
    (tmp_path / "real" / "topic.md").write_text(
        "---\nid: real\ndisplay_name: \"Real\"\n---\n", encoding="utf-8"
    )
    (tmp_path / "index.md").write_text(
        "| id | display name | HSK band | summary |\n"
        "|---|---|---|---|\n"
        "| [real](real/topic.md) | Real | 1–2 | A real topic. |\n"
        "| [ghost](ghost/topic.md) | Ghost | 1–2 | Deleted last week. |\n",
        encoding="utf-8",
    )
    topics = kb.list_topics(str(tmp_path))
    assert [t.id for t in topics] == ["real"]


def test_list_topics_falls_back_to_frontmatter_when_row_is_missing(tmp_path):
    """A topic with no catalog row still lists — with an empty summary.

    Degrading beats hiding: an un-catalogued topic is drawable by
    `/api/session`, so it has to be nameable by `/api/topics` too.
    """
    (tmp_path / "orphan").mkdir()
    (tmp_path / "orphan" / "topic.md").write_text(
        "---\nid: orphan\ndisplay_name: \"Orphan (孤)\"\n---\n", encoding="utf-8"
    )
    (tmp_path / "index.md").write_text("no table here\n", encoding="utf-8")
    topics = kb.list_topics(str(tmp_path))
    assert [(t.id, t.display_name, t.summary) for t in topics] == [
        ("orphan", "Orphan (孤)", "")
    ]


# --- authored withholding (V2) --------------------------------------------
#
# `docs/VALIDITY.md`: a goal-blind converser cannot withhold a `request` slot's
# answer, because it does not know the slot exists. The scene has to do that
# work instead, so the withholding becomes an authored field — prose about what
# the situation does not offer, never a per-slot list, which would be the rubric
# under another name.


def test_parse_scenario_reads_the_withholding_field():
    md = SCENARIO_MD.replace(
        "  goal:",
        '  withholding: "No prices are posted, and the vendor does not name one'
        ' unless asked."\n  goal:',
    )
    scenario = kb.parse_topic_frontmatter(md).scenario
    assert scenario.withholding == (
        "No prices are posted, and the vendor does not name one unless asked."
    )


def test_withholding_defaults_to_none_so_topics_can_land_before_it():
    assert kb.parse_topic_frontmatter(SCENARIO_MD).scenario.withholding is None


def test_render_scenario_block_never_carries_the_withholding_prose():
    """The converser's cached prefix must not receive it.

    Withholding reaches the partner as *scene*, through the sketch worker's
    persona — never through the slot block, whose whole problem is that it tells
    the partner what is being scored. Rendering it here would put the two
    channels back in one place and hand a blind converser the rubric it is meant
    to have lost.
    """
    md = SCENARIO_MD.replace(
        "  goal:", '  withholding: "No prices are posted."\n  goal:'
    )
    scenario = kb.parse_topic_frontmatter(md).scenario
    assert "No prices are posted" not in kb.render_scenario_block(scenario)


# --- V2: the converser is blind to the rubric -----------------------------
#
# `docs/VALIDITY.md`. The partner used to be handed the slot list and told not to
# give the answers away. It cannot be trusted with that: a partner that knows
# `recommendation` is a slot will take a non-sequitur about dishes as an answer
# to a question about drinks, because it can see the checkbox behind it. So it
# stops being told. What replaces the instruction is the *scene*.


def test_the_converser_block_carries_the_scene_and_not_the_rubric():
    block = kb.load_converser_block("greetings")
    scenario = kb.load_scenario("greetings")
    assert scenario.situation in block
    assert scenario.withholding in block
    # The rubric, in every form it could leak.
    assert scenario.goal not in block
    for slot in scenario.slots:
        assert slot.id not in block
        assert slot.description not in block
    # `kind` is checked against the scene alone: "inform" is a substring of
    # "informal" in the vocab glosses, so the whole-block form is a false alarm.
    scene = kb.render_scene_block(scenario)
    for slot in scenario.slots:
        assert slot.kind not in scene


def test_the_converser_block_is_byte_identical_across_calls():
    """Still the cached prefix — blinding it must not unfreeze it."""
    kb.load_converser_block.cache_clear()
    first = kb.load_converser_block("greetings")
    kb.load_converser_block.cache_clear()
    assert kb.load_converser_block("greetings") == first
    assert kb.load_converser_block("greetings") == first


def test_the_converser_block_starts_from_the_vocab_the_partner_speaks():
    block = kb.load_converser_block("greetings")
    vocab = kb.load_vocab_block("greetings")
    # Everything above the authoring notes, which the partner never sees.
    assert block.startswith(vocab.split("## Notes for the sketch generator")[0].rstrip())


def test_the_scene_block_omits_the_goal_and_the_slots(tmp_path):
    scenario = kb.load_scenario("greetings")
    scene = kb.render_scene_block(scenario)
    assert "SCENE" in scene
    assert scenario.situation in scene
    assert scenario.withholding in scene
    assert "Goal" not in scene
    assert "Slots" not in scene


def test_a_topic_without_a_scenario_still_has_a_converser_block(tmp_path):
    """Topics land before their scenarios do. No scene is not an error."""
    md = "---\nid: bare2\ndisplay_name: Bare\ntarget_vocab: [\u4f60]\n---\n"
    topic_id, root = _write_topic(tmp_path, md, topic_id="bare2")
    block = kb.load_converser_block(topic_id, root)
    assert "SCENE" not in block
    assert block == kb.load_vocab_block(topic_id, root)


def test_a_scene_with_nothing_withheld_says_only_the_situation(tmp_path):
    """`withholding` is required only once a scenario has a `request` slot, so
    a scene may legitimately have none. It must not render an empty heading."""
    scenario = dataclasses.replace(kb.load_scenario("greetings"), withholding=None)
    scene = kb.render_scene_block(scenario)
    assert scenario.situation in scene
    assert "Withheld" not in scene


def test_the_converser_block_drops_the_authoring_notes():
    """`dialogues.md` ends with notes addressed to the *sketch generator*, and
    they are written in rubric terms: `self-intro` names its `request` slots and
    cites `SCENARIOS.md`, `food-ordering` lists the slot arc as "mid-arc beats",
    `greetings` tells the partner to close once `target_turns` is approached.

    They rode the converser's cached prefix for the whole of M2. Blinding the
    scenario block alone would have left the rubric in the prompt and the
    blindness invariant satisfied on paper — so the notes come out here, where
    the partner reads, and stay where the sketch worker reads.
    """
    for topic_id in kb.list_topic_ids():
        block = kb.load_converser_block(topic_id)
        assert "Notes for the sketch generator" not in block
        assert "target_turns" not in block
        assert "slot" not in block


def test_the_sketch_still_gets_the_authoring_notes():
    """They are useful there — that is who they were written for."""
    assert "Notes for the sketch generator" in kb.load_vocab_block("greetings")
