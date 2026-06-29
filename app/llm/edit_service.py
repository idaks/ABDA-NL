"""Edit pipeline: turns an NL edit request into a vetted diff_op.

Two-stage pipeline, split along the syntactic/semantic seam:

  1. **Proposer** (LLM, forced tool-use) emits a candidate diff_op.
  2. **Validator** (deterministic, in `edit_validator.py`) checks for
     syntactic / structural errors: dangling premise refs, id
     collisions, id length, schema shape, strict+inactive. These are
     blocking: on failure we retry the Proposer with the Validator's
     errors as feedback.  Up to `MAX_PROPOSER_ATTEMPTS` total
     attempts; if all fail we raise `ProposerRetryExhausted` (surfaced
     as HTTP 422 by the API layer).
  3. **Reviewer** (LLM, forced tool-use) is called once on the first
     valid op and emits *advisory* issues tagged by severity. The
     Reviewer never blocks and never triggers a retry; its output
     rides along in the result for the UI to surface. For trivial
     edits (see `is_trivial_edit`) the Reviewer call is skipped
     entirely.

The user-facing UI shows the op + any advisory issues; the user is the
arbiter and clicks Apply, Refine, or Cancel. "Refine" restarts the
pipeline with additional NL feedback -- the cheap alternative to a
third LLM agent.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.llm.chat_service import build_scenario_block, build_state_block
from app.llm.client import LLMClient, ToolCallResponse
from app.llm.corpus import build_corpus_block
from app.llm.edit_schemas import (
    PROPOSER_TOOLS,
    REVIEWER_TOOL,
    diff_op_from_tool_input,
    notes_from_tool_input,
    tool_for,
)
from app.llm.edit_validator import (
    MAX_ID_LEN,
    ValidationIssue,
    is_trivial_edit,
    split_issues,
    validate_op,
)
from app.llm.prompts import load_prompt

log = logging.getLogger(__name__)

MAX_TOKENS_PER_PROPOSE = 2048  # Proposer output is structured, so this is generous.
MAX_TOKENS_PER_REVIEW = 1024   # Reviewer emits structured issues, not prose.
# Total Proposer attempts (initial + retries). 3 = one original + up to two
# retries with Validator feedback. Deterministic validator errors are
# explicit enough that 3 is plenty; beyond that the user's instruction is
# the problem, not the Proposer.
MAX_PROPOSER_ATTEMPTS = 3

VALID_TASKS = set(PROPOSER_TOOLS.keys())


class ProposerRetryExhausted(Exception):
    """Proposer failed the deterministic Validator N times in a row.

    The API surfaces this as HTTP 422; the user is expected to
    rephrase the instruction rather than retry blindly.

    `last_notes` carries the Proposer's self-annotations of new
    premises (`{id, description}` pairs) from the final failing
    attempt. When the failure is an `unknown_premise` cluster, the API
    cross-references these with the Validator's issues so the user
    sees NL descriptions of what's missing rather than raw identifier
    strings.
    """

    def __init__(
        self,
        attempts: int,
        last_issues: list[ValidationIssue],
        last_notes: list[dict[str, str]] | None = None,
    ) -> None:
        self.attempts = attempts
        self.last_issues = list(last_issues)
        self.last_notes = list(last_notes or [])
        super().__init__(
            f"proposer exhausted after {attempts} attempts; "
            f"{len(last_issues)} validator issue(s) remaining"
        )


@dataclass
class ReviewIssue:
    """One advisory issue emitted by the LLM Reviewer."""

    severity: str  # "blocker" | "warning" | "note"
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"severity": self.severity, "message": self.message}


@dataclass
class ProposeResult:
    """Result envelope for one end-to-end propose run."""

    op: dict[str, Any]  # ready-to-apply diff_op
    model: str
    stop_reason: str
    usage: dict[str, int]  # summed across all LLM calls
    latency_ms: int        # summed across all LLM calls
    # Number of Proposer attempts the Validator required (1 = no retry).
    proposer_attempts: int = 1
    # True if the LLM Reviewer was called on this op (False for trivial
    # edits where it's skipped).
    reviewed: bool = False
    # Advisory issues from the Reviewer, severity-tagged. Empty list =
    # clean review or skipped Reviewer.
    review_issues: list[ReviewIssue] = field(default_factory=list)


@dataclass
class ReviewResult:
    """Structured envelope for one Reviewer turn."""

    issues: list[ReviewIssue]
    usage: dict[str, int]
    latency_ms: int


# --- Prompt builders -------------------------------------------------------


def build_proposer_system_prompt(
    scenario: Any,
    af: dict[str, Any],
    diff_ops: list[dict[str, Any]],
    *,
    scenario_dir: Path,
) -> str:
    corpus_block = build_corpus_block(
        scenario_dir,
        list(scenario.corpus or []),
        getattr(scenario, "title", "") or scenario_dir.name,
    )
    return load_prompt(
        "proposer_system",
        scenario_block=build_scenario_block(scenario),
        corpus_block=corpus_block,
        state_block=build_state_block(scenario, af, diff_ops),
    )


def build_reviewer_system_prompt(
    scenario: Any,
    af: dict[str, Any],
    diff_ops: list[dict[str, Any]],
    *,
    scenario_dir: Path,
    user_instruction: str,
    proposed_edit: dict[str, Any],
) -> str:
    corpus_block = build_corpus_block(
        scenario_dir,
        list(scenario.corpus or []),
        getattr(scenario, "title", "") or scenario_dir.name,
    )
    return load_prompt(
        "reviewer_system",
        scenario_block=build_scenario_block(scenario),
        corpus_block=corpus_block,
        state_block=build_state_block(scenario, af, diff_ops),
        user_instruction=user_instruction.strip(),
        proposed_edit=pretty_op_for_review(proposed_edit),
    )


def _build_user_message(task: str, instruction: str, existing_id: str | None) -> str:
    lines = [f"**Edit task:** `{task}`"]
    if task == "modify-rule":
        if not existing_id:
            raise ValueError("modify-rule requires `existing_id`")
        lines.append(f"**Rule to modify:** `{existing_id}`")
    lines.append("**User instruction:**")
    lines.append(instruction.strip())
    return "\n\n".join(lines)


_ID_IN_BACKTICKS = re.compile(r"`(-?[A-Za-z_][A-Za-z0-9_]*)`")


def _shorten_id_hint(long_id: str) -> str:
    """Generate a worked transformation hint for an over-length id.

    The retry feedback to the Proposer becomes a lot more directive
    when we show the model a *specific* shortening of the id it just
    emitted.  Open models (Qwen, Llama, Phi-mini, Gemma) all benefit
    -- they tend to abstract from worked examples better than from a
    generic rule.

    Strategy: drop trailing tokens until the id fits in MAX_ID_LEN
    characters. If we can't, abbreviate the longest token to its first
    4 characters. Returns a short suggestion string the retry message
    can inline. Pure text -- the Proposer is free to ignore and pick
    its own shorter id.
    """
    if not long_id:
        return ""
    if len(long_id) <= MAX_ID_LEN:
        return long_id
    parts = long_id.split("_")
    # Try dropping tokens from the right one at a time.
    for n in range(len(parts) - 1, 0, -1):
        candidate = "_".join(parts[:n])
        if 0 < len(candidate) <= MAX_ID_LEN:
            return candidate
    # Fallback: abbreviate the longest token to its first 4 characters.
    longest_idx = max(range(len(parts)), key=lambda i: len(parts[i]))
    parts[longest_idx] = parts[longest_idx][:4]
    candidate = "_".join(parts)
    if len(candidate) > MAX_ID_LEN:
        candidate = candidate[:MAX_ID_LEN]
    return candidate


def _build_validator_retry_message(
    original_user_message: str,
    validator_issues: list[ValidationIssue],
) -> str:
    """Retry prompt for the Proposer after a Validator failure.

    Deliberately omits the previous op JSON: the previous attempt
    anchors the model on the wrong structure. Issues are specific
    enough that the Proposer can act on them without seeing the broken
    prior output.

    Carries explicit guidance for the most common failure modes:

    - `id_too_long` -- inline a worked transformation showing a
      specific shortening of the offending id. Open models follow
      worked examples much better than abstract rules.
    - `unknown_premise` / `unknown_rule_id` -- the Proposer literally
      cannot invent the missing literal as part of this single
      edit. The instruction may need to be split. Tell the Proposer to
      either rewrite using existing literals or emit its best
      well-formed attempt and let the API surface the shortfall.
    """
    bullets = "\n".join(f"- {i.message}" for i in validator_issues)
    has_missing_literal = any(
        i.code in {"unknown_premise", "unknown_rule_id"} for i in validator_issues
    )
    has_long_id = any(i.code == "id_too_long" for i in validator_issues)

    guidance_parts = [
        "Fix exactly these issues and keep the rest of the proposal "
        "consistent with the original instruction."
    ]

    if has_long_id:
        # Pull the offending id out of the message and propose a concrete
        # shortening for the Proposer to use as a starting point.
        long_issue = next(i for i in validator_issues if i.code == "id_too_long")
        m = _ID_IN_BACKTICKS.search(long_issue.message)
        long_id = m.group(1) if m else None
        suggestion = _shorten_id_hint(long_id) if long_id else ""
        if long_id and suggestion:
            guidance_parts.append(
                f"The id `{long_id}` was {len(long_id)} characters; emit a "
                f"NEW id at most {MAX_ID_LEN} characters long. For example: `{suggestion}` "
                f"({len(suggestion)} chars). You may use this exact id, or pick "
                "an equally short alternative that captures the domain meaning. "
                "Do NOT re-emit the same too-long id."
            )
        else:
            guidance_parts.append(
                f"Emit a NEW id at most {MAX_ID_LEN} characters long. Do NOT "
                "re-emit the same too-long id."
            )

    if has_missing_literal:
        guidance_parts.append(
            "For `unknown_premise` / `unknown_rule_id`: you cannot invent new "
            "literals as part of this single edit. Either rewrite the proposal "
            "using only literals that already exist in the scenario (accepting "
            "that the rule may be less precise than requested), or, if no "
            "reasonable alternative exists, emit your best well-formed attempt "
            "-- the Validator will escalate the missing literal to the user "
            "for a follow-up edit."
        )

    guidance = "\n\n".join(guidance_parts)

    return (
        f"{original_user_message}\n\n"
        "---\n\n"
        "Your previous proposal failed deterministic validation:\n\n"
        f"{bullets}\n\n"
        f"{guidance}\n\n"
        "Emit a revised proposal via the same tool."
    )


def pretty_op_for_review(op: dict[str, Any]) -> str:
    """Render a proposed op as indented JSON for the Reviewer prompt."""
    return json.dumps(op, indent=2, sort_keys=True)


# --- Reviewer (advisory only) ---------------------------------------------


def run_review(
    scenario: Any,
    af: dict[str, Any],
    diff_ops: list[dict[str, Any]],
    *,
    user_instruction: str,
    proposed_edit: dict[str, Any],
    scenario_dir: Path,
    client: LLMClient,
) -> ReviewResult:
    """Run one Reviewer turn. Never blocks; returns advisory issues."""
    system_prompt = build_reviewer_system_prompt(
        scenario,
        af,
        diff_ops,
        scenario_dir=scenario_dir,
        user_instruction=user_instruction,
        proposed_edit=proposed_edit,
    )
    response: ToolCallResponse = client.tool_call(
        system=system_prompt,
        messages=[{"role": "user", "content": "Please review the proposed edit."}],
        tool=REVIEWER_TOOL,
        max_tokens=MAX_TOKENS_PER_REVIEW,
        cache=True,
    )

    issues: list[ReviewIssue] = []
    for raw in response.tool_input.get("issues") or []:
        if not isinstance(raw, dict):
            continue
        sev = str(raw.get("severity", "")).strip()
        msg = str(raw.get("message", "")).strip()
        if not msg or sev not in {"blocker", "warning", "note"}:
            continue
        issues.append(ReviewIssue(severity=sev, message=msg))

    log.info("review_turn issues=%d latency_ms=%d", len(issues), response.latency_ms)

    return ReviewResult(
        issues=issues,
        usage=response.usage,
        latency_ms=response.latency_ms,
    )


# --- Proposer + Validator loop --------------------------------------------


def _sum_usage(*usages: dict[str, int]) -> dict[str, int]:
    keys = {"input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"}
    out = {k: 0 for k in keys}
    for u in usages:
        for k in keys:
            out[k] += u.get(k, 0) or 0
    return out


def _coerce_modify_id(task: str, op: dict[str, Any], existing_id: str | None) -> dict[str, Any]:
    """For modify-rule, force the op id back to the requested existing_id."""
    if task == "modify-rule" and existing_id and op.get("id") != existing_id:
        log.info("proposer_id_coerced requested=%s proposer=%s", existing_id, op.get("id"))
        op["id"] = existing_id
    return op


def run_propose(
    scenario: Any,
    af: dict[str, Any],
    diff_ops: list[dict[str, Any]],
    *,
    task: str,
    instruction: str,
    existing_id: str | None = None,
    scenario_dir: Path,
    client: LLMClient,
) -> ProposeResult:
    """Run the end-to-end edit pipeline for one user instruction.

    Raises `ProposerRetryExhausted` if the Proposer can't produce a
    Validator-clean op within `MAX_PROPOSER_ATTEMPTS` tries.
    """
    if task not in VALID_TASKS:
        raise ValueError(f"unknown edit task: {task!r}; valid: {sorted(VALID_TASKS)}")
    if not instruction or not instruction.strip():
        raise ValueError("instruction must be a non-empty string")

    proposer_system = build_proposer_system_prompt(
        scenario, af, diff_ops, scenario_dir=scenario_dir
    )
    user_message = _build_user_message(task, instruction, existing_id)
    tool = tool_for(task)

    usages: list[dict[str, int]] = []
    latencies: list[int] = []
    op: dict[str, Any] | None = None
    last_response: ToolCallResponse | None = None
    last_blocking: list[ValidationIssue] = []
    last_notes: list[dict[str, str]] = []
    accepted_advisory: list[ValidationIssue] = []
    accepted_notes: list[dict[str, str]] = []

    # --- Proposer + Validator loop ---
    # Blocking issues trigger a retry; advisory issues
    # (e.g. unknown_premise) let the op through but ride along as
    # severity=warning review issues.
    next_user_message = user_message
    for attempt in range(1, MAX_PROPOSER_ATTEMPTS + 1):
        response: ToolCallResponse = client.tool_call(
            system=proposer_system,
            messages=[{"role": "user", "content": next_user_message}],
            tool=tool,
            max_tokens=MAX_TOKENS_PER_PROPOSE,
            cache=True,
        )
        usages.append(response.usage)
        latencies.append(response.latency_ms)
        last_response = response

        candidate = _coerce_modify_id(
            task,
            diff_op_from_tool_input(task, response.tool_input),
            existing_id,
        )
        notes = notes_from_tool_input(task, response.tool_input)
        issues = validate_op(candidate, scenario)
        blocking, advisory = split_issues(issues)

        if not blocking:
            op = candidate
            accepted_advisory = advisory
            accepted_notes = notes
            log.info(
                "propose_validated task=%s op_id=%s attempts=%d advisory=%d notes=%d",
                task, candidate.get("id"), attempt, len(advisory), len(notes),
            )
            break

        last_blocking = blocking
        last_notes = notes
        log.info(
            "propose_validator_flagged task=%s op_id=%s attempt=%d blocking=%d advisory=%d notes=%d",
            task, candidate.get("id"), attempt, len(blocking), len(advisory), len(notes),
        )
        if attempt < MAX_PROPOSER_ATTEMPTS:
            next_user_message = _build_validator_retry_message(user_message, blocking)

    if op is None:
        log.warning(
            "propose_exhausted task=%s attempts=%d last_blocking_codes=%s notes=%d",
            task,
            MAX_PROPOSER_ATTEMPTS,
            [i.code for i in last_blocking],
            len(last_notes),
        )
        raise ProposerRetryExhausted(MAX_PROPOSER_ATTEMPTS, last_blocking, last_notes)

    assert last_response is not None  # loop always sets it on success

    # --- Reviewer (advisory) ---
    review_issues: list[ReviewIssue] = []
    reviewed = False
    if not is_trivial_edit(op):
        review = run_review(
            scenario,
            af,
            diff_ops,
            user_instruction=instruction,
            proposed_edit=op,
            scenario_dir=scenario_dir,
            client=client,
        )
        review_issues = review.issues
        reviewed = True
        usages.append(review.usage)
        latencies.append(review.latency_ms)

    # Prepend Validator-advisory issues (e.g. unknown_premise) as
    # severity=warning so they render in the same UI panel as Reviewer
    # issues. When the Proposer annotated new_premise_notes, use the
    # NL description in the warning so the user sees plain English.
    advisory_prefix = _advisory_to_review_issues(accepted_advisory, accepted_notes)
    review_issues = advisory_prefix + review_issues

    # Attach the Proposer's forward-premise notes to the op so they
    # travel with it through the UI and into diff_ops.apply. There,
    # auto-declared propositions pick up the NL description from this
    # list rather than falling back to a generic placeholder --
    # critical for the promotion UX (user later says "add a fact that
    # the store is open", Proposer sees the matching proposition
    # description, reuses the id).
    if accepted_notes and op.get("op") in {"add-rule", "modify-rule"}:
        op["new_premise_notes"] = accepted_notes

    return ProposeResult(
        op=op,
        model=last_response.model,
        stop_reason=last_response.stop_reason,
        usage=_sum_usage(*usages),
        latency_ms=sum(latencies),
        proposer_attempts=len(latencies) - (1 if reviewed else 0),
        reviewed=reviewed,
        review_issues=review_issues,
    )


# Regex to extract the backticked literal from an advisory Validator
# message.  Kept local to this module so the API doesn't need to know
# Validator internals.
_ADVISORY_LITERAL_RE = re.compile(r"`(-?[A-Za-z_][A-Za-z0-9_]*)`")


def _advisory_to_review_issues(
    advisory: list[ValidationIssue],
    notes: list[dict[str, str]],
) -> list[ReviewIssue]:
    """Convert Validator advisory issues to severity=warning review
    issues.

    For `unknown_premise` we prefer the Proposer's NL description
    (from `new_premise_notes`) over the raw identifier: the UI message
    reads "This rule won't fire until 'the restaurant is open' is
    added as a fact or assumption" instead of "... `store_open` ...".
    """
    notes_by_id: dict[str, str] = {}
    for n in notes:
        nid = n.get("id")
        desc = n.get("description")
        if nid and desc:
            notes_by_id[nid] = desc

    out: list[ReviewIssue] = []
    for iss in advisory:
        msg = iss.message
        if iss.code == "unknown_premise":
            m = _ADVISORY_LITERAL_RE.search(msg)
            lit_ref = m.group(1).lstrip("-") if m else None
            desc = notes_by_id.get(lit_ref) if lit_ref else None
            if desc:
                msg = (
                    f"This rule references \u201c{desc}\u201d, which is not yet in the "
                    "scenario. The rule will land but will not fire until that item "
                    "is added as a fact or assumption."
                )
        out.append(ReviewIssue(severity="warning", message=msg))
    return out
