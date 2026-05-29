#!/usr/bin/env python3
"""Tests for scripts/validate_repo_governance.py."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "validate_repo_governance.py"

spec = importlib.util.spec_from_file_location("validate_repo_governance", SCRIPT_PATH)
assert spec and spec.loader
validate_repo_governance = importlib.util.module_from_spec(spec)
sys.modules["validate_repo_governance"] = validate_repo_governance
spec.loader.exec_module(validate_repo_governance)

Finding = validate_repo_governance.Finding
validate_policy_file = validate_repo_governance.validate_policy_file
validate_repository = validate_repo_governance.validate_repository
validate_workflow_text = validate_repo_governance.validate_workflow_text


def messages(findings: list[Finding]) -> list[str]:
    return [finding.message for finding in findings]


def assert_message(findings: list[Finding], expected: str) -> None:
    if not any(expected in message for message in messages(findings)):
        raise AssertionError(f"missing {expected!r} in {messages(findings)!r}")


def workflow_validation_rejects_rollout_footguns() -> None:
    empty_ref = validate_workflow_text(
        Path("templates/workflows/broken.yml"),
        "steps:\n  - uses: heurema/repo-governance/actions/codex-review-gate@\n",
    )
    assert_message(empty_ref, "empty action ref")

    placeholder_ref = validate_workflow_text(
        Path("templates/workflows/broken.yml"),
        "steps:\n  - uses: heurema/repo-governance/actions/codex-review-ready@<commit-sha>\n",
    )
    assert_message(placeholder_ref, "unresolved placeholder")

    unsafe_checkout = validate_workflow_text(
        Path(".github/workflows/broken.yml"),
        "\n".join(
            [
                "on:",
                "  pull_request_target:",
                "steps:",
                "  - uses: actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5",
            ]
        ),
    )
    assert_message(unsafe_checkout, "persist-credentials: false")
    print("ok - workflow validation rejects rollout footguns")


def policy_validation_rejects_empty_marker() -> None:
    with tempfile.TemporaryDirectory(prefix="repo-governance-policy-") as tmp_raw:
        policy = Path(tmp_raw) / "pr-intake-gate.yml"
        policy.write_text("bot_comment:\n  marker: ''\n", encoding="utf-8")
        findings = validate_policy_file(ROOT, policy)
    assert_message(findings, "bot_comment.marker must be non-empty and unique")
    print("ok - policy validation rejects empty marker")


def policy_validation_rejects_unknown_root_key() -> None:
    with tempfile.TemporaryDirectory(prefix="repo-governance-policy-") as tmp_raw:
        policy = Path(tmp_raw) / "pr-intake-gate.yml"
        policy.write_text(
            "\n".join(
                [
                    "bot_comment:",
                    "  marker: '<!-- test-pr-intake-gate -->'",
                    "highrisk_path_globs:",
                    "  - '.github/**'",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        findings = validate_policy_file(ROOT, policy)
    assert_message(findings, "Unknown top-level policy key: highrisk_path_globs")
    print("ok - policy validation rejects unknown root key")


def current_repository_validates() -> None:
    findings = validate_repository(ROOT)
    if findings:
        rendered = "\n".join(f"{finding.path}: {finding.message}" for finding in findings)
        raise AssertionError(f"current repository should validate:\n{rendered}")
    print("ok - current repository validates")


def main() -> int:
    workflow_validation_rejects_rollout_footguns()
    policy_validation_rejects_empty_marker()
    policy_validation_rejects_unknown_root_key()
    current_repository_validates()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
