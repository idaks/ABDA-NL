"""Integration tests composing loader + diff_ops + serialize end-to-end."""
from __future__ import annotations

import pytest

from app.abda_bridge import (
    ArgumentationGraph,
    build_arguments,
    build_attacks,
    init_engine,
)
from app.scenario.diff_ops import apply
from app.scenario.loader import scenario_from_dict, scenario_to_rule_collection
from app.scenario.serialize import serialize_af


@pytest.fixture(scope="module", autouse=True)
def _engine():
    init_engine()


def _compute(scenario):
    rc = scenario_to_rule_collection(scenario)
    arguments = build_arguments(rc.get_all_rules())
    attacks = build_attacks(arguments)
    graph = ArgumentationGraph(arguments, attacks)
    labelling = graph.get_grounded_labelling()
    return serialize_af(scenario, arguments, attacks, labelling)


def test_toggling_assumption_flips_conclusion_label():
    # Minimal scenario: assumption a supports conclusion x via one rule.
    # With a active -> x accepted. Toggle a off -> x absent (no argument).
    raw = {
        "title": "toggle-integration",
        "assumptions": {"a": {"description": "an assumption"}},
        "conclusions": {"x": {"description": "x"}},
        "rules": {
            "r": {"type": "defeasible", "premises": ["a"], "conclusion": "x"},
        },
    }
    baseline = scenario_from_dict(raw)

    baseline_snap = _compute(baseline)
    assert baseline_snap["labels_by_proposition"]["x"] == "accepted"

    toggled = apply(baseline, [{"op": "toggle-assumption", "id": "a"}])
    toggled_snap = _compute(toggled)
    assert toggled_snap["labels_by_proposition"]["x"] == "absent"

    # Baseline must not be mutated.
    assert baseline.assumptions["a"].active is True


def test_undercut_targeting_inactive_rule_is_inert():
    # r1 is inactive, so it's omitted from the RuleCollection. r2 concludes
    # -r1 and should produce an argument, but there is nothing to attack
    # because no argument uses r1.
    raw = {
        "title": "inactive-undercut",
        "facts": {
            "p": {"description": "p"},
            "q": {"description": "q"},
        },
        "propositions": {"x": {"description": "x"}},
        "conclusions": {"c": {"description": "c"}},
        "rules": {
            "r1": {
                "type": "defeasible",
                "premises": ["p"],
                "conclusion": "x",
                "active": False,
            },
            "r2": {
                "type": "defeasible",
                "premises": ["q"],
                "conclusion": "-r1",
            },
            "r_c": {
                "type": "defeasible",
                "premises": ["x"],
                "conclusion": "c",
            },
        },
    }
    scenario = scenario_from_dict(raw)
    snap = _compute(scenario)

    # An argument concluding -r1 exists (built from r2 via fact q).
    assert any(a["conclusion"] == "-r1" for a in snap["arguments"])
    # No arguments conclude x or c (r1 is omitted, so the chain is broken).
    assert not any(a["conclusion"] == "x" for a in snap["arguments"])
    assert not any(a["conclusion"] == "c" for a in snap["arguments"])
    # And no attack edges exist, because nothing uses r1.
    assert snap["attacks"] == []
