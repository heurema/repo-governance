#!/usr/bin/env python3
"""Tests for scripts/audit_repos.py."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "audit_repos.py"

spec = importlib.util.spec_from_file_location("audit_repos", SCRIPT_PATH)
assert spec and spec.loader
audit_repos = importlib.util.module_from_spec(spec)
sys.modules["audit_repos"] = audit_repos
spec.loader.exec_module(audit_repos)

inspect_repo = audit_repos.inspect_repo


def init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "remote", "add", "origin", "git@github.com:heurema/example.git"], check=True)


def audit_reports_governance_hygiene_files() -> None:
    with tempfile.TemporaryDirectory(prefix="repo-governance-audit-") as tmp_raw:
        repo = Path(tmp_raw) / "example"
        repo.mkdir()
        init_repo(repo)
        (repo / ".github" / "workflows").mkdir(parents=True)
        (repo / ".github" / "pr-intake-gate.yml").write_text("project:\n  name: Example\n", encoding="utf-8")
        (repo / ".github" / "PULL_REQUEST_TEMPLATE.md").write_text("## Summary\n", encoding="utf-8")
        (repo / ".github" / "CODEOWNERS").write_text("* @t3chn\n", encoding="utf-8")
        (repo / "SECURITY.md").write_text("# Security\n", encoding="utf-8")
        (repo / "CONTRIBUTING.md").write_text("# Contributing\n", encoding="utf-8")
        (repo / "LICENSE").write_text("Example license\n", encoding="utf-8")
        (repo / ".github" / "workflows" / "pr-intake-gate.yml").write_text(
            "uses: heurema/repo-governance/actions/pr-intake-gate@v0.3.0\n",
            encoding="utf-8",
        )

        status = inspect_repo(repo)

    assert status.policy is True
    assert status.workflow is True
    assert status.pr_template is True
    assert status.shared_action is True
    assert status.codeowners is True
    assert status.security is True
    assert status.contributing is True
    assert status.license is True
    print("ok - audit reports governance hygiene files")


def main() -> int:
    audit_reports_governance_hygiene_files()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
