"""Chat turn service — prompt builder, deterministic validator,
single-retry.

The LLM is bound to cited state via the system prompt, and a
deterministic post-hoc scan checks that every identifier and label it
asserts exists in the scenario / AF. On failure, the turn is retried
once with corrective feedback (silent to the user). If the retry still
flags, the second response is returned as-is.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.llm.client import LLMClient, LLMResponse
from app.llm.corpus import CorpusLoadError, build_corpus_block
from app.llm.prompts import load_prompt

log = logging.getLogger(__name__)

MAX_CONVERSATION_TURNS = 20  # user+assistant pairs = 40 messages max
MAX_TOKENS_PER_RESPONSE = 4096


@dataclass
class ChatTurnResult:
    """Result envelope for one chat turn, including observability
    fields."""

    text: str
    stop_reason: str
    usage: dict[str, int]
    model: str
    latency_ms: int
    validator_flags: list[str] = field(default_factory=list)
    retried: bool = False


# --- State block formatting -----------------------------------------------


def _format_labels(scenario: Any, af: dict[str, Any]) -> str:
    """Render each key conclusion with its label, description,
    producing rules, and undercutter rules — so the LLM doesn't have
    to guess rule relationships.

    For every key conclusion `C` we list:

    - Rules whose conclusion is exactly `C` (supporting arguments)
    - Rules whose conclusion is `-C` (opposing arguments)
    - For each supporting / opposing rule, any rule that undercuts it
      (rules whose conclusion is `-<rule_id>`)

    This is deterministic and comes directly from the scenario YAML —
    there is no room for the model to invent rule relationships.
    """
    labels = af.get("labels_by_proposition", {})
    rules = scenario.rules or {}
    assumptions = scenario.assumptions or {}

    # Group rules by conclusion literal (e.g., "hayashi_no_return",
    # "-hayashi_no_return").
    by_conclusion: dict[str, list[str]] = {}
    for rid, r in rules.items():
        by_conclusion.setdefault(r.conclusion, []).append(rid)

    # For each rule, which other rules undercut it? (Rules with
    # conclusion `-<rid>`.)
    undercutters: dict[str, list[str]] = {}
    for rid, r in rules.items():
        target = r.conclusion[1:] if r.conclusion.startswith("-") else None
        if target and target in rules:
            undercutters.setdefault(target, []).append(rid)

    def _render_rule(rid: str) -> str:
        r = rules[rid]
        premises_str = ", ".join(f"`{p}`" for p in (r.premises or [])) or "(no premises)"
        ucs = undercutters.get(rid, [])
        uc_str = f" — undercut by: {', '.join(f'`{u}`' for u in ucs)}" if ucs else ""
        kind = getattr(r, "type", "defeasible")
        block = getattr(r, "block", None)
        block_str = f", block={block}" if block is not None else ""
        return f"    - `{rid}` ({kind}{block_str}): {premises_str} → `{r.conclusion}`{uc_str}"

    lines = ["### Key conclusions — label, rules, and undercuts"]
    for conc_id, conc in (scenario.conclusions or {}).items():
        label = labels.get(conc_id, "absent")
        desc = getattr(conc, "description", "") or ""
        lines.append(f"\n- `{conc_id}` ({label}): {desc}")
        pro = by_conclusion.get(conc_id, [])
        con = by_conclusion.get(f"-{conc_id}", [])
        if pro:
            lines.append("  Supporting rules (conclude this):")
            for rid in pro:
                lines.append(_render_rule(rid))
        if con:
            lines.append("  Opposing rules (conclude its negation):")
            for rid in con:
                lines.append(_render_rule(rid))
        if not pro and not con:
            lines.append("  (no rule directly concludes this claim; label comes from absence of support)")
    return "\n".join(lines)


def _format_assumptions(scenario: Any) -> str:
    assumptions = scenario.assumptions or {}
    if not assumptions:
        return "### Assumptions\n\n(none)"
    lines = ["### Assumptions (toggleable facts)"]
    for aid, a in assumptions.items():
        state = "ACTIVE" if getattr(a, "active", False) else "inactive"
        desc = getattr(a, "description", "") or ""
        lines.append(f"- `{aid}` ({state}): {desc}")
    return "\n".join(lines)


def _format_attacks(af: dict[str, Any], scenario: Any) -> str:
    """Summarise attacks between key-conclusion arguments."""
    attacks = af.get("attacks") or []
    arguments = {a["id"]: a for a in (af.get("arguments") or [])}
    key_conclusions = set((scenario.conclusions or {}).keys())
    seen: set[tuple[str, str, str]] = set()
    pairs: list[str] = []
    for atk in attacks:
        from_arg = arguments.get(atk.get("from"))
        to_arg = arguments.get(atk.get("to"))
        if not from_arg or not to_arg:
            continue
        src = from_arg.get("conclusion", "").lstrip("-")
        dst = to_arg.get("conclusion", "").lstrip("-")
        if src not in key_conclusions and dst not in key_conclusions:
            continue
        key = (src, dst, atk.get("type", "rebut"))
        if key in seen:
            continue
        seen.add(key)
        pairs.append(f"- `{src}` {atk.get('type', 'rebut')}s argument for `{dst}`")
    if not pairs:
        return "### Attacks between key conclusions\n\n(none between key conclusions)"
    return "### Attacks between key conclusions\n\n" + "\n".join(pairs)


def _format_categories(scenario: Any) -> str:
    """List every category label currently used anywhere in the
    scenario.

    The Proposer uses this as the canonical vocabulary for `category`
    -- picking a new edit's category from this list is the strong
    default; inventing a fresh one is only a fallback when no existing
    label fits.
    """
    cats: set[str] = set()
    for section_name in ("facts", "assumptions", "propositions", "conclusions", "rules"):
        section = getattr(scenario, section_name, None) or {}
        for item in section.values():
            cat = getattr(item, "category", None)
            if cat:
                cats.add(cat)
    if not cats:
        return "### Categories used\n\n(none; this scenario does not use category labels)"
    ordered = sorted(cats)
    lines = [
        "### Categories used in this scenario",
        "These are the category labels in use across facts, assumptions, "
        "propositions, conclusions, and rules. Prefer one of these when "
        "categorising a new edit.",
    ]
    lines.append("- " + ", ".join(f"`{c}`" for c in ordered))
    return "\n".join(lines)


def _format_pending_propositions(scenario: Any) -> str:
    """List propositions that have no rule deriving them.

    These are typically forward-references auto-declared during a
    prior incremental rule edit. When the user later asks to add a
    fact or assumption whose NL description matches one of these, the
    Proposer should reuse the pending id (promoting the proposition)
    rather than minting a fresh one -- otherwise the upstream rule
    stays dormant.
    """
    propositions = scenario.propositions or {}
    if not propositions:
        return ""
    rules = scenario.rules or {}
    concluded: set[str] = set()
    for r in rules.values():
        c = getattr(r, "conclusion", "") or ""
        concluded.add(c[1:] if c.startswith("-") else c)
    pending = [(pid, p) for pid, p in propositions.items() if pid not in concluded]
    if not pending:
        return ""
    lines = [
        "### Pending propositions (declared but not yet derivable)",
        "These were introduced as premises in a prior rule edit but no fact, "
        "assumption, or rule currently concludes them. If the user's instruction "
        "describes one of these, reuse its id so the upstream rule can fire.",
    ]
    for pid, p in pending:
        desc = getattr(p, "description", "") or ""
        lines.append(f"- `{pid}`: {desc}")
    return "\n".join(lines)


def _format_diff_ops(diff_ops: list[dict[str, Any]]) -> str:
    """Render diff ops as a present-state delta, not a session timeline.

    The heading and framing bind modifications to current state so the
    model describes configuration ("X is currently active") rather than
    narrating user actions ("you toggled X").
    """
    if not diff_ops:
        return "### Modifications from baseline scenario\n\n(none; scenario is at its baseline)"
    lines = [
        "### Modifications from baseline scenario",
        "The items below differ from the as-authored scenario. Describe the "
        "resulting configuration in present tense (\"the scenario currently has "
        "X active\"), not as a sequence of user actions (\"you toggled X\").",
    ]
    for op in diff_ops:
        op_kind = op.get("op", "?")
        target = op.get("id", op.get("target", "?"))
        lines.append(f"- `{op_kind}` `{target}`")
    return "\n".join(lines)


def build_state_block(scenario: Any, af: dict[str, Any], diff_ops: list[dict[str, Any]]) -> str:
    sections = [
        _format_labels(scenario, af),
        _format_assumptions(scenario),
        _format_attacks(af, scenario),
        _format_categories(scenario),
    ]
    pending = _format_pending_propositions(scenario)
    if pending:
        sections.append(pending)
    sections.append(_format_diff_ops(diff_ops))
    return "\n\n".join(sections)


def build_scenario_block(scenario: Any) -> str:
    title = getattr(scenario, "title", "") or ""
    description = (getattr(scenario, "description", "") or "").strip()
    return f"**Title:** {title}\n\n**Description:** {description}"


def build_system_prompt(
    scenario: Any,
    af: dict[str, Any],
    diff_ops: list[dict[str, Any]],
    *,
    scenario_dir: Path,
) -> str:
    corpus_block = build_corpus_block(
        scenario_dir,
        list((scenario.corpus or [])),
        getattr(scenario, "title", "") or scenario_dir.name,
    )
    scenario_block = build_scenario_block(scenario)
    state_block = build_state_block(scenario, af, diff_ops)
    return load_prompt(
        "chat_system",
        scenario_block=scenario_block,
        corpus_block=corpus_block,
        state_block=state_block,
    )


# --- Deterministic validator ----------------------------------------------


_CITED_FILE_RE = re.compile(r"\[([A-Za-z0-9_\-]+\.(?:txt|pdf|md))\]")
_CITED_ID_RE = re.compile(r"`([a-zA-Z_][a-zA-Z0-9_]*)`")


def _declared_ids(scenario: Any) -> set[str]:
    ids: set[str] = set()
    for section in ("facts", "assumptions", "propositions", "conclusions", "rules"):
        d = getattr(scenario, section, {}) or {}
        ids.update(d.keys())
    return ids


def validate_response(response: str, scenario: Any, af: dict[str, Any]) -> list[str]:
    """Deterministic grounding scan.

    Three failure modes flagged:

    1. Unknown corpus citation — `[filename.ext]` naming a file not in
       the scenario's corpus list.
    2. Hallucinated identifier — an underscore-shaped backticked token
       that does not match any declared id or label.
    3. Identifier leak — a backticked token that *does* match a
       declared scenario id. The system prompt forbids surfacing raw
       identifiers; when one slips through, the retry tells the model
       to rephrase using natural-language descriptions.

    Returns a list of human-readable issues; empty list = OK.
    """
    issues: list[str] = []
    corpus = set(scenario.corpus or [])
    declared = _declared_ids(scenario)
    labels = {k.lower() for k in (af.get("labels_by_proposition") or {}).keys()}

    for m in _CITED_FILE_RE.finditer(response):
        fname = m.group(1)
        if fname not in corpus:
            issues.append(
                f"cited filename `{fname}` is not in the scenario's corpus list"
            )

    for m in _CITED_ID_RE.finditer(response):
        ident = m.group(1)
        is_declared = ident in declared or ident.lower() in labels
        if is_declared:
            # Declared scenario id in backticks = leak. Prompt forbids.
            issues.append(
                f"identifier `{ident}` was emitted in backticks; the response "
                "must describe items in natural language, never by raw scenario id"
            )
        elif "_" in ident:
            # Underscore-shaped token that isn't declared = hallucination.
            issues.append(f"cited identifier `{ident}` is not declared in the scenario")
        # Other backticked tokens (single English words without underscores and
        # not matching any id) are left alone.

    return issues


# --- Chat turn ------------------------------------------------------------


def _coerce_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cap history to the most recent N turns and sanity-check shape."""
    pairs = messages[-MAX_CONVERSATION_TURNS * 2 :]
    out = []
    for m in pairs:
        role = m.get("role")
        content = m.get("content")
        if role not in ("user", "assistant"):
            continue
        if not isinstance(content, str):
            continue
        out.append({"role": role, "content": content})
    if not out or out[-1]["role"] != "user":
        raise ValueError("conversation must end with a user message")
    return out


