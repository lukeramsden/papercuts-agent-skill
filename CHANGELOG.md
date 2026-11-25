# Changelog

## 0.2.1

- `setup --install-deps` now saves the configuration in one run. It loads the SDK
  it just installed ahead of the system's packages, so a distribution's own older
  copy (such as Ubuntu's `google.protobuf`) no longer breaks the storage check
  with "GCS dependencies missing".

## 0.2.0

- Every record has a `repo` field naming the repository it is about, as a
  normalised `HOST/OWNER/NAME` (for example `github.com/example-org/example-repo`).
  SSH, `ssh://` and HTTPS remotes are accepted; credentials are never stored.
- `add` resolves it from `--repo`, then `PAPERCUTS_REPO`, then the current
  checkout's `origin` (or only) Git remote, and fails if none is found.
  `--repo none` deliberately records a papercut without a repository.
- `list`, `search` and `export` take `--repo` as an exact filter; `show`, JSON
  output and import/export carry the field.
- Schema version stays 1: `repo` is optional, older records read as `null`,
  and older clients ignore the new field.

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
