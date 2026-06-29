"""Tests for the Proposer/Reviewer tool-use schemas."""
from __future__ import annotations

import pytest

from app.llm.edit_schemas import (
    PROPOSER_TOOLS,
    REVIEWER_TOOL,
    diff_op_from_tool_input,
    notes_from_tool_input,
    tool_for,
)
from app.scenario.diff_ops import apply
from app.scenario.loader import load_scenario

EXAMPLES_ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent / "examples"


def test_all_four_tasks_have_tools():
    assert set(PROPOSER_TOOLS) == {"add-rule", "modify-rule", "add-fact", "add-assumption"}
    for task, tool in PROPOSER_TOOLS.items():
        assert tool["name"].startswith("propose_")
        assert tool["input_schema"]["type"] == "object"
        assert "id" in tool["input_schema"]["required"]


def test_reviewer_tool_schema():
    assert REVIEWER_TOOL["name"] == "review_edit"
    required = REVIEWER_TOOL["input_schema"]["required"]
    assert "issues" in required
    # Issues are severity-tagged objects, not bare strings.
    item_schema = REVIEWER_TOOL["input_schema"]["properties"]["issues"]["items"]
    assert "severity" in item_schema["required"]
    assert set(item_schema["properties"]["severity"]["enum"]) == {"blocker", "warning", "note"}


def test_tool_for_unknown_task():
    with pytest.raises(ValueError):
        tool_for("delete-rule")


@pytest.mark.parametrize(
    "task,payload_key,example",
    [
        ("add-rule", "rule", {"type": "defeasible", "premises": ["a"], "conclusion": "b"}),
        ("modify-rule", "rule", {"type": "strict", "premises": ["a", "b"], "conclusion": "c"}),
        ("add-fact", "fact", {"description": "it rained today"}),
        (
            "add-assumption",
            "assumption",
            {"description": "the witness is reliable", "active": True, "block": 2},
        ),
    ],
)
def test_diff_op_from_tool_input_round_trip(task, payload_key, example):
    tool_input = {"id": "new_id", payload_key: example}
    op = diff_op_from_tool_input(task, tool_input)
    assert op == {"op": task, "id": "new_id", payload_key: example}


def test_new_premise_notes_optional_on_rule_tools():
    """add-rule and modify-rule expose new_premise_notes; fact/assumption don't."""
    for task in ("add-rule", "modify-rule"):
        tool = tool_for(task)
        props = tool["input_schema"]["properties"]
        assert "new_premise_notes" in props
        # Optional: must NOT appear in the required list.
        assert "new_premise_notes" not in tool["input_schema"]["required"]
    for task in ("add-fact", "add-assumption"):
        tool = tool_for(task)
        assert "new_premise_notes" not in tool["input_schema"]["properties"]


def test_notes_from_tool_input_extracts_only_valid_entries():
    tool_input = {
        "id": "r_x",
        "rule": {"type": "defeasible", "premises": ["p"], "conclusion": "c"},
        "new_premise_notes": [
            {"id": "p", "description": "the witness lied"},
            {"id": "", "description": "empty id — dropped"},
            {"id": "q"},  # missing description — dropped
            "not a dict",  # wrong type — dropped
        ],
    }
    notes = notes_from_tool_input("add-rule", tool_input)
    assert notes == [{"id": "p", "description": "the witness lied"}]


def test_notes_ignored_for_fact_and_assumption_tasks():
    tool_input = {
        "id": "new_fact",
        "fact": {"description": "x"},
        "new_premise_notes": [{"id": "y", "description": "z"}],  # wouldn't be in schema anyway
    }
    assert notes_from_tool_input("add-fact", tool_input) == []


def test_diff_op_from_tool_input_strips_new_premise_notes():
    """new_premise_notes is metadata, not part of the diff_op payload."""
    tool_input = {
        "id": "r_x",
        "rule": {"type": "defeasible", "premises": ["p"], "conclusion": "c"},
        "new_premise_notes": [{"id": "p", "description": "..."}],
    }
    op = diff_op_from_tool_input("add-rule", tool_input)
    assert "new_premise_notes" not in op
    assert "new_premise_notes" not in op["rule"]


def test_end_to_end_add_rule_applies_to_popov():
    """A minimally-valid tool-use output for add-rule must pass diff_ops.apply."""
    baseline = load_scenario(EXAMPLES_ROOT / "popov_v_hayashi" / "scenario.yaml")
    tool_input = {
        "id": "phase4_test_rule",
        "rule": {
            "type": "defeasible",
            "premises": ["popov_preposs_interest"],
            "conclusion": "popov_legit_claim",
            "category": "test",
            "source": "phase4 schema test",
            "block": 1,
        },
    }
    op = diff_op_from_tool_input("add-rule", tool_input)
    updated = apply(baseline, [op])
    assert "phase4_test_rule" in updated.rules
