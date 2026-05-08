# Rollout guide

Use this checklist when adding repository governance gates to another repository.

## 1. Classify the target repo

Before editing, classify the repo:

- Active/public/high-risk: install the gate and branch protection now.
- Active/private/internal: install if outside contributors or multiple agents can open PRs.
- Demo/archive: do not add branch protection unless the repo becomes active.

## 2. Add local files

From the target repository root:

```bash
mkdir -p .github/workflows
python3 /path/to/repo-governance/scripts/render_repo_policy.py \
  --project-name "Project Name" \
  --output .github/pr-intake-gate.yml
cp /path/to/repo-governance/templates/workflows/pr-intake-gate.yml \
  .github/workflows/pr-intake-gate.yml
```

`pr-intake-gate` runs Codex Review Gate by default and blocks active unresolved Codex Review threads without requiring a current-head Codex Review completion signal. Keep it on `pull_request_target` events because it writes labels and comments. Configure a bounded resolution wait so the same run can turn green after Codex threads are resolved; GitHub Actions does not emit a review-thread-resolution trigger. Copy `templates/workflows/codex-review-gate.yml` when the target repo intentionally wants a separate read-only `codex-review-gate` status context that can rerun on review activity. Do not require a current Codex Review artifact in either mode unless the target repo has a reliable external trigger that submits one for every PR head.

Append `templates/pull-request-template-sections.md` to the repo's PR template, or merge equivalent sections into the existing template.

## 3. Tune policy

Edit `.github/pr-intake-gate.yml` in the target repo.

Minimum tuning:

- `project.name`: human-readable project name.
- `trivial.allowed_path_globs`: paths external contributors can change directly if the PR is tiny.
- `high_risk_path_globs`: workflows, dependencies, runtime code, governance, product canon, security-sensitive paths.
- `instruction_surface.path_globs`: files AI agents may read as instructions or operating context, such as `AGENTS.md`, PR templates, product canon, command docs, eval specs, prompt templates, work ledgers, or project memory.
- `prompt_injection.text_path_globs`: text-like paths where obvious prompt-injection-like additions should turn an external PR into `high-risk`.
- `external_context.required_sections`: sections external non-trivial PRs must fill.
- `linked_intent.accept_patterns`: local issue, discussion, ADR, research, goal, or report references.
- `bot_comment.marker`: unique marker for that repo.

Do not move repo-specific risk decisions into this central repo. The local policy is the reviewable source of truth.

## 4. Install labels

Dry run first:

```bash
python3 /path/to/repo-governance/scripts/install_labels.py \
  --repo owner/name \
  --policy .github/pr-intake-gate.yml \
  --dry-run
```

Then apply:

```bash
GITHUB_TOKEN="$GITHUB_TOKEN" \
python3 /path/to/repo-governance/scripts/install_labels.py \
  --repo owner/name \
  --policy .github/pr-intake-gate.yml
```

The workflow can also create missing labels lazily, but explicit bootstrap makes rollout easier to inspect.

## 5. Test with fixtures locally

Use dry-run fixtures before opening a PR:

```bash
python3 /path/to/repo-governance/tests/test_pr_intake_gate.py
python3 /path/to/repo-governance/tests/test_codex_review_gate.py
```

For target-specific testing, create a small event JSON and run:

```bash
GITHUB_EVENT_PATH=/tmp/event.json \
PR_INTAKE_GATE_CHANGED_FILES_JSON='[{"filename":".github/workflows/ci.yml","additions":1,"deletions":0}]' \
PR_INTAKE_GATE_AUTHOR_PERMISSION=none \
PR_INTAKE_GATE_DRY_RUN=1 \
python3 /path/to/repo-governance/actions/pr-intake-gate/pr_intake_gate.py \
  --policy .github/pr-intake-gate.yml
```

Expected high-risk external result: exit `1`, verdict `high-risk`.

## 6. Enable branch protection

After the workflows have run at least once on the default branch, require status checks:

- `pr-intake-gate`
- the repo's normal CI/docs checks

Recommended default branch protection:

- require branches to be up to date before merging;
- require `pr-intake-gate` and the repo's normal CI/docs checks;
- include administrators for public/core repos;
- block force-pushes and branch deletion.

Require `codex-review-gate` separately only if the standalone workflow was added intentionally. Add the required context after the workflow has landed on the default branch; otherwise the rollout PR can block itself on a check that does not exist on the base branch yet.

The standalone Codex Review Gate workflow needs `contents: read`,
`pull-requests: read`, and `issues: read` permissions. The `issues: read`
permission is required for strict current-review mode because a clean Codex
review may be represented as a `+1` issue reaction.

## 7. Live test both paths

Open two temporary PRs:

1. Trusted maintainer/admin PR touching a high-risk path. Expected: pass with `trusted_author: true`.
2. External fixture/fork or simulated external author. Expected outcomes:
   - high-risk path: fail with `intake/high-risk`;
   - instruction-surface path such as `AGENTS.md`: fail with `intake/high-risk`;
   - small docs PR adding text like `Ignore previous instructions`: fail with `intake/high-risk`;
   - non-trivial missing sections: fail with context labels;
   - non-trivial full context plus linked intent: pass;
   - `intake/accepted-for-pr`: pass only for non-high-risk PRs;
   - `maintainer/override-intake`: pass even for high-risk PRs.

## 8. Live test Codex Review Gate

Before relying on the bundled `pr-intake-gate` Codex Review behavior, open temporary PRs or use an existing test PR:

1. PR with no Codex Review threads before Codex has finished current-head review. Expected: `pr-intake-gate` passes without waiting for a separate current-review artifact.
2. PR with an active unresolved inline Codex Review thread. Expected: `pr-intake-gate` fails and the step summary links to the thread.
3. Resolve the Codex thread while the configured resolution polling window is still open. Expected: branch protection conversation resolution unblocks the PR and the check passes on the next poll.
4. Push a change that makes the Codex thread outdated. Expected: the check passes by default.
5. PR with unresolved non-Codex review thread. Expected: the Codex Review phase passes, while branch protection conversation resolution may still block merge.
6. PR with standalone `codex-review-gate` and recommended `require-current-review: false`, before Codex has reviewed the current head. Expected: the check passes when no unresolved Codex threads exist.
7. Optional strict-mode PR with standalone `codex-review-gate` and `require-current-review: true`, before Codex has reviewed the current head. Expected: the check waits, then fails instead of going green.
8. Optional strict-mode PR with standalone `codex-review-gate` and a clean Codex `+1` reaction after the current PR update. Expected: the check passes.
