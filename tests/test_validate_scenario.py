"""Tests for the validate_scenario CLI status ladder.

Exercises ``_check_scenario`` directly with a tmp_path-built scenario dir
so we cover the three exit-code outcomes without spawning a subprocess.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from app.abda_bridge import init_engine
from app.cli.validate_scenario import _check_scenario


@pytest.fixture(scope="module", autouse=True)
def _engine():
    init_engine()


def _write_minimal_scenario(dirpath: Path) -> None:
    dirpath.mkdir(parents=True, exist_ok=True)
    (dirpath / "scenario.yaml").write_text(
        textwrap.dedent(
            """\
            title: tiny
            facts:
              a:
                description: fact a
            conclusions:
              x:
                description: conclusion x
            rules:
              r:
                type: defeasible
                premises: [a]
                conclusion: x
            corpus: []
            """
        )
    )
    (dirpath / "corpus").mkdir(exist_ok=True)


def test_missing_expected_labels_is_warning(tmp_path: Path):
    scen = tmp_path / "tiny"
    _write_minimal_scenario(scen)
    result = _check_scenario(scen, update_snapshot=False)
    assert result["status"] == "warning"
    assert any("expected_labels.yaml is missing" in w for w in result["warnings"])


def test_matching_snapshot_is_ok(tmp_path: Path):
    scen = tmp_path / "tiny"
    _write_minimal_scenario(scen)
    _check_scenario(scen, update_snapshot=True)  # bootstrap
    result = _check_scenario(scen, update_snapshot=False)
    assert result["status"] == "ok"


def test_mismatched_snapshot_is_error(tmp_path: Path):
    scen = tmp_path / "tiny"
    _write_minimal_scenario(scen)
    (scen / "expected_labels.yaml").write_text(
        "propositions:\n  x: rejected\n"  # actual will be `accepted`
    )
    result = _check_scenario(scen, update_snapshot=False)
    assert result["status"] == "error"
    assert any("label mismatch" in e for e in result["errors"])


def test_missing_corpus_file_is_error(tmp_path: Path):
    scen = tmp_path / "tiny"
    _write_minimal_scenario(scen)
    # Overwrite scenario.yaml to reference a corpus file that doesn't exist.
    (scen / "scenario.yaml").write_text(
        textwrap.dedent(
            """\
            title: tiny
            facts:
              a:
                description: fact a
            conclusions:
              x:
                description: x
            rules:
              r:
                type: defeasible
                premises: [a]
                conclusion: x
            corpus:
              - phantom.txt
            """
        )
    )
    result = _check_scenario(scen, update_snapshot=False)
    assert result["status"] == "error"
    assert any("phantom.txt" in e for e in result["errors"])


def test_negated_description_lint_warns_when_missing(tmp_path: Path):
    scen = tmp_path / "tiny"
    scen.mkdir()
    (scen / "corpus").mkdir()
    (scen / "scenario.yaml").write_text(
        textwrap.dedent(
            """\
            title: tiny
            facts:
              a:
                description: fact a
            conclusions:
              x:
                description: conclusion x
            rules:
              r_pos:
                type: defeasible
                premises: [a]
                conclusion: x
              r_neg:
                type: defeasible
                premises: [a]
                conclusion: -x
            corpus: []
            """
        )
    )
    result = _check_scenario(scen, update_snapshot=True)
    assert any("negated_description" in w and "conclusions/x" in w for w in result["warnings"])


def test_negated_description_lint_silent_when_authored(tmp_path: Path):
    scen = tmp_path / "tiny"
    scen.mkdir()
    (scen / "corpus").mkdir()
    (scen / "scenario.yaml").write_text(
        textwrap.dedent(
            """\
            title: tiny
            facts:
              a:
                description: fact a
            conclusions:
              x:
                description: x holds
                negated_description: x does not hold
            rules:
              r_pos:
                type: defeasible
                premises: [a]
                conclusion: x
              r_neg:
                type: defeasible
                premises: [a]
                conclusion: -x
            corpus: []
            """
        )
    )
    result = _check_scenario(scen, update_snapshot=True)
    assert not any("negated_description" in w for w in result["warnings"])


def test_schema_violation_is_error(tmp_path: Path):
    scen = tmp_path / "tiny"
    scen.mkdir()
    (scen / "scenario.yaml").write_text("facts: {}\n")  # missing title + conclusions + rules
    (scen / "corpus").mkdir()
    result = _check_scenario(scen, update_snapshot=False)
    assert result["status"] == "error"
    assert result["errors"]
