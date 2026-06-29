"""YAML scenario loader.

Parses `scenario.yaml` files against `scenario.schema.json`, performs
reference-integrity checks, and compiles the result to an ABDA
`RuleCollection` by constructing `StrictRule`/`DefeasibleRule`
instances directly (no text-format roundtrip).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Union

import yaml
from jsonschema import Draft202012Validator

from app.abda_bridge import DefeasibleRule, RuleCollection, StrictRule
from app.scenario.model import (
    Assumption,
    Fact,
    Proposition,
    Rule,
    Scenario,
)

_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "scenario.schema.json"


class ScenarioValidationError(Exception):
    """Raised when a scenario fails schema or reference-integrity
    checks.

    `errors` is the list of individual problems; the message joins
    them.
    """

    def __init__(self, errors: list[str]):
        super().__init__("\n".join(errors))
        self.errors = errors


def _load_schema() -> dict:
    with _SCHEMA_PATH.open() as fh:
        return json.load(fh)


_SCHEMA: dict = _load_schema()
_VALIDATOR = Draft202012Validator(_SCHEMA)


def load_scenario(path: Union[Path, str]) -> Scenario:
    """Read a scenario.yaml file, validate, and return a `Scenario`."""
    path = Path(path)
    with path.open() as fh:
        try:
            raw = yaml.safe_load(fh)
        except yaml.YAMLError as exc:
            raise ScenarioValidationError(
                [f"{path}: YAML parse error: {exc}"]
            ) from exc
    if raw is None:
        raise ScenarioValidationError([f"{path}: file is empty"])
    return scenario_from_dict(raw)


def scenario_from_dict(raw: dict[str, Any]) -> Scenario:
    """Validate a raw dict (already YAML-parsed) and return a
    `Scenario`.

    Runs schema validation first, then reference-integrity
    checks. Raises `ScenarioValidationError` with the full list of
    problems on failure.
    """
    schema_errors = sorted(_VALIDATOR.iter_errors(raw), key=lambda e: list(e.absolute_path))
    if schema_errors:
        msgs = [
            f"schema: {'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
            for e in schema_errors
        ]
        raise ScenarioValidationError(msgs)

    scenario = _build_scenario(raw)
    check_reference_integrity(scenario)
    return scenario


def _build_scenario(raw: dict[str, Any]) -> Scenario:
    facts = {k: Fact(id=k, **v) for k, v in (raw.get("facts") or {}).items()}
    assumptions = {
        k: Assumption(id=k, **v) for k, v in (raw.get("assumptions") or {}).items()
    }
    propositions = {
        k: Proposition(id=k, **v) for k, v in (raw.get("propositions") or {}).items()
    }
    conclusions = {
        k: Proposition(id=k, **v) for k, v in (raw.get("conclusions") or {}).items()
    }
    rules = {k: Rule(id=k, **v) for k, v in (raw.get("rules") or {}).items()}

    return Scenario(
        title=raw["title"],
        description=raw.get("description", ""),
        facts=facts,
        assumptions=assumptions,
        propositions=propositions,
        conclusions=conclusions,
        rules=rules,
        corpus=list(raw.get("corpus") or []),
    )


def check_reference_integrity(scenario: Scenario) -> None:
    """Verify no id collisions and every premise/conclusion reference
    resolves.

    Raises `ScenarioValidationError` with every problem listed. A
    literal may be negated with a leading `-`; the base identifier
    must exist in at least one section (facts / assumptions /
    propositions / conclusions / rules).
    """
    errors: list[str] = []

    by_id: dict[str, list[str]] = {}
    for section_name, section in (
        ("facts", scenario.facts),
        ("assumptions", scenario.assumptions),
        ("propositions", scenario.propositions),
        ("conclusions", scenario.conclusions),
        ("rules", scenario.rules),
    ):
        for k in section:
            by_id.setdefault(k, []).append(section_name)
    for k, where in sorted(by_id.items()):
        if len(where) > 1:
            errors.append(f"id '{k}' declared in multiple sections: {', '.join(where)}")

    all_ids = scenario.all_ids()

    def resolve(lit: str, where: str) -> None:
        base = lit[1:] if lit.startswith("-") else lit
        if base not in all_ids:
            errors.append(f"{where}: unknown identifier '{base}' (literal '{lit}')")

    for rule in scenario.rules.values():
        for prem in rule.premises:
            resolve(prem, f"rule '{rule.id}' premise")
        resolve(rule.conclusion, f"rule '{rule.id}' conclusion")

    if errors:
        raise ScenarioValidationError(errors)


def scenario_to_rule_collection(scenario: Scenario) -> RuleCollection:
    """Compile a Scenario to an ABDA RuleCollection.

    - Facts → bodyless `StrictRule`
    - Assumptions → bodyless `DefeasibleRule` (skipped when
      `active=False`)
    - Strict rules → `StrictRule` (always active)
    - Defeasible rules → `DefeasibleRule` (skipped when
      `active=False`)

    Skipped rules are omitted entirely. Any undercut targeting a
    skipped rule has no effect.
    """
    rc = RuleCollection()

    def _stamp(abda_rule, scenario_id: str):
        abda_rule._scenario_id = scenario_id
        return abda_rule

    for fact in scenario.facts.values():
        rc.StrictRules.add(_stamp(StrictRule(left_side=[], right_side=fact.id), fact.id))

    for asm in scenario.assumptions.values():
        if not asm.active:
            continue
        rc.DefeasibleRules.add(
            _stamp(
                DefeasibleRule(
                    left_side=[],
                    right_side=asm.id,
                    name=asm.id,
                    strength=asm.block,
                ),
                asm.id,
            )
        )

    for rule in scenario.rules.values():
        if rule.type == "defeasible":
            if not rule.active:
                continue
            rc.DefeasibleRules.add(
                _stamp(
                    DefeasibleRule(
                        left_side=list(rule.premises),
                        right_side=rule.conclusion,
                        name=rule.id,
                        strength=rule.block,
                    ),
                    rule.id,
                )
            )
        else:
            rc.StrictRules.add(
                _stamp(
                    StrictRule(
                        left_side=list(rule.premises),
                        right_side=rule.conclusion,
                    ),
                    rule.id,
                )
            )

    return rc
