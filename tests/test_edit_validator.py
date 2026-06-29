"""Tests for the deterministic edit Validator."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.llm.edit_validator import MAX_ID_LEN, is_trivial_edit, validate_op
from app.scenario.loader import load_scenario

EXAMPLES_ROOT = Path(__file__).resolve().parent.parent / "examples"


@pytest.fixture(scope="module")
def popov():
    return load_scenario(EXAMPLES_ROOT / "popov_v_hayashi" / "scenario.yaml")


# --- clean ops ---


def test_clean_add_rule_returns_no_issues(popov):
    op = {
        "op": "add-rule",
        "id": "r_test",
        "rule": {
            "type": "defeasible",
            "premises": ["popov_preposs_interest"],
            "conclusion": "popov_legit_claim",
        },
    }
    assert validate_op(op, popov) == []


def test_clean_modify_rule_returns_no_issues(popov):
    op = {
        "op": "modify-rule",
        "id": "mc1",
        "rule": {
            "type": "defeasible",
            "premises": ["popov_preposs_interest"],
            "conclusion": "popov_qual_right",
        },
    }
    assert validate_op(op, popov) == []


def test_clean_add_fact_returns_no_issues(popov):
    op = {
        "op": "add-fact",
        "id": "new_f",
        "fact": {"description": "the weather was clear"},
    }
    assert validate_op(op, popov) == []


# --- id discipline ---


def test_id_collision_on_add(popov):
    op = {
        "op": "add-rule",
        "id": "mc1",  # already exists
        "rule": {"type": "defeasible", "premises": ["popov_preposs_interest"], "conclusion": "x"},
    }
    issues = validate_op(op, popov)
    codes = {i.code for i in issues}
    assert "id_collision" in codes


def test_id_too_long_flagged(popov):
    long_id = "a" * (MAX_ID_LEN + 1)
    op = {
        "op": "add-rule",
        "id": long_id,
        "rule": {"type": "defeasible", "premises": ["popov_preposs_interest"], "conclusion": "x"},
    }
    issues = validate_op(op, popov)
    assert any(i.code == "id_too_long" for i in issues)


def test_id_exactly_at_ceiling_ok(popov):
    ok_id = "a" * MAX_ID_LEN
    op = {
        "op": "add-rule",
        "id": ok_id,
        "rule": {"type": "defeasible", "premises": ["popov_preposs_interest"], "conclusion": "x"},
    }
    issues = validate_op(op, popov)
    assert not any(i.code == "id_too_long" for i in issues)


def test_modify_rule_with_long_existing_id_is_not_blocked():
    """Legacy rule ids that predate the id-length ceiling must remain
    modifiable. The ceiling is a UI-compactness constraint for newly-
    minted ids; blocking modify-rule on an id that's already in the
    scenario just locks the user out of editing their own content.

    Uses a synthetic legacy rule because the current bundled scenarios
    may not contain an existing id over the cap.
    """
    from app.scenario.loader import load_scenario
    from app.scenario.model import Rule
    medical = load_scenario(EXAMPLES_ROOT / "medical_ppi" / "scenario.yaml")
    long_id = "legacy_rule_id_over_length_cap"
    medical.rules[long_id] = Rule(
        id=long_id,
        type="defeasible",
        premises=["clopidogrel"],
        conclusion="-needs_cyp2c19_chk",
        category="pharmacology",
    )
    assert len(long_id) > MAX_ID_LEN

    op = {
        "op": "modify-rule",
        "id": long_id,
        "rule": {
            "type": "defeasible",
            "premises": ["clopidogrel"],
            "conclusion": "-needs_cyp2c19_chk",
            "category": "pharmacology",
        },
    }
    issues = validate_op(op, medical)
    assert not any(i.code == "id_too_long" for i in issues), (
        f"modify-rule on legacy long id should be allowed; got issues: {[i.to_dict() for i in issues]}"
    )


def test_add_rule_still_rejects_long_id():
    """Sanity check: the modify-rule exemption must not weaken the
    length check for newly-minted ids.
    """
    from app.scenario.loader import load_scenario
    medical = load_scenario(EXAMPLES_ROOT / "medical_ppi" / "scenario.yaml")
    op = {
        "op": "add-rule",
        "id": "a" * (MAX_ID_LEN + 1),
        "rule": {
            "type": "defeasible",
            "premises": ["clopidogrel"],
            "conclusion": "cardiac_risk",
        },
    }
    assert any(i.code == "id_too_long" for i in validate_op(op, medical))


def test_unknown_modify_rule_id(popov):
    op = {
        "op": "modify-rule",
        "id": "does_not_exist",
        "rule": {"type": "defeasible", "premises": ["popov_preposs_interest"], "conclusion": "x"},
    }
    issues = validate_op(op, popov)
    assert any(i.code == "unknown_rule_id" for i in issues)


# --- reference integrity ---


def test_unknown_premise_is_advisory(popov):
    """unknown_premise surfaces as a warning the UI shows, not a blocker.

    The rule is still structurally valid -- it just won't fire until the
    missing premise is added as a fact or assumption.
    """
    op = {
        "op": "add-rule",
        "id": "rbad",
        "rule": {
            "type": "defeasible",
            "premises": ["a_made_up_premise"],
            "conclusion": "popov_legit_claim",
        },
    }
    issues = validate_op(op, popov)
    unknowns = [i for i in issues if i.code == "unknown_premise"]
    assert len(unknowns) == 1
    assert unknowns[0].severity == "advisory"
    # id_too_long (21 chars) is blocking but unrelated; check it's absent here.
    blocking = [i for i in issues if i.severity == "blocking"]
    assert all(b.code != "unknown_premise" for b in blocking)


def test_split_issues_separates_by_severity(popov):
    """The split_issues helper partitions cleanly so run_propose can
    retry only on blocking and surface advisory as warnings."""
    from app.llm.edit_validator import split_issues

    op = {
        "op": "add-rule",
        "id": "mc1",  # id_collision: blocking
        "rule": {
            "type": "defeasible",
            "premises": ["a_made_up_premise"],  # unknown_premise: advisory
            "conclusion": "x",
        },
    }
    issues = validate_op(op, popov)
    blocking, advisory = split_issues(issues)
    assert any(b.code == "id_collision" for b in blocking)
    assert any(a.code == "unknown_premise" for a in advisory)
    assert all(b.severity == "blocking" for b in blocking)
    assert all(a.severity == "advisory" for a in advisory)


def test_negated_premise_resolves_by_stripping_prefix(popov):
    op = {
        "op": "add-rule",
        "id": "rtest",
        "rule": {
            "type": "defeasible",
            "premises": ["-popov_has_poss"],  # negation of an existing prop
            "conclusion": "popov_legit_claim",
        },
    }
    assert not any(i.code == "unknown_premise" for i in validate_op(op, popov))


# --- type discipline ---


def test_strict_with_inactive_flagged(popov):
    # The JSON schema's oneOf catches this first. Either it surfaces as a
    # schema issue or as a dedicated strict_inactive issue depending on
    # implementation order -- we just want to see it flagged somewhere.
    op = {
        "op": "add-rule",
        "id": "rstrict",
        "rule": {
            "type": "strict",
            "premises": ["popov_preposs_interest"],
            "conclusion": "popov_qual_right",
            "active": False,
        },
    }
    issues = validate_op(op, popov)
    codes = {i.code for i in issues}
    assert codes & {"strict_inactive", "schema"}


# --- schema passthrough ---


def test_missing_required_field_caught(popov):
    op = {"op": "add-rule", "id": "rtest", "rule": {"type": "defeasible"}}  # no premises, no conclusion
    issues = validate_op(op, popov)
    assert any(i.code == "schema" for i in issues)


def test_missing_payload_caught(popov):
    op = {"op": "add-rule", "id": "rtest"}  # no "rule"
    issues = validate_op(op, popov)
    assert any(i.code == "missing_payload" for i in issues)


# --- trivial-edit short-circuit ---


def test_trivial_add_fact():
    assert is_trivial_edit({"op": "add-fact", "id": "x", "fact": {"description": "y"}})


def test_trivial_add_assumption():
    assert is_trivial_edit({"op": "add-assumption", "id": "x", "assumption": {"description": "y"}})


def test_nontrivial_add_rule():
    op = {"op": "add-rule", "id": "r", "rule": {"type": "defeasible", "premises": ["p"], "conclusion": "c"}}
    assert not is_trivial_edit(op)


def test_nontrivial_modify_rule():
    op = {"op": "modify-rule", "id": "r", "rule": {"type": "defeasible", "premises": ["p"], "conclusion": "c"}}
    assert not is_trivial_edit(op)


# --- forward-premise promotion ---


def test_add_fact_may_promote_pure_proposition(popov):
    """If an id exists only as a proposition (e.g. auto-declared from a
    prior rule-add), adding a fact with that id is NOT an id_collision --
    it's a promotion."""
    from app.scenario.diff_ops import apply

    # First: add a rule referencing a forward premise. apply() auto-
    # declares it as a proposition.
    rule_op = {
        "op": "add-rule",
        "id": "mob_rule",
        "rule": {
            "type": "defeasible",
            "premises": ["bouncer_absent"],
            "conclusion": "popov_qual_right",
        },
    }
    after_rule = apply(popov, [rule_op])
    assert "bouncer_absent" in after_rule.propositions

    # Now: add a fact with the same id. Validator should NOT flag
    # id_collision.
    fact_op = {
        "op": "add-fact",
        "id": "bouncer_absent",
        "fact": {"description": "no bouncer was present"},
    }
    issues = validate_op(fact_op, after_rule)
    assert not any(i.code == "id_collision" for i in issues), [i.to_dict() for i in issues]


