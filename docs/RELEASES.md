# Release Notes

This repository currently has tags for `v0.1.0`, `v0.2.0`, and `v0.3.0`. GitHub Releases currently contains a published release entry for `v0.1.0`; `v0.2.0` and `v0.3.0` are tagged release points without matching GitHub Release entries.

## v0.3.0

Tag: `v0.3.0`

Summary:

- Added deterministic guardrails for AI instruction surfaces and prompt-injection-like additions.
- Expanded the policy schema, starter template, and real example policies for instruction-surface paths.
- Updated policy and rollout docs for the new high-risk classification behavior.

## v0.2.0

Tag: `v0.2.0`

Summary:

- Integrated Codex Review Gate into PR Intake Gate.
- Updated the bundled workflow, docs, and rollout guide for Codex Review thread enforcement.
- Added action inputs needed to configure bundled Codex Review behavior.

## Release model

- Use protected tags or exact commit SHAs for mature consumers.
- Keep GitHub Release notes aligned with public tags when a tag is intended as a stable consumer pin.
- Mention migration or behavior changes that affect workflow permissions, `pull_request_target`, action inputs, labels, templates, or policy files.
- Do not publish a tag for local-only WIP or unverified rollout experiments.
