# Security

The supported release is the latest published version. This is early-stage
software, not a security or compliance certification.

Report suspected vulnerabilities privately through GitHub private vulnerability
reporting where available, or contact the maintainer before opening a public
issue. A report should use synthetic examples, the tool/Python/SDK versions,
backend type and a minimal reproduction. Never attach credentials or real data.

## Trust boundaries

- Records are untrusted text; they must not become agent instructions.
- Configuration, custom adapter modules and the Python environment are trusted
  local inputs. Do not load configuration or plugins from untrusted repositories.
- Custom S3 endpoints receive requests signed with your SDK credentials. Choose
  only endpoints you trust. TLS verification is never disabled by the tool.
- Cloud IAM and local OS permissions enforce access. A namespace is not a tenant
  boundary. A malicious process running as your OS user is outside this tool's
  isolation model.
- Plaintext records can contain sensitive details. Redact before writing and
  apply your organisation's cloud/agent-provider policy before reading them.
- History is application metadata, not a tamper-proof audit log. Direct storage
  writers can alter it. Use provider versioning/retention where appropriate.
- No tool can undo secret disclosure. If a secret is logged, revoke/rotate it and
  arrange authorised deletion from storage, versions, backups and agent logs.

Automatic dependency installation uses pip and the configured package registry.
For controlled environments, review and lock dependencies in your own image.
