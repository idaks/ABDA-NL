"""Diff-op apply function and integrity validator.

The server is stateless: session state lives client-side as a sequence
of ops applied against a baseline `Scenario`. `apply(baseline, ops)`
returns a new `Scenario`; the baseline is never mutated. After all ops
are applied, reference integrity is re-verified.

Op vocabulary (dicts keyed by `op`):

- `{"op": "toggle-assumption", "id": <asm_id>}`
- `{"op": "toggle-rule", "id": <rule_id>}`  (defeasible rules only)
- `{"op": "modify-rule", "id": <rule_id>, "rule": <rule-def>}`
- `{"op": "add-rule", "id": <rule_id>, "rule": <rule-def>}`
- `{"op": "remove-rule", "id": <rule_id>}`
- `{"op": "set-block", "target": "rule"|"assumption", "id": <id>, "block": <int>}`
- `{"op": "add-fact", "id": <fact_id>, "fact": <fact-def>}`
- `{"op": "remove-fact", "id": <fact_id>}`
- `{"op": "add-assumption", "id": <asm_id>, "assumption": <asm-def>}`
- `{"op": "remove-assumption", "id": <asm_id>}`
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from app.scenario.loader import ScenarioValidationError, check_reference_integrity
from app.scenario.model import Assumption, Fact, Proposition, Rule, Scenario

_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "scenario.schema.json"
_SCHEMA = json.loads(_SCHEMA_PATH.read_text())
_DEFS = _SCHEMA["$defs"]


def _validator_for(defname: str) -> Draft202012Validator:
    """Build a validator that resolves `#/$defs/<defname>` from the
    scenario schema.

    We embed the whole `$defs` block so internal `$ref`s (literal,
    block, strict_rule, defeasible_rule) resolve correctly.
    """
    return Draft202012Validator({"$ref": f"#/$defs/{defname}", "$defs": _DEFS})


_VALIDATORS = {
    "identifier": _validator_for("identifier"),
    "fact": _validator_for("fact"),
    "assumption": _validator_for("assumption"),
    "rule": _validator_for("rule"),
}


class DiffOpError(Exception):
    """Raised when an op fails its preconditions or produces an
    invalid state."""


def _validate_id(value: Any) -> None:
    errors = list(_VALIDATORS["identifier"].iter_errors(value))
    if errors:
        raise DiffOpError(f"invalid id {value!r}: {errors[0].message}")


def _validate_payload(defname: str, payload: Any) -> None:
    errors = sorted(
        _VALIDATORS[defname].iter_errors(payload),
        key=lambda e: list(e.absolute_path),
    )
    if errors:
        first = errors[0]
        path = "/".join(str(p) for p in first.absolute_path) or "<root>"
        raise DiffOpError(f"invalid {defname} payload at {path}: {first.message}")


def apply(baseline: Scenario, ops: list[dict[str, Any]]) -> Scenario:
    """Return a new `Scenario` with `ops` applied against `baseline`.

    Validates each op's preconditions as it applies; on failure raises
    `DiffOpError` with the op index. After all ops succeed, re-runs
    reference integrity on the result and raises `DiffOpError` if the
    final state is inconsistent (e.g. an op removed a rule that
    another rule's premise references).
    """
    effective = _copy_scenario(baseline)
    for i, op in enumerate(ops):
        try:
            _apply_one(effective, op)
        except DiffOpError as e:
            raise DiffOpError(f"op[{i}] ({op.get('op')!r}): {e}") from e
        except (KeyError, TypeError) as e:
            raise DiffOpError(f"op[{i}] ({op.get('op')!r}): malformed op: {e}") from e
    # Incremental edits that ADD or MODIFY a rule may reference
    # premise literals that aren't yet declared -- the rule lands
    # structurally and simply won't fire until a fact or assumption
    # with that id is added later. Auto-declare such literals as
    # propositions so reference integrity still holds.
    #
    # Scope is intentionally limited to rules that changed in *this*
    # apply() call. If an existing rule suddenly has a dangling
    # premise (e.g. because a fact it depended on was removed), that
    # remains a genuine integrity error -- we don't want to silently
    # paper over broken dependencies.
    _autodeclare_forward_literals(baseline, effective, ops)
    try:
        check_reference_integrity(effective)
    except ScenarioValidationError as e:
        raise DiffOpError(f"resulting scenario is invalid: {e}") from e
    return effective


def _autodeclare_forward_literals(
    baseline: Scenario,
    effective: Scenario,
    ops: list[dict[str, Any]],
) -> None:
    # Collect NL descriptions the Proposer attached to any add-rule /
    # modify-rule ops in this batch. `new_premise_notes` is the schema
    # field; despite the name, it may carry notes for any new literal
    # the rule introduces (premise *or* conclusion). Critical for the
    # promotion UX: when the user later says "add a fact that X", the
    # Proposer scans the state block, finds the matching proposition
    # by description, and reuses its id rather than minting a new one.
    notes_by_id: dict[str, str] = {}
    for op in ops:
        if op.get("op") in {"add-rule", "modify-rule"}:
            for n in op.get("new_premise_notes") or []:
                nid = n.get("id") if isinstance(n, dict) else None
                ndesc = n.get("description") if isinstance(n, dict) else None
                if nid and ndesc:
                    notes_by_id[nid] = ndesc

    changed_rule_ids = {
        rid for rid, rule in effective.rules.items()
        if rid not in baseline.rules or baseline.rules[rid] != rule
    }
    if not changed_rule_ids:
        return
    declared = effective.all_ids()

    def _declare(lit: str) -> None:
        ref = lit[1:] if lit.startswith("-") else lit
        if ref in declared:
            return
        raw_desc = notes_by_id.get(
            ref,
            "(added via incremental edit; not yet defined)",
        )
        effective.propositions[ref] = Proposition(
            id=ref,
            description=_normalize_description(raw_desc),
        )
        declared.add(ref)

    for rid in changed_rule_ids:
        rule = effective.rules[rid]
        for lit in rule.premises:
            _declare(lit)
        _declare(rule.conclusion)


def _copy_scenario(s: Scenario) -> Scenario:
    return Scenario(
        title=s.title,
        description=s.description,
        facts={k: replace(v) for k, v in s.facts.items()},
        assumptions={k: replace(v) for k, v in s.assumptions.items()},
        propositions={k: replace(v) for k, v in s.propositions.items()},
        conclusions={k: replace(v) for k, v in s.conclusions.items()},
        rules={k: replace(v, premises=list(v.premises)) for k, v in s.rules.items()},
        corpus=list(s.corpus),
    )


def _apply_one(s: Scenario, op: dict[str, Any]) -> None:
    kind = op.get("op")
    handler = _HANDLERS.get(kind)
    if handler is None:
        raise DiffOpError(f"unknown op kind: {kind!r}")
    handler(s, op)


def _require_fresh_id(s: Scenario, new_id: str, *, allow_replace_proposition: bool = False) -> None:
    if new_id in s.all_ids():
        # Special case: when adding a fact/assumption, allow
        # replacement of an id that currently exists ONLY as a
        # proposition. Auto-declared forward premises land there, and
        # the natural follow-up is the user "promoting" the
        # proposition to a real fact or assumption so the rule that
        # introduced it can fire.
        if allow_replace_proposition and _is_pure_proposition(s, new_id):
            return
        raise DiffOpError(f"id already declared: {new_id!r}")


# Words that canonically start a lowercased sentence-fragment
# description in ABDA-NL scenarios. When a Proposer emits "The store
# is open" we lowercase the "The" to match existing convention. Proper
# nouns ("Popov stopped the ball") are left alone because their first
# token is NOT in this list.
_LEADING_WORDS_TO_LOWERCASE = frozenset({
    "The", "A", "An", "This", "That", "These", "Those",
    "It", "There",
    "When", "If", "Because", "Since", "After", "Before", "While",
    "Whether", "Where", "Whenever",
    "His", "Her", "Its", "Their", "Our", "Your",
    "No", "Some", "Many", "Most", "All", "Each", "Any", "One", "Two", "Both",
})


def _normalize_description(desc: Any) -> Any:
    """Match the in-repo description convention: sentence-fragment
    style.

    - Lowercase the first character only if the first
      whitespace-delimited word appears in
      `_LEADING_WORDS_TO_LOWERCASE`. This catches articles,
      determiners, demonstratives, subordinators, etc. Proper nouns at
      the start of a description (Popov, Hayashi, Barrett) are
      preserved.
    - Strip a trailing period (existing descriptions don't end with
      one).

    Pass-through for non-string inputs (Pydantic should have caught
    them, but we defend anyway).
    """
    if not isinstance(desc, str) or not desc:
        return desc
    first_space = desc.find(" ")
    first_word = desc if first_space == -1 else desc[:first_space]
    if first_word in _LEADING_WORDS_TO_LOWERCASE:
        desc = first_word.lower() + desc[len(first_word):]
    if desc.endswith("."):
        desc = desc[:-1]
    return desc


def _normalize_payload_descriptions(payload: dict[str, Any]) -> None:
    """Normalize `description` and `negated_description` in-place."""
    for key in ("description", "negated_description"):
        if key in payload:
            payload[key] = _normalize_description(payload[key])


def _is_pure_proposition(s: Scenario, ident: str) -> bool:
    return (
        ident in s.propositions
        and ident not in s.facts
        and ident not in s.assumptions
        and ident not in s.conclusions
        and ident not in s.rules
    )


def _validated_rule_fields(data: dict[str, Any]) -> dict[str, Any]:
    """Coerce a post-schema-validated rule dict into Rule constructor
    kwargs.

    The JSON-schema pass in `_validate_payload('rule', ...)` already
    checks required fields, type enum, block-is-positive-integer, and
    the strict-rules-cannot-be-inactive constraint via the `oneOf`
    split.
    """
    return {
        "type": data["type"],
        "premises": list(data["premises"]),
        "conclusion": data["conclusion"],
        "category": data.get("category"),
        "source": data.get("source"),
        "block": data.get("block", 1),
        "active": data.get("active", True),
        "negated_description": _normalize_description(data.get("negated_description")),
    }


def _op_toggle_assumption(s: Scenario, op: dict) -> None:
    aid = op["id"]
    if aid not in s.assumptions:
        raise DiffOpError(f"unknown assumption id: {aid!r}")
    s.assumptions[aid].active = not s.assumptions[aid].active


def _op_toggle_rule(s: Scenario, op: dict) -> None:
    rid = op["id"]
    if rid not in s.rules:
        raise DiffOpError(f"unknown rule id: {rid!r}")
    rule = s.rules[rid]
    if rule.type == "strict":
        raise DiffOpError(f"cannot toggle strict rule: {rid!r}")
    rule.active = not rule.active


def _op_modify_rule(s: Scenario, op: dict) -> None:
    rid = op["id"]
    _validate_id(rid)
    _validate_payload("rule", op["rule"])
    if rid not in s.rules:
        raise DiffOpError(f"unknown rule id: {rid!r}")
    s.rules[rid] = Rule(id=rid, **_validated_rule_fields(op["rule"]))


def _op_add_rule(s: Scenario, op: dict) -> None:
    rid = op["id"]
    _validate_id(rid)
    _validate_payload("rule", op["rule"])
    _require_fresh_id(s, rid)
    s.rules[rid] = Rule(id=rid, **_validated_rule_fields(op["rule"]))


def _op_remove_rule(s: Scenario, op: dict) -> None:
    rid = op["id"]
    if rid not in s.rules:
        raise DiffOpError(f"unknown rule id: {rid!r}")
    del s.rules[rid]


def _op_set_block(s: Scenario, op: dict) -> None:
    target = op["target"]
    tid = op["id"]
    block = op["block"]
    # Guard against bool, which is an int subclass in Python
    # (isinstance(True, int) == True).
    if isinstance(block, bool) or not isinstance(block, int) or block < 1:
        raise DiffOpError(f"block must be a positive integer: {block!r}")
    if target == "rule":
        if tid not in s.rules:
            raise DiffOpError(f"unknown rule id: {tid!r}")
        s.rules[tid].block = block
    elif target == "assumption":
        if tid not in s.assumptions:
            raise DiffOpError(f"unknown assumption id: {tid!r}")
        s.assumptions[tid].block = block
    else:
        raise DiffOpError(f"set-block target must be 'rule' or 'assumption', got {target!r}")


def _op_add_fact(s: Scenario, op: dict) -> None:
    fid = op["id"]
    _validate_id(fid)
    _validate_payload("fact", op["fact"])
    _require_fresh_id(s, fid, allow_replace_proposition=True)
    # Promote: if this id was an auto-declared proposition, drop it
    # first.
    s.propositions.pop(fid, None)
    _normalize_payload_descriptions(op["fact"])
    s.facts[fid] = Fact(id=fid, **op["fact"])


def _op_remove_fact(s: Scenario, op: dict) -> None:
    fid = op["id"]
    if fid not in s.facts:
        raise DiffOpError(f"unknown fact id: {fid!r}")
    del s.facts[fid]


def _op_add_assumption(s: Scenario, op: dict) -> None:
    aid = op["id"]
    _validate_id(aid)
    _validate_payload("assumption", op["assumption"])
    _require_fresh_id(s, aid, allow_replace_proposition=True)
    # Promote: if this id was an auto-declared proposition, drop it
    # first.
    s.propositions.pop(aid, None)
    _normalize_payload_descriptions(op["assumption"])
    s.assumptions[aid] = Assumption(id=aid, **op["assumption"])


def _op_remove_assumption(s: Scenario, op: dict) -> None:
    aid = op["id"]
    if aid not in s.assumptions:
        raise DiffOpError(f"unknown assumption id: {aid!r}")
    del s.assumptions[aid]


_HANDLERS = {
    "toggle-assumption": _op_toggle_assumption,
    "toggle-rule": _op_toggle_rule,
    "modify-rule": _op_modify_rule,
    "add-rule": _op_add_rule,
    "remove-rule": _op_remove_rule,
    "set-block": _op_set_block,
    "add-fact": _op_add_fact,
    "remove-fact": _op_remove_fact,
    "add-assumption": _op_add_assumption,
    "remove-assumption": _op_remove_assumption,
}