# --- fact description modality ---


def test_fact_description_probably_flagged_advisory(popov):
    op = {
        "op": "add-fact",
        "id": "hpyl",
        "fact": {"description": "the patient probably has H. pylori infection"},
    }
    issues = validate_op(op, popov)
    modal = [i for i in issues if i.code == "fact_modal_wording"]
    assert len(modal) == 1
    assert modal[0].severity == "advisory"
    assert "probably" in modal[0].message.lower()


def test_fact_description_should_flagged_advisory(popov):
    op = {
        "op": "add-fact",
        "id": "takewater",
        "fact": {"description": "the patient should take their medication with water"},
    }
    issues = validate_op(op, popov)
    assert any(i.code == "fact_modal_wording" and i.severity == "advisory" for i in issues)


def test_fact_description_likely_flagged(popov):
    op = {
        "op": "add-fact",
        "id": "ltpi",
        "fact": {"description": "the patient likely has a gastric ulcer"},
    }
    assert any(i.code == "fact_modal_wording" for i in validate_op(op, popov))


def test_fact_description_clean_no_modal_issue(popov):
    op = {
        "op": "add-fact",
        "id": "clear_wx",
        "fact": {"description": "the weather was clear at Pacific Bell Park"},
    }
    assert not any(i.code == "fact_modal_wording" for i in validate_op(op, popov))


