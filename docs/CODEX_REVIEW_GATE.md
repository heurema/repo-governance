# Codex Review Gate

Codex Review Gate is a reusable GitHub Action that fails a pull request when active Codex Review inline threads are unresolved.

It is meant to complement GitHub branch protection conversation resolution:

- branch protection blocks unresolved review conversations generically;
- this gate makes Codex Review backlog visible as a named required check;
- the step summary lists the unresolved Codex Review thread URLs.

## Security model

The action is designed for trusted workflow contexts such as `pull_request_target`.

Rules:

1. Do not checkout PR head code before this gate runs.
2. Do not import, install, execute, or shell-evaluate PR head code.
3. Read review-thread metadata through GitHub GraphQL API.
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

## Target workflow

Copy `templates/workflows/codex-review-gate.yml` into the consuming repository.
After the workflow has run once on the default branch, require this status check in branch protection:

```text
codex-review-gate
```

Use a pinned action reference in mature repositories:

```yaml
uses: heurema/repo-governance/actions/codex-review-gate@<commit-sha>
```

## Inputs

| Input | Default | Meaning |
| --- | --- | --- |
| `github-token` | empty | Token used for GitHub GraphQL reads. Use `${{ secrets.GITHUB_TOKEN }}` in GitHub Actions. |
| `review-author-logins` | `chatgpt-codex-connector` | Comma-separated author logins whose unresolved threads should block. |
| `ignore-outdated` | `true` | When `true`, outdated unresolved threads do not block. |

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
