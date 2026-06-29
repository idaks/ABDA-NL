"""Tests for app.scenario.diff_ops: each op's semantics plus integrity guard."""
from __future__ import annotations

import pytest

from app.scenario.diff_ops import DiffOpError, _normalize_description, apply
from app.scenario.loader import scenario_from_dict


def _baseline() -> dict:
    return {
        "title": "test",
        "facts": {"f1": {"description": "fact one"}},
        "assumptions": {"a1": {"description": "assumption one", "block": 1}},
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
                "premises": ["p1", "a1"],
                "conclusion": "c1",
            },
            "strict1": {
                "type": "strict",
                "premises": ["f1"],
                "conclusion": "p1",
            },
        },
    }


def _load_baseline():
    return scenario_from_dict(_baseline())


def test_toggle_assumption_flips_active():
    s = _load_baseline()
    assert s.assumptions["a1"].active is True
    out = apply(s, [{"op": "toggle-assumption", "id": "a1"}])
    assert out.assumptions["a1"].active is False
    out2 = apply(out, [{"op": "toggle-assumption", "id": "a1"}])
    assert out2.assumptions["a1"].active is True


def test_toggle_unknown_assumption_rejected():
    s = _load_baseline()
    with pytest.raises(DiffOpError) as exc:
        apply(s, [{"op": "toggle-assumption", "id": "nope"}])
    assert "nope" in str(exc.value)


def test_toggle_rule_flips_active_on_defeasible():
    s = _load_baseline()
    out = apply(s, [{"op": "toggle-rule", "id": "r1"}])
    assert out.rules["r1"].active is False


def test_toggle_strict_rule_rejected():
    s = _load_baseline()
    with pytest.raises(DiffOpError) as exc:
        apply(s, [{"op": "toggle-rule", "id": "strict1"}])
    assert "strict" in str(exc.value)


def test_modify_rule_replaces_definition():
    s = _load_baseline()
    new_rule = {
        "type": "defeasible",
        "premises": ["a1"],
        "conclusion": "p1",
        "block": 3,
    }
    out = apply(s, [{"op": "modify-rule", "id": "r1", "rule": new_rule}])
    assert out.rules["r1"].premises == ["a1"]
    assert out.rules["r1"].block == 3


def test_add_rule_inserts_new_rule():
    s = _load_baseline()
    out = apply(
        s,
        [
            {
                "op": "add-rule",
                "id": "r_new",
                "rule": {
                    "type": "defeasible",
                    "premises": ["f1"],
                    "conclusion": "p1",
                },
            }
        ],
    )
    assert "r_new" in out.rules


def test_add_rule_rejects_existing_id():
    s = _load_baseline()
    with pytest.raises(DiffOpError) as exc:
        apply(
            s,
            [
                {
                    "op": "add-rule",
                    "id": "r1",
                    "rule": {
                        "type": "defeasible",
                        "premises": ["f1"],
                        "conclusion": "p1",
                    },
                }
            ],
        )
    assert "already declared" in str(exc.value)


def test_remove_rule_removes():
    s = _load_baseline()
    out = apply(s, [{"op": "remove-rule", "id": "strict1"}])
    assert "strict1" not in out.rules


def test_remove_rule_rejects_missing():
    s = _load_baseline()
    with pytest.raises(DiffOpError):
        apply(s, [{"op": "remove-rule", "id": "nope"}])


def test_set_block_on_rule():
    s = _load_baseline()
    out = apply(s, [{"op": "set-block", "target": "rule", "id": "r1", "block": 5}])
    assert out.rules["r1"].block == 5


def test_set_block_on_assumption():
    s = _load_baseline()
    out = apply(s, [{"op": "set-block", "target": "assumption", "id": "a1", "block": 9}])
    assert out.assumptions["a1"].block == 9


def test_set_block_rejects_zero():
    s = _load_baseline()
    with pytest.raises(DiffOpError):
        apply(s, [{"op": "set-block", "target": "rule", "id": "r1", "block": 0}])


def test_add_remove_fact():
    s = _load_baseline()
    out1 = apply(s, [{"op": "add-fact", "id": "f2", "fact": {"description": "f2 desc"}}])
    assert "f2" in out1.facts
    out2 = apply(out1, [{"op": "remove-fact", "id": "f2"}])
    assert "f2" not in out2.facts


def test_add_remove_assumption():
    s = _load_baseline()
    new_asm = {"description": "a2 desc", "block": 1}
    out1 = apply(s, [{"op": "add-assumption", "id": "a2", "assumption": new_asm}])
    assert "a2" in out1.assumptions
    out2 = apply(out1, [{"op": "remove-assumption", "id": "a2"}])
    assert "a2" not in out2.assumptions


def test_modify_rule_strict_cannot_be_inactive():
    s = _load_baseline()
    bad = {
        "type": "strict",
        "premises": ["f1"],
        "conclusion": "p1",
        "active": False,
    }
    with pytest.raises(DiffOpError) as exc:
        apply(s, [{"op": "modify-rule", "id": "strict1", "rule": bad}])
    assert "strict" in str(exc.value)


def test_unknown_op_rejected():
    s = _load_baseline()
    with pytest.raises(DiffOpError):
        apply(s, [{"op": "teleport", "id": "r1"}])


def test_baseline_not_mutated():
    s = _load_baseline()
    _ = apply(s, [{"op": "toggle-assumption", "id": "a1"}])
    assert s.assumptions["a1"].active is True


