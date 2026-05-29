#!/usr/bin/env python3
"""Static checks for repository governance hygiene artifacts."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def community_files_exist_and_are_operational() -> None:
    codeowners = read(".github/CODEOWNERS")
    security = read("SECURITY.md")
    contributing = read("CONTRIBUTING.md")
    releases = read("docs/RELEASES.md")

    assert "@t3chn" in codeowners
    assert "pull_request_target" in security
    assert "private vulnerability" in security.lower()
    assert "python3 tests/test_pr_intake_gate.py" in contributing
    assert "No license has been selected" in contributing
    assert "v0.2.0" in releases
    assert "v0.3.0" in releases
    assert "v0.4.0" in releases
    assert "docs/releases/v0.5.0.md" in releases
    assert "GitHub Release" in releases
    print("ok - community files exist and are operational")


def v050_release_guidance_is_consistent() -> None:
    release_notes = read("docs/releases/v0.5.0.md")
    consumer_guidance = "\n".join(
        [
            read("README.md"),
            read("templates/workflows/pr-intake-gate.yml"),
            read("templates/workflows/codex-review-gate.yml"),
            read("templates/workflows/codex-review-ready.yml"),
        ]
    )

    assert "@v0.5.0" in consumer_guidance
    assert "@v0.4.0" not in consumer_guidance
    assert "strict root-level PR Intake policy validation" in release_notes
    assert "git tag -a v0.5.0" in release_notes
    assert "gh release create v0.5.0" in release_notes
    assert "after this PR is merged" in release_notes
    print("ok - v0.5.0 release guidance is consistent")


def main() -> int:
    community_files_exist_and_are_operational()
    v050_release_guidance_is_consistent()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
