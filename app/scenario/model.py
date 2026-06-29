"""Dataclasses representing a parsed ABDA-NL scenario.

A `Scenario` is the authored state — after YAML parsing and before
ABDA compilation. `diff_ops.apply` takes a baseline `Scenario` plus a
list of ops and returns a new `Scenario` with the mutations
applied. The scenario is then compiled to an ABDA `RuleCollection` by
`loader.scenario_to_rule_collection`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

RuleType = Literal["strict", "defeasible"]


@dataclass
class Fact:
    id: str
    description: str
    negated_description: Optional[str] = None
    category: Optional[str] = None
    source: Optional[str] = None


@dataclass
class Assumption:
    id: str
    description: str
    negated_description: Optional[str] = None
    category: Optional[str] = None
    source: Optional[str] = None
    active: bool = True
    block: int = 1


@dataclass
class Proposition:
    """Used for both `propositions` (intermediate) and `conclusions`
    (headline).

    The two sections share the same shape; the distinction is semantic
    -- conclusions are the focal questions shown on the dashboard,
    propositions are internal machinery surfaced inside argument
    cards.
    """
    id: str
    description: str
    negated_description: Optional[str] = None
    category: Optional[str] = None


@dataclass
class Rule:
    id: str
    type: RuleType
    premises: list[str]
    conclusion: str
    negated_description: Optional[str] = None
    category: Optional[str] = None
    source: Optional[str] = None
    block: int = 1
    # active is ignored for strict rules
    active: bool = True


@dataclass
class Scenario:
    title: str
    description: str = ""
    facts: dict[str, Fact] = field(default_factory=dict)
    assumptions: dict[str, Assumption] = field(default_factory=dict)
    propositions: dict[str, Proposition] = field(default_factory=dict)
    conclusions: dict[str, Proposition] = field(default_factory=dict)
    rules: dict[str, Rule] = field(default_factory=dict)
    corpus: list[str] = field(default_factory=list)

    def all_ids(self) -> set[str]:
        """All declared identifiers across every section (used for
        reference checks)."""
        return (
            set(self.facts)
            | set(self.assumptions)
            | set(self.propositions)
            | set(self.conclusions)
            | set(self.rules)
        )
