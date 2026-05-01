#!/usr/bin/env python3
"""Fixture-backed tests for actions/codex-review-gate/codex_review_gate.py."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE_PATH = ROOT / "actions" / "codex-review-gate" / "codex_review_gate.py"

spec = importlib.util.spec_from_file_location("codex_review_gate", ENGINE_PATH)
assert spec and spec.loader
codex_review_gate = importlib.util.module_from_spec(spec)
sys.modules["codex_review_gate"] = codex_review_gate
spec.loader.exec_module(codex_review_gate)

blocking_findings = codex_review_gate.blocking_findings
extract_priority = codex_review_gate.extract_priority
extract_title = codex_review_gate.extract_title
fetch_review_threads = codex_review_gate.fetch_review_threads
parse_author_logins = codex_review_gate.parse_author_logins
PullRequestContext = codex_review_gate.PullRequestContext

CODEX = "chatgpt-codex-connector"
OTHER = "human-reviewer"


def thread(
    *,
    body: str,
    author: str = CODEX,
    resolved: bool = False,
    outdated: bool = False,
    path: str = "src/app.go",
    line: int | None = 42,
    thread_id: str = "thread-1",
) -> dict[str, object]:
    return {
        "id": thread_id,
        "isResolved": resolved,
        "isOutdated": outdated,
        "path": path,
        "line": line,
        "originalLine": line,
        "comments": {
            "nodes": [
                {
                    "author": {"login": author},
                    "body": body,
                    "createdAt": "2026-05-01T00:00:00Z",
                    "url": f"https://example.test/{thread_id}",
                }
            ]
        },
    }


def write_event(path: Path) -> None:
    event = {
        "repository": {"full_name": "heurema/example"},
        "pull_request": {
            "number": 123,
            "base": {"sha": "base-sha"},
            "head": {"sha": "head-sha"},
        },
    }
    path.write_text(json.dumps(event), encoding="utf-8")


def run_case(
    name: str,
    expected_status: int,
    expected_verdict: str,
    threads: list[dict[str, object]],
    *,
    author_logins: str | None = None,
    ignore_outdated: str | None = None,
) -> tuple[dict[str, object], str, str]:
    with tempfile.TemporaryDirectory(prefix=f"codex-review-{name}-") as tmp_raw:
        tmp = Path(tmp_raw)
        event_path = tmp / "event.json"
        summary_path = tmp / "summary.md"
        write_event(event_path)

        env = os.environ.copy()
        env.update(
            {
                "GITHUB_EVENT_PATH": str(event_path),
                "GITHUB_STEP_SUMMARY": str(summary_path),
                "CODEX_REVIEW_GATE_THREADS_JSON": json.dumps(threads),
            }
        )
        if author_logins is not None:
            env["CODEX_REVIEW_GATE_AUTHOR_LOGINS"] = author_logins
        if ignore_outdated is not None:
            env["CODEX_REVIEW_GATE_IGNORE_OUTDATED"] = ignore_outdated

        result = subprocess.run(
            [sys.executable, str(ENGINE_PATH)],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

        if result.returncode != expected_status:
            raise AssertionError(
                f"{name}: expected exit {expected_status}, got {result.returncode}\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        payload = json.loads(result.stdout)
        if payload["verdict"] != expected_verdict:
            raise AssertionError(f"{name}: expected verdict {expected_verdict}, got {payload['verdict']}")
        summary = summary_path.read_text(encoding="utf-8")
        if "Codex Review Gate" not in summary:
            raise AssertionError(f"{name}: missing step summary")
        print(f"ok - {name}")
        return payload, summary, result.stderr


def helper_semantics() -> None:
    assert parse_author_logins("chatgpt-codex-connector, other-bot") == {
        "chatgpt-codex-connector",
        "other-bot",
    }
    assert extract_priority("![P1 Badge](x)\n\nBad thing") == "P1"
    assert extract_priority("No explicit priority") is None
    assert extract_title("**<sub><sub>![P2 Badge](x)</sub></sub>  Parse multipart payloads**\n\nBody") == "Parse multipart payloads"

    findings = blocking_findings(
        [thread(body="**<sub><sub>![P1 Badge](x)</sub></sub>  Fix it**")],
        author_logins={CODEX},
        ignore_outdated=True,
    )
    assert len(findings) == 1
    assert findings[0].priority == "P1"
    assert findings[0].title == "Fix it"
    assert findings[0].path == "src/app.go"
    assert findings[0].line == 42
    print("ok - helper semantics")


def paginated_thread_comments_are_author_matched() -> None:
    """A late Codex comment beyond the first 100 comments must still block."""

    old_graphql_request = codex_review_gate.graphql_request
    old_token = os.environ.get("GITHUB_TOKEN")
    calls: list[dict[str, object]] = []

    other_comments = [
        {
            "author": {"login": OTHER},
            "body": f"Human comment {index}",
            "createdAt": "2026-05-01T00:00:00Z",
            "url": f"https://example.test/human-{index}",
        }
        for index in range(100)
    ]

    def fake_graphql_request(token: str, query: str, variables: dict[str, object]) -> dict[str, object]:
        calls.append({"query": query, "variables": variables})
        assert token == "fixture-token"
        if "reviewThreads(first: 100" in query:
            return {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": [
                                {
                                    "id": "thread-paginated",
                                    "isResolved": False,
                                    "isOutdated": False,
                                    "path": "actions/example.py",
                                    "line": 17,
                                    "originalLine": 17,
                                    "comments": {
                                        "pageInfo": {"hasNextPage": True, "endCursor": "comment-page-1"},
                                        "nodes": other_comments,
                                    },
                                }
                            ],
                        }
                    }
                }
            }
        if "node(id: $threadId)" in query:
            assert variables == {"threadId": "thread-paginated", "after": "comment-page-1"}
            return {
                "node": {
                    "comments": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": [
                            {
                                "author": {"login": CODEX},
                                "body": "**<sub><sub>![P2 Badge](x)</sub></sub>  Late Codex finding**",
                                "createdAt": "2026-05-01T00:01:00Z",
                                "url": "https://example.test/codex-late",
                            }
                        ],
                    }
                }
            }
        raise AssertionError("unexpected GraphQL query")

    try:
        codex_review_gate.graphql_request = fake_graphql_request
        os.environ["GITHUB_TOKEN"] = "fixture-token"
        threads = fetch_review_threads(PullRequestContext(repository="heurema/example", number=123))
    finally:
        codex_review_gate.graphql_request = old_graphql_request
        if old_token is None:
            os.environ.pop("GITHUB_TOKEN", None)
        else:
            os.environ["GITHUB_TOKEN"] = old_token

    assert len(calls) == 2
    assert len(threads) == 1
    assert len(threads[0]["comments"]["nodes"]) == 101
    findings = blocking_findings(threads, author_logins={CODEX}, ignore_outdated=True)
    assert len(findings) == 1
    assert findings[0].title == "Late Codex finding"
    assert findings[0].url == "https://example.test/codex-late"
    print("ok - paginated thread comments are author matched")


def main() -> int:
    helper_semantics()
    paginated_thread_comments_are_author_matched()

    empty, _, _ = run_case("no_threads_passes", 0, "pass", [])
    assert empty["unresolved_codex_threads"] == 0

    unresolved, summary, _ = run_case(
        "unresolved_codex_thread_fails",
        1,
        "fail",
        [thread(body="**<sub><sub>![P1 Badge](x)</sub></sub>  Pin mutable action**\n\nBody")],
    )
    assert unresolved["unresolved_codex_threads"] == 1
    assert "Pin mutable action" in summary
    assert "https://example.test/thread-1" in summary

    resolved, _, _ = run_case(
        "resolved_codex_thread_passes",
        0,
        "pass",
        [thread(body="**<sub><sub>![P1 Badge](x)</sub></sub>  Already fixed**", resolved=True)],
    )
    assert resolved["unresolved_codex_threads"] == 0

    outdated, _, _ = run_case(
        "outdated_codex_thread_ignored_by_default",
        0,
        "pass",
        [thread(body="**<sub><sub>![P2 Badge](x)</sub></sub>  Old diff**", outdated=True)],
    )
    assert outdated["unresolved_codex_threads"] == 0

    outdated_fail, _, _ = run_case(
        "outdated_codex_thread_can_fail_when_configured",
        1,
        "fail",
        [thread(body="**<sub><sub>![P2 Badge](x)</sub></sub>  Old diff**", outdated=True)],
        ignore_outdated="false",
    )
    assert outdated_fail["findings"][0]["is_outdated"] is True

    non_codex, _, _ = run_case(
        "non_codex_unresolved_thread_is_ignored",
        0,
        "pass",
        [thread(body="**<sub><sub>![P1 Badge](x)</sub></sub>  Human finding**", author=OTHER)],
    )
    assert non_codex["unresolved_codex_threads"] == 0

    configured_author, _, _ = run_case(
        "configured_author_blocks",
        1,
        "fail",
        [thread(body="**<sub><sub>![P2 Badge](x)</sub></sub>  Other bot finding**", author="other-bot")],
        author_logins="other-bot",
    )
    assert configured_author["findings"][0]["author_login"] == "other-bot"

    reply_thread, _, _ = run_case(
        "codex_reply_in_thread_blocks",
        1,
        "fail",
        [
            {
                "id": "thread-reply",
                "isResolved": False,
                "isOutdated": False,
                "path": "README.md",
                "line": 9,
                "originalLine": 9,
                "comments": {
                    "nodes": [
                        {"author": {"login": OTHER}, "body": "Human opening", "url": "https://example.test/human"},
                        {
                            "author": {"login": CODEX},
                            "body": "**<sub><sub>![P2 Badge](x)</sub></sub>  Codex reply**",
                            "url": "https://example.test/codex",
                        },
                    ]
                },
            }
        ],
    )
    assert reply_thread["findings"][0]["path"] == "README.md"

    malformed_env = os.environ.copy()
    with tempfile.TemporaryDirectory(prefix="codex-review-error-") as tmp_raw:
        event_path = Path(tmp_raw) / "event.json"
        event_path.write_text(json.dumps({"repository": {"full_name": "heurema/example"}}), encoding="utf-8")
        malformed_env.update({"GITHUB_EVENT_PATH": str(event_path), "CODEX_REVIEW_GATE_THREADS_JSON": "[]"})
        result = subprocess.run(
            [sys.executable, str(ENGINE_PATH)],
            cwd=ROOT,
            env=malformed_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        assert result.returncode == 2
        assert "event does not contain pull_request" in result.stderr
        print("ok - missing pull_request errors")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
