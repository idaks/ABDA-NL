"""Tool-use schemas for the edit Proposer.

Each edit task (add-rule, modify-rule, add-fact, add-assumption) has
one tool. The backend forces the model to emit via that tool, so we
always get structured output that maps directly onto a `diff_op` dict.

The `input_schema` shapes mirror the scenario JSON-schema defs under
`app/schemas/scenario.schema.json`.
"""
from __future__ import annotations

from typing import Any

# ID shape: non-empty, starts with a letter, then letters/digits/underscores.
_ID_PATTERN = r"^[a-zA-Z][a-zA-Z0-9_]*$"

# A literal is either an ID ("popov_has_poss") or "-" + ID ("-popov_has_poss").
_LITERAL_PATTERN = r"^-?[a-zA-Z][a-zA-Z0-9_]*$"

_NEW_PREMISE_NOTES_SCHEMA: dict[str, Any] = {
    "type": "array",
    "description": (
        "OPTIONAL -- leave empty unless a premise you listed is NOT already in the scenario. "
        "When a rule references a literal that does not exist, include one entry here per new "
        "literal, with the id you used and a one-sentence natural-language description of what "
        "the premise means. The system surfaces these descriptions to the user so they can add "
        "the missing fact or assumption in a separate edit."
    ),
    "items": {
        "type": "object",
        "required": ["id", "description"],
        "additionalProperties": False,
        "properties": {
            "id": {
                "type": "string",
                "pattern": _ID_PATTERN,
                "description": "The premise id that doesn't yet exist in the scenario. Must match exactly one of the premise ids in the rule.",
            },
            "description": {
                "type": "string",
                "minLength": 1,
                "description": "One concise declarative sentence describing what the premise means in domain terms. Written in the same voice as existing fact descriptions in the scenario.",
            },
        },
    },
}


_RULE_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["id", "rule"],
    "additionalProperties": False,
    "properties": {
        "id": {
            "type": "string",
            "pattern": _ID_PATTERN,
            "description": "A unique new identifier for this rule (must not collide with any existing rule, fact, assumption, or proposition in the scenario).",
        },
        "new_premise_notes": _NEW_PREMISE_NOTES_SCHEMA,
        "rule": {
            "type": "object",
            "required": ["type", "premises", "conclusion"],
            "additionalProperties": False,
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["strict", "defeasible"],
                    "description": "Strict rules cannot be defeated; defeasible rules can.",
                },
                "premises": {
                    "type": "array",
                    "items": {"type": "string", "pattern": _LITERAL_PATTERN},
                    "description": "Literals that must hold for the rule to fire. Each premise must refer to a known fact, assumption, proposition, or rule-conclusion in the scenario.",
                },
                "conclusion": {
                    "type": "string",
                    "pattern": _LITERAL_PATTERN,
                    "description": "The literal concluded when all premises hold. May be the negation of any known literal (prefix with '-').",
                },
                "category": {
                    "type": "string",
                    "description": "Short scenario-specific category label (e.g. 'ecology', 'cardiac'). Omit if unclear.",
                },
                "source": {
                    "type": "string",
                    "description": "Where this rule comes from in the source material. Cite a filename from the corpus when possible.",
                },
                "block": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Preference tier. Higher blocks override lower ones. Default is 1.",
                },
                "active": {
                    "type": "boolean",
                    "description": "Whether the rule is currently in effect. Only defeasible rules may be inactive.",
                },
                "negated_description": {
                    "type": "string",
                    "description": "NL rendering of the undercut literal '-<rule_id>'. Used by the UI when this rule is undercut.",
                },
            },
        },
    },
}

_FACT_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["id", "fact"],
    "additionalProperties": False,
    "properties": {
        "id": {
            "type": "string",
            "pattern": _ID_PATTERN,
            "description": "A unique new identifier for this fact.",
        },
        "fact": {
            "type": "object",
            "required": ["description"],
            "additionalProperties": False,
            "properties": {
                "description": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Natural-language rendering of the fact. Reads as a declarative sentence fragment ('the patient has biopsy-confirmed Barrett's esophagus').",
                },
                "negated_description": {"type": "string", "minLength": 1},
                "category": {"type": "string"},
                "source": {"type": "string"},
            },
        },
    },
}

_ASSUMPTION_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["id", "assumption"],
    "additionalProperties": False,
    "properties": {
        "id": {
            "type": "string",
            "pattern": _ID_PATTERN,
            "description": "A unique new identifier for this assumption.",
        },
        "assumption": {
            "type": "object",
            "required": ["description"],
            "additionalProperties": False,
            "properties": {
                "description": {
                    "type": "string",
                    "minLength": 1,
                    "description": "NL rendering of the assumption. Reads as a declarative sentence.",
                },
                "negated_description": {"type": "string", "minLength": 1},
                "category": {"type": "string"},
                "source": {"type": "string"},
                "active": {"type": "boolean"},
                "block": {"type": "integer", "minimum": 1},
            },
        },
    },
}


