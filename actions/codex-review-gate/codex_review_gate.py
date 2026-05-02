#!/usr/bin/env python3
"""Fail when active Codex Review threads remain unresolved on a pull request.

Security model:
- Designed for trusted workflow contexts such as pull_request_target.
- Reads only GitHub event metadata and review-thread metadata through GitHub API.
- Does not checkout, import, install, execute, or shell-evaluate PR head code.
- Does not write comments, labels, or other repository state.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

DEFAULT_API_URL = "https://api.github.com"
DEFAULT_REVIEW_AUTHOR_LOGINS = ("chatgpt-codex-connector",)
DEFAULT_USER_AGENT = "repo-governance-codex-review-gate"


class GateError(RuntimeError):
    """Raised for event, configuration, or GitHub API errors."""


@dataclass(frozen=True)
class PullRequestContext:
    repository: str
    number: int
    head_sha: str = ""
    updated_at: str | None = None


@dataclass(frozen=True)
class ReviewThreadFinding:
    thread_id: str
    path: str | None
    line: int | None
    is_outdated: bool
    author_login: str
    priority: str | None
    title: str
    url: str | None


@dataclass(frozen=True)
class ReviewCompletion:
    is_complete: bool
    reason: str
    head_sha: str | None
    completed_by: str | None = None
    completed_at: str | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Codex Review Gate.")
    parser.add_argument(
        "--review-author-logins",
        default=os.environ.get("CODEX_REVIEW_GATE_AUTHOR_LOGINS", ",".join(DEFAULT_REVIEW_AUTHOR_LOGINS)),
        help="Comma-separated GitHub logins whose unresolved threads should block the gate.",
    )
    parser.add_argument(
        "--ignore-outdated",
        default=os.environ.get("CODEX_REVIEW_GATE_IGNORE_OUTDATED", "true"),
        help="Whether to ignore outdated review threads: true or false.",
    )
    parser.add_argument(
        "--require-current-review",
        default=os.environ.get("CODEX_REVIEW_GATE_REQUIRE_CURRENT_REVIEW", "false"),
        help="Whether a current Codex Review result is required before passing.",
    )
    parser.add_argument(
        "--wait-seconds",
        default=os.environ.get("CODEX_REVIEW_GATE_WAIT_SECONDS", "0"),
        help="Seconds to wait for Codex Review completion before failing.",
    )
    parser.add_argument(
        "--poll-interval-seconds",
        default=os.environ.get("CODEX_REVIEW_GATE_POLL_INTERVAL_SECONDS", "10"),
        help="Seconds between Codex Review completion polls.",
    )
    return parser.parse_args()


def env_flag(value: str | None, *, default: bool = False) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def parse_author_logins(raw: str) -> set[str]:
    logins = {item.strip().lower() for item in raw.split(",") if item.strip()}
    if not logins:
        raise GateError("at least one review author login is required")
    return logins


def parse_non_negative_int(raw: str, *, field: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise GateError(f"{field} must be a non-negative integer") from exc
    if value < 0:
        raise GateError(f"{field} must be a non-negative integer")
    return value


def load_event() -> dict[str, Any]:
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        raise GateError("GITHUB_EVENT_PATH is required")
    with open(event_path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def get_pr_context(event: dict[str, Any]) -> PullRequestContext:
    pr = event.get("pull_request")
    if not isinstance(pr, dict):
        raise GateError("event does not contain pull_request")

    repository = event.get("repository", {}).get("full_name") or os.environ.get("GITHUB_REPOSITORY")
    if not repository:
        raise GateError("repository full name is missing")

    number = pr.get("number")
    if number is None:
        raise GateError("pull_request.number is missing")

    head = pr.get("head")
    head_sha = head.get("sha") if isinstance(head, dict) else ""
    updated_at = pr.get("updated_at")

    return PullRequestContext(
        repository=str(repository),
        number=int(number),
        head_sha=str(head_sha or ""),
        updated_at=str(updated_at) if updated_at else None,
    )


def get_token() -> str:
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise GateError("GITHUB_TOKEN is required unless CODEX_REVIEW_GATE_THREADS_JSON is provided")
    return token


def graphql_url() -> str:
    explicit = os.environ.get("GITHUB_GRAPHQL_URL")
    if explicit:
        return explicit.rstrip("/")
    api_url = os.environ.get("GITHUB_API_URL", DEFAULT_API_URL).rstrip("/")
    if api_url.endswith("/api/v3"):
        return f"{api_url[:-7]}/api/graphql"
    return f"{api_url}/graphql"


def graphql_request(token: str, query: str, variables: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        graphql_url(),
        data=json.dumps({"query": query, "variables": variables}).encode("utf-8"),
        method="POST",
    )
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Content-Type", "application/json")
    request.add_header("User-Agent", DEFAULT_USER_AGENT)
    request.add_header("X-GitHub-Api-Version", "2022-11-28")

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise GateError(f"GitHub GraphQL request failed: HTTP {exc.code}: {detail}") from exc
    except (TimeoutError, urllib.error.URLError) as exc:
        raise GateError(f"GitHub GraphQL request failed: {exc}") from exc

    if payload.get("errors"):
        raise GateError(f"GitHub GraphQL returned errors: {json.dumps(payload['errors'], ensure_ascii=False)}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise GateError("GitHub GraphQL response is missing data")
    return data


def split_repository(repository: str) -> tuple[str, str]:
    owner, separator, name = repository.partition("/")
    if not separator or not owner or not name:
        raise GateError(f"invalid repository full name: {repository}")
    return owner, name


def rest_api_url(path: str) -> str:
    api_url = os.environ.get("GITHUB_API_URL", DEFAULT_API_URL).rstrip("/")
    return f"{api_url}{path}"


def rest_request_url(token: str, url: str) -> tuple[Any, str | None]:
    request = urllib.request.Request(url, method="GET")
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("User-Agent", DEFAULT_USER_AGENT)
    request.add_header("X-GitHub-Api-Version", "2022-11-28")

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
            link = response.headers.get("Link")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise GateError(f"GitHub REST request failed: HTTP {exc.code}: {detail}") from exc
    except (TimeoutError, urllib.error.URLError) as exc:
        raise GateError(f"GitHub REST request failed: {exc}") from exc
    return payload, next_link(link)


def rest_request(token: str, path: str) -> Any:
    payload, _ = rest_request_url(token, rest_api_url(path))
    return payload


def rest_paginate(token: str, path: str) -> list[Any]:
    items: list[Any] = []
    url: str | None = rest_api_url(path)
    while url:
        payload, url = rest_request_url(token, url)
        if not isinstance(payload, list):
            raise GateError(f"GitHub REST paginated response is not a list: {path}")
        items.extend(payload)
    return items


def next_link(link_header: str | None) -> str | None:
    if not link_header:
        return None
    for part in link_header.split(","):
        url_part, separator, rel_part = part.partition(";")
        if not separator:
            continue
        if 'rel="next"' not in rel_part:
            continue
        stripped = url_part.strip()
        if stripped.startswith("<") and stripped.endswith(">"):
            return stripped[1:-1]
    return None


REVIEW_THREADS_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          isResolved
          isOutdated
          path
          line
          originalLine
          comments(first: 100) {
            pageInfo { hasNextPage endCursor }
            nodes {
              author { login }
              body
              createdAt
              url
            }
          }
        }
      }
    }
  }
}
"""


