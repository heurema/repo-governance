#!/usr/bin/env python3
"""Validate checked-in repo-governance policies and workflow templates."""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EMPTY_ACTION_REF = re.compile(r"^\s*-?\s*uses:\s+[^#\s@]+@\s*(?:#.*)?$")
UNRESOLVED_PLACEHOLDER = re.compile(r"<[A-Za-z0-9._/-]+>")


@dataclass(frozen=True)
class Finding:
    path: str
    message: str


def relative_path(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def load_pr_intake_gate(root: Path) -> Any:
    engine_path = root / "actions" / "pr-intake-gate" / "pr_intake_gate.py"
    spec = importlib.util.spec_from_file_location("pr_intake_gate_for_validation", engine_path)
    if not spec or not spec.loader:
        raise RuntimeError(f"cannot load {engine_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["pr_intake_gate_for_validation"] = module
    spec.loader.exec_module(module)
    return module


def policy_files(root: Path) -> list[Path]:
    candidates = [
        root / ".github" / "pr-intake-gate.yml",
        root / "templates" / "pr-intake-gate.yml",
    ]
    candidates.extend(sorted((root / "examples").glob("*.pr-intake-gate.yml")))
    return [path for path in candidates if path.exists()]


def workflow_files(root: Path) -> list[Path]:
    candidates: list[Path] = []
    for folder in (root / ".github" / "workflows", root / "templates" / "workflows"):
        candidates.extend(sorted(folder.glob("*.yml")))
        candidates.extend(sorted(folder.glob("*.yaml")))
    return candidates


def validate_policy_file(root: Path, path: Path) -> list[Finding]:
    engine = load_pr_intake_gate(root)
    rel = relative_path(root, path)
    try:
        config = engine.load_minimal_yaml(str(path))
        engine.validate_policy(config)
        engine.marker_for(config)
    except Exception as exc:
        return [Finding(rel, str(exc))]
    return []


def validate_workflow_text(path: Path, text: str) -> list[Finding]:
    findings: list[Finding] = []
    display_path = path.as_posix()

    for line_number, line in enumerate(text.splitlines(), start=1):
        if EMPTY_ACTION_REF.match(line):
            findings.append(Finding(display_path, f"line {line_number}: empty action ref after `@`"))
        placeholder = UNRESOLVED_PLACEHOLDER.search(line)
        if placeholder:
            findings.append(
                Finding(display_path, f"line {line_number}: unresolved placeholder {placeholder.group(0)!r}")
            )

    if "pull_request_target:" in text and "actions/checkout@" in text and "persist-credentials: false" not in text:
        findings.append(Finding(display_path, "pull_request_target checkout must set persist-credentials: false"))

    return findings


def validate_workflow_file(root: Path, path: Path) -> list[Finding]:
    text = path.read_text(encoding="utf-8")
    rel = Path(relative_path(root, path))
    return validate_workflow_text(rel, text)


def validate_repository(root: Path) -> list[Finding]:
    root = root.resolve()
    findings: list[Finding] = []
    for path in policy_files(root):
        findings.extend(validate_policy_file(root, path))
    for path in workflow_files(root):
        findings.extend(validate_workflow_file(root, path))
    return findings


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate repo-governance policies and workflow templates.")
    parser.add_argument("--root", default=str(ROOT), help="Repository root to validate.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    findings = validate_repository(Path(args.root))
    if findings:
        for finding in findings:
            print(f"{finding.path}: {finding.message}", file=sys.stderr)
        return 1
    print("repo-governance validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
