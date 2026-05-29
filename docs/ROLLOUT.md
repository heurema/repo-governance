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
cp /path/to/repo-governance/templates/workflows/codex-review-ready.yml \
  .github/workflows/codex-review-ready.yml
```

`pr-intake-gate` runs Codex Review Gate by default and blocks active unresolved Codex Review threads that already exist. Keep it on `pull_request_target` events because it writes labels and comments.

`codex-review-ready` is the pre-merge Codex completion barrier. It writes a commit status on the PR head:

- `pending` before Codex has produced a current-head terminal signal;
- `failure` while active Codex threads remain unresolved;
- `success` after Codex has reviewed the current head and all active Codex threads are clear;
- `success` after an existing Codex clean `+1` survives the grace window with no active Codex threads unresolved.

Use `codex-review-ready` instead of strict `require-current-review` mode for branch protection. The ready workflow polls on PR/review events and also runs on a 5-minute schedule, so review-thread resolution and clean `+1` reactions can turn the status green without a manual rerun or dummy push.

Copy `templates/workflows/codex-review-gate.yml` only when the target repo intentionally wants a separate read-only `codex-review-gate` status context for thread-only visibility.

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

Unknown top-level keys fail fast. Validate local policy spelling before upgrading the central action; for example, `highrisk_path_globs` is rejected because the supported key is `high_risk_path_globs`.

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
python3 /path/to/repo-governance/tests/test_codex_review_ready.py
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
- `codex-review-ready`
- the repo's normal CI/docs checks

Recommended default branch protection:

- require branches to be up to date before merging;
- require `pr-intake-gate`, `codex-review-ready`, and the repo's normal CI/docs checks;
- include administrators for public/core repos;
- block force-pushes and branch deletion.

Require `codex-review-gate` separately only if the standalone workflow was added intentionally. Add the required context after the workflow has landed on the default branch; otherwise the rollout PR can block itself on a check that does not exist on the base branch yet.

The `codex-review-ready` workflow needs `contents: read`, `pull-requests:
read`, `issues: read`, and `statuses: write` permissions. Require the
`codex-review-ready` commit status context, not the
`codex-review-ready-reconcile` workflow job name.

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

## 8. Live test Codex Review readiness

Before relying on the Codex gates, open temporary PRs or use an existing test PR:

1. PR before Codex has reviewed the current head. Expected: `pr-intake-gate` can pass, but `codex-review-ready` stays `pending` and blocks merge.
2. Codex leaves unresolved inline threads. Expected: `codex-review-ready` is `failure`; bundled or standalone thread gate also fails when it observes the thread.
3. Resolve Codex threads while the ready event poller is still running. Expected: `codex-review-ready` turns `success` on the next poll.
4. Resolve Codex threads after the event poller has stopped. Expected: the next scheduled run turns `codex-review-ready` `success` within about 5 minutes.
5. Codex returns clean `+1` after the current head commit. Expected: the event poller or next scheduled run turns `codex-review-ready` `success`.
6. Push a change that makes an old Codex thread outdated. Expected: old outdated threads stop blocking, but `codex-review-ready` remains non-green until Codex reviews the new head.
7. PR with unresolved non-Codex review thread. Expected: Codex gates ignore it; branch protection conversation resolution may still block merge.
8. Optional standalone `codex-review-gate` with recommended `require-current-review: false`, before Codex has reviewed the current head. Expected: the thread-only check passes when no unresolved Codex threads exist.

## Updating consumer action refs

Use `scripts/rollout_release.py` when a repo-governance release needs to be rolled out across local consumer repository checkouts. The script only updates existing `heurema/repo-governance/actions/...` refs in workflow, template, README, and rollout-doc files. It does not add missing workflows, change branch protection, or alter consumer policy semantics.

Audit local consumers first:

```bash
python3 /path/to/repo-governance/scripts/rollout_release.py \
  --root ~/personal/heurema \
  --from v0.4.0 \
  --to v0.5.0 \
  --release-label v0.5.0 \
  --mode audit
```

Prepare local commits without pushing:

```bash
python3 /path/to/repo-governance/scripts/rollout_release.py \
  --root ~/personal/heurema \
  --from v0.4.0 \
  --to v0.5.0 \
  --release-label v0.5.0 \
  --mode patch \
  --apply
```

Open PRs for selected repos:

```bash
python3 /path/to/repo-governance/scripts/rollout_release.py \
  --root ~/personal/heurema \
  --from v0.4.0 \
  --to v0.5.0 \
  --release-label v0.5.0 \
  --mode pr \
  --repos signum,punk,goalrail \
  --apply
```

To pin consumers directly to the v0.5.0 release commit instead of the tag after the release exists:

```bash
V050_SHA="$(git -C /path/to/repo-governance rev-list -n 1 v0.5.0)"
python3 /path/to/repo-governance/scripts/rollout_release.py \
  --root ~/personal/heurema \
  --from v0.4.0 \
  --to "$V050_SHA" \
  --pin sha \
  --release-label v0.5.0 \
  --release-commit "$V050_SHA" \
  --mode patch \
  --apply
```

`patch` and `pr` are dry-run by default; pass `--apply` to modify consumer checkouts. Dirty repos and repos not on `main`, `master`, or the detected default branch are skipped unless `--allow-non-default` is passed.
