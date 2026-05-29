#!/usr/bin/env python3
"""Roll out repo-governance action refs across local consumer repositories."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ACTION_REF_RE = re.compile(
    r"heurema/repo-governance/actions/"
    r"(?P<action>pr-intake-gate|codex-review-gate|codex-review-ready)"
    r"@(?P<ref>[A-Za-z0-9._/\-]+)"
)
DEFAULT_RELEASE_LABEL = "v0.5.0"
DEFAULT_RELEASE_COMMIT = "resolve-from-v0.5.0-after-release"


@dataclass(frozen=True)
class ActionRef:
    action: str
    ref: str
    line: int


@dataclass(frozen=True)
class AuditMatch:
    repo: str
    repo_path: Path
    relative_file: str
    line: int
    action: str
    old_ref: str
    suggested_ref: str


@dataclass
class Summary:
    scanned_repos: int = 0
    matched_repos: int = 0
    changed_repos: int = 0
    skipped_dirty_repos: int = 0
    skipped_no_match_repos: int = 0
    errors: int = 0
    skipped_non_default_repos: int = 0
    dry_run_repos: int = 0


@dataclass(frozen=True)
class ProcessResult:
    changed: bool = False
    skipped_dirty: bool = False
    skipped_no_match: bool = False
    skipped_non_default: bool = False
    dry_run: bool = False
    error: str = ""


@dataclass(frozen=True)
class ReleaseContext:
    from_ref: str
    to_ref: str
    release_label: str
    release_commit: str
    branch_name: str
    commit_message: str
    pr_title: str
    pr_body: str


def find_action_refs(text: str) -> list[ActionRef]:
    refs: list[ActionRef] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for match in ACTION_REF_RE.finditer(line):
            refs.append(ActionRef(action=match.group("action"), ref=match.group("ref"), line=line_number))
    return refs


def replace_action_refs(text: str, from_ref: str, to_ref: str) -> tuple[str, int]:
    count = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal count
        if match.group("ref") != from_ref:
            return match.group(0)
        count += 1
        return f"heurema/repo-governance/actions/{match.group('action')}@{to_ref}"

    return ACTION_REF_RE.sub(replace, text), count


def porcelain_status_is_dirty(status: str) -> bool:
    return bool(status.strip())


def iter_git_repos(root: Path) -> list[Path]:
    repos: set[Path] = set()
    for git_path in root.rglob(".git"):
        if any(part in {"node_modules", ".cache", ".venv", "vendor"} for part in git_path.parts):
            continue
        if git_path.is_dir() or git_path.is_file():
            repos.add(git_path.parent)
    return sorted(repos)


def iter_relevant_files(repo: Path) -> list[Path]:
    patterns = (
        ".github/workflows/**/*.yml",
        ".github/workflows/**/*.yaml",
        ".github/dependabot.yml",
        ".github/dependabot.yaml",
        "docs/**/*.md",
        "templates/**/*.yml",
        "templates/**/*.yaml",
        "README.md",
    )
    files: set[Path] = set()
    for pattern in patterns:
        for path in repo.glob(pattern):
            if path.is_file():
                files.add(path)
    return sorted(files, key=lambda item: item.relative_to(repo).as_posix())


def audit_repo(repo: Path, from_ref: str, to_ref: str) -> list[AuditMatch]:
    matches: list[AuditMatch] = []
    for path in iter_relevant_files(repo):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        relative_file = path.relative_to(repo).as_posix()
        for ref in find_action_refs(text):
            if ref.ref != from_ref:
                continue
            matches.append(
                AuditMatch(
                    repo=repo.name,
                    repo_path=repo,
                    relative_file=relative_file,
                    line=ref.line,
                    action=ref.action,
                    old_ref=ref.ref,
                    suggested_ref=to_ref,
                )
            )
    return matches


def run_git(repo: Path, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def git_output(repo: Path, args: list[str]) -> str:
    return run_git(repo, args).stdout.strip()


def worktree_is_dirty(repo: Path) -> bool:
    return porcelain_status_is_dirty(git_output(repo, ["status", "--porcelain"]))


def current_branch(repo: Path) -> str:
    return git_output(repo, ["rev-parse", "--abbrev-ref", "HEAD"])


def default_branch(repo: Path) -> str:
    try:
        value = git_output(repo, ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"])
    except subprocess.CalledProcessError:
        return ""
    if value.startswith("origin/"):
        return value.removeprefix("origin/")
    return value


def branch_exists(repo: Path, branch_name: str) -> bool:
    return run_git(repo, ["rev-parse", "--verify", "--quiet", branch_name], check=False).returncode == 0


def branch_is_allowed(current: str, default: str, rollout_branch: str, allow_non_default: bool) -> bool:
    if allow_non_default:
        return True
    allowed = {"main", "master"}
    if default:
        allowed.add(default)
    if current == rollout_branch:
        return True
    return current in allowed


def command_exists(command: str) -> bool:
    try:
        subprocess.run([command, "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    except FileNotFoundError:
        return False
    return True


def patch_files(repo: Path, from_ref: str, to_ref: str) -> list[Path]:
    changed: list[Path] = []
    for path in iter_relevant_files(repo):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        updated, count = replace_action_refs(text, from_ref, to_ref)
        if count == 0 or updated == text:
            continue
        path.write_text(updated, encoding="utf-8")
        changed.append(path)
    return changed


def switch_to_rollout_branch(repo: Path, branch_name: str) -> None:
    if branch_exists(repo, branch_name):
        if current_branch(repo) != branch_name:
            run_git(repo, ["switch", branch_name])
        return
    run_git(repo, ["switch", "-c", branch_name])


def commit_changes(repo: Path, paths: list[Path], message: str) -> None:
    relative_paths = [path.relative_to(repo).as_posix() for path in paths]
    run_git(repo, ["add", *relative_paths])
    run_git(repo, ["commit", "-m", message])


def create_pr(repo: Path, context: ReleaseContext) -> None:
    if not command_exists("gh"):
        print(f"{repo.name}: gh unavailable; run manually:")
        print(f"  git -C {repo} push -u origin {context.branch_name}")
        print(f"  cd {repo} && gh pr create --title {context.pr_title!r} --body {context.pr_body!r}")
        return
    run_git(repo, ["push", "-u", "origin", context.branch_name])
    subprocess.run(
        [
            "gh",
            "pr",
            "create",
            "--title",
            context.pr_title,
            "--body",
            context.pr_body,
        ],
        cwd=repo,
        check=True,
        text=True,
    )


def process_repo(repo: Path, context: ReleaseContext, mode: str, apply: bool, allow_non_default: bool) -> ProcessResult:
    matches = audit_repo(repo, context.from_ref, context.to_ref)
    if not matches:
        return ProcessResult(skipped_no_match=True)

    if mode == "audit":
        return ProcessResult()

    if not apply:
        print(
            f"{repo.name}: dry-run would update {len(matches)} ref(s) on "
            f"{context.branch_name} and commit {context.commit_message!r}"
        )
        return ProcessResult(dry_run=True)

    try:
        if worktree_is_dirty(repo):
            print(f"{repo.name}: skipped dirty working tree")
            return ProcessResult(skipped_dirty=True)

        branch = current_branch(repo)
        default = default_branch(repo)
        if not branch_is_allowed(branch, default, context.branch_name, allow_non_default):
            print(f"{repo.name}: skipped non-default branch {branch!r}")
            return ProcessResult(skipped_non_default=True)

        switch_to_rollout_branch(repo, context.branch_name)

        if worktree_is_dirty(repo):
            print(f"{repo.name}: skipped dirty rollout branch")
            return ProcessResult(skipped_dirty=True)

        changed = patch_files(repo, context.from_ref, context.to_ref)
        if not changed:
            print(f"{repo.name}: no changes after switching to {context.branch_name}")
            return ProcessResult(skipped_no_match=True)

        commit_changes(repo, changed, context.commit_message)
        print(f"{repo.name}: committed {len(changed)} file(s)")

        if mode == "pr":
            create_pr(repo, context)
            print(f"{repo.name}: pushed and opened PR")

        return ProcessResult(changed=True)
    except (subprocess.CalledProcessError, OSError) as error:
        message = str(error)
        if isinstance(error, subprocess.CalledProcessError):
            message = (error.stderr or error.stdout or str(error)).strip()
        print(f"{repo.name}: error: {message}", file=sys.stderr)
        return ProcessResult(error=message)


def print_matches(matches: list[AuditMatch]) -> None:
    print("| repo | file | line | action | old_ref | suggested_ref |")
    print("| --- | --- | ---: | --- | --- | --- |")
    for match in matches:
        print(
            f"| {match.repo} | `{match.relative_file}` | {match.line} | "
            f"`{match.action}` | `{match.old_ref}` | `{match.suggested_ref}` |"
        )


def print_summary(summary: Summary) -> None:
    print()
    print("Summary:")
    print(f"  scanned repos: {summary.scanned_repos}")
    print(f"  matched repos: {summary.matched_repos}")
    print(f"  changed repos: {summary.changed_repos}")
    print(f"  skipped dirty repos: {summary.skipped_dirty_repos}")
    print(f"  skipped no-match repos: {summary.skipped_no_match_repos}")
    print(f"  skipped non-default repos: {summary.skipped_non_default_repos}")
    print(f"  dry-run repos: {summary.dry_run_repos}")
    print(f"  errors: {summary.errors}")


def parse_repos(value: str) -> set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}


def build_release_context(args: argparse.Namespace) -> ReleaseContext:
    release_label = args.release_label
    branch_name = args.branch_name or f"chore/repo-governance-{release_label}"
    commit_message = args.commit_message or f"chore: update repo-governance actions to {release_label}"
    pr_title = args.pr_title or commit_message
    pr_body = (
        f"Updates repo-governance action refs to the {release_label} release.\n\n"
        f"- Release commit: {args.release_commit}\n"
        "- Updates only `heurema/repo-governance/actions/...` refs.\n"
        "- Does not change consumer policy semantics."
    )
    return ReleaseContext(
        from_ref=args.from_ref,
        to_ref=args.to_ref,
        release_label=release_label,
        release_commit=args.release_commit,
        branch_name=branch_name,
        commit_message=commit_message,
        pr_title=pr_title,
        pr_body=pr_body,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Roll out repo-governance action refs across local consumer repos.")
    parser.add_argument("--root", required=True, help="Root directory containing local git repositories.")
    parser.add_argument("--from", dest="from_ref", required=True, help="Existing repo-governance action ref to replace.")
    parser.add_argument("--to", dest="to_ref", required=True, help="New repo-governance action ref.")
    parser.add_argument("--mode", choices=("audit", "patch", "pr"), required=True)
    parser.add_argument("--pin", choices=("tag", "sha"), default="tag", help="Documentation label for the target ref.")
    parser.add_argument("--repos", default="", help="Comma-separated repo directory names to include.")
    parser.add_argument("--apply", action="store_true", help="Apply patch/pr changes. patch and pr are dry-run by default.")
    parser.add_argument("--allow-non-default", action="store_true", help="Allow patch/pr from non-default branches.")
    parser.add_argument("--release-label", default=DEFAULT_RELEASE_LABEL, help="Release label used in branch, commit, and PR text.")
    parser.add_argument("--release-commit", default=DEFAULT_RELEASE_COMMIT, help="Release commit used in PR text.")
    parser.add_argument("--branch-name", default="", help="Override rollout branch name.")
    parser.add_argument("--commit-message", default="", help="Override rollout commit message.")
    parser.add_argument("--pr-title", default="", help="Override rollout PR title.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.root).expanduser().resolve()
    include_repos = parse_repos(args.repos)
    context = build_release_context(args)
    repos = iter_git_repos(root)
    if include_repos:
        repos = [repo for repo in repos if repo.name in include_repos]

    summary = Summary(scanned_repos=len(repos))
    all_matches: list[AuditMatch] = []

    for repo in repos:
        matches = audit_repo(repo, context.from_ref, context.to_ref)
        if matches:
            summary.matched_repos += 1
            all_matches.extend(matches)
        result = process_repo(repo, context, args.mode, args.apply, args.allow_non_default)
        if result.changed:
            summary.changed_repos += 1
        if result.skipped_dirty:
            summary.skipped_dirty_repos += 1
        if result.skipped_no_match:
            summary.skipped_no_match_repos += 1
        if result.skipped_non_default:
            summary.skipped_non_default_repos += 1
        if result.dry_run:
            summary.dry_run_repos += 1
        if result.error:
            summary.errors += 1

    if args.mode == "audit":
        print_matches(all_matches)
    elif not args.apply:
        print()
        print("Dry run only. Re-run with --apply to modify repositories.")

    print_summary(summary)
    return 1 if summary.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