def test_dangling_reference_after_remove_surfaces_integrity_error():
    s = _load_baseline()
    with pytest.raises(DiffOpError) as exc:
        apply(s, [{"op": "remove-fact", "id": "f1"}])
    assert "unknown identifier" in str(exc.value) or "resulting scenario" in str(exc.value)


def test_ops_apply_in_sequence():
    s = _load_baseline()
    ops = [
        {"op": "toggle-assumption", "id": "a1"},
        {"op": "set-block", "target": "rule", "id": "r1", "block": 2},
        {"op": "remove-rule", "id": "strict1"},
    ]
    out = apply(s, ops)
    assert out.assumptions["a1"].active is False
    assert out.rules["r1"].block == 2
    assert "strict1" not in out.rules


# --- Op-payload schema validation (post-review additions) ---


def test_add_fact_rejects_bad_identifier():
    s = _load_baseline()
    with pytest.raises(DiffOpError) as exc:
        apply(s, [{"op": "add-fact", "id": "1bad", "fact": {"description": "x"}}])
    assert "invalid id" in str(exc.value)


def test_add_fact_rejects_empty_description():
    s = _load_baseline()
    with pytest.raises(DiffOpError):
        apply(s, [{"op": "add-fact", "id": "f_new", "fact": {"description": ""}}])


def test_add_fact_rejects_unknown_field():
    s = _load_baseline()
    with pytest.raises(DiffOpError):
        apply(
            s,
            [
                {
                    "op": "add-fact",
                    "id": "f_new",
                    "fact": {"description": "ok", "active": False},
                }
            ],
        )


# --- Description normalization ---


def test_normalize_description_lowercases_articles():
    assert _normalize_description("The store is open") == "the store is open"
    assert _normalize_description("A new claim") == "a new claim"
    assert _normalize_description("An important premise") == "an important premise"
    assert _normalize_description("This condition holds") == "this condition holds"
    assert _normalize_description("When the patient arrived") == "when the patient arrived"


def test_normalize_description_preserves_proper_nouns():
    # First word is a proper noun, not in the whitelist -- leave untouched.
    assert _normalize_description("Popov stopped the ball") == "Popov stopped the ball"
    assert _normalize_description("Hayashi retrieved it") == "Hayashi retrieved it"
    assert _normalize_description("Barrett's esophagus is a compelling indication") \
        == "Barrett's esophagus is a compelling indication"


def test_normalize_description_preserves_acronyms():
    # PPI isn't in the whitelist, and "PPI" as a word is all-caps -- leave untouched.
    assert _normalize_description("PPI therapy is indicated") == "PPI therapy is indicated"


def test_normalize_description_strips_trailing_period():
    assert _normalize_description("the store is open.") == "the store is open"
    assert _normalize_description("The store is open.") == "the store is open"
    # Internal periods untouched.
    assert _normalize_description("dr. Smith arrived") == "dr. Smith arrived"


def test_normalize_description_passthrough_non_strings():
    assert _normalize_description(None) is None
    assert _normalize_description("") == ""


def test_add_fact_normalizes_description_on_apply():
    s = _load_baseline()
    after = apply(s, [{
        "op": "add-fact",
        "id": "f_new",
        "fact": {"description": "The store is currently open."},
    }])
    assert after.facts["f_new"].description == "the store is currently open"


def test_add_assumption_normalizes_description_on_apply():
    s = _load_baseline()
    after = apply(s, [{
        "op": "add-assumption",
        "id": "a_new",
        "assumption": {"description": "The witness is reliable."},
    }])
    assert after.assumptions["a_new"].description == "the witness is reliable"


def test_add_assumption_rejects_empty_description():
    s = _load_baseline()
    with pytest.raises(DiffOpError):
        apply(
            s,
            [
                {
                    "op": "add-assumption",
                    "id": "a_new",
                    "assumption": {"description": ""},
                }
            ],
        )


def test_add_rule_rejects_malformed_literal_in_conclusion():
    s = _load_baseline()
    bad_rule = {
        "type": "defeasible",
        "premises": ["f1"],
        "conclusion": "1illegal",
    }
    with pytest.raises(DiffOpError):
        apply(s, [{"op": "add-rule", "id": "r_new", "rule": bad_rule}])


def test_add_rule_rejects_missing_type_discriminator():
    s = _load_baseline()
    bad_rule = {"premises": ["f1"], "conclusion": "p1"}
    with pytest.raises(DiffOpError):
        apply(s, [{"op": "add-rule", "id": "r_new", "rule": bad_rule}])


def test_modify_rule_rejects_strict_active_false_via_schema():
    # The schema's oneOf splits strict vs. defeasible; strict has no `active` field,
    # so active:false on a strict payload must fail schema validation.
    s = _load_baseline()
    bad = {"type": "strict", "premises": ["f1"], "conclusion": "p1", "active": False}
    with pytest.raises(DiffOpError):
        apply(s, [{"op": "modify-rule", "id": "strict1", "rule": bad}])


def test_set_block_rejects_bool_value():
    # isinstance(True, int) is True in Python; the explicit bool guard must catch this.
    s = _load_baseline()
    with pytest.raises(DiffOpError) as exc:
        apply(s, [{"op": "set-block", "target": "rule", "id": "r1", "block": True}])
    assert "block" in str(exc.value)


def test_add_rule_rejects_bool_block_via_schema():
    s = _load_baseline()
    bad = {"type": "defeasible", "premises": ["f1"], "conclusion": "p1", "block": True}
    with pytest.raises(DiffOpError):
        apply(s, [{"op": "add-rule", "id": "r_new", "rule": bad}])
