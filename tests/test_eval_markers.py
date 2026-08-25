"""A0.6: an excluded suite rots. Keep the split from quietly collapsing.

Behavioral evals must stay in the default gate. Contact tests must stay live.
If someone re-marks the evals `live`, they fall back behind the exclusion
that hid every ValueError after the grader split, and this test fails first.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).parent

EVAL_FILES = (
    "test_conversation_eval.py",
    "test_session_eval.py",
    "test_grader_eval.py",
    "test_turn_eval.py",
)

CONTACT_FILES = (
    "test_conversation_live.py",
    "test_session_live.py",
    "test_turn_live.py",
    "test_tts_live.py",
    "test_stt.py",
    "test_pronunciation.py",
)


def _mark_names(expr):
    """Mark identifiers on a pytest.mark expression, including `pytestmark =`."""
    names = []

    def walk(node):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute):
            if (
                isinstance(node.value.value, ast.Name)
                and node.value.value.id == "pytest"
                and node.value.attr == "mark"
            ):
                names.append(node.attr)
        for child in ast.iter_child_nodes(node):
            walk(child)

    walk(expr)
    return names


def _file_marks(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    marks = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "pytestmark":
                    marks.extend(_mark_names(node.value))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for deco in node.decorator_list:
                marks.extend(_mark_names(deco))
    return marks


def test_cassette_evals_are_not_marked_live():
    for name in EVAL_FILES:
        marks = _file_marks(ROOT / name)
        assert "live" not in marks, (
            f"{name} is marked live — it will rot behind the default exclusion"
        )
        assert "cassette" in marks


def test_contact_files_are_marked_live():
    for name in CONTACT_FILES:
        marks = _file_marks(ROOT / name)
        assert "live" in marks, (
            f"{name} is not marked live — it will spend money in the default gate"
        )


def test_pytest_cassette_client_replays_by_default(cassette_client):
    assert cassette_client.record is False