def _corrective_retry_message(issues: list[str]) -> str:
    bullets = "\n".join(f"- {i}" for i in issues)
    # Framed as an automated pre-send check, NOT user feedback. If the model
    # thinks the user complained, it responds with an apology/acknowledgment
    # ("You're right to flag that...") that leaks the retry mechanism. The
    # "do not apologize / reference this check" line is load-bearing.
    return (
        "[AUTOMATED VALIDATOR -- not user feedback]\n\n"
        "The draft answer you emitted cannot be sent to the user because it "
        "references material not present in the scenario:\n\n"
        f"{bullets}\n\n"
        "Produce a corrected answer to the user's most recent question, using "
        "only identifiers, labels, and corpus filenames that appear in the "
        "Current State and Corpus sections. If the question cannot be answered "
        "from the provided material, say so plainly.\n\n"
        "IMPORTANT: Write only the final answer, addressed directly to the "
        "user. Do not apologize, do not reference this validator message, do "
        "not meta-comment about your previous draft, and do not use phrases "
        "like \"you're right\" or \"let me restate\". The user is not aware "
        "that any earlier draft existed."
    )


def run_turn(
    scenario: Any,
    af: dict[str, Any],
    diff_ops: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    *,
    scenario_dir: Path,
    client: LLMClient,
) -> ChatTurnResult:
    """Run one chat turn through the Proposer (+ one corrective
    retry)."""
    system_prompt = build_system_prompt(scenario, af, diff_ops, scenario_dir=scenario_dir)
    conversation = _coerce_messages(messages)

    first: LLMResponse = client.complete(
        system=system_prompt,
        messages=conversation,
        max_tokens=MAX_TOKENS_PER_RESPONSE,
        cache=True,
    )
    issues = validate_response(first.text, scenario, af)

    if not issues:
        return ChatTurnResult(
            text=first.text,
            stop_reason=first.stop_reason,
            usage=first.usage,
            model=first.model,
            latency_ms=first.latency_ms,
            validator_flags=[],
            retried=False,
        )

    # Silent retry with corrective feedback.
    log.info("chat_validator_retry issues=%d", len(issues))
    retry_conversation = conversation + [
        {"role": "assistant", "content": first.text},
        {"role": "user", "content": _corrective_retry_message(issues)},
    ]
    second: LLMResponse = client.complete(
        system=system_prompt,
        messages=retry_conversation,
        max_tokens=MAX_TOKENS_PER_RESPONSE,
        cache=True,
    )
    # Accumulate token usage from both calls.
    total_usage = {
        k: first.usage.get(k, 0) + second.usage.get(k, 0)
        for k in set(first.usage) | set(second.usage)
    }
    remaining = validate_response(second.text, scenario, af)
    if remaining:
        log.warning("chat_validator_retry_still_flagged issues=%d", len(remaining))

    return ChatTurnResult(
        text=second.text,
        stop_reason=second.stop_reason,
        usage=total_usage,
        model=second.model,
        latency_ms=first.latency_ms + second.latency_ms,
        validator_flags=remaining,
        retried=True,
    )
