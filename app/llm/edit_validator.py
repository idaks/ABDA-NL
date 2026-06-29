"""Deterministic Validator for Proposer output.

Runs before the LLM Reviewer. Catches purely syntactic / structural
problems that have no judgment component: dangling premise refs, id
collisions, id length violations, schema errors, etc. These are
*blocking* -- the Proposer must retry until the Validator passes (or
the pipeline gives up).

Design note: by hoisting these checks out of the LLM Reviewer, the
Reviewer prompt can focus on semantic judgment (NL faithfulness,
bridging-rule smell) and emit advisory issues only. The flow splits
cleanly into "must fix (mechanical)" and "worth considering
(judgment)".
"""
from __future__ import annotations

import re
from typing import Any

from app.scenario.diff_ops import DiffOpError, _validate_payload
from app.scenario.model import Scenario

# Soft ceiling surfaced to users via the Proposer prompt and retry
# feedback.  The Validator flags strictly at MAX_ID_LEN + 1 -- the
# ceiling is real, not aspirational.
MAX_ID_LEN = 24

# Modal / deontic tokens whose presence in a fact description
# indicates the Proposer has smuggled a hedge into what should be a
# categorical assertion.  Matches the Proposer prompt's explicit norm
# ("facts must not smuggle in deontic or epistemic
# modality"). Assumptions are permissive ("treated as", "presumed")
# and are not checked.
_FACT_MODAL_RE = re.compile(
    r"\b(probably|likely|might|seems|should|ought|apparently|presumably|perhaps|possibly|supposedly|allegedly)\b",
    re.IGNORECASE,
)


class ValidationIssue:
    """A single syntax/structure issue, classified by severity.

    - `blocking` (default): the op cannot be applied as-is; the
      Proposer must retry with corrective feedback.
    - `advisory`: the op is structurally valid and applies cleanly,
      but has a downstream concern worth surfacing to the user (e.g. a
      premise that is not yet in the scenario -- the rule will land
      but won't fire until the missing premise exists as a fact or
      assumption).

    Advisory issues are passed through as `severity: warning` review
    issues in the API response.
    """

    __slots__ = ("code", "message", "severity")

    def __init__(self, code: str, message: str, severity: str = "blocking") -> None:
        self.code = code
        self.message = message
        self.severity = severity

    def __repr__(self) -> str:  # pragma: no cover — debug only
        return f"ValidationIssue({self.code!r}, {self.message!r}, severity={self.severity!r})"

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "severity": self.severity}


def split_issues(issues: list["ValidationIssue"]) -> tuple[list["ValidationIssue"], list["ValidationIssue"]]:
    """Partition into (blocking, advisory)."""
    blocking = [i for i in issues if i.severity == "blocking"]
    advisory = [i for i in issues if i.severity == "advisory"]
    return blocking, advisory


