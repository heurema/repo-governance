#!/usr/bin/env python3
"""Render a starter .github/pr-intake-gate.yml policy from the generic template."""

from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TEMPLATE = ROOT / "templates" / "pr-intake-gate.yml"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render a starter PR intake gate policy.")
    parser.add_argument("--project-name", required=True, help="Human-readable project name.")
    parser.add_argument("--output", default=".github/pr-intake-gate.yml", help="Output path in the target repo.")
    parser.add_argument("--template", default=str(DEFAULT_TEMPLATE), help="Policy template path.")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing output file.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    template = Path(args.template)
    output = Path(args.output)
    if output.exists() and not args.force:
        raise SystemExit(f"refusing to overwrite existing file: {output} (use --force)")
    content = template.read_text(encoding="utf-8").replace("<PROJECT_NAME>", args.project_name)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
