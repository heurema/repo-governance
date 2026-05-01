# Prompt injection guardrails for PR intake

Prompt injection is relevant to repository governance because AI-assisted repositories often treat documentation, PR templates, product canon, command docs, prompt templates, and work ledgers as agent context.

## Research basis

- OWASP GenAI LLM01 defines prompt injection as user prompts altering model behavior in unintended ways, and describes indirect prompt injection from external sources such as websites or files. It recommends segregating and identifying external/untrusted content and requiring human approval for high-risk actions.
- OWASP LLM Prompt Injection Prevention Cheat Sheet lists malicious instructions hidden in external content as remote/indirect prompt injection, including code comments, documentation, commit or merge request descriptions, issue descriptions, web pages, documents, email bodies, hidden text, and encoded/obfuscated instructions.
- GitHub Security Lab warns that external pull requests are untrusted input, especially in `pull_request_target` workflows.
- GitHub Actions hardening guidance treats pull request metadata such as titles as untrusted input and recommends safe handling rather than direct execution/interpolation.

## Policy stance

Documentation-only changes can be high-risk when they affect AI-readable instruction surfaces or add obvious prompt-injection-like text.

The PR Intake Gate therefore uses two deterministic layers for external PRs:

1. `instruction_surface.path_globs` marks configured AI-readable files as high-risk even when they are documentation.
2. `prompt_injection` scans added lines in text-like files for obvious prompt-injection-like patterns and hidden text markers.

This is not a complete scanner. It is an intake guardrail: suspicious external PRs become `high-risk` and need maintainer attention. They are not automatically rejected.

## Sources

- https://genai.owasp.org/llmrisk/llm01-prompt-injection/
- https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html
- https://securitylab.github.com/resources/github-actions-preventing-pwn-requests/
- https://docs.github.com/en/actions/security-for-github-actions/security-guides/security-hardening-for-github-actions
