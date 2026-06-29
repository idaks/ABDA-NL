"""Save a diff-applied scenario to examples/<id>/.

Writes to a temp directory first, verifies the result loads and
builds cleanly, then swaps into place. On failure the temp is
removed and the existing target is left untouched. The swap is not
fully atomic; on catastrophic failure, look for
`examples/.tmp_save_<id>/`.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

import yaml

from app.abda_bridge import ArgumentationGraph, build_arguments, build_attacks
from app.scenario.loader import load_scenario, scenario_to_rule_collection
from app.scenario.model import Scenario
from app.scenario.serialize import scenario_to_dict

# Mirrors the scenario-schema `identifier` pattern. No max-length cap --
# directory names can be longer than scenario-internal ids, which are kept
# compact for labels on rules/facts/conclusions.
ID_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# scenario.yaml is always replaced by the diff-applied version; the copy
# exclusion for expected_labels.yaml is conditional on whether the save is
# a fresh as-new-scenario (exclude, regression snapshot belongs only to
# the baseline) or an overwrite of the source itself (preserve, the user
# is updating in place). Resolved inside save_scenario.


class SaveError(Exception):
    """Base class for save-flow failures."""


class InvalidScenarioId(SaveError):
    """save_as_id fails the identifier pattern."""


class ScenarioIdCollision(SaveError):
    """Target directory exists and overwrite was not requested."""


class SaveVerificationFailed(SaveError):
    """Post-write rebuild failed; the just-written scenario is inconsistent."""


def save_scenario(
    *,
    effective: Scenario,
    title: str,
    save_as_id: str,
    baseline_dir: Path,
    examples_root: Path,
    overwrite: bool = False,
) -> Path:
    """Write `effective` as a new scenario under
    `examples_root/save_as_id/`.

    `baseline_dir` is the scenario directory to copy non-YAML
    artefacts from (corpus files, corpus_summary.yaml if
    present). `title` overrides the Scenario's `title` field in the
    written YAML.

    Returns the target Path on success.

    Raises:
      InvalidScenarioId -- save_as_id fails the identifier pattern.
      ScenarioIdCollision -- target exists and `overwrite` is False.
      SaveVerificationFailed -- written YAML fails to load/build.
    """
    if not ID_PATTERN.match(save_as_id):
        raise InvalidScenarioId(
            f"save_as_id {save_as_id!r} must match [A-Za-z_][A-Za-z0-9_]*"
        )
    if not title.strip():
        raise InvalidScenarioId("title must be non-empty")

    target_dir = examples_root / save_as_id
    is_source_overwrite = baseline_dir.resolve() == target_dir.resolve()
    if target_dir.exists() and not overwrite:
        raise ScenarioIdCollision(
            f"scenario id {save_as_id!r} already exists"
        )

    temp_dir = examples_root / f".tmp_save_{save_as_id}"
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True)

    # Overwriting the source scenario preserves expected_labels.yaml;
    # save-as-new excludes it (belongs to the baseline only).
    skip_names = {"scenario.yaml"}
    if not is_source_overwrite:
        skip_names.add("expected_labels.yaml")

    try:
        # 1. Copy baseline artifacts except skipped names.
        if baseline_dir.is_dir():
            for child in baseline_dir.iterdir():
                if child.name in skip_names:
                    continue
                dest = temp_dir / child.name
                if child.is_dir():
                    shutil.copytree(child, dest)
                else:
                    shutil.copy2(child, dest)

        # 2. Write the diff-applied scenario with the title override.
        #    Don't mutate effective.title -- the caller may still need
        #    the object. Patch the serialized dict instead.
        scenario_dict = scenario_to_dict(effective)
        scenario_dict["title"] = title
        with (temp_dir / "scenario.yaml").open("w") as f:
            yaml.safe_dump(
                scenario_dict,
                f,
                sort_keys=False,
                default_flow_style=False,
                allow_unicode=True,
            )

        # 3. Verify: round-trip load + AF build + grounded labelling.
        #    Matches what _compute_state_bundle will do on the next
        #    request; catches anything the diff pipeline might have
        #    missed (schema drift, orphaned refs, labelling-time
        #    consistency errors).
        try:
            check = load_scenario(temp_dir / "scenario.yaml")
            rc = scenario_to_rule_collection(check)
            arguments = build_arguments(rc.get_all_rules())
            attacks = build_attacks(arguments)
            ArgumentationGraph(arguments, attacks).get_grounded_labelling()
        except Exception as exc:
            raise SaveVerificationFailed(
                f"saved scenario failed post-write verification: {exc}"
            ) from exc

        # 4. Swap temp into place.
        if target_dir.exists():
            shutil.rmtree(target_dir)
        temp_dir.rename(target_dir)
        return target_dir

    except Exception:
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
        raise
