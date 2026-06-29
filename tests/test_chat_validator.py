"""Unit tests for the deterministic chat response validator.

Covers the three failure modes: unknown corpus citation, hallucinated
identifier, and declared-id leak. Exercises real Popov scenario ids so
the tests stay anchored in the live scenario vocabulary.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.llm.chat_service import _format_diff_ops, validate_response
from app.scenario.loader import load_scenario

EXAMPLES_ROOT = Path(__file__).resolve().parent.parent / "examples"


@pytest.fixture(scope="module")
def popov():
    return load_scenario(EXAMPLES_ROOT / "popov_v_hayashi" / "scenario.yaml")


@pytest.fixture(scope="module")
def popov_af(popov):
    # Minimal AF dict with labels_by_proposition populated for the labels path.
    labels = {cid: "absent" for cid in (popov.conclusions or {}).keys()}
    return {"labels_by_proposition": labels, "arguments": [], "attacks": []}


# --- clean responses ---


def test_clean_plain_prose_no_issues(popov, popov_af):
    text = "The scenario is balanced between Popov and Hayashi. Neither has full possession."
    assert validate_response(text, popov, popov_af) == []


def test_backticked_english_word_without_underscore_is_fine(popov, popov_af):
    """Generic English words in backticks are left alone -- only declared ids
    and underscore-shaped tokens are inspected.
    """
    text = "The `undecided` label indicates an impasse under grounded semantics."
    assert validate_response(text, popov, popov_af) == []


# --- leak detection ---


def test_declared_id_in_backticks_is_flagged_as_leak(popov, popov_af):
    """The chat prompt forbids surfacing raw scenario ids; if the model
    emits one in backticks, the validator flags it so the retry path
    rephrases in natural language.
    """
    text = "The `popov_has_poss` conclusion is undecided."
    issues = validate_response(text, popov, popov_af)
    assert any("popov_has_poss" in i and "backticks" in i for i in issues), issues


def test_declared_short_id_in_backticks_is_flagged(popov, popov_af):
    """Even short ids without underscores (like rule ids `rh`, `mc1`) are
    leaks when surfaced -- they're scenario identifiers, not English.
    """
    text = "This is handled by the `mc1` rule."
    issues = validate_response(text, popov, popov_af)
    assert any("mc1" in i and "backticks" in i for i in issues), issues


def test_declared_conclusion_id_via_labels_is_flagged(popov, popov_af):
    """A conclusion id that lives in labels_by_proposition is still a leak."""
    text = "We can see that `equal_division` is absent."
    issues = validate_response(text, popov, popov_af)
    assert any("equal_division" in i and "backticks" in i for i in issues), issues


# --- hallucination detection ---


def test_hallucinated_underscore_id_is_flagged(popov, popov_af):
    text = "The `totally_made_up_rule` decides the case."
    issues = validate_response(text, popov, popov_af)
    assert any("totally_made_up_rule" in i and "not declared" in i for i in issues), issues


# --- corpus citation validation ---


def test_unknown_corpus_filename_is_flagged(popov, popov_af):
    text = "See [nonexistent_file.txt] for details."
    issues = validate_response(text, popov, popov_af)
    assert any("nonexistent_file.txt" in i for i in issues), issues


def test_known_corpus_filename_is_fine(popov, popov_af):
    # Pick any real corpus filename from the scenario.
    assert popov.corpus, "Popov scenario should list corpus files"
    fname = popov.corpus[0]
    text = f"As noted in [{fname}], the court described..."
    issues = validate_response(text, popov, popov_af)
    assert not any(fname in i for i in issues), issues


# --- diff-ops present-tense framing ---


def test_empty_diff_ops_renders_baseline():
    out = _format_diff_ops([])
    assert "baseline" in out.lower()
    # Must not suggest a session timeline.
    assert "this session" not in out.lower()
    assert "applied" not in out.lower()


def test_non_empty_diff_ops_framed_as_present_state():
    ops = [{"op": "add-assumption", "id": "equity_compromise_open"}]
    out = _format_diff_ops(ops)
    # Heading and body both present-tense, not event-narrative.
    assert "Modifications from baseline" in out
    assert "currently" in out.lower()
    # Do not invite "you toggled X earlier in this session" framing.
    assert "this session" not in out.lower()
    assert "earlier" not in out.lower()
    # Op id and kind are still surfaced so the model knows what differs.
    assert "add-assumption" in out
    assert "equity_compromise_open" in out