def test_assumption_description_with_presumed_no_modal_issue(popov):
    """Assumptions may use 'treated as' / 'presumed' framings per Proposer
    convention; the modal check applies only to facts.
    """
    op = {
        "op": "add-assumption",
        "id": "presumed_x",
        "assumption": {"description": "the witness is presumed to be reliable"},
    }
    assert not any(i.code == "fact_modal_wording" for i in validate_op(op, popov))


def test_fact_modal_token_matched_as_whole_word(popov):
    """`shoulder` should not trip the `should` token; word-boundary anchors
    prevent substring false positives.
    """
    op = {
        "op": "add-fact",
        "id": "shoulder_x",
        "fact": {"description": "the patient has a shoulder injury"},
    }
    assert not any(i.code == "fact_modal_wording" for i in validate_op(op, popov))


def test_add_rule_cannot_collide_with_proposition(popov):
    """add-rule still flags id_collision even against pure propositions --
    a proposition is a literal declaration, not a rule, and replacing it
    with a rule changes the structural meaning."""
    from app.scenario.diff_ops import apply
    from app.scenario.model import Proposition

    scn = apply(popov, [])
    scn.propositions["open_slot"] = Proposition(id="open_slot", description="x")
    op = {
        "op": "add-rule",
        "id": "open_slot",  # collides with proposition
        "rule": {"type": "defeasible", "premises": ["popov_preposs_interest"], "conclusion": "x"},
    }
    issues = validate_op(op, scn)
    assert any(i.code == "id_collision" for i in issues)
