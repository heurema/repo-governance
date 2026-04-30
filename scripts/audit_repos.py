#!/usr/bin/env python3
"""Audit local git repositories for PR Intake Gate installation status."""

from __future__ import annotations

import argparse
import csv
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RepoStatus:
    path: Path
    name: str
    remote: str
    policy: bool
    workflow: bool
    pr_template: bool
    shared_action: bool


def git_remote(repo: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo), "remote", "get-url", "origin"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except subprocess.CalledProcessError:
        return ""


def iter_repos(root: Path) -> list[Path]:
    repos: list[Path] = []
    for git_dir in root.rglob(".git"):
        if not git_dir.is_dir():
            continue
        repo = git_dir.parent
        if any(part in {"node_modules", ".cache", ".venv", "vendor"} for part in repo.parts):
            continue
        repos.append(repo)
    return sorted(set(repos))


def inspect_repo(repo: Path) -> RepoStatus:
    policy = repo / ".github" / "pr-intake-gate.yml"
    workflow = repo / ".github" / "workflows" / "pr-intake-gate.yml"
    template = repo / ".github" / "PULL_REQUEST_TEMPLATE.md"
    workflow_text = workflow.read_text(encoding="utf-8") if workflow.exists() else ""
    return RepoStatus(
        path=repo,
        name=repo.name,
        remote=git_remote(repo),
        policy=policy.exists(),
        workflow=workflow.exists(),
        pr_template=template.exists(),
        shared_action="heurema/repo-governance/actions/pr-intake-gate" in workflow_text,
    )


def print_markdown(statuses: list[RepoStatus]) -> None:
    print("| repo | policy | workflow | PR template | shared action | path |")
    print("| --- | --- | --- | --- | --- | --- |")
    for item in statuses:
        print(
            f"| {item.name} | {yes(item.policy)} | {yes(item.workflow)} | "
            f"{yes(item.pr_template)} | {yes(item.shared_action)} | `{item.path}` |"
        )


def print_csv(statuses: list[RepoStatus]) -> None:
    writer = csv.writer(sys.stdout)
    writer.writerow(["repo", "policy", "workflow", "pr_template", "shared_action", "path", "remote"])
    for item in statuses:
        writer.writerow([item.name, item.policy, item.workflow, item.pr_template, item.shared_action, item.path, item.remote])


def yes(value: bool) -> str:
    return "yes" if value else "no"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit local repos for PR Intake Gate files.")
    parser.add_argument("--root", default=".", help="Root directory to scan recursively.")
    parser.add_argument("--format", choices=("markdown", "csv"), default="markdown")
    parser.add_argument("--only-missing", action="store_true", help="Only show repos missing policy or workflow.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    statuses = [inspect_repo(repo) for repo in iter_repos(Path(args.root).resolve())]
    if args.only_missing:
        statuses = [item for item in statuses if not (item.policy and item.workflow)]
    if args.format == "csv":
        print_csv(statuses)
    else:
        print_markdown(statuses)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
