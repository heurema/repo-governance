#!/usr/bin/env python3
"""Maintain the codex-review-ready commit status for pull requests.

This reconciler is intentionally state-based. It writes a commit status for the
current pull request head and can be run from pull request events, review
events, workflow_dispatch, and a schedule. The schedule is important because
GitHub does not emit workflow events for review-thread resolution or PR
reactions.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


GATE_PATH = Path(__file__).resolve().parents[1] / "codex-review-gate" / "codex_review_gate.py"
spec = importlib.util.spec_from_file_location("codex_review_gate", GATE_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"cannot load Codex Review Gate engine: {GATE_PATH}")
gate = importlib.util.module_from_spec(spec)
sys.modules["codex_review_gate"] = gate
spec.loader.exec_module(gate)

DEFAULT_STATUS_CONTEXT = "codex-review-ready"
DEFAULT_USER_AGENT = "repo-governance-codex-review-ready"
STATUS_STATES = {"error", "failure", "pending", "success"}


@dataclass(frozen=True)
class ReadyResult:
    state: str
    description: str
    findings: list[Any]
    completion: Any | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Maintain Codex Review ready commit status.")
    parser.add_argument(
        "--review-author-logins",
        default=os.environ.get("CODEX_REVIEW_READY_AUTHOR_LOGINS", ",".join(gate.DEFAULT_REVIEW_AUTHOR_LOGINS)),
        help="Comma-separated GitHub logins whose review activity controls readiness.",
    )
    parser.add_argument(
        "--ignore-outdated",
        default=os.environ.get("CODEX_REVIEW_READY_IGNORE_OUTDATED", "true"),
        help="Whether to ignore outdated Codex Review threads.",
    )
    parser.add_argument(
        "--status-context",
        default=os.environ.get("CODEX_REVIEW_READY_STATUS_CONTEXT", DEFAULT_STATUS_CONTEXT),
        help="Commit status context to write.",
    )
    parser.add_argument(
        "--poll-seconds",
        default=os.environ.get("CODEX_REVIEW_READY_POLL_SECONDS", "1800"),
        help="Seconds to poll before timing out.",
    )
    parser.add_argument(
        "--poll-interval-seconds",
        default=os.environ.get("CODEX_REVIEW_READY_POLL_INTERVAL_SECONDS", "10"),
        help="Seconds between polling attempts.",
    )
    parser.add_argument(
        "--timeout-state",
        default=os.environ.get("CODEX_REVIEW_READY_TIMEOUT_STATE", "failure"),
        choices=("failure", "pending"),
        help="Status state to write when Codex Review does not complete before timeout.",
    )
    parser.add_argument(
        "--reaction-grace-seconds",
        default=os.environ.get("CODEX_REVIEW_READY_REACTION_GRACE_SECONDS", "480"),
        help=(
            "Seconds to wait after the current head update before accepting an "
            "older clean-review reaction when no Codex threads remain unresolved."
        ),
    )
    parser.add_argument(
        "--max-open-prs",
        default=os.environ.get("CODEX_REVIEW_READY_MAX_OPEN_PRS", "50"),
        help="Maximum open PRs to reconcile on schedule or workflow_dispatch events.",
    )
    parser.add_argument(
        "--dry-run",
        default=os.environ.get("CODEX_REVIEW_READY_DRY_RUN", "false"),
        help="Print planned statuses without writing them.",
    )
    return parser.parse_args()


def env_flag(value: str | None, *, default: bool = False) -> bool:
    return gate.env_flag(value, default=default)


def parse_non_negative_int(raw: str, *, field: str) -> int:
    return gate.parse_non_negative_int(raw, field=field)


def truncate_description(description: str) -> str:
    if len(description) <= 140:
        return description
    return description[:137] + "..."


def current_run_url() -> str | None:
    server = os.environ.get("GITHUB_SERVER_URL")
    repository = os.environ.get("GITHUB_REPOSITORY")
    run_id = os.environ.get("GITHUB_RUN_ID")
    if not server or not repository or not run_id:
        return None
    return f"{server.rstrip('/')}/{repository}/actions/runs/{run_id}"


def request_json(token: str, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(gate.rest_api_url(path), data=data, method=method)
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Content-Type", "application/json")
    request.add_header("User-Agent", DEFAULT_USER_AGENT)
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise gate.GateError(f"GitHub REST request failed: HTTP {exc.code}: {detail}") from exc
    if not raw:
        return None
    return json.loads(raw)


def post_commit_status(
    ctx: Any,
    *,
    token: str,
    context: str,
    state: str,
    description: str,
    target_url: str | None,
    dry_run: bool,
) -> None:
    if state not in STATUS_STATES:
        raise gate.GateError(f"invalid commit status state: {state}")
    if not ctx.head_sha:
        raise gate.GateError(f"pull request #{ctx.number} head SHA is missing")

    payload: dict[str, Any] = {
        "context": context,
        "description": truncate_description(description),
        "state": state,
    }
    if target_url:
        payload["target_url"] = target_url

    print(json.dumps({"pull_request": ctx.number, "sha": ctx.head_sha, "status": payload}, sort_keys=True))
    if dry_run:
        return

    path = f"{gate.repository_api_prefix(ctx.repository)}/statuses/{urllib.parse.quote(ctx.head_sha, safe='')}"
    request_json(token, "POST", path, payload)


def repository_from_event(event: dict[str, Any]) -> str:
    repository = event.get("repository")
    if isinstance(repository, dict) and repository.get("full_name"):
        return str(repository["full_name"])
    repository_env = os.environ.get("GITHUB_REPOSITORY")
    if repository_env:
        return repository_env
    raise gate.GateError("repository full name is missing")


def pr_context_from_payload(repository: str, pull_request: dict[str, Any]) -> Any:
    number = pull_request.get("number")
    if number is None:
        raise gate.GateError("pull_request.number is missing")
    head = pull_request.get("head")
    head_sha = head.get("sha") if isinstance(head, dict) else ""
    return gate.PullRequestContext(
        repository=repository,
        number=int(number),
        head_sha=str(head_sha or ""),
        updated_at=str(pull_request.get("updated_at") or "") or None,
    )


def event_pr_contexts(event: dict[str, Any]) -> list[Any]:
    pull_request = event.get("pull_request")
    if isinstance(pull_request, dict):
        if str(pull_request.get("state") or "").lower() != "open":
            return []
        return [pr_context_from_payload(repository_from_event(event), pull_request)]
    return []


def list_open_pr_contexts(repository: str, *, limit: int) -> list[Any]:
    token = gate.get_token()
    contexts: list[Any] = []
    page = 1
    while len(contexts) < limit:
        path = f"{gate.repository_api_prefix(repository)}/pulls?state=open&per_page=100&page={page}"
        payload = request_json(token, "GET", path)
        if not isinstance(payload, list):
            raise gate.GateError("GitHub REST open PR response is not a list")
        if not payload:
            break
        for item in payload:
            if isinstance(item, dict):
                contexts.append(pr_context_from_payload(repository, item))
                if len(contexts) >= limit:
                    break
        page += 1
    return contexts


def reconcile_contexts(event: dict[str, Any], *, max_open_prs: int) -> list[Any]:
    contexts = event_pr_contexts(event)
    if contexts:
        return contexts
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    if event_name not in {"schedule", "workflow_dispatch"}:
        return []
    return list_open_pr_contexts(repository_from_event(event), limit=max_open_prs)


def now_utc(state: dict[str, Any]) -> datetime:
    fixture_now = gate.parse_timestamp(state.get("now"))
    if fixture_now is not None:
        return fixture_now
    return datetime.now(timezone.utc)


def clean_reaction_completion_after_grace(
    ctx: Any,
    state: dict[str, Any],
    *,
    author_logins: set[str],
    reaction_grace_seconds: int,
) -> Any | None:
    """Accept an existing PR-level clean reaction after a bounded grace window.

    GitHub issue reactions are one-per-user per PR body, so a Codex connector
    that uses a `+1` reaction as the clean-review signal cannot create a fresh
    reaction for every later rebase or no-diff retry commit. The strict
    current-head check still wins when a fresh review, thread, or reaction
    exists. This fallback only applies when no unresolved Codex threads are
    present and the current head has had time to receive late review comments.
    """

    reactions = state.get("reactions") or []
    if not isinstance(reactions, list):
        raise gate.GateError("review completion state reactions must be a list")

    clean_reactions: list[dict[str, Any]] = []
    for reaction in reactions:
        if not isinstance(reaction, dict):
            continue
        if str(reaction.get("content") or "").upper() not in {"+1", "THUMBS_UP"}:
            continue
        if not gate.login_matches(gate.actor_login(reaction), author_logins):
            continue
        clean_reactions.append(reaction)
    if not clean_reactions:
        return None

    head_sha = str(state.get("head_sha") or ctx.head_sha or "")
    baseline = gate.current_head_baseline(ctx, state)
    latest_reaction = max(
        clean_reactions,
        key=lambda item: gate.parse_timestamp(item.get("created_at") or item.get("createdAt"))
        or datetime.min.replace(tzinfo=timezone.utc),
    )
    reaction_created_at_raw = latest_reaction.get("created_at") or latest_reaction.get("createdAt")

    if baseline is None:
        return gate.ReviewCompletion(
            True,
            "Codex clean-review reaction present",
            head_sha,
            completed_by=gate.actor_login(latest_reaction),
            completed_at=str(reaction_created_at_raw or "") or None,
        )

    grace_deadline = baseline + timedelta(seconds=reaction_grace_seconds)
    if now_utc(state) < grace_deadline:
        return gate.ReviewCompletion(
            False,
            "Codex clean-review reaction exists; waiting for review grace window",
            head_sha,
            completed_by=gate.actor_login(latest_reaction),
            completed_at=str(reaction_created_at_raw or "") or None,
        )

    return gate.ReviewCompletion(
        True,
        "Codex clean-review reaction accepted after grace window",
        head_sha,
        completed_by=gate.actor_login(latest_reaction),
        completed_at=str(reaction_created_at_raw or "") or None,
    )


def evaluate_ready_once(
    ctx: Any,
    *,
    author_logins: set[str],
    ignore_outdated: bool,
    reaction_grace_seconds: int = 480,
) -> ReadyResult:
    threads = gate.fetch_review_threads(ctx)
    findings = gate.blocking_findings(threads, author_logins=author_logins, ignore_outdated=ignore_outdated)
    review_state = gate.fetch_review_completion_state(ctx)
    completion = gate.current_review_completion(ctx, review_state, author_logins=author_logins)
    if not completion.is_complete:
        completion = gate.current_thread_completion(
            ctx,
            threads,
            review_state,
            author_logins=author_logins,
            ignore_outdated=ignore_outdated,
        )
    if not completion.is_complete:
        fallback_completion = clean_reaction_completion_after_grace(
            ctx,
            review_state,
            author_logins=author_logins,
            reaction_grace_seconds=reaction_grace_seconds,
        )
        if fallback_completion is not None:
            completion = fallback_completion

    if findings:
        count = len(findings)
        suffix = "thread" if count == 1 else "threads"
        return ReadyResult(
            state="failure",
            description=f"Resolve {count} Codex Review {suffix}",
            findings=findings,
            completion=completion,
        )
    if completion.is_complete:
        return ReadyResult(
            state="success",
            description=completion.reason,
            findings=[],
            completion=completion,
        )
    return ReadyResult(
        state="pending",
        description=completion.reason,
        findings=[],
        completion=completion,
    )


def timed_out_result(result: ReadyResult, *, timeout_state: str) -> ReadyResult:
    if result.state != "pending":
        return result
    return ReadyResult(
        state=timeout_state,
        description="Codex Review did not complete before timeout",
        findings=result.findings,
        completion=result.completion,
    )


def reconcile_one(
    ctx: Any,
    *,
    token: str,
    author_logins: set[str],
    ignore_outdated: bool,
    status_context: str,
    poll_seconds: int,
    poll_interval_seconds: int,
    timeout_state: str,
    reaction_grace_seconds: int,
    dry_run: bool,
) -> ReadyResult:
    deadline = time.monotonic() + poll_seconds
    target_url = current_run_url()
    last_status_key: tuple[str, str] | None = None
    result = ReadyResult(
        state="pending",
        description="Codex Review status has not been evaluated yet",
        findings=[],
        completion=None,
    )

    while True:
        result = evaluate_ready_once(
            ctx,
            author_logins=author_logins,
            ignore_outdated=ignore_outdated,
            reaction_grace_seconds=reaction_grace_seconds,
        )
        status_key = (result.state, result.description)
        if status_key != last_status_key:
            post_commit_status(
                ctx,
                token=token,
                context=status_context,
                state=result.state,
                description=result.description,
                target_url=target_url,
                dry_run=dry_run,
            )
            last_status_key = status_key

        if result.state == "success":
            return result
        if poll_seconds == 0 or time.monotonic() >= deadline:
            timeout_result = timed_out_result(result, timeout_state=timeout_state)
            timeout_key = (timeout_result.state, timeout_result.description)
            if timeout_key != last_status_key:
                post_commit_status(
                    ctx,
                    token=token,
                    context=status_context,
                    state=timeout_result.state,
                    description=timeout_result.description,
                    target_url=target_url,
                    dry_run=dry_run,
                )
            return timeout_result

        remaining = deadline - time.monotonic()
        time.sleep(min(poll_interval_seconds, max(0.1, remaining)))


def write_summary(results: list[tuple[Any, ReadyResult]]) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    lines = ["# Codex Review Ready", ""]
    if not results:
        lines.append("No open pull request context was found for this event.")
    for ctx, result in results:
        lines.extend(summary_lines(ctx, result))
    with open(summary_path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def finding_location(finding: Any) -> str:
    path = getattr(finding, "path", None) or "unknown path"
    line = getattr(finding, "line", None)
    if isinstance(line, int):
        return f"{path}:{line}"
    return path


def summary_lines(ctx: Any, result: ReadyResult) -> list[str]:
    lines = [
        f"## `{ctx.repository}#{ctx.number}`",
        "",
        f"- Head: `{ctx.head_sha}`",
        f"- State: `{result.state}` - {result.description}",
        "",
    ]
    if result.state == "failure" and result.findings:
        lines.extend(
            [
                "Agent next action:",
                "",
                "1. Fix or verify each Codex Review finding.",
                "2. Push the fix when code changed.",
                "3. Resolve each linked GitHub review conversation after the finding is handled. Do not only wait on the gate while Codex conversations remain unresolved.",
                "4. Wait for this polling run or the scheduled reconciler to turn `codex-review-ready` success. Do not move the head only to rerun the gate.",
                "",
                "Blocking Codex Review threads:",
                "",
            ]
        )
        for finding in result.findings:
            priority = f"{getattr(finding, 'priority', None)} " if getattr(finding, "priority", None) else ""
            title = getattr(finding, "title", None) or "Codex Review thread"
            url = getattr(finding, "url", None)
            suffix = f" - {url}" if url else ""
            lines.append(f"- {priority}{finding_location(finding)}: {title}{suffix}")
        lines.extend(
            [
                "",
                "Note: an outdated GitHub review conversation is not the same as a resolved conversation. This status ignores outdated active findings by default, but repository conversation-resolution rules or external review gates may still require `isResolved=true`.",
                "",
            ]
        )
        return lines

    if result.state == "pending":
        lines.extend(
            [
                "Agent next action:",
                "",
                "- Wait for Codex Review to finish on the current head, or request a fresh Codex review if none is running.",
                "- Resolving stale or outdated threads is not a substitute for a current-head Codex Review signal.",
                "- Do not push an empty commit just to rerun this gate; the scheduled reconciler handles review reactions and thread-resolution updates.",
                "",
            ]
        )
        return lines

    if result.state == "success":
        lines.extend(
            [
                "Agent next action:",
                "",
                "- Codex Review is ready for this head.",
                "- If another GitHub conversation-resolution gate is still red, resolve the remaining GitHub conversations in the PR UI or via the review-thread API.",
                "",
            ]
        )
    return lines


def main() -> int:
    args = parse_args()
    author_logins = gate.parse_author_logins(str(args.review_author_logins))
    ignore_outdated = env_flag(str(args.ignore_outdated), default=True)
    poll_seconds = parse_non_negative_int(str(args.poll_seconds), field="poll-seconds")
    poll_interval_seconds = parse_non_negative_int(str(args.poll_interval_seconds), field="poll-interval-seconds")
    reaction_grace_seconds = parse_non_negative_int(
        str(args.reaction_grace_seconds),
        field="reaction-grace-seconds",
    )
    max_open_prs = parse_non_negative_int(str(args.max_open_prs), field="max-open-prs")
    if poll_seconds > 0 and poll_interval_seconds == 0:
        raise gate.GateError("poll-interval-seconds must be greater than zero when poll-seconds is greater than zero")
    event = gate.load_event()
    contexts = reconcile_contexts(event, max_open_prs=max_open_prs)
    token = gate.get_token()
    dry_run = env_flag(str(args.dry_run), default=False)

    results: list[tuple[Any, ReadyResult]] = []
    for ctx in contexts:
        result = reconcile_one(
            ctx,
            token=token,
            author_logins=author_logins,
            ignore_outdated=ignore_outdated,
            status_context=str(args.status_context),
            poll_seconds=poll_seconds,
            poll_interval_seconds=poll_interval_seconds,
            timeout_state=str(args.timeout_state),
            reaction_grace_seconds=reaction_grace_seconds,
            dry_run=dry_run,
        )
        results.append((ctx, result))

    write_summary(results)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except gate.GateError as exc:
        print(json.dumps({"verdict": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)
