# Security Policy

This repository contains shared GitHub Actions governance tooling. Treat reports about workflow execution, `pull_request_target`, token permissions, checkout behavior, or action pinning as security-sensitive.

## Supported versions

Security fixes target the default branch first. Protected release tags are useful for consumers, but mature consumers should pin this action to a reviewed tag or exact commit SHA and update intentionally.

## Reporting a vulnerability

Use GitHub private vulnerability reporting if it is available for this repository. If it is not available, open a minimal public issue that says a private vulnerability report is needed, without exploit details, secrets, private tokens, or sensitive repository data.

Include:

- affected action, workflow, script, or template;
- reproduction steps that do not require secrets;
- expected impact;
- suggested mitigation if known.

## Security boundaries

Do not propose fixes that checkout, import, install, execute, or shell-evaluate pull request head code from a `pull_request_target` workflow. PR metadata, comments, labels, changed files, and review state must be read through GitHub APIs or trusted base-branch files.
