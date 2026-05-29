# Contributing

This repository is shared governance infrastructure for Heurema projects. Keep changes small, reviewable, and scoped to reusable engine behavior or generic templates.

## Before opening a PR

- Read `AGENTS.md`, `docs/POLICY.md`, and the relevant action docs.
- Keep repository-specific policy decisions out of the action engine.
- Do not add runtime dependencies to action engines unless there is an explicit design decision.
- Do not include secrets, private tokens, private data, or exploit details in PR text, examples, fixtures, or logs.
- No license has been selected for this public repository yet. Do not assume external reuse rights beyond the repository's current public visibility.

## Required local checks

Run the focused checks for the files you changed. For broad changes, run:

```bash
python3 tests/test_pr_intake_gate.py
python3 tests/test_codex_review_gate.py
python3 tests/test_codex_review_ready.py
python3 tests/test_repo_governance_validation.py
python3 tests/test_audit_repos.py
python3 tests/test_governance_hygiene.py
python3 scripts/validate_repo_governance.py
git diff --check
```

## PR expectations

Use the repository PR template. External non-trivial PRs should include the problem, timing, existing options checked, alternatives considered, no-code alternative, why code is needed, and linked intent.

For changes to `pull_request_target` workflows or action engines, call out how the change preserves the trusted-base checkout boundary and avoids executing PR-head code.
