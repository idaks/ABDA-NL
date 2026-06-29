"""Serialize ABDA output to the JSON format used by the API.

Takes (scenario, arguments, attacks, grounded_labelling) and produces
a dict for the API response. Argument ids are assigned
deterministically (`a1`, `a2`, ...) sorted by the argument's string
representation so the same AF always serializes identically.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from app.abda_bridge import Argument, Attack, DefeasibleRule
from app.abda.ArgumentationSystem.ArgumentationGraph import ArgumentationGraph
from app.scenario.model import Scenario


def assign_argument_ids(arguments: Iterable[Argument]) -> dict[Argument, str]:
    """Return `{arg: 'aN'}` sorted by the argument's flattened
    string."""
    ordered = sorted(arguments, key=str)
    return {arg: f"a{i + 1}" for i, arg in enumerate(ordered)}


def scenario_to_dict(scenario: Scenario) -> dict[str, Any]:
    """Serialize a `Scenario` to the wire format (mirrors
    scenario.yaml shape).

    Optional fields (`category`, `source`, `block` when default) are
    omitted to keep the payload tight. Section ordering matches the
    scenario schema for predictability.
    """
    def _strip(d: dict) -> dict:
        return {k: v for k, v in d.items() if v is not None}

    return {
        "title": scenario.title,
        "description": scenario.description,
        "facts": {
            k: _strip({
                "description": v.description,
                "negated_description": v.negated_description,
                "category": v.category,
                "source": v.source,
            })
            for k, v in scenario.facts.items()
        },
        "assumptions": {
            k: _strip({
                "description": v.description,
                "negated_description": v.negated_description,
                "category": v.category,
                "source": v.source,
                "active": v.active,
                "block": v.block,
            })
            for k, v in scenario.assumptions.items()
        },
        "propositions": {
            k: _strip({
                "description": v.description,
                "negated_description": v.negated_description,
                "category": v.category,
            })
            for k, v in scenario.propositions.items()
        },
        "conclusions": {
            k: _strip({
                "description": v.description,
                "negated_description": v.negated_description,
                "category": v.category,
            })
            for k, v in scenario.conclusions.items()
        },
        "rules": {
            k: _strip({
                "type": v.type,
                "premises": list(v.premises),
                "conclusion": v.conclusion,
                "negated_description": v.negated_description,
                "category": v.category,
                "source": v.source,
                "block": v.block,
                "active": v.active if v.type == "defeasible" else None,
            })
            for k, v in scenario.rules.items()
        },
        "corpus": list(scenario.corpus),
    }


def serialize_af(
    scenario: Scenario,
    arguments: Iterable[Argument],
    attacks: Iterable[Attack],
    labelling: Mapping[Argument, str],
) -> dict[str, Any]:
    """Build the wire-format AF snapshot dict."""
    id_by_arg = assign_argument_ids(arguments)

    # Min-max numbering is a layered depth assigned by the engine to
    # every in/out argument, used by the AF graph layout as the
    # y-coordinate. Undecided arguments receive no engine value; we
    # surface them as `"inf"` so the UI renders them in a separate
    # cluster.
    engine_min_max = ArgumentationGraph.get_min_max(labelling)
    min_max_by_arg: dict[Argument, Any] = {}
    for arg in arguments:
        v = engine_min_max.get(arg)
        min_max_by_arg[arg] = "inf" if v is None else v

    argument_dicts: list[dict[str, Any]] = []
    for arg, aid in id_by_arg.items():
        argument_dicts.append(
            _argument_dict(arg, aid, id_by_arg, scenario, labelling[arg], min_max_by_arg[arg])
        )

    attack_dicts = sorted(
        (_attack_dict(atk, id_by_arg) for atk in attacks),
        key=lambda d: (d["from"], d["to"]),
    )

    labels_by_proposition = _aggregate_proposition_labels(scenario, arguments, labelling)

    return {
        "arguments": argument_dicts,
        "attacks": attack_dicts,
        "labels_by_proposition": labels_by_proposition,
    }


def _argument_dict(
    arg: Argument,
    aid: str,
    id_by_arg: Mapping[Argument, str],
    scenario: Scenario,
    label: str,
    min_max: Any,
) -> dict[str, Any]:
    top_rule_id = _rule_id(arg.TopRule)
    build_from = arg.BuildFromArguments or set()
    premise_ids: list[str] = []
    for condition in arg.TopRule.LeftSide:
        match = next((s for s in build_from if s.Conclusion == condition), None)
        if match is not None:
            premise_ids.append(id_by_arg[match])
    sub_ids = sorted(
        (id_by_arg[s] for s in arg.Sub if s is not arg),
        key=lambda x: int(x[1:]),
    )
    rules_used = sorted({_rule_id(s.TopRule) for s in arg.Sub})
    return {
        "id": aid,
        "conclusion": arg.Conclusion,
        "conclusion_nl": _conclusion_nl(arg.Conclusion, scenario),
        "top_rule": top_rule_id,
        "premises": premise_ids,
        "sub_arguments": sub_ids,
        "rules_used": rules_used,
        "label": label,
        "min_max": min_max,
        "is_fact": top_rule_id in scenario.facts,
    }


def _attack_dict(attack: Attack, id_by_arg: Mapping[Argument, str]) -> dict[str, Any]:
    return {
        "from": id_by_arg[attack.From],
        "to": id_by_arg[attack.To],
        "type": classify_attack(attack.From, attack.To),
    }


def classify_attack(attacker: Argument, target: Argument) -> str:
    """Classify an existing attack edge as `"undercut"` or `"rebut"`.

    This mirrors the undercut predicate inside
    `ArgumentationSystem.ArgumentBuilder.does_attacks`: if the
    attacker's conclusion is the negation of a defeasible
    sub-argument's top-rule name, it is an undercut; otherwise it is a
    rebut. The ABDA engine is kept untouched, so this classification
    is done at the serialization layer.
    """
    for sub in target.Sub:
        if isinstance(sub.TopRule, DefeasibleRule) and _is_negation(
            sub.TopRule.Name, attacker.Conclusion
        ):
            return "undercut"
    return "rebut"


def _is_negation(x: str, y: str) -> bool:
    return x == "-" + y or y == "-" + x


def _rule_id(rule) -> str:
    """Return the scenario id for an ABDA rule object.

    `scenario_to_rule_collection` stamps `_scenario_id` on every rule
    it builds, covering both strict (which lacks a `Name` field) and
    defeasible rules uniformly.
    """
    return getattr(rule, "_scenario_id", "") or getattr(rule, "Name", "") or ""


def _conclusion_nl(literal: str, scenario: Scenario) -> str:
    negated = literal.startswith("-")
    base = literal[1:] if negated else literal
    for section in (
        scenario.facts,
        scenario.assumptions,
        scenario.propositions,
        scenario.conclusions,
    ):
        entry = section.get(base)
        if entry is not None:
            if not negated:
                return entry.description
            return entry.negated_description or f"it is not the case that {entry.description}"
    rule = scenario.rules.get(base)
    if rule is not None:
        if not negated:
            return f"rule '{base}' applies"
        return rule.negated_description or f"rule '{base}' does not apply"
    return literal


def _aggregate_proposition_labels(
    scenario: Scenario,
    arguments: Iterable[Argument],
    labelling: Mapping[Argument, str],
) -> dict[str, str]:
    """Proposition-level labelling, per Caminada et al. (2015)
    Definition 11.

    For each atom c in the Herbrand base:

        ConcLab(c) = max({ArgLab(A) | Conc(A) = c} ∪ {out})

    with ordering **in > undec > out**. I.e., the conclusion is
    labelled by the highest label any argument for it carries (in),
    else undec, else out (including the default-out for atoms with no
    arguments).

    Mapped to UI labels: in → accepted, undec → undecided, out →
    rejected.

    One UI-layer deviation: we surface `absent` when *neither* X nor
    -X has any argument at all. Caminada's default-out would classify
    this as rejected, but distinguishing "totally unargued" from
    "argued and defeated" is useful for the Conclusions dashboard --
    the Explain button is disabled for absent propositions since there
    is no derivation to walk.

    A proposition where both X and -X are warranted (both have "in"
    arguments) violates direct consistency and raises; callers should
    surface this as a scenario-level error.
    """
    candidate_ids: set[str] = set()
    candidate_ids.update(scenario.facts)
    candidate_ids.update(scenario.assumptions)
    candidate_ids.update(scenario.propositions)
    candidate_ids.update(scenario.conclusions)

    # Collect labels per conclusion string (pid and -pid both relevant).
    labels_by_conclusion: dict[str, list[str]] = {}
    for arg in arguments:
        labels_by_conclusion.setdefault(arg.Conclusion, []).append(labelling[arg])

    labels: dict[str, str] = {}
    for pid in sorted(candidate_ids):
        x_labels = labels_by_conclusion.get(pid, [])
        neg_labels = labels_by_conclusion.get(f"-{pid}", [])

        # Direct-consistency check: both X and -X warranted at once.
        if "in" in x_labels and "in" in neg_labels:
            raise ValueError(
                f"proposition '{pid}': both positive and negative are warranted "
                "(scenario is contradictory)"
            )

        # Absent: nothing argued for either side.
        if not x_labels and not neg_labels:
            labels[pid] = "absent"
            continue

        # Caminada Def 11: max over {in, undec, out} with ordering in > undec > out.
        # Default "out" applies when x_labels is empty but -X has arguments.
        if "in" in x_labels:
            labels[pid] = "accepted"
        elif "undec" in x_labels:
            labels[pid] = "undecided"
        else:
            labels[pid] = "rejected"
    return labels
