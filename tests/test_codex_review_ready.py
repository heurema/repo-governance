#!/usr/bin/env python3
"""Fixture-backed tests for actions/codex-review-ready/codex_review_ready.py."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE_PATH = ROOT / "actions" / "codex-review-ready" / "codex_review_ready.py"
TEMPLATE_PATH = ROOT / "templates" / "workflows" / "codex-review-ready.yml"

spec = importlib.util.spec_from_file_location("codex_review_ready", ENGINE_PATH)
assert spec and spec.loader
codex_review_ready = importlib.util.module_from_spec(spec)
sys.modules["codex_review_ready"] = codex_review_ready
spec.loader.exec_module(codex_review_ready)

gate = codex_review_ready.gate
evaluate_ready_once = codex_review_ready.evaluate_ready_once
write_summary = codex_review_ready.write_summary
PullRequestContext = gate.PullRequestContext

CODEX = "chatgpt-codex-connector"


def thread(
    *,
    resolved: bool = False,
    outdated: bool = False,
    created_at: str = "2026-05-01T00:02:00Z",
) -> dict[str, object]:
    return {
        "id": "thread-1",
        "isResolved": resolved,
        "isOutdated": outdated,
        "path": "src/app.go",
        "line": 42,
        "originalLine": 42,
        "comments": {
            "nodes": [
                {
                    "author": {"login": CODEX},
                    "body": "**<sub><sub>![P2 Badge](x)</sub></sub>  Fix it**",
                    "createdAt": created_at,
                    "url": "https://example.test/thread-1",
                }
            ]
        },
    }


def review_state(
    *,
    reviews: list[dict[str, object]] | None = None,
    reactions: list[dict[str, object]] | None = None,
    head_committed_at: str = "2026-05-01T00:00:00Z",
    now: str | None = None,
) -> dict[str, object]:
    state = {
        "head_sha": "head-sha",
        "head_committed_at": head_committed_at,
        "updated_at": "2026-05-01T00:10:00Z",
        "reviews": reviews or [],
        "reactions": reactions or [],
    }
    if now is not None:
        state["now"] = now
    return state


def codex_reaction(*, created_at: str = "2026-05-01T00:02:00Z") -> dict[str, object]:
    return {
        "user": {"login": f"{CODEX}[bot]"},
        "content": "+1",
        "created_at": created_at,
    }


def with_engine_state(threads: list[dict[str, object]], state: dict[str, object]):
    old_fetch_review_threads = gate.fetch_review_threads
    old_fetch_review_completion_state = gate.fetch_review_completion_state

    def restore() -> None:
        gate.fetch_review_threads = old_fetch_review_threads
        gate.fetch_review_completion_state = old_fetch_review_completion_state

    gate.fetch_review_threads = lambda ctx: threads
    gate.fetch_review_completion_state = lambda ctx: state
    return restore


def evaluate(threads: list[dict[str, object]], state: dict[str, object]):
    restore = with_engine_state(threads, state)
    try:
        return evaluate_ready_once(
            PullRequestContext(repository="heurema/example", number=123, head_sha="head-sha"),
            author_logins={CODEX},
            ignore_outdated=True,
        )
    finally:
        restore()


def write_event(path: Path) -> None:
    event = {
        "repository": {"full_name": "heurema/example"},
        "pull_request": {
            "number": 123,
            "state": "open",
            "updated_at": "2026-05-01T00:00:00Z",
            "head": {"sha": "head-sha"},
        },
    }
    path.write_text(json.dumps(event), encoding="utf-8")


def run_cli(
    name: str,
    *,
    threads: list[dict[str, object]],
    state: dict[str, object],
    poll_seconds: str = "0",
) -> list[dict[str, object]]:
    with tempfile.TemporaryDirectory(prefix=f"codex-ready-{name}-") as tmp_raw:
        tmp = Path(tmp_raw)
        event_path = tmp / "event.json"
        write_event(event_path)

        env = os.environ.copy()
        env.update(
            {
                "GITHUB_EVENT_NAME": "pull_request_target",
                "GITHUB_EVENT_PATH": str(event_path),
                "GITHUB_TOKEN": "fixture-token",
                "CODEX_REVIEW_GATE_THREADS_JSON": json.dumps(threads),
                "CODEX_REVIEW_GATE_REVIEW_STATE_JSON": json.dumps(state),
                "CODEX_REVIEW_READY_DRY_RUN": "true",
                "CODEX_REVIEW_READY_POLL_SECONDS": poll_seconds,
                "CODEX_REVIEW_READY_POLL_INTERVAL_SECONDS": "1",
            }
        )
        result = subprocess.run(
            [sys.executable, str(ENGINE_PATH)],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode != 0:
            raise AssertionError(f"{name}: unexpected exit {result.returncode}\n{result.stderr}\n{result.stdout}")
        return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


def write_summary_text(result) -> str:
    with tempfile.TemporaryDirectory(prefix="codex-ready-summary-") as tmp_raw:
        summary_path = Path(tmp_raw) / "summary.md"
        old_summary = os.environ.get("GITHUB_STEP_SUMMARY")
        os.environ["GITHUB_STEP_SUMMARY"] = str(summary_path)
        try:
            write_summary([(PullRequestContext(repository="heurema/example", number=123, head_sha="head-sha"), result)])
        finally:
            if old_summary is None:
                os.environ.pop("GITHUB_STEP_SUMMARY", None)
            else:
                os.environ["GITHUB_STEP_SUMMARY"] = old_summary
        return summary_path.read_text(encoding="utf-8")


def main() -> int:
    pending = evaluate([], review_state())
    assert pending.state == "pending"
    assert pending.description == "Codex Review has not completed for current head"
    print("ok - missing Codex review stays pending")

    success_from_reaction = evaluate([], review_state(reactions=[codex_reaction()]))
    assert success_from_reaction.state == "success"
    assert success_from_reaction.completion.is_complete is True
    print("ok - clean Codex reaction makes ready")

    stale_reaction_before_grace = evaluate(
        [],
        review_state(
            reactions=[codex_reaction(created_at="2026-05-01T00:02:00Z")],
            head_committed_at="2026-05-01T00:10:00Z",
            now="2026-05-01T00:12:00Z",
        ),
    )
    assert stale_reaction_before_grace.state == "pending"
    assert stale_reaction_before_grace.description == "Codex clean-review reaction exists; waiting for review grace window"
    print("ok - stale clean reaction waits for grace window")

    stale_reaction_after_grace = evaluate(
        [],
        review_state(
            reactions=[codex_reaction(created_at="2026-05-01T00:02:00Z")],
            head_committed_at="2026-05-01T00:10:00Z",
            now="2026-05-01T00:20:00Z",
        ),
    )
    assert stale_reaction_after_grace.state == "success"
    assert stale_reaction_after_grace.description == "Codex clean-review reaction accepted after grace window"
    print("ok - stale clean reaction passes after grace window")

    failure_from_thread = evaluate([thread()], review_state())
    assert failure_from_thread.state == "failure"
    assert failure_from_thread.description == "Resolve 1 Codex Review thread"
    assert len(failure_from_thread.findings) == 1
    print("ok - unresolved Codex thread fails ready")

    failure_summary = write_summary_text(failure_from_thread)
    assert "Agent next action" in failure_summary
    assert "Resolve each linked GitHub review conversation" in failure_summary
    assert "Do not only wait on the gate" in failure_summary
    assert "https://example.test/thread-1" in failure_summary
    assert "outdated GitHub review conversation is not the same as a resolved conversation" in failure_summary
    print("ok - failure summary tells agents to resolve review conversations")

    success_from_resolved_thread = evaluate([thread(resolved=True)], review_state())
    assert success_from_resolved_thread.state == "success"
    assert success_from_resolved_thread.completion.reason == "Codex Review thread found for current head"
    print("ok - resolved current Codex thread makes ready")

    stale_thread = evaluate([thread(resolved=True, created_at="2026-04-30T23:58:00Z")], review_state())
    assert stale_thread.state == "pending"
    pending_summary = write_summary_text(stale_thread)
    assert "current-head Codex Review signal" in pending_summary
    assert "Do not push an empty commit" in pending_summary
    print("ok - stale resolved thread does not make ready")

    outputs = run_cli("success", threads=[], state=review_state(reactions=[codex_reaction()]))
    assert len(outputs) == 1
    assert outputs[0]["status"]["context"] == "codex-review-ready"
    assert outputs[0]["status"]["state"] == "success"
    print("ok - cli writes success status payload")

    timeout_outputs = run_cli("timeout", threads=[], state=review_state())
    assert [item["status"]["state"] for item in timeout_outputs] == ["pending", "failure"]
    assert timeout_outputs[-1]["status"]["description"] == "Codex Review did not complete before timeout"
    print("ok - cli turns pending into timeout failure")

    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    assert "statuses: write" in template
    assert "cron: '*/5 * * * *'" in template
    assert "status-context: codex-review-ready" in template
    assert "poll-seconds: '1800'" in template
    assert "poll-seconds: '0'" in template
    assert "reaction-grace-seconds: '480'" in template
    assert "github.event_name != 'workflow_dispatch'" in template
    assert "github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'" in template
    print("ok - workflow template defines status reconciler")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
