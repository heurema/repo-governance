# Codex Review Gate

Codex Review Gate is a reusable GitHub Action that fails a pull request when active Codex Review inline threads are unresolved.

It is meant to complement GitHub branch protection conversation resolution:

- branch protection blocks unresolved review conversations generically;
- this gate makes Codex Review backlog visible as a named required check;
- the step summary lists the unresolved Codex Review thread URLs.

`actions/pr-intake-gate` runs this gate by default after intake checks. In bundled mode, `pr-intake-gate` requires a Codex Review completion signal for the current PR head before passing. Use the standalone action only when a repository needs a separate `codex-review-gate` status context.

The recommended standalone mode blocks actual unresolved Codex review threads
without requiring a fresh Codex Review artifact on every PR head. Enable
standalone `require-current-review` only in repositories that have a reliable
external trigger which always submits a Codex review artifact for every PR head.

## Security model

The action is designed for trusted workflow contexts such as `pull_request_target`.

Rules:

1. Do not checkout PR head code before this gate runs.
2. Do not import, install, execute, or shell-evaluate PR head code.
3. Read review-thread metadata through GitHub GraphQL API, and review/reaction metadata through GitHub REST API when current-review completion is required.
4. Keep the action read-only: it does not write labels, comments, or repository state.
5. In consuming repositories, pin this action to a protected tag or exact commit SHA.

## What blocks

By default, the action fails when a review thread satisfies all of these:

- the thread is unresolved;
- the thread is not outdated;
- at least one comment in the thread is authored by `chatgpt-codex-connector`.

Resolved threads pass.
Outdated unresolved threads pass by default because they refer to stale diffs.
Unresolved threads from other reviewers are ignored by default.

The author list is configurable with `review-author-logins`.

When `require-current-review` is explicitly enabled, the action also requires
one of these current-head completion signals before passing:

- a pull-request review by a configured Codex author on the current head SHA;
- a clean-review `+1` reaction by a configured Codex author after the current PR
  update timestamp.

If neither signal appears before `wait-seconds` expires, the check fails. Do not
use this mode as a required branch-protection check unless the Codex Review
producer is guaranteed to run for every PR head; otherwise the gate can block all
merges even when there are no unresolved Codex threads.

When active Codex findings exist, the check summary links the blocking review
threads. After applying fixes, resolve each linked GitHub review conversation or
push a new commit that makes stale diff threads outdated. GitHub branch
protection with required conversation resolution may still block merge until the
conversation is resolved.

## Bundled PR Intake Gate mode

Existing repositories can usually keep one required status context:

```text
pr-intake-gate
```

In that mode, update the existing `actions/pr-intake-gate` reference to a release or commit that includes Codex Review Gate. Repositories pinned to an older commit SHA will keep running the older code until their workflow reference is updated.

The bundled mode can be disabled explicitly:

```yaml
with:
  codex-review-gate: 'false'
```

The bundled current-review wait can be tuned or disabled explicitly:

```yaml
with:
  codex-review-require-current-review: 'true'
  codex-review-wait-seconds: '480'
  codex-review-poll-interval-seconds: '10'
```

Set `codex-review-require-current-review: 'false'` only when the repository does
not have a reliable Codex Review producer for every PR head.

## Standalone target workflow

Copy `templates/workflows/codex-review-gate.yml` into the consuming repository.
After the workflow has run once on the default branch, require this status check in branch protection:

```text
codex-review-gate
```

Use a pinned action reference in mature repositories:

```yaml
uses: heurema/repo-governance/actions/codex-review-gate@<commit-sha>
```

Required workflow permissions:

```yaml
permissions:
  contents: read
  issues: read
  pull-requests: read
```

`issues: read` is required when `require-current-review` uses a clean Codex
`+1` issue reaction as the completion signal.

## Inputs

| Input | Default | Meaning |
| --- | --- | --- |
| `github-token` | empty | Token used for GitHub GraphQL reads. Use `${{ secrets.GITHUB_TOKEN }}` in GitHub Actions. |
| `review-author-logins` | `chatgpt-codex-connector` | Comma-separated author logins whose unresolved threads should block. |
| `ignore-outdated` | `true` | When `true`, outdated unresolved threads do not block. |
| `require-current-review` | `false` | When `true`, the gate must see a Codex result for the current PR head before passing. |
| `wait-seconds` | `0` | Seconds to wait for the current Codex result before failing. |
| `poll-interval-seconds` | `10` | Seconds between polling attempts while waiting. |

Bundled `pr-intake-gate` defaults are stricter than standalone defaults:
`codex-review-require-current-review: 'true'` and
`codex-review-wait-seconds: '480'`.

## Local fixture test

The engine supports `CODEX_REVIEW_GATE_THREADS_JSON` for local tests without GitHub API calls:

```bash
GITHUB_EVENT_PATH=/tmp/event.json \
CODEX_REVIEW_GATE_THREADS_JSON='[]' \
python3 actions/codex-review-gate/codex_review_gate.py
```

Run the repository tests:

```bash
python3 tests/test_codex_review_gate.py
```

## Rollout test cases

Use temporary PRs in the consuming repo before requiring the check:

1. No Codex threads: check passes.
2. Unresolved active Codex thread: check fails and prints the thread URL.
3. Resolve the Codex thread: branch protection conversation resolution unblocks the PR; rerun this check if GitHub did not trigger it automatically.
4. Outdated Codex thread after new push: check passes.
5. Unresolved non-Codex thread: this gate passes; branch protection conversation resolution may still block merge.
6. Standalone workflow with recommended `require-current-review: false` and no Codex threads: check passes without waiting for an external Codex review artifact.
7. Optional strict mode with `require-current-review: true` and no current Codex result: check waits, then fails.
8. Optional strict mode with `require-current-review: true` and Codex returns clean `+1` for the current PR update: check passes.