THREAD_COMMENTS_QUERY = """
query($threadId: ID!, $after: String) {
  node(id: $threadId) {
    ... on PullRequestReviewThread {
      comments(first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          author { login }
          body
          createdAt
          url
        }
      }
    }
  }
}
"""


def fetch_review_threads(ctx: PullRequestContext) -> list[dict[str, Any]]:
    fixture = os.environ.get("CODEX_REVIEW_GATE_THREADS_JSON")
    if fixture is not None:
        parsed = json.loads(fixture)
        if not isinstance(parsed, list):
            raise GateError("CODEX_REVIEW_GATE_THREADS_JSON must be a JSON array")
        return parsed

    owner, name = split_repository(ctx.repository)

    token = get_token()
    threads: list[dict[str, Any]] = []
    after: str | None = None
    while True:
        data = graphql_request(
            token,
            REVIEW_THREADS_QUERY,
            {"owner": owner, "name": name, "number": ctx.number, "after": after},
        )
        repository = data.get("repository")
        pull_request = repository.get("pullRequest") if isinstance(repository, dict) else None
        if not isinstance(pull_request, dict):
            raise GateError(f"pull request not found: {ctx.repository}#{ctx.number}")
        review_threads = pull_request.get("reviewThreads")
        if not isinstance(review_threads, dict):
            raise GateError("GitHub GraphQL response is missing reviewThreads")
        nodes = review_threads.get("nodes") or []
        if not isinstance(nodes, list):
            raise GateError("GitHub GraphQL reviewThreads.nodes is not a list")
        for thread in nodes:
            if isinstance(thread, dict):
                fetch_remaining_thread_comments(token, thread)
        threads.extend(nodes)
        page_info = review_threads.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        after = page_info.get("endCursor")
        if not after:
            raise GateError("GitHub GraphQL pagination is missing endCursor")
    return threads