# The modify-rule tool takes an *existing* rule id. The schema is
# otherwise identical to add-rule. The backend Reviewer validates the
# id exists.
_MODIFY_RULE_INPUT_SCHEMA: dict[str, Any] = {
    **_RULE_INPUT_SCHEMA,
    "properties": {
        **_RULE_INPUT_SCHEMA["properties"],
        "id": {
            "type": "string",
            "pattern": _ID_PATTERN,
            "description": "The id of an EXISTING rule to replace. Must already appear in the scenario.",
        },
    },
}


PROPOSER_TOOLS: dict[str, dict[str, Any]] = {
    "add-rule": {
        "name": "propose_add_rule",
        "description": "Propose adding a new rule to the scenario. Use when the user asks to add a new inference step or default.",
        "input_schema": _RULE_INPUT_SCHEMA,
    },
    "modify-rule": {
        "name": "propose_modify_rule",
        "description": "Propose replacing an existing rule with a revised version. Use when the user asks to edit, strengthen, qualify, or reformulate an existing rule.",
        "input_schema": _MODIFY_RULE_INPUT_SCHEMA,
    },
    "add-fact": {
        "name": "propose_add_fact",
        "description": "Propose adding a new fact (strict premise always in effect) to the scenario.",
        "input_schema": _FACT_INPUT_SCHEMA,
    },
    "add-assumption": {
        "name": "propose_add_assumption",
        "description": "Propose adding a new assumption (defeasible premise that may be toggled off) to the scenario.",
        "input_schema": _ASSUMPTION_INPUT_SCHEMA,
    },
}


REVIEWER_TOOL: dict[str, Any] = {
    "name": "review_edit",
    "description": "Emit semantic review notes on the proposed edit. Issues are advisory -- they surface in the UI but do not block the user from applying the edit. Most reviews emit an empty `issues` array.",
    "input_schema": {
        "type": "object",
        "required": ["issues"],
        "additionalProperties": False,
        "properties": {
            "issues": {
                "type": "array",
                "description": "Advisory notes about the edit's semantic quality. Empty array is the expected output when the edit is well-formed.",
                "items": {
                    "type": "object",
                    "required": ["severity", "message"],
                    "additionalProperties": False,
                    "properties": {
                        "severity": {
                            "type": "string",
                            "enum": ["blocker", "warning", "note"],
                            "description": "blocker: edit would clearly break the scenario's meaning. warning: substantive concern the user should see before applying. note: minor stylistic remark.",
                        },
                        "message": {
                            "type": "string",
                            "description": "One-sentence description of the concern. Concrete and specific; no hedging.",
                        },
                    },
                },
            },
        },
    },
}


def tool_for(task: str) -> dict[str, Any]:
    """Look up the tool definition for a given task name."""
    if task not in PROPOSER_TOOLS:
        raise ValueError(
            f"unknown edit task: {task!r}; valid: {sorted(PROPOSER_TOOLS)}"
        )
    return PROPOSER_TOOLS[task]


def diff_op_from_tool_input(task: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    """Wrap the tool's `{id, rule|fact|assumption}` input as a diff_op
    dict.

    Metadata fields the Proposer emits alongside the payload
    (currently `new_premise_notes`) are *not* part of the diff_op --
    the scenario substrate's schema forbids unknown keys. Callers that
    need the metadata use `notes_from_tool_input` separately.
    """
    op = {"op": task, "id": tool_input["id"]}
    payload_key = {"add-rule": "rule", "modify-rule": "rule",
                   "add-fact": "fact", "add-assumption": "assumption"}[task]
    op[payload_key] = tool_input[payload_key]
    return op


def notes_from_tool_input(task: str, tool_input: dict[str, Any]) -> list[dict[str, str]]:
    """Extract the Proposer's notes on new premises, if any.

    Only `add-rule` and `modify-rule` carry `new_premise_notes`; the
    fact/assumption tasks never reference other literals. Returns a
    list of `{id, description}` dicts, empty if the Proposer didn't
    emit any.
    """
    if task not in {"add-rule", "modify-rule"}:
        return []
    raw = tool_input.get("new_premise_notes") or []
    notes: list[dict[str, str]] = []
    for n in raw:
        if not isinstance(n, dict):
            continue
        nid = str(n.get("id", "")).strip()
        desc = str(n.get("description", "")).strip()
        if nid and desc:
            notes.append({"id": nid, "description": desc})
    return notes
