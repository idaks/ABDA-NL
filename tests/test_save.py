"""Unit tests for app/scenario/save.py.

Covers the core save-flow guarantees: id validation, collision handling,
what gets copied from the baseline, title override, post-write
verification and rollback.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from app.scenario.loader import load_scenario
from app.scenario.save import (
    InvalidScenarioId,
    SaveVerificationFailed,
    ScenarioIdCollision,
    save_scenario,
)

REAL_EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


@pytest.fixture
def baseline_dir(tmp_path: Path) -> Path:
    """Copy the real Popov scenario into a tmp baseline location."""
    src = REAL_EXAMPLES / "popov_v_hayashi"
    dst = tmp_path / "popov_v_hayashi"
    shutil.copytree(src, dst)
    return dst


@pytest.fixture
def examples_root(tmp_path: Path, baseline_dir: Path) -> Path:
    """tmp_path hosts the baseline plus any saved-scenario output."""
    return tmp_path


def _load_effective(baseline_dir: Path):
    """Load the baseline as the 'effective' scenario (zero ops applied)."""
    return load_scenario(baseline_dir / "scenario.yaml")


# --- happy path ---


def test_save_writes_scenario_yaml_and_copies_corpus(baseline_dir, examples_root):
    effective = _load_effective(baseline_dir)
    target = save_scenario(
        effective=effective,
        title="My Popov Copy",
        save_as_id="popov_copy",
        baseline_dir=baseline_dir,
        examples_root=examples_root,
    )
    assert target == examples_root / "popov_copy"
    assert (target / "scenario.yaml").is_file()
    # Corpus subdir copied.
    assert (target / "corpus").is_dir()
    original_corpus = list((baseline_dir / "corpus").iterdir())
    saved_corpus = list((target / "corpus").iterdir())
    assert sorted(c.name for c in saved_corpus) == sorted(c.name for c in original_corpus)


def test_save_copies_corpus_summary_if_present(baseline_dir, examples_root):
    # Popov has corpus_summary.yaml.
    assert (baseline_dir / "corpus_summary.yaml").is_file()
    effective = _load_effective(baseline_dir)
    target = save_scenario(
        effective=effective,
        title="copy",
        save_as_id="popov_copy2",
        baseline_dir=baseline_dir,
        examples_root=examples_root,
    )
    assert (target / "corpus_summary.yaml").is_file()


def test_save_does_not_copy_expected_labels(baseline_dir, examples_root):
    assert (baseline_dir / "expected_labels.yaml").is_file()
    effective = _load_effective(baseline_dir)
    target = save_scenario(
        effective=effective,
        title="copy",
        save_as_id="popov_copy3",
        baseline_dir=baseline_dir,
        examples_root=examples_root,
    )
    # expected_labels.yaml is baseline-only; saved scenarios are user
    # explorations, not regression fixtures.
    assert not (target / "expected_labels.yaml").exists()


def test_save_overrides_title_in_yaml(baseline_dir, examples_root):
    effective = _load_effective(baseline_dir)
    target = save_scenario(
        effective=effective,
        title="My Renamed Popov",
        save_as_id="popov_renamed",
        baseline_dir=baseline_dir,
        examples_root=examples_root,
    )
    saved = yaml.safe_load((target / "scenario.yaml").read_text())
    assert saved["title"] == "My Renamed Popov"
    # Loader should also accept the written file.
    loaded = load_scenario(target / "scenario.yaml")
    assert loaded.title == "My Renamed Popov"


def test_save_with_zero_ops_produces_equivalent_scenario(baseline_dir, examples_root):
    """Saving an unmodified scenario produces a loadable copy with the
    same structural content (rules, facts, conclusions)."""
    original = _load_effective(baseline_dir)
    target = save_scenario(
        effective=original,
        title="Identical Copy",
        save_as_id="popov_identical",
        baseline_dir=baseline_dir,
        examples_root=examples_root,
    )
    saved = load_scenario(target / "scenario.yaml")
    assert set(saved.facts.keys()) == set(original.facts.keys())
    assert set(saved.assumptions.keys()) == set(original.assumptions.keys())
    assert set(saved.rules.keys()) == set(original.rules.keys())
    assert set(saved.conclusions.keys()) == set(original.conclusions.keys())


# --- id validation ---


@pytest.mark.parametrize("bad_id", [
    "has spaces",
    "has-hyphens",
    "starts_with_.",
    "1_starts_with_digit",
    "contains/slash",
    "contains..traversal",
    "",
])
def test_invalid_id_rejected(baseline_dir, examples_root, bad_id):
    effective = _load_effective(baseline_dir)
    with pytest.raises(InvalidScenarioId):
        save_scenario(
            effective=effective,
            title="x",
            save_as_id=bad_id,
            baseline_dir=baseline_dir,
            examples_root=examples_root,
        )


def test_empty_title_rejected(baseline_dir, examples_root):
    effective = _load_effective(baseline_dir)
    with pytest.raises(InvalidScenarioId):
        save_scenario(
            effective=effective,
            title="   ",  # whitespace only
            save_as_id="popov_copy",
            baseline_dir=baseline_dir,
            examples_root=examples_root,
        )


# --- collision ---


def test_collision_without_overwrite_raises(baseline_dir, examples_root):
    effective = _load_effective(baseline_dir)
    save_scenario(
        effective=effective,
        title="First",
        save_as_id="popov_dup",
        baseline_dir=baseline_dir,
        examples_root=examples_root,
    )
    with pytest.raises(ScenarioIdCollision):
        save_scenario(
            effective=effective,
            title="Second",
            save_as_id="popov_dup",
            baseline_dir=baseline_dir,
            examples_root=examples_root,
            overwrite=False,
        )


def test_collision_with_overwrite_replaces(baseline_dir, examples_root):
    effective = _load_effective(baseline_dir)
    save_scenario(
        effective=effective,
        title="First",
        save_as_id="popov_over",
        baseline_dir=baseline_dir,
        examples_root=examples_root,
    )
    target = save_scenario(
        effective=effective,
        title="Second",
        save_as_id="popov_over",
        baseline_dir=baseline_dir,
        examples_root=examples_root,
        overwrite=True,
    )
    saved = yaml.safe_load((target / "scenario.yaml").read_text())
    assert saved["title"] == "Second"


# --- overwrite-source path ---


def test_overwrite_source_succeeds_and_preserves_expected_labels(
    baseline_dir, examples_root
):
    """Saving with save_as_id == source_id is allowed when overwrite=True;
    expected_labels.yaml is preserved (user is updating the scenario in
    place, the snapshot belongs with it). Without this preservation the
    rmtree during the temp→target swap would wipe the snapshot silently.
    """
    original_snapshot = (baseline_dir / "expected_labels.yaml").read_bytes()
    effective = _load_effective(baseline_dir)
    effective.title = "Overwriting Source"

    target = save_scenario(
        effective=effective,
        title="Overwriting Source",
        save_as_id=baseline_dir.name,  # same as source
        baseline_dir=baseline_dir,
        examples_root=examples_root,
        overwrite=True,
    )
    assert target == baseline_dir  # identity
    # scenario.yaml updated with the new title.
    saved = yaml.safe_load((target / "scenario.yaml").read_text())
    assert saved["title"] == "Overwriting Source"
    # expected_labels.yaml survives byte-for-byte.
    assert (target / "expected_labels.yaml").read_bytes() == original_snapshot


def test_overwrite_source_without_overwrite_flag_collides(baseline_dir, examples_root):
    """Even same-source, the overwrite flag must be explicit. Forgetting
    it still produces a 409-equivalent ScenarioIdCollision.
    """
    effective = _load_effective(baseline_dir)
    with pytest.raises(ScenarioIdCollision):
        save_scenario(
            effective=effective,
            title="no-overwrite",
            save_as_id=baseline_dir.name,
            baseline_dir=baseline_dir,
            examples_root=examples_root,
            overwrite=False,
        )


# --- verification & rollback ---


def test_verification_failure_rolls_back(monkeypatch, baseline_dir, examples_root):
    """When the post-write verification fails, no target is left behind
    and any pre-existing target is untouched.

    Forces the failure by having load_scenario raise from within save_scenario.
    """
    effective = _load_effective(baseline_dir)

    # Pre-populate a target with known content that must survive the
    # failed overwrite.
    pre_existing = examples_root / "popov_verify"
    pre_existing.mkdir()
    (pre_existing / "scenario.yaml").write_text("sentinel: true\n")

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated load failure")

    import app.scenario.save as save_module
    monkeypatch.setattr(save_module, "load_scenario", _boom)

    with pytest.raises(SaveVerificationFailed):
        save_scenario(
            effective=effective,
            title="x",
            save_as_id="popov_verify",
            baseline_dir=baseline_dir,
            examples_root=examples_root,
            overwrite=True,
        )

    # Sentinel file still present (rollback left original target intact).
    assert (pre_existing / "scenario.yaml").read_text() == "sentinel: true\n"
    # No stray temp dir.
    assert not (examples_root / ".tmp_save_popov_verify").exists()