def repository_api_prefix(repository: str) -> str:
    owner, name = split_repository(repository)
    return f"/repos/{urllib.parse.quote(owner, safe='')}/{urllib.parse.quote(name, safe='')}"


def fetch_review_completion_state(ctx: PullRequestContext) -> dict[str, Any]:
    fixture = os.environ.get("CODEX_REVIEW_GATE_REVIEW_STATE_JSON")
    if fixture is not None:
        parsed = json.loads(fixture)
        if not isinstance(parsed, dict):
            raise GateError("CODEX_REVIEW_GATE_REVIEW_STATE_JSON must be a JSON object")
        return parsed

    token = get_token()
    prefix = repository_api_prefix(ctx.repository)
    pull_request = rest_request(token, f"{prefix}/pulls/{ctx.number}")
    if not isinstance(pull_request, dict):
        raise GateError("GitHub REST pull request response is not an object")
    reviews = rest_paginate(token, f"{prefix}/pulls/{ctx.number}/reviews?per_page=100")
    reactions = rest_paginate(token, f"{prefix}/issues/{ctx.number}/reactions?per_page=100")

    head = pull_request.get("head")
    head_sha = head.get("sha") if isinstance(head, dict) else ""
    return {
        "head_sha": str(head_sha or ""),
        "updated_at": pull_request.get("updated_at"),
        "reviews": reviews,
        "reactions": reactions,
    }


def comments_connection(thread: dict[str, Any]) -> dict[str, Any]:
    comments = thread.get("comments")
    if not isinstance(comments, dict):
        comments = {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}
        thread["comments"] = comments
    nodes = comments.get("nodes")
    if not isinstance(nodes, list):
        comments["nodes"] = []
    page_info = comments.get("pageInfo")
    if not isinstance(page_info, dict):
        comments["pageInfo"] = {"hasNextPage": False, "endCursor": None}
    return comments


def fetch_remaining_thread_comments(token: str, thread: dict[str, Any]) -> None:
    """Fetch all comment pages for one review thread.

    The first review-thread query already returns the first comment page. A
    Codex comment can appear later in a long discussion, so author matching must
    not treat that first page as complete.
    """

    comments = comments_connection(thread)
    page_info = comments.get("pageInfo") or {}
    after = page_info.get("endCursor")
    while page_info.get("hasNextPage"):
        thread_id = thread.get("id")
        if not thread_id:
            raise GateError("GitHub GraphQL review thread is missing id for comment pagination")
        if not after:
            raise GateError("GitHub GraphQL comment pagination is missing endCursor")
        data = graphql_request(token, THREAD_COMMENTS_QUERY, {"threadId": str(thread_id), "after": after})
        node = data.get("node")
        if not isinstance(node, dict):
            raise GateError(f"GitHub GraphQL review thread not found for comment pagination: {thread_id}")
        next_comments = node.get("comments")
        if not isinstance(next_comments, dict):
            raise GateError("GitHub GraphQL response is missing review-thread comments")
        next_nodes = next_comments.get("nodes") or []
        if not isinstance(next_nodes, list):
            raise GateError("GitHub GraphQL review-thread comments.nodes is not a list")
        comments["nodes"].extend(node for node in next_nodes if isinstance(node, dict))
        page_info = next_comments.get("pageInfo") or {}
        comments["pageInfo"] = page_info
        if not isinstance(page_info, dict):
            raise GateError("GitHub GraphQL review-thread comments.pageInfo is not an object")
        after = page_info.get("endCursor")


def thread_comments(thread: dict[str, Any]) -> list[dict[str, Any]]:
    comments = thread.get("comments") or {}
    nodes = comments.get("nodes") if isinstance(comments, dict) else None
    if not isinstance(nodes, list):
        return []
    return [node for node in nodes if isinstance(node, dict)]


