"""Tests for the scenario rules in `kb/zh/_tools/validate.py` (M2).

Note what is under test: **the validator**, not KB content. `CLAUDE.md`'s rule
that the KB's guardrail is `validate.py` and not pytest still holds — no test
here asserts anything about a topic's vocabulary. What they assert is that each
authoring rule fires on a topic that breaks it, because the rules are the whole
value of the format and a silent guardrail is worse than none.

Each fixture under `tests/fixtures/kb_scenarios/` breaks exactly one rule. They
live outside `kb/zh/` so `validate.py --all` never picks them up, and the tests
pass a synthetic `index.md` so the unrelated "listed in index.md" rule cannot
contaminate a single-rule assertion.
"""
import importlib.util
import json
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "kb_scenarios")
GREETINGS = os.path.join(ROOT, "kb", "zh", "greetings")


def _load_validate():
    """Import validate.py by path — it is a script, not an installed module."""
    path = os.path.join(ROOT, "kb", "zh", "_tools", "validate.py")
    spec = importlib.util.spec_from_file_location("kb_validate", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validate = _load_validate()


@pytest.fixture(scope="module")
def hsk():
    with open(os.path.join(ROOT, "kb", "zh", "_hsk", "hsk-3.0.json"), encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def ceiling():
    with open(os.path.join(ROOT, "kb", "zh", "_hsk", "ceiling.json"), encoding="utf-8") as f:
        return int(json.load(f)["band_ceiling"])


def _run(name, hsk, ceiling):
    """Validate one fixture against an index that already lists it."""
    topic_dir = os.path.join(FIXTURES, name)
    index_text = f"- {name}\n"
    return validate.validate_topic(topic_dir, hsk, ceiling, index_text)


# --- the reference example (acceptance) -----------------------------------


def test_committed_greetings_validates_clean(hsk, ceiling):
    with open(os.path.join(ROOT, "kb", "zh", "index.md"), encoding="utf-8") as f:
        index_text = f.read()
    errors, _ = validate.validate_topic(GREETINGS, hsk, ceiling, index_text)
    assert errors == []


# --- rule 1: an unachievable slot -----------------------------------------


def test_out_of_band_slot_word_is_rejected(hsk, ceiling):
    errors, _ = _run("out_of_band_slot", hsk, ceiling)
    assert any("苹果" in e and "band 3" in e for e in errors)
    assert any("苹果" in e and "target_vocab" in e for e in errors)


# --- rules 2 and 3: substance, and an obstacle ----------------------------


def test_a_single_request_slot_fails_on_the_substance_rule_only(hsk, ceiling):
    # The load-bearing case: a genuine obstacle, but 明天下雨吗？→ 不下雨。 and it
    # is over. Exactly one error proves the two guardrail rules are independent —
    # neither implies the other (SCENARIOS.md, "The guardrail").
    errors, _ = _run("single_request_slot", hsk, ceiling)
    assert len(errors) == 1, errors
    assert "one-exchange scenario" in errors[0]


def test_an_inform_only_scenario_fails_on_the_obstacle_rule_only(hsk, ceiling):
    errors, _ = _run("inform_only", hsk, ceiling)
    assert len(errors) == 1, errors
    assert "no request slots" in errors[0]


# --- rule 4: a malformed slot graph ---------------------------------------


def test_duplicate_slot_ids_are_rejected(hsk, ceiling):
    errors, _ = _run("duplicate_ids", hsk, ceiling)
    assert any("duplicate slot id" in e and "item" in e for e in errors)


def test_depends_on_an_unknown_slot_is_rejected(hsk, ceiling):
    errors, _ = _run("unknown_dep", hsk, ceiling)
    assert any("unknown slot" in e and "vendor" in e for e in errors)


def test_a_dependency_cycle_is_rejected(hsk, ceiling):
    errors, _ = _run("dep_cycle", hsk, ceiling)
    assert any("cycle" in e for e in errors)


# --- rule 5: a pacing override that starves the goal ----------------------


def test_an_override_below_the_derived_cap_is_rejected(hsk, ceiling):
    errors, _ = _run("override_too_low", hsk, ceiling)
    assert any("max_turns 3" in e and "derived 5" in e for e in errors)


def test_an_override_without_a_stated_reason_is_rejected(hsk, ceiling):
    # 9 is above the derived 5, so pacing is fine; the missing rationale is not.
    errors, _ = _run("override_no_reason", hsk, ceiling)
    assert len(errors) == 1, errors
    assert "max_turns_reason" in errors[0]


# --- rule 6: a task description the learner cannot read -------------------


def test_a_non_ascii_goal_is_rejected(hsk, ceiling):
    errors, _ = _run("chinese_goal", hsk, ceiling)
    assert len(errors) == 1, errors
    assert "ASCII" in errors[0]


# --- topics without a scenario --------------------------------------------


def test_a_topic_without_a_scenario_warns_but_does_not_fail(tmp_path, hsk, ceiling):
    # Topics land before their scenarios (#29), so the absence is advisory.
    (tmp_path / "topic.md").write_text(
        "---\nid: bare\ndisplay_name: Bare\ntarget_vocab: [你, 好]\n---\n", encoding="utf-8"
    )
    errors, warns = validate.validate_topic(str(tmp_path), hsk, ceiling, "- bare\n")
    assert errors == []
    assert any("scenario" in w for w in warns)


def test_unparsable_frontmatter_is_reported_as_an_error_not_a_traceback(tmp_path, hsk, ceiling):
    (tmp_path / "topic.md").write_text(
        "---\nid: broken\ndisplay_name: Broken\ntarget_vocab: [你]\n"
        "scenario:\n  situation: \"A place.\"\n  goal: \"A goal.\"\n"
        "  slots:\n    - id: x\n      kind: shout\n      description: \"x\"\n---\n",
        encoding="utf-8",
    )
    errors, _ = validate.validate_topic(str(tmp_path), hsk, ceiling, "- broken\n")
    assert any("kind" in e for e in errors)
