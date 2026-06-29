"""Validate a scenario directory end-to-end.

Steps (in order):
  1. YAML schema validation against app/schemas/scenario.schema.json.
  2. Reference integrity (every premise / conclusion / undercut target resolves).
  3. Corpus files listed under ``corpus:`` exist on disk.
  4. AF build succeeds (scenario compiles to a RuleCollection and ABDA
     produces a grounded labelling).
  5. Default labelling matches the committed ``expected_labels.yaml``.

Exit codes:
  0 -- clean
  1 -- errors
  2 -- warnings (e.g. missing snapshot file)

Usage:
  python -m app.cli.validate_scenario <scenario_dir>
  python -m app.cli.validate_scenario --all                 # walks examples/
  python -m app.cli.validate_scenario <scenario_dir> --update-snapshot
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Optional

import yaml

from app.abda_bridge import (
    ArgumentationGraph,
    build_arguments,
    build_attacks,
    init_engine,
)
from app.scenario.loader import (
    ScenarioValidationError,
    load_scenario,
    scenario_to_rule_collection,
)
from app.scenario.serialize import serialize_af

EXIT_CLEAN = 0
EXIT_ERROR = 1
EXIT_WARNING = 2


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="validate_scenario",
        description="Validate a scenario directory (or all under examples/).",
    )
    parser.add_argument(
        "path",
        nargs="?",
        help="Path to a single scenario directory. Omit with --all.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Validate every scenario directory under --examples-root.",
    )
    parser.add_argument(
        "--examples-root",
        default="examples",
        help="Root directory when --all is used (default: examples).",
    )
    parser.add_argument(
        "--update-snapshot",
        action="store_true",
        help=(
            "Write expected_labels.yaml from the current AF output. Use to "
            "bootstrap new scenarios or accept intentional changes."
        ),
    )
    args = parser.parse_args(argv)

    if args.all and args.path:
        parser.error("pass either a path or --all, not both")
    if not args.all and not args.path:
        parser.error("pass either a path or --all")

    init_engine()

    if args.all:
        root = Path(args.examples_root)
        if not root.is_dir():
            print(f"ERR  examples root not found: {root}", file=sys.stderr)
            return EXIT_ERROR
        scenario_dirs = sorted(
            p for p in root.iterdir() if (p / "scenario.yaml").is_file()
        )
        if not scenario_dirs:
            print(f"WARN no scenario directories found under {root}", file=sys.stderr)
            return EXIT_WARNING
    else:
        scenario_dirs = [Path(args.path)]

    overall = EXIT_CLEAN
    for scenario_dir in scenario_dirs:
        result = _check_scenario(scenario_dir, update_snapshot=args.update_snapshot)
        print(_format_result(scenario_dir, result))
        if result["status"] == "error":
            overall = EXIT_ERROR
        elif result["status"] == "warning" and overall != EXIT_ERROR:
            overall = EXIT_WARNING
    return overall


def _check_scenario(scenario_dir: Path, update_snapshot: bool) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    scenario_path = scenario_dir / "scenario.yaml"
    if not scenario_path.is_file():
        errors.append(f"{scenario_path}: missing")
        return {"status": "error", "errors": errors, "warnings": warnings}

    try:
        scenario = load_scenario(scenario_path)
    except ScenarioValidationError as exc:
        errors.extend(exc.errors)
        return {"status": "error", "errors": errors, "warnings": warnings}
    except yaml.YAMLError as exc:
        errors.append(f"YAML parse error: {exc}")
        return {"status": "error", "errors": errors, "warnings": warnings}

    corpus_dir = scenario_dir / "corpus"
    for filename in scenario.corpus:
        if not (corpus_dir / filename).is_file():
            errors.append(f"corpus file missing: corpus/{filename}")

    try:
        rule_collection = scenario_to_rule_collection(scenario)
        arguments = build_arguments(rule_collection.get_all_rules())
        attacks = build_attacks(arguments)
        graph = ArgumentationGraph(arguments, attacks)
        labelling = graph.get_grounded_labelling()
    except Exception as exc:  # noqa: BLE001 - surface any engine failure as a scenario error
        errors.append(f"AF build failed: {type(exc).__name__}: {exc}")
        return {"status": "error", "errors": errors, "warnings": warnings}

    try:
        snapshot = serialize_af(scenario, arguments, attacks, labelling)
    except ValueError as exc:
        errors.append(f"proposition label aggregation failed: {exc}")
        return {"status": "error", "errors": errors, "warnings": warnings}

    warnings.extend(_lint_negated_descriptions(scenario))

    actual_labels = snapshot["labels_by_proposition"]
    snapshot_path = scenario_dir / "expected_labels.yaml"
    if update_snapshot:
        _write_snapshot(snapshot_path, actual_labels)
        warnings.append(
            f"wrote expected_labels.yaml ({len(actual_labels)} propositions)"
        )
    elif snapshot_path.is_file():
        expected_labels = _load_snapshot(snapshot_path)
        diffs = _diff_labels(expected_labels, actual_labels)
        if diffs:
            errors.extend(diffs)
    else:
        warnings.append(
            "expected_labels.yaml is missing; run --update-snapshot to bootstrap"
        )

    if errors:
        return {"status": "error", "errors": errors, "warnings": warnings}
    if warnings:
        return {"status": "warning", "errors": errors, "warnings": warnings}
    return {"status": "ok", "errors": errors, "warnings": warnings}


def _load_snapshot(path: Path) -> dict[str, str]:
    with path.open() as fh:
        raw = yaml.safe_load(fh) or {}
    props = raw.get("propositions") or {}
    return dict(props)


def _write_snapshot(path: Path, labels: dict[str, str]) -> None:
    sorted_labels = dict(sorted(labels.items()))
    with path.open("w") as fh:
        fh.write(
            "# Regression snapshot of the default AF labelling for this scenario.\n"
            "# validate_scenario fails if the actual labelling diverges from this.\n"
            "# Regenerate with: python -m app.cli.validate_scenario <dir> --update-snapshot\n\n"
        )
        yaml.safe_dump(
            {"propositions": sorted_labels},
            fh,
            sort_keys=False,
            default_flow_style=False,
        )


def _lint_negated_descriptions(scenario) -> list[str]:
    """Warn about any literal used negated in a rule without an authored
    ``negated_description``. Covers both proposition/fact/assumption
    negation and rule-name undercuts, since the fallback phrasings for
    both ("it is not the case that X" / "rule X does not apply") are
    generic and almost always worth overriding.
    """
    negated_bases: set[str] = set()
    for rule in scenario.rules.values():
        for lit in [*rule.premises, rule.conclusion]:
            if lit.startswith("-"):
                negated_bases.add(lit[1:])

    lacking: list[str] = []
    for base in sorted(negated_bases):
        for section_name, section in (
            ("facts", scenario.facts),
            ("assumptions", scenario.assumptions),
            ("propositions", scenario.propositions),
            ("conclusions", scenario.conclusions),
            ("rules", scenario.rules),
        ):
            entry = section.get(base)
            if entry is not None:
                if not entry.negated_description:
                    lacking.append(f"{section_name}/{base}")
                break
    if lacking:
        return [
            "missing negated_description for literals used negated: "
            + ", ".join(lacking)
        ]
    return []


def _diff_labels(expected: dict[str, str], actual: dict[str, str]) -> list[str]:
    diffs: list[str] = []
    for key in sorted(set(expected) | set(actual)):
        exp = expected.get(key, "<missing>")
        act = actual.get(key, "<missing>")
        if exp != act:
            diffs.append(f"label mismatch: {key}: expected {exp}, got {act}")
    return diffs


def _format_result(scenario_dir: Path, result: dict[str, Any]) -> str:
    symbol = {"ok": "OK  ", "warning": "WARN", "error": "ERR "}[result["status"]]
    lines = [f"{symbol} {scenario_dir}"]
    for err in result["errors"]:
        lines.append(f"       error:   {err}")
    for warn in result["warnings"]:
        lines.append(f"       warning: {warn}")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
