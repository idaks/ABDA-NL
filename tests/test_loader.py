"""Tests for app.scenario.loader: schema validation, reference integrity, and
compilation to an ABDA RuleCollection."""
from __future__ import annotations

import pytest

from app.abda_bridge import DefeasibleRule, StrictRule
from app.scenario.loader import (
    ScenarioValidationError,
    load_scenario,
    scenario_from_dict,
    scenario_to_rule_collection,
)

import pytest  # noqa: E402  (keeps test_corrupt_yaml's decorator happy)


def _minimal_scenario() -> dict:
    return {
        "title": "test",
        "facts": {"f1": {"description": "fact one"}},
        "propositions": {"p1": {"description": "prop one"}},
        "conclusions": {"c1": {"description": "conclusion"}},
        "rules": {
            "r1": {
                "type": "defeasible",
                "premises": ["f1"],
                "conclusion": "p1",
            },
            "r2": {
                "type": "defeasible",
                "premises": ["p1"],
                "conclusion": "c1",
            },
        },
    }


def test_loads_medical_ppi_scenario():
    s = load_scenario("examples/medical_ppi/scenario.yaml")
    assert s.title == "PPI Therapy"
    assert "barretts" in s.facts
    assert "ppi_is_panto" in s.assumptions
    assert s.assumptions["ppi_is_panto"].active is False
    assert "continue_ppi" in s.conclusions
    assert "panto_spares" in s.rules


def test_missing_title_is_schema_error():
    raw = _minimal_scenario()
    del raw["title"]
    with pytest.raises(ScenarioValidationError) as exc:
        scenario_from_dict(raw)
    assert any("title" in e for e in exc.value.errors)


def test_unknown_section_is_rejected():
    raw = _minimal_scenario()
    raw["garbage"] = {"x": 1}
    with pytest.raises(ScenarioValidationError):
        scenario_from_dict(raw)


def test_bad_identifier_pattern_rejected():
    raw = _minimal_scenario()
    raw["facts"]["1starts_with_digit"] = {"description": "bad"}
    with pytest.raises(ScenarioValidationError):
        scenario_from_dict(raw)


def test_rule_with_bad_type_rejected():
    raw = _minimal_scenario()
    raw["rules"]["r1"]["type"] = "magical"
    with pytest.raises(ScenarioValidationError):
        scenario_from_dict(raw)


def test_dangling_premise_reference_rejected():
    raw = _minimal_scenario()
    raw["rules"]["r1"]["premises"] = ["no_such_thing"]
    with pytest.raises(ScenarioValidationError) as exc:
        scenario_from_dict(raw)
    assert any("no_such_thing" in e for e in exc.value.errors)


def test_dangling_conclusion_reference_rejected():
    raw = _minimal_scenario()
    raw["rules"]["r1"]["conclusion"] = "-ghost"
    with pytest.raises(ScenarioValidationError) as exc:
        scenario_from_dict(raw)
    assert any("ghost" in e for e in exc.value.errors)


def test_duplicate_id_across_sections_rejected():
    raw = _minimal_scenario()
    raw["facts"]["p1"] = {"description": "same id as a proposition"}
    with pytest.raises(ScenarioValidationError) as exc:
        scenario_from_dict(raw)
    assert any("p1" in e and "multiple sections" in e for e in exc.value.errors)


def test_inactive_assumption_omitted_from_rule_collection():
    raw = {
        "title": "test",
        "assumptions": {
            "on": {"description": "active asm"},
            "off": {"description": "inactive asm", "active": False},
        },
        "conclusions": {"c1": {"description": "c"}},
        "rules": {
            "r": {"type": "defeasible", "premises": ["on"], "conclusion": "c1"},
        },
    }
    scenario = scenario_from_dict(raw)
    rc = scenario_to_rule_collection(scenario)
    names = {r.Name for r in rc.DefeasibleRules}
    assert "on" in names
    assert "off" not in names


def test_inactive_defeasible_rule_omitted_from_rule_collection():
    raw = _minimal_scenario()
    raw["rules"]["r1"]["active"] = False
    scenario = scenario_from_dict(raw)
    rc = scenario_to_rule_collection(scenario)
    names = {r.Name for r in rc.DefeasibleRules}
    assert "r1" not in names
    assert "r2" in names


def test_strict_rule_cannot_have_active_false():
    raw = _minimal_scenario()
    raw["rules"]["strict_r"] = {
        "type": "strict",
        "premises": ["f1"],
        "conclusion": "p1",
        "active": False,
    }
    with pytest.raises(ScenarioValidationError):
        scenario_from_dict(raw)


def test_scenario_compiles_facts_to_bodyless_strict_rules():
    raw = _minimal_scenario()
    scenario = scenario_from_dict(raw)
    rc = scenario_to_rule_collection(scenario)
    fact_rules = [r for r in rc.StrictRules if not r.LeftSide]
    assert {r.RightSide for r in fact_rules} == {"f1"}


def test_scenario_compiles_assumptions_to_bodyless_defeasibles():
    raw = _minimal_scenario()
    raw["assumptions"] = {"a1": {"description": "some assumption", "block": 2}}
    raw["rules"]["r1"]["premises"] = ["f1", "a1"]
    scenario = scenario_from_dict(raw)
    rc = scenario_to_rule_collection(scenario)
    bodyless_def = [r for r in rc.DefeasibleRules if not r.LeftSide]
    assert any(r.Name == "a1" and r.RightSide == "a1" and r.Strength == 2 for r in bodyless_def)


def test_rule_objects_are_stamped_with_scenario_id():
    raw = _minimal_scenario()
    scenario = scenario_from_dict(raw)
    rc = scenario_to_rule_collection(scenario)
    for r in rc.get_all_rules():
        assert getattr(r, "_scenario_id", None) is not None


def test_negated_description_parses_on_all_sections():
    raw = _minimal_scenario()
    raw["facts"]["f1"]["negated_description"] = "fact one is not the case"
    raw["propositions"]["p1"]["negated_description"] = "prop one does not hold"
    raw["conclusions"]["c1"]["negated_description"] = "the conclusion fails"
    raw["rules"]["r1"]["negated_description"] = "r1 does not fire"
    scenario = scenario_from_dict(raw)
    assert scenario.facts["f1"].negated_description == "fact one is not the case"
    assert scenario.propositions["p1"].negated_description == "prop one does not hold"
    assert scenario.conclusions["c1"].negated_description == "the conclusion fails"
    assert scenario.rules["r1"].negated_description == "r1 does not fire"


def test_negated_description_empty_string_rejected():
    raw = _minimal_scenario()
    raw["propositions"]["p1"]["negated_description"] = ""
    with pytest.raises(ScenarioValidationError):
        scenario_from_dict(raw)


def test_corrupt_yaml_raises_scenario_validation_error(tmp_path):
    """Malformed YAML must surface as ScenarioValidationError (→ 400 at the
    API boundary), not as an uncaught ``yaml.YAMLError`` (→ 500).
    """
    bad = tmp_path / "bad.yaml"
    bad.write_text("title: test\nfacts: [this is not: valid: yaml\n")
    with pytest.raises(ScenarioValidationError) as exc:
        load_scenario(bad)
    assert any("YAML parse error" in e for e in exc.value.errors)