def validate_op(op: dict[str, Any], scenario: Scenario) -> list[ValidationIssue]:
    """Return a list of blocking issues; empty list = clean.

    Runs in four passes:

      1. Schema -- shape/type/required-fields, via
      diff_ops._validate_payload.
      2. Id discipline -- length, collision, existence (for
      modify-rule).
      3. Reference integrity -- premise literals must resolve.
      4. Type discipline -- strict rules cannot be inactive.

    Passes are skipped on earlier failure only when continuing would
    raise a confusing secondary error (e.g. skip reference checks if
    the rule payload is malformed).
    """
    kind = op.get("op")
    issues: list[ValidationIssue] = []

    # --- Pass 1: schema ---
    # Delegate to the existing Draft-2020-12 validator. Surfaces the same
    # errors POST /state would surface when applying the op directly, but
    # without the exception having to bubble through the HTTP layer.
    payload_key = _payload_key_for(kind)
    if payload_key is not None:
        payload = op.get(payload_key)
        if not isinstance(payload, dict):
            issues.append(ValidationIssue(
                "missing_payload",
                f"op[{kind!r}] is missing a well-formed `{payload_key}` payload",
            ))
            return issues  # can't sanely continue without a payload
        try:
            _validate_payload(payload_key, payload)
        except DiffOpError as e:
            issues.append(ValidationIssue("schema", str(e)))
            # Schema failures often cascade into confusing downstream
            # errors; bail early and let the Proposer fix them first.
            return issues

    # --- Pass 2: id discipline ---
    op_id = op.get("id")
    if not isinstance(op_id, str) or not op_id:
        issues.append(ValidationIssue("missing_id", "op is missing a string `id`"))
        return issues

    all_ids = scenario.all_ids()

    if kind in {"add-rule", "add-fact", "add-assumption"}:
        if op_id in all_ids:
            # Exception: add-fact / add-assumption may replace an id that
            # exists ONLY as a proposition (auto-declared forward premise
            # from a previous rule edit). The user's intent there is to
            # "promote" the proposition so the rule that introduced it can
            # fire. The diff_ops layer performs the promotion.
            is_pure_prop = (
                op_id in (scenario.propositions or {})
                and op_id not in (scenario.facts or {})
                and op_id not in (scenario.assumptions or {})
                and op_id not in (scenario.conclusions or {})
                and op_id not in (scenario.rules or {})
            )
            promoteable = kind in {"add-fact", "add-assumption"} and is_pure_prop
            if not promoteable:
                issues.append(ValidationIssue(
                    "id_collision",
                    f"id `{op_id}` already exists in the scenario; pick a new id",
                ))
    elif kind == "modify-rule":
        if op_id not in (scenario.rules or {}):
            issues.append(ValidationIssue(
                "unknown_rule_id",
                f"modify-rule target `{op_id}` is not an existing rule; pick one of the declared rule ids",
            ))

    # id_too_long applies only to newly-minted ids. modify-rule keeps the
    # rule's existing id (see _coerce_modify_id in edit_service), so a
    # legacy scenario that was authored before the id-length ceiling is
    # still editable -- otherwise any rule whose id is over the cap would be
    # permanently unmodifiable through the UI.
    if kind != "modify-rule" and len(op_id) > MAX_ID_LEN:
        issues.append(ValidationIssue(
            "id_too_long",
            (
                f"id `{op_id}` is {len(op_id)} characters; the UI target is "
                f"≤{MAX_ID_LEN}. Shorten to 1-3 meaningful snake_case tokens"
            ),
        ))

    # --- Pass 3: reference integrity (rules only) ---
    if kind in {"add-rule", "modify-rule"}:
        rule = op.get("rule") or {}
        premises = rule.get("premises") or []
        # The rule's own id is the conclusion of an undercut literal `-<id>`
        # that may appear elsewhere -- but it is never a *premise* of itself
        # in the existing scenario encoding. We don't pre-approve it here.
        known = set(all_ids)
        # For modify-rule, the target rule is being replaced; its own id
        # stays in the scenario.
        for lit in premises:
            if not isinstance(lit, str):
                continue  # schema pass would have caught non-string
            ref = lit[1:] if lit.startswith("-") else lit
            if ref not in known:
                # Advisory, not blocking: the rule is structurally valid
                # and applies; it just won't fire until the missing
                # literal is declared as a fact or assumption. The user
                # sees this as a severity=warning review issue and can
                # choose to Apply anyway.
                issues.append(ValidationIssue(
                    "unknown_premise",
                    (
                        f"premise `{lit}` is not declared in the scenario. "
                        "The rule will land but will not fire until this literal "
                        "is added as a fact or assumption"
                    ),
                    severity="advisory",
                ))
        # Conclusion may be a new proposition (allowed) or reference
        # an existing literal (normal case). We don't flag unknown
        # conclusions; the UI naturally surfaces them as new nodes.

    # --- Pass 4: type discipline ---
    if kind in {"add-rule", "modify-rule"}:
        rule = op.get("rule") or {}
        if rule.get("type") == "strict" and rule.get("active") is False:
            issues.append(ValidationIssue(
                "strict_inactive",
                "strict rules cannot be inactive; drop the `active: false` field or change `type` to `defeasible`",
            ))

    # --- Pass 5: fact description modality ---
    # Facts assert categorical claims; modal / deontic hedges belong in an
    # assumption ("treated as X", "presumed Y") or in a rule's defeasible
    # conclusion, not in a fact's description. Advisory only -- the op still
    # applies, but the user sees the warning and can rephrase or switch to
    # an assumption.
    if kind == "add-fact":
        desc = (op.get("fact") or {}).get("description") or ""
        match = _FACT_MODAL_RE.search(desc)
        if match:
            issues.append(ValidationIssue(
                "fact_modal_wording",
                (
                    f"fact description contains hedging wording (`{match.group(0).lower()}`). "
                    "Facts are categorical assertions; modal or deontic wording typically "
                    "belongs in an assumption or a defeasible rule's conclusion. Consider "
                    "rephrasing without the hedge, or adding this as an assumption instead."
                ),
                severity="advisory",
            ))

    return issues


def _payload_key_for(kind: str | None) -> str | None:
    """Return the payload sub-key for an edit op, or None for non-edit
    ops."""
    return {
        "add-rule": "rule",
        "modify-rule": "rule",
        "add-fact": "fact",
        "add-assumption": "assumption",
    }.get(kind or "")


def is_trivial_edit(op: dict[str, Any]) -> bool:
    """Edits with no rule semantics benefit little from the LLM
    Reviewer.

    A bare add-fact or add-assumption with no premises and no unusual
    modifiers has essentially no way to violate faithfulness or smell
    like a bridging rule. Skip the Reviewer call to save tokens and
    latency.
    """
    kind = op.get("op")
    return kind in {"add-fact", "add-assumption"}
