#!/usr/bin/env python3
"""Create or update GitHub labels declared in a PR Intake Gate policy."""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import urllib.parse
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENGINE_PATH = ROOT / "actions" / "pr-intake-gate" / "pr_intake_gate.py"


def load_engine() -> Any:
    spec = importlib.util.spec_from_file_location("pr_intake_gate", ENGINE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load engine from {ENGINE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["pr_intake_gate"] = module
    spec.loader.exec_module(module)
    return module


def labels_from_policy(engine: Any, policy: dict[str, Any]) -> dict[str, dict[str, str]]:
    raw = policy.get("label_details", {})
    if not isinstance(raw, dict):
        return {}
    result: dict[str, dict[str, str]] = {}
    for label, details in raw.items():
        normalized = engine.get_label_details(policy, str(label))
        if normalized:
            result[str(label)] = normalized
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install PR Intake Gate labels in a GitHub repo.")
    parser.add_argument("--repo", required=True, help="GitHub repository, for example owner/name.")
    parser.add_argument("--policy", default=".github/pr-intake-gate.yml", help="Policy path.")
    parser.add_argument("--dry-run", action="store_true", help="Print planned changes without writing.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    engine = load_engine()
    policy = engine.load_minimal_yaml(args.policy)
    labels = labels_from_policy(engine, policy)
    if not labels:
        print("no label_details found")
        return 0

    token = os.environ.get("GITHUB_TOKEN")
    if not token and not args.dry_run:
        raise SystemExit("GITHUB_TOKEN is required unless --dry-run is used")

    repo = urllib.parse.quote(args.repo, safe="/")
    for label, details in labels.items():
        color = details.get("color", "ededed")
        description = details.get("description", "")
        if args.dry_run:
            print(f"would ensure {label}: color={color} description={description}")
            continue

        encoded_label = urllib.parse.quote(label, safe="")
        existing = engine.api_request("GET", f"/repos/{repo}/labels/{encoded_label}", token, allow_404=True)
        create_body = {"name": label, "color": color, "description": description}
        update_body = {"new_name": label, "color": color, "description": description}
        if existing is None:
            engine.api_request("POST", f"/repos/{repo}/labels", token, create_body)
            print(f"created {label}")
        else:
            engine.api_request("PATCH", f"/repos/{repo}/labels/{encoded_label}", token, update_body)
            print(f"updated {label}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