def comment_author_login(comment: dict[str, Any]) -> str:
    author = comment.get("author")
    if not isinstance(author, dict):
        return ""
    return str(author.get("login") or "")


def actor_login(value: dict[str, Any]) -> str:
    for key in ("user", "author"):
        actor = value.get(key)
        if isinstance(actor, dict):
            login = actor.get("login")
            if login:
                return str(login)
    return ""


def normalize_login(login: str) -> str:
    normalized = login.strip().lower()
    if normalized.endswith("[bot]"):
        normalized = normalized[:-5]
    return normalized


def login_matches(login: str, author_logins: set[str]) -> bool:
    normalized_authors = {normalize_login(author) for author in author_logins}
    return normalize_login(login) in normalized_authors


def parse_timestamp(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def review_commit_id(review: dict[str, Any]) -> str:
    commit_id = review.get("commit_id")
    if commit_id:
        return str(commit_id)
    commit = review.get("commit")
    if isinstance(commit, dict):
        oid = commit.get("oid")
        if oid:
            return str(oid)
    return ""


def current_review_completion(
    ctx: PullRequestContext,
    state: dict[str, Any],
    *,
    author_logins: set[str],
) -> ReviewCompletion:
    head_sha = str(state.get("head_sha") or ctx.head_sha or "")
    if not head_sha:
        return ReviewCompletion(False, "pull request head SHA is missing", None)

    reviews = state.get("reviews") or []
    if not isinstance(reviews, list):
        raise GateError("review completion state reviews must be a list")
    for review in reviews:
        if not isinstance(review, dict):
            continue
        if not login_matches(actor_login(review), author_logins):
            continue
        state_name = str(review.get("state") or "").upper()
        if state_name not in {"COMMENTED", "APPROVED", "CHANGES_REQUESTED"}:
            continue
        if review_commit_id(review) != head_sha:
            continue
        return ReviewCompletion(
            True,
            "Codex Review submitted for current head",
            head_sha,
            completed_by=actor_login(review),
            completed_at=str(review.get("submitted_at") or review.get("submittedAt") or "") or None,
        )

    baseline = parse_timestamp(ctx.updated_at)
    if baseline is None:
        baseline = parse_timestamp(state.get("updated_at"))
    reactions = state.get("reactions") or []
    if not isinstance(reactions, list):
        raise GateError("review completion state reactions must be a list")
    if baseline is not None:
        for reaction in reactions:
            if not isinstance(reaction, dict):
                continue
            if str(reaction.get("content") or "").upper() not in {"+1", "THUMBS_UP"}:
                continue
            if not login_matches(actor_login(reaction), author_logins):
                continue
            created_at = parse_timestamp(reaction.get("created_at") or reaction.get("createdAt"))
            if created_at is None or created_at < baseline:
                continue
            return ReviewCompletion(
                True,
                "Codex clean-review reaction found after current PR update",
                head_sha,
                completed_by=actor_login(reaction),
                completed_at=str(reaction.get("created_at") or reaction.get("createdAt") or "") or None,
            )

    return ReviewCompletion(False, "Codex Review has not completed for current head", head_sha)


def extract_priority(body: str) -> str | None:
    match = re.search(r"\bP([0-3])\b", body)
    if not match:
        return None
    return f"P{match.group(1)}"


def strip_markdown_noise(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[[^\]]+\]\([^)]*\)", " ", text)
    text = re.sub(r"[*_`>#]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_title(body: str) -> str:
    for line in body.splitlines():
        cleaned = strip_markdown_noise(line)
        cleaned = re.sub(r"\bP[0-3]\b", "", cleaned).strip(" -:—")
        if cleaned:
            return cleaned[:160]
    return "Codex Review thread"


def first_matching_comment(thread: dict[str, Any], author_logins: set[str]) -> dict[str, Any] | None:
    for comment in thread_comments(thread):
        if login_matches(comment_author_login(comment), author_logins):
            return comment
    return None


def blocking_findings(
    threads: Iterable[dict[str, Any]],
    *,
    author_logins: set[str],
    ignore_outdated: bool,
) -> list[ReviewThreadFinding]:
    findings: list[ReviewThreadFinding] = []
    for thread in threads:
        if not isinstance(thread, dict):
            continue
        if thread.get("isResolved") is True:
            continue
        is_outdated = thread.get("isOutdated") is True
        if ignore_outdated and is_outdated:
            continue
        comment = first_matching_comment(thread, author_logins)
        if comment is None:
            continue
        body = str(comment.get("body") or "")
        line = thread.get("line") or thread.get("originalLine")
        findings.append(
            ReviewThreadFinding(
                thread_id=str(thread.get("id") or ""),
                path=str(thread.get("path") or "") or None,
                line=int(line) if isinstance(line, int) else None,
                is_outdated=is_outdated,
                author_login=comment_author_login(comment),
                priority=extract_priority(body),
                title=extract_title(body),
                url=str(comment.get("url") or "") or None,
            )
        )
    return findings


def write_summary(
    ctx: PullRequestContext,
    findings: list[ReviewThreadFinding],
    completion: ReviewCompletion | None,
) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    lines = ["# Codex Review Gate", ""]
    if not findings:
        if completion is not None and not completion.is_complete:
            lines.append("Codex Review has not completed for the current head yet.")
            lines.append("")
            lines.append(f"Reason: {completion.reason}")
        else:
            lines.append("No active unresolved Codex Review threads were found.")
    else:
        lines.append("Active unresolved Codex Review threads must be resolved before merge.")
        lines.append("")
        for finding in findings:
            location = finding.path or "unknown path"
            if finding.line is not None:
                location = f"{location}:{finding.line}"
            priority = f"{finding.priority} " if finding.priority else ""
            outdated = " outdated" if finding.is_outdated else ""
            link = f" — {finding.url}" if finding.url else ""
            lines.append(f"- {priority}{location}{outdated}: {finding.title}{link}")
    lines.append("")
    lines.append(f"Repository: `{ctx.repository}`")
    lines.append(f"Pull request: `#{ctx.number}`")
    if completion is not None:
        lines.append(f"Current head: `{completion.head_sha or ctx.head_sha or 'unknown'}`")
        lines.append(f"Completion: `{completion.reason}`")
    with open(summary_path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def result_payload(
    ctx: PullRequestContext,
    findings: list[ReviewThreadFinding],
    completion: ReviewCompletion | None,
) -> dict[str, Any]:
    review_complete = None
    if completion is not None:
        review_complete = {
            "is_complete": completion.is_complete,
            "reason": completion.reason,
            "head_sha": completion.head_sha,
            "completed_by": completion.completed_by,
            "completed_at": completion.completed_at,
        }
    return {
        "verdict": "fail" if findings or (completion is not None and not completion.is_complete) else "pass",
        "repository": ctx.repository,
        "pull_request": ctx.number,
        "unresolved_codex_threads": len(findings),
        "current_codex_review": review_complete,
        "findings": [finding.__dict__ for finding in findings],
    }


def main() -> int:
    args = parse_args()
    author_logins = parse_author_logins(str(args.review_author_logins))
    ignore_outdated = env_flag(str(args.ignore_outdated), default=True)
    require_current_review = env_flag(str(args.require_current_review), default=False)
    wait_seconds = parse_non_negative_int(str(args.wait_seconds), field="wait-seconds")
    poll_interval_seconds = parse_non_negative_int(
        str(args.poll_interval_seconds),
        field="poll-interval-seconds",
    )
    if require_current_review and wait_seconds > 0 and poll_interval_seconds == 0:
        raise GateError("poll-interval-seconds must be greater than zero when wait-seconds is greater than zero")
    event = load_event()
    ctx = get_pr_context(event)

    deadline = time.monotonic() + wait_seconds
    findings: list[ReviewThreadFinding] = []
    completion: ReviewCompletion | None = None
    while True:
        threads = fetch_review_threads(ctx)
        findings = blocking_findings(threads, author_logins=author_logins, ignore_outdated=ignore_outdated)
        if findings:
            completion = None
            break

        if require_current_review:
            review_state = fetch_review_completion_state(ctx)
            completion = current_review_completion(ctx, review_state, author_logins=author_logins)
            if completion.is_complete:
                break
        else:
            break

        if wait_seconds == 0 or time.monotonic() >= deadline:
            break
        remaining = deadline - time.monotonic()
        time.sleep(min(poll_interval_seconds, max(0.1, remaining)))

    write_summary(ctx, findings, completion)
    payload = result_payload(ctx, findings, completion)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if payload["verdict"] == "fail" else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except GateError as exc:
        print(json.dumps({"verdict": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)
