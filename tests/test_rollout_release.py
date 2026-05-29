#!/usr/bin/env python3
"""Tests for scripts/rollout_release.py."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "rollout_release.py"

spec = importlib.util.spec_from_file_location("rollout_release", SCRIPT_PATH)
assert spec and spec.loader
rollout_release = importlib.util.module_from_spec(spec)
sys.modules["rollout_release"] = rollout_release
spec.loader.exec_module(rollout_release)


def action_ref_parser_extracts_action_and_ref() -> None:
    text = "uses: heurema/repo-governance/actions/pr-intake-gate@v0.3.0\n"

    refs = rollout_release.find_action_refs(text)

    assert len(refs) == 1
    assert refs[0].action == "pr-intake-gate"
    assert refs[0].ref == "v0.3.0"
    print("ok - action ref parser extracts action and ref")


def replacement_updates_only_repo_governance_action_refs() -> None:
    text = "\n".join(
        [
            "uses: heurema/repo-governance/actions/pr-intake-gate@v0.4.0",
            "uses: owner/other-action@v0.4.0",
            "Current docs mention @v0.4.0 as text.",
            "uses: heurema/repo-governance/actions/codex-review-ready@v0.5.0",
        ]
    )

    updated, count = rollout_release.replace_action_refs(text, "v0.4.0", "v0.5.0")

    assert count == 1
    assert "heurema/repo-governance/actions/pr-intake-gate@v0.5.0" in updated
    assert "owner/other-action@v0.4.0" in updated
    assert "Current docs mention @v0.4.0 as text." in updated
    assert "heurema/repo-governance/actions/codex-review-ready@v0.5.0" in updated
    print("ok - replacement updates only repo-governance action refs")


def audit_finds_refs_in_workflow_text() -> None:
    with tempfile.TemporaryDirectory(prefix="repo-governance-rollout-") as tmp_raw:
        repo = Path(tmp_raw) / "consumer"
        workflow = repo / ".github" / "workflows" / "pr-intake-gate.yml"
        workflow.parent.mkdir(parents=True)
        workflow.write_text(
            "jobs:\n"
            "  gate:\n"
            "    steps:\n"
            "      - uses: heurema/repo-governance/actions/pr-intake-gate@v0.4.0\n",
            encoding="utf-8",
        )

        matches = rollout_release.audit_repo(repo, "v0.4.0", "v0.5.0")

    assert len(matches) == 1
    assert matches[0].repo == "consumer"
    assert matches[0].relative_file == ".github/workflows/pr-intake-gate.yml"
    assert matches[0].line == 4
    assert matches[0].action == "pr-intake-gate"
    assert matches[0].old_ref == "v0.4.0"
    assert matches[0].suggested_ref == "v0.5.0"
    print("ok - audit finds refs in workflow text")


def dirty_repo_safety_logic_is_unit_testable() -> None:
    assert rollout_release.porcelain_status_is_dirty("") is False
    assert rollout_release.porcelain_status_is_dirty(" M README.md\n") is True
    assert rollout_release.porcelain_status_is_dirty("?? scratch.txt\n") is True
    print("ok - dirty repo safety logic is unit testable")


def default_pr_body_omits_unresolved_release_commit() -> None:
    args = Namespace(
        from_ref="v0.4.0",
        to_ref="v0.5.0",
        release_label="v0.5.0",
        release_commit=rollout_release.DEFAULT_RELEASE_COMMIT,
        branch_name="",
        commit_message="",
        pr_title="",
    )

    context = rollout_release.build_release_context(args)

    assert "Release commit:" not in context.pr_body
    assert "resolve-from" not in context.pr_body
    print("ok - default PR body omits unresolved release commit")


def explicit_pr_body_includes_release_commit() -> None:
    args = Namespace(
        from_ref="v0.4.0",
        to_ref="abc123",
        release_label="v0.5.0",
        release_commit="abc123",
        branch_name="",
        commit_message="",
        pr_title="",
    )

    context = rollout_release.build_release_context(args)

    assert "Release commit: abc123" in context.pr_body
    print("ok - explicit PR body includes release commit")


def main() -> int:
    action_ref_parser_extracts_action_and_ref()
    replacement_updates_only_repo_governance_action_refs()
    audit_finds_refs_in_workflow_text()
    dirty_repo_safety_logic_is_unit_testable()
    default_pr_body_omits_unresolved_release_commit()
    explicit_pr_body_includes_release_commit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
