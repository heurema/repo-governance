# Policy reference

Each consuming repository owns its local policy at `.github/pr-intake-gate.yml`.

The central action deliberately supports a small YAML subset: nested mappings, scalar lists, quoted or unquoted scalars, booleans, nulls, and integers. Do not use anchors, tabs, multiline strings, or flow-style YAML.

## Decision order

The gate evaluates PRs in this order:

1. `maintainer/override-intake` label: pass.
2. Trusted author by GitHub permission: pass.
3. Trusted author fallback by `author_association`: pass only when permission could not be resolved.
4. External PR touching `high_risk_path_globs`, `instruction_surface.path_globs`, or suspicious prompt-injection-like additions: fail as `high-risk`.
5. External trivial PR: pass if changed lines are under the threshold and all paths are in `trivial.allowed_path_globs`.
6. `intake/accepted-for-pr`: pass only if the PR is external and non-high-risk.
7. Missing `No-code alternative`: fail as `no-code-alternative`.
8. Missing other required external context sections: fail as `needs-more-context`.
9. Missing linked intent: fail as `needs-linked-intent`.
10. Otherwise pass.

## Required sections

`external_context.required_sections` names headings that must be present in the PR body as Markdown headings (`##` through `######`) and must contain meaningful text.

Values treated as empty include:

- `-`
- `N/A`
- `NA`
- `TBD`
- `TODO`

## Trusted authors

`trusted_authors.permissions` uses GitHub's collaborator permission API. Recommended values:

```yaml
trusted_authors:
  permissions:
    - 'admin'
    - 'maintain'
    - 'write'
```

`trusted_authors.fallback_author_associations` is only used when collaborator permission is unavailable. This preserves the useful fallback for owner/member/collaborator PRs without trusting outside contributors.

## Labels

`label_details` is used by both the action and `scripts/install_labels.py`.

The action manages only verdict labels:

- `intake/pass`
- `intake/needs-linked-intent` or legacy `intake/needs-issue`
- `intake/needs-more-context`
- `intake/no-code-alternative`
- `intake/high-risk`

It does not remove control/signal labels:

- `intake/accepted-for-pr`
- `intake/first-time-contributor`
- `maintainer/override-intake`

## `accepted-for-pr` vs `override`

Use `intake/accepted-for-pr` when a maintainer accepts the intent of a non-high-risk external PR and wants ordinary review to proceed.

Use `maintainer/override-intake` only when a maintainer explicitly accepts responsibility for bypassing intake, including high-risk PRs.

## AI instruction surfaces and prompt injection

Documentation-only PRs are not automatically safe in AI-assisted repositories. Files that agents, coding assistants, or PR automation read as instructions are treated as high-risk when changed by external contributors.

Use `instruction_surface.path_globs` for these paths. Common examples:

- `README.md`, `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, and `SKILL.md`;
- PR and issue templates;
- repository-local intake policy files;
- product canon, operating docs, contracts, eval specs, work ledgers, command docs, prompt templates, and other project-specific agent context.

`prompt_injection.enabled` is `true` by default. It scans added lines in text-like files for obvious prompt-injection patterns such as instructions to ignore previous/system/developer instructions, reveal prompts, switch into developer/admin/jailbreak mode, exfiltrate secrets, hidden text, and invisible Unicode control characters.

This scan is intentionally deterministic and conservative. It is not a complete security scanner and can produce false positives when a PR intentionally documents prompt-injection examples. For external PRs, that is acceptable: the result is `high-risk`, not an automatic rejection, and a maintainer can review or explicitly override.
