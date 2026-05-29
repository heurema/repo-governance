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
    assert "GitHub Release" in releases
    print("ok - community files exist and are operational")


def main() -> int:
    community_files_exist_and_are_operational()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
