# Changelog

## 0.1.1

- Require first-use project onboarding: add a portable Papercuts reminder to
  the root `AGENTS.md`, or reuse/update an existing equivalent section.
- Include the note template, project-root detection guidance, and safeguards
  for existing instructions, read-only projects and pending storage setup.
- Clarify that agents perform this edit; CLI setup does not modify projects.

## 0.1.0

Initial public release.

- Self-contained `papercuts` Agent Skill and Python CLI.
- Guided local/cloud setup, optional private SDK runtime and offline/read-only/
  explicit conditional-write diagnostics.
- Local filesystem, AWS S3, R2/S3-compatible and GCS storage; SQLite reference
  adapter and a documented custom-factory interface.
- Capture, substring search, notes, evidence-backed terminal dispositions,
  bounded owner-backed deferrals and deliberate batch closure.
- Stable Markdown object keys with conditional updates, revision-aware tools,
  explicit partial-batch reporting and portable JSONL import/export.
- Synthetic tests, official-SDK contract checks and macOS/Linux CI.

Live cloud-provider behaviour is not certified by mocked/SDK tests. Run the
explicit write probe against an approved destination before shared use.
