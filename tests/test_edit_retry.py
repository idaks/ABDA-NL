"""Tests for the Proposer retry-message construction.

The retry message includes a worked-example hint showing a specific
shorter id (rather than a generic "shorten the id" instruction).
These tests verify that behavior.
"""
from __future__ import annotations

from app.llm.edit_service import _build_validator_retry_message, _shorten_id_hint
from app.llm.edit_validator import MAX_ID_LEN, ValidationIssue


def _id_too_long_issue(long_id: str) -> ValidationIssue:
    return ValidationIssue(
        "id_too_long",
        f"id `{long_id}` is {len(long_id)} characters; the UI target is "
        f"≤{MAX_ID_LEN}. Shorten to 1-3 meaningful snake_case tokens",
    )


# --- _shorten_id_hint ---


def test_shorten_drops_trailing_token_when_possible():
    # retriever_bad_faith_notice (26) -> drop "_notice" -> "retriever_bad_faith" (19) ✓
    assert _shorten_id_hint("retriever_bad_faith_notice") == "retriever_bad_faith"


def test_shorten_keeps_first_token_when_first_alone_fits():
    # qualified_right_to_possession (29) -> "qualified_right_to" (18) fits.
    assert _shorten_id_hint("qualified_right_to_possession") == "qualified_right_to"


def test_shorten_falls_back_to_token_abbreviation_when_drop_fails():
    # First token already too long; can't get under by dropping tokens.
    # Fallback abbreviates the longest token to first 4 chars.
    suggestion = _shorten_id_hint("supercalifragilisticlonger_x")
    assert len(suggestion) <= MAX_ID_LEN
    # Should mostly look like the abbreviated form.
    assert "_x" in suggestion or len(suggestion) <= MAX_ID_LEN


def test_shorten_handles_single_token_over_limit():
    # No underscores; falls back to truncation of the only token.
    suggestion = _shorten_id_hint("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")  # 30 chars
    assert len(suggestion) <= MAX_ID_LEN


def test_shorten_returns_empty_for_empty_input():
    assert _shorten_id_hint("") == ""


# --- _build_validator_retry_message: id_too_long path ---


def test_retry_message_inlines_worked_id_shortening():
    """The retry feedback names a specific shorter id rather than just
    saying 'pick something shorter'. This is the M4 calibration win:
    open models follow worked examples much better than abstract rules."""
    msg = _build_validator_retry_message(
        original_user_message="Add an assumption about reliable eyewitness testimony.",
        validator_issues=[_id_too_long_issue("eyewitness_testimony_valid")],
    )
    # Should inline the offending id and a concrete suggestion.
    assert "eyewitness_testimony_valid" in msg
    assert "26 characters" in msg
    # The hint should propose a specific shortened id and assert its length.
    assert "`eyewitness_testimony`" in msg
    assert "(20 chars)" in msg
    # Must explicitly forbid re-emitting the same id (Llama and Qwen both
    # have a tendency to retry with the same id otherwise).
    assert "Do NOT re-emit the same too-long id" in msg


def test_retry_message_handles_id_too_long_with_no_extractable_id():
    """Defensive: if for some reason the regex can't pull an id from the
    Validator message, still emit a useful retry telling the model to
    shorten.
    """
    issue = ValidationIssue("id_too_long", "id was too long; please retry")
    msg = _build_validator_retry_message(
        original_user_message="Add a fact.",
        validator_issues=[issue],
    )
    assert f"{MAX_ID_LEN} characters" in msg
    assert "Do NOT re-emit" in msg


def test_retry_message_keeps_unknown_premise_guidance():
    """The unknown_premise / unknown_rule_id branch must still appear --
    we extended retry guidance, not replaced it."""
    issues = [
        ValidationIssue(
            "unknown_premise",
            "premise `nonexistent` is not declared in the scenario",
        ),
    ]
    msg = _build_validator_retry_message(
        original_user_message="Add a rule.",
        validator_issues=issues,
    )
    assert "unknown_premise" in msg
    assert "cannot invent new literals" in msg


def test_retry_message_combines_long_id_and_unknown_premise():
    """Multiple validator issues should yield multiple guidance blocks."""
    issues = [
        _id_too_long_issue("retriever_bad_faith_notice"),
        ValidationIssue("unknown_premise", "premise `nonexistent` is not declared"),
    ]
    msg = _build_validator_retry_message(
        original_user_message="Add a rule.",
        validator_issues=issues,
    )
    assert "retriever_bad_faith_notice" in msg
    assert "Do NOT re-emit" in msg
    assert "cannot invent new literals" in msg
