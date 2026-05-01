# Agent instructions for repo-governance

This repository contains reusable governance tooling. Treat it as shared infrastructure: small changes here can affect every consuming repository.

## Core rule

Centralize the **engine**, not repository policy.

- The reusable action lives here.
- Each consuming repository owns `.github/pr-intake-gate.yml`.
- Do not hard-code GoalRail, Signum, Punk, or any target repo into the action engine.
- Repo-specific risk decisions belong in local policy or `examples/`, not in `actions/pr-intake-gate/pr_intake_gate.py`.

## Security invariants

Never weaken these without an explicit design decision:

1. The target workflow uses `pull_request_target` only because it checks out the trusted base commit.
2. The target workflow must not checkout PR head code before this gate runs.
3. The action must not import, install, execute, or shell-evaluate anything from the PR head.
4. The action reads PR metadata and changed files through GitHub REST API.
5. PR title/body values must never be interpolated into shell commands.
6. Third-party actions in templates must be pinned to a commit SHA.
7. Mature consumers should pin this action to a protected tag or exact commit SHA.

## Repository layout

- `actions/pr-intake-gate/action.yml` - composite GitHub Action wrapper.
- `actions/pr-intake-gate/pr_intake_gate.py` - deterministic engine, stdlib only.
- `actions/codex-review-gate/action.yml` - composite GitHub Action wrapper for unresolved Codex Review threads.
- `actions/codex-review-gate/codex_review_gate.py` - read-only Codex Review thread gate, stdlib only.
- `templates/pr-intake-gate.yml` - generic local policy starter.
- `templates/workflows/pr-intake-gate.yml` - target repo workflow wrapper.
- `templates/workflows/codex-review-gate.yml` - target repo workflow wrapper for Codex Review thread enforcement.
- `templates/pull-request-template-sections.md` - required PR body sections for external contributors.
- `examples/*.pr-intake-gate.yml` - reference policies copied from real repos and lightly normalized.
- `schemas/pr-intake-gate.schema.json` - documented schema for editors and future validators.
- `scripts/render_repo_policy.py` - render a starter policy into a target repo.
- `scripts/install_labels.py` - create/update labels from local policy.
- `scripts/audit_repos.py` - inspect local repos for gate rollout status.
- `tests/test_pr_intake_gate.py` - fixture-backed PR Intake Gate tests.
- `tests/test_codex_review_gate.py` - fixture-backed Codex Review Gate tests.

## How to add PR Intake Gate to another repo

Work in the target repo, not here, except when improving shared tooling.

1. Inspect target repo structure:
   - language/runtime;
   - CI workflows;
   - dependency manifests;
   - public API or CLI surfaces;
   - docs/product/governance paths;
   - security/auth/deployment/migration paths.
2. Add local policy:
   ```bash
   python3 /path/to/repo-governance/scripts/render_repo_policy.py \
     --project-name "Project Name" \
     --output .github/pr-intake-gate.yml
   ```
3. Copy workflow wrapper:
   ```bash
   mkdir -p .github/workflows
   cp /path/to/repo-governance/templates/workflows/pr-intake-gate.yml \
     .github/workflows/pr-intake-gate.yml
   ```
4. Merge PR template sections from:
   ```text
   /path/to/repo-governance/templates/pull-request-template-sections.md
   ```
5. Tune `.github/pr-intake-gate.yml`:
   - set `project.name`;
   - make `bot_comment.marker` unique;
   - keep trivial paths narrow;
   - mark workflows, dependencies, runtime code, governance, product canon, auth/security, migrations, and deployment config as high-risk;
   - add project-specific intent patterns: issues, discussions, ADRs, research notes, goals, reports, eval specs.
6. Bootstrap labels:
   ```bash
   GITHUB_TOKEN="$GITHUB_TOKEN" \
   python3 /path/to/repo-governance/scripts/install_labels.py \
     --repo owner/name \
     --policy .github/pr-intake-gate.yml
   ```
7. Test locally with dry-run fixtures.
8. Open a PR in the target repo.
9. After merge, enable branch protection requiring `pr-intake-gate` plus normal CI/docs checks.
10. Live-test both paths:
    - trusted maintainer/admin high-risk PR must pass;
    - external high-risk PR must fail;
    - external non-trivial PR missing sections must fail;
    - external non-trivial PR with full context and linked intent must pass;
    - `intake/accepted-for-pr` must pass only non-high-risk external PRs;
    - `maintainer/override-intake` must pass high-risk PRs.

## How to edit the engine

Before changing `actions/pr-intake-gate/pr_intake_gate.py`:

1. Read `docs/POLICY.md`.
2. Add or update tests in `tests/test_pr_intake_gate.py` first.
3. Preserve stdlib-only implementation unless there is a strong reason.
4. Preserve backward compatibility for existing policies when practical.
5. Run:
   ```bash
   python3 tests/test_pr_intake_gate.py
   python3 tests/test_codex_review_gate.py
   ```
6. If behavior changes, update:
   - `README.md`
   - `docs/POLICY.md`
   - `docs/ROLLOUT.md`
   - `schemas/pr-intake-gate.schema.json`
   - relevant `examples/*.pr-intake-gate.yml`

## YAML policy limitations

The engine intentionally uses a tiny YAML parser to avoid runtime dependencies.

Supported:

- nested mappings;
- scalar lists;
- quoted and unquoted scalar values;
- booleans, nulls, and integers.

Not supported:

- tabs for indentation;
- anchors and aliases;
- multiline strings;
- flow-style lists/maps;
- complex scalar quoting behavior.

Keep policy files simple.

## Decision order to preserve

The current gate order is intentional:

1. maintainer override label;
2. trusted author by permission;
3. trusted author by association fallback only if permission is unavailable;
4. high-risk external path failure;
5. trivial external pass;
6. accepted-for-pr pass for non-high-risk external PRs;
7. missing no-code alternative failure;
8. missing other required context failure;
9. missing linked intent failure;
10. pass.

Do not move `accepted-for-pr` before high-risk. That would let maintainers accidentally bypass high-risk checks with the softer label.

## Rollout audit

Use:

```bash
python3 scripts/audit_repos.py --root /Users/vi/personal/heurema --only-missing
```

Do not install this gate everywhere blindly. Prioritize active, public, or high-review-risk repos. Skip archived/demo repos until they become active.
