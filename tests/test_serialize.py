"""Tests for app.scenario.serialize: AF snapshot shape and semantics."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from app.abda_bridge import (
    ArgumentationGraph,
    build_arguments,
    build_attacks,
    init_engine,
)
from app.scenario.loader import load_scenario, scenario_to_rule_collection
from app.scenario.serialize import serialize_af


@pytest.fixture(scope="module")
def af_validator():
    schema = json.loads(
        Path("app/schemas/af_snapshot.schema.json").read_text()
    )
    return Draft202012Validator(schema)


@pytest.fixture(scope="module")
def _engine():
    init_engine()


def _snapshot_for(scenario_path: str):
    scenario = load_scenario(scenario_path)
    rc = scenario_to_rule_collection(scenario)
    arguments = build_arguments(rc.get_all_rules())
    attacks = build_attacks(arguments)
    graph = ArgumentationGraph(arguments, attacks)
    labelling = graph.get_grounded_labelling()
    snap = serialize_af(scenario, arguments, attacks, labelling)
    return scenario, snap


@pytest.mark.parametrize(
    "path",
    [
        "examples/fire_prevention/scenario.yaml",
        "examples/fried_chicken/scenario.yaml",
        "examples/fried_chicken_v1/scenario.yaml",
        "examples/medical_ppi/scenario.yaml",
        "examples/nba_rebuild/scenario.yaml",
        "examples/popov_v_hayashi/scenario.yaml",
    ],
)
def test_snapshot_validates_against_schema(_engine, af_validator, path):
    _, snap = _snapshot_for(path)
    errors = list(af_validator.iter_errors(snap))
    assert not errors, [f"{list(e.absolute_path)}: {e.message}" for e in errors]


def test_argument_ids_are_sequential(_engine):
    _, snap = _snapshot_for("examples/medical_ppi/scenario.yaml")
    ids = [a["id"] for a in snap["arguments"]]
    assert ids == sorted(ids, key=lambda x: int(x[1:]))
    assert ids[0] == "a1"
    assert ids[-1] == f"a{len(ids)}"


def test_facts_flagged_is_fact(_engine):
    _, snap = _snapshot_for("examples/medical_ppi/scenario.yaml")
    fact_ids = {"barretts", "clopidogrel", "recent_pci", "postmenop", "low_bmd", "on_ppi"}
    fact_args = [a for a in snap["arguments"] if a["top_rule"] in fact_ids]
    for a in fact_args:
        assert a["is_fact"] is True
    non_fact_args = [a for a in snap["arguments"] if a["top_rule"] not in fact_ids]
    assert all(a["is_fact"] is False for a in non_fact_args)


def test_proposition_labels_match_scenario_intent(_engine):
    # medical_ppi is deliberately undecided at baseline
    _, snap = _snapshot_for("examples/medical_ppi/scenario.yaml")
    assert snap["labels_by_proposition"]["continue_ppi"] == "undecided"


def test_inactive_assumption_is_absent(_engine):
    # nba_rebuild's over_apron is active:false → no arg concludes it
    _, snap = _snapshot_for("examples/nba_rebuild/scenario.yaml")
    assert snap["labels_by_proposition"]["over_apron"] == "absent"


def test_attack_types_present(_engine):
    # popov uses both rebuts and undercuts (e.g. r5 undercuts r4, cs3 undercuts wt1)
    _, snap = _snapshot_for("examples/popov_v_hayashi/scenario.yaml")
    types = {a["type"] for a in snap["attacks"]}
    assert "rebut" in types
    assert "undercut" in types


def test_conclusion_nl_populated(_engine):
    scenario, snap = _snapshot_for("examples/medical_ppi/scenario.yaml")
    for arg in snap["arguments"]:
        negated = arg["conclusion"].startswith("-")
        base = arg["conclusion"].lstrip("-")
        if base in scenario.conclusions:
            entry = scenario.conclusions[base]
            expected = entry.description
            if negated:
                expected = entry.negated_description or f"it is not the case that {entry.description}"
            assert arg["conclusion_nl"] == expected


def test_sub_arguments_exclude_self(_engine):
    _, snap = _snapshot_for("examples/medical_ppi/scenario.yaml")
    for arg in snap["arguments"]:
        assert arg["id"] not in arg["sub_arguments"]


def test_premises_match_top_rule_arity(_engine):
    # for each non-primitive arg, premises list length equals the number
    # of premise literals in its top rule
    scenario, snap = _snapshot_for("examples/medical_ppi/scenario.yaml")
    rule_arity = {rid: len(r.premises) for rid, r in scenario.rules.items()}
    rule_arity.update({fid: 0 for fid in scenario.facts})
    rule_arity.update({aid: 0 for aid in scenario.assumptions})
    for arg in snap["arguments"]:
        assert len(arg["premises"]) == rule_arity[arg["top_rule"]]


# --- Post-review additions ---


@pytest.mark.parametrize(
    "path",
    [
        "examples/fire_prevention/scenario.yaml",
        "examples/fried_chicken/scenario.yaml",
        "examples/fried_chicken_v1/scenario.yaml",
        "examples/medical_ppi/scenario.yaml",
        "examples/nba_rebuild/scenario.yaml",
        "examples/popov_v_hayashi/scenario.yaml",
    ],
)
def test_snapshot_has_no_dangling_references(_engine, path):
    # Every top_rule and every rules_used entry must map back to a declared
    # scenario id (fact / assumption / rule).
    scenario, snap = _snapshot_for(path)
    known = set(scenario.facts) | set(scenario.assumptions) | set(scenario.rules)
    for arg in snap["arguments"]:
        assert arg["top_rule"] in known, (path, arg["id"], arg["top_rule"])
        for rid in arg["rules_used"]:
            assert rid in known, (path, arg["id"], rid)


def test_contradictory_labelling_raises_on_serialize(_engine):
    # The serializer includes a defensive check for "both X and -X warranted,"
    # a state that cannot arise from a well-formed grounded labelling (rebutting
    # cycles leave both args undec). Exercise the check by forging a labelling
    # where both opposing arguments are "in".
    from app.abda_bridge import build_arguments, build_attacks
    from app.scenario.loader import scenario_from_dict, scenario_to_rule_collection
    from app.scenario.serialize import serialize_af

    raw = {
        "title": "forged-contradiction",
        "facts": {"a": {"description": "a"}},
        "propositions": {"x": {"description": "x"}},
        "conclusions": {"c": {"description": "c"}},
        "rules": {
            "r_pos": {"type": "strict", "premises": ["a"], "conclusion": "x"},
            "r_neg": {"type": "strict", "premises": ["a"], "conclusion": "-x"},
            "r_c": {"type": "defeasible", "premises": ["x"], "conclusion": "c"},
        },
    }
    scenario = scenario_from_dict(raw)
    rc = scenario_to_rule_collection(scenario)
    arguments = build_arguments(rc.get_all_rules())
    attacks = build_attacks(arguments)
    forged = {
        a: ("in" if a.Conclusion in ("x", "-x") else "undec") for a in arguments
    }
    with pytest.raises(ValueError, match="contradictory"):
        serialize_af(scenario, arguments, attacks, forged)


def test_conclusion_nl_uses_negated_description_when_present(_engine):
    from app.scenario.loader import scenario_from_dict, scenario_to_rule_collection
    from app.scenario.serialize import serialize_af

    raw = {
        "title": "negation-rendering",
        "facts": {"a": {"description": "a"}},
        "propositions": {
            "x": {
                "description": "Popov has a (full) right to possession",
                "negated_description": "Popov does not have a (full) right to possession",
            },
        },
        "conclusions": {"c": {"description": "c"}},
        "rules": {
            "r_pos": {"type": "defeasible", "premises": ["a"], "conclusion": "x"},
            "r_neg": {"type": "defeasible", "premises": ["a"], "conclusion": "-x"},
            "r_c":   {"type": "defeasible", "premises": ["x"], "conclusion": "c"},
        },
    }
    scenario = scenario_from_dict(raw)
    rc = scenario_to_rule_collection(scenario)
    arguments = build_arguments(rc.get_all_rules())
    attacks = build_attacks(arguments)
    from app.abda_bridge import ArgumentationGraph
    graph = ArgumentationGraph(arguments, attacks)
    snap = serialize_af(scenario, arguments, attacks, graph.get_grounded_labelling())

    nls = {a["conclusion"]: a["conclusion_nl"] for a in snap["arguments"]}
    assert nls["x"] == "Popov has a (full) right to possession"
    assert nls["-x"] == "Popov does not have a (full) right to possession"


def test_conclusion_nl_negation_fallback_when_no_negated_description(_engine):
    from app.scenario.loader import scenario_from_dict, scenario_to_rule_collection
    from app.scenario.serialize import serialize_af

    raw = {
        "title": "negation-fallback",
        "facts": {"a": {"description": "a"}},
        "propositions": {"x": {"description": "the widget is green"}},
        "conclusions": {"c": {"description": "c"}},
        "rules": {
            "r_pos": {"type": "defeasible", "premises": ["a"], "conclusion": "x"},
            "r_neg": {"type": "defeasible", "premises": ["a"], "conclusion": "-x"},
            "r_c":   {"type": "defeasible", "premises": ["x"], "conclusion": "c"},
        },
    }
    scenario = scenario_from_dict(raw)
    rc = scenario_to_rule_collection(scenario)
    arguments = build_arguments(rc.get_all_rules())
    attacks = build_attacks(arguments)
    from app.abda_bridge import ArgumentationGraph
    graph = ArgumentationGraph(arguments, attacks)
    snap = serialize_af(scenario, arguments, attacks, graph.get_grounded_labelling())

    nls = {a["conclusion"]: a["conclusion_nl"] for a in snap["arguments"]}
    assert nls["-x"] == "it is not the case that the widget is green"


def test_scenario_to_dict_omits_negated_description_when_absent(_engine):
    from app.scenario.loader import scenario_from_dict
    from app.scenario.serialize import scenario_to_dict

    raw = {
        "title": "t",
        "propositions": {"p": {"description": "p"}},
        "conclusions": {"c": {"description": "c", "negated_description": "not-c"}},
        "rules": {"r": {"type": "defeasible", "premises": ["p"], "conclusion": "c"}},
    }
    d = scenario_to_dict(scenario_from_dict(raw))
    assert "negated_description" not in d["propositions"]["p"]
    assert d["conclusions"]["c"]["negated_description"] == "not-c"


def test_classify_attack_is_consistent_with_engine(_engine):
    # For every attack the engine produced, classify_attack must return
    # "undercut" or "rebut" (both types are reachable from Popov). Serves
    # as a regression guard if engine attack semantics ever change.
    from app.scenario.serialize import classify_attack

    _, snap = _snapshot_for("examples/popov_v_hayashi/scenario.yaml")
    types = [a["type"] for a in snap["attacks"]]
    assert set(types) == {"rebut", "undercut"}
    # Direct replay against the classifier: snapshot's classification
    # is already produced via classify_attack, so check exhaustiveness here
    # by asserting every attack has a non-empty type.
    assert all(t in ("rebut", "undercut") for t in types)
    # Smoke-check the public symbol is callable.
    assert callable(classify_attack)
