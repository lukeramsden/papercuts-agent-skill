# Papercuts Agent Skill

**Capture small annoyances while working. Fix them in batches without creating
an endless backlog.**

A portable [Agent Skill](https://agentskills.io/) and self-contained `papercuts`
CLI for developers and local/cloud agents. Log confusing docs, broken links,
flaky commands, dead-end tool calls and repeated workarounds. Search for existing
reports, add evidence, then close with a decision or defer to a named owner.

No hosted service, agent-harness dependency, or organisation-specific tooling.
Your storage, your credentials, your data. MIT licensed.

## Install

```bash
npx skills add lukeramsden/papercuts-agent-skill --skill papercuts
```

Or from a checkout:

```bash
npx skills add /path/to/papercuts-agent-skill --skill papercuts
```

**Requirements:** macOS or Linux, Python 3.10+. Local files and the optional
SQLite adapter use only the standard library. Cloud SDKs can be installed by
setup into a private virtual environment, or supplied in your own image.

Commands below run from `skills/papercuts/` in a checkout, or the installed skill
directory. You can invoke its `papercuts` executable by absolute path anywhere.

## First use: guided setup

```bash
./papercuts setup
./papercuts doctor --write-test
```

Like a first-use account wizard, setup walks through **choosing storage,
creating/selecting a private bucket, authenticating through the provider,
choosing a namespace, and checking access**. Cloud account/Console steps remain
manual. You do not need to know the config format before installing.

Setup never asks you to paste secrets, provisions billable resources, or changes
IAM/public-access settings. It can install the provider SDK with consent and
checks listing before saving. The separate explicit write test verifies
conditional create/update semantics and removes its temporary diagnostic object.

For local files, setup can be one command:

```bash
./papercuts setup --storage "$HOME/.local/share/papercuts" --namespace my-project --yes
```

Read the [setup guide](skills/papercuts/references/setup.md) for AWS SSO, GCS ADC,
R2 credentials, IAM permissions, headless runners and troubleshooting.

## Project onboarding: keep the reminder in `AGENTS.md`

When you first invoke the skill in a project, it instructs the agent to add a
small **“Papercuts (log the friction you hit)”** section to the project's root
`AGENTS.md`, creating the file if needed. Future agents that read that file are
prompted to use the skill when they encounter friction, without waiting for
another explicit invocation.

The note covers quick capture, checking for duplicates, and closing or deferring
triaged reports. Existing equivalent notes are reused, unrelated instructions
are preserved, and no machine-specific skill paths or credentials are added.
Storage setup is separate; the note can be installed while setup is pending.
The agent reports the edit but does not commit it automatically. Read-only
projects or users who decline receive the snippet instead.

This is an instruction for the agent using the skill, **not a side effect of
running the CLI's `setup` command**. It relies on the harness reading `AGENTS.md`;
it is not an automatic background hook.

## Storage choices

| Storage | Use it for |
| --- | --- |
| **Local files** | Local agents, developers, same-host workers, persistent agent volumes |
| **AWS S3** | Shared teams/CI with IAM roles, SSO or workload identity |
| **Cloudflare R2** | Shared cloud-agent storage using the S3 API |
| **Other S3-compatible stores** | Providers that enforce conditional writes; explicit HTTPS endpoint |
| **Google Cloud Storage** | GCP teams/agents with ADC or workload identity |
| **SQLite reference adapter** | A compact, transactional single-host store; explicit config |
| **Custom adapters** | Your own Postgres, Azure Blob or authenticated HTTP service |

S3/R2/GCS adapters are implemented, not shell wrappers. They use the official
SDK credential chains and conditional writes. Custom providers must enforce
the same contract; “S3-compatible” by itself is not enough.

```bash
# AWS (authenticate the profile yourself first)
./papercuts setup --storage s3://YOUR-BUCKET/papercuts --namespace my-project \
  --region us-east-1 --profile papercuts --install-deps --yes

# R2 (credentials in an approved profile/runner secret store)
./papercuts setup --storage r2://YOUR-BUCKET/papercuts --namespace my-project \
  --endpoint https://ACCOUNT_ID.r2.cloudflarestorage.com --install-deps --yes

# GCS (configure ADC or a runner identity first)
./papercuts setup --storage gs://YOUR-BUCKET/papercuts --namespace my-project \
  --install-deps --yes
```

These are alternative **first-time** configurations. Use separate config files
for multiple stores; `setup --replace` intentionally changes an existing config
without migrating data. Namespaces organise records but do not enforce access
control. Use IAM/bucket separation for different trust boundaries.

For ephemeral agents, configuration can come from environment variables:

```bash
export PAPERCUTS_STORAGE=s3://YOUR-BUCKET/papercuts
export PAPERCUTS_NAMESPACE=my-project
export PAPERCUTS_REGION=us-east-1
export PAPERCUTS_AUTHOR=ci-agent
export PAPERCUTS_SOURCE=agent
/path/to/skill/papercuts add 'The lint failure points to a removed config file.'
```

Bake the SDK into the image; inject a scoped runtime identity, not a developer's
credentials. See [storage and alternatives](skills/papercuts/references/storage.md)
and [the adapter contract](skills/papercuts/references/adapters.md).

## Everyday use

```bash
./papercuts search 'broken quickstart link'       # open + closed records
./papercuts add --source agent -t docs 'Quickstart links to a removed install page.'
./papercuts list                                 # open records
./papercuts list --deferred --blocked-on tools-team
./papercuts show ID                              # record + opaque revision
./papercuts note ID 'Seen again in the Linux setup path.'

./papercuts close ID --as fixed --link https://example.com/pull/42
./papercuts close ID --as escalated --link https://example.com/issues/123
./papercuts close ID --as wontfix --reason 'Upstream limitation; workaround documented.'
./papercuts close ID --as duplicate --duplicate-of CANONICAL_ID
./papercuts defer ID --blocked-on tools-team --reason 'Waiting for the approved replacement command.'
```

`add` and `note` also accept a sanitised UTF-8 body from stdin. `--source` on
`add` overrides its source; configure `PAPERCUTS_SOURCE=agent` for all mutations.
Data output is JSON, with `show --markdown` and JSONL `export` as explicit
exceptions. Errors go to stderr; exit 3 means a revision conflict.

### No endless deferral loop

- Every triaged papercut is **closed with evidence** or **still open with an
  owner and a genuinely new deferral reason**.
- `fixed`/`escalated` need an evidence/tracker link (`http(s)` or a `urn`, such as
  `urn:git:commit:HASH`). `wontfix` needs a reason; `duplicate` needs a canonical ID.
- At most three deferrals; repeated reasons are rejected, including punctuation
  or case changes and cycling back to an earlier reason. No force bypass.
- Don't re-log a deferred entry: `add -t deferred` is rejected. Append another
  occurrence with `note` instead.
- Closed records remain searchable. Their disposition cannot be overwritten;
  later notes can add context without rewriting the decision.

### Deliberate batches

```bash
./papercuts sweep --id ID1 --id ID2 --as fixed --link https://example.com/pr/42 --dry-run
./papercuts sweep --id ID1 --id ID2 --as fixed --link https://example.com/pr/42 --yes
```

A sweep is **batch closure with a disposition**, not silent archiving of
unresolved work. Select explicit IDs or deliberately use `--all`, optionally
with tags/`--before`. Deferred entries are skipped unless explicitly included.
Batches preflight but are not transactions; failures report completed and
pending IDs. Prefer IDs after reviewing a preview.

### Conflict-safe updates

Each entry is one Markdown object for its entire life. Closing changes its state
at that same key; it does not copy/delete between inbox and archive folders.
This avoids losing concurrent updates during a move.

S3/R2 use ETag conditions; GCS uses generation conditions. Local files use a
same-host lock and atomic rename. Pass `--if-revision` from a previous `show` to
protect decisions spanning commands. On a conflict, reread before retrying.

Cloud writes can have uncertain outcomes after a network failure. The tool does
not claim exactly-once delivery or cross-record transactions. Search before
retrying a failed `add`; do not assume every pending batch write failed.

## Portability and privacy

- No telemetry, hosted backend or bundled account credentials.
- Markdown records with JSON frontmatter, versioned schema and explicit history.
- `export`/`import` migrate between backends, preserving IDs and dispositions.
  Import is create-only, skips identical data and refuses conflicting overwrites.
- User config and SDK runtime live outside the skill directory. Installing or
  updating the skill does not erase the inbox.
- Local files/SQLite are plaintext with owner-only permissions for newly created
  files. Cloud encryption, IAM, retention, backup and access logs are your policy.
- Treat entries as untrusted input, not commands. Redact sensitive information
  before recording it; the tool does not promise automatic secret detection.
- Reading records with an agent puts their content into its context. Your
  agent/model provider's data policies still apply.
- The source repository is public; **your feedback records must not be** unless
  you explicitly intend that. No real project records or credentials are bundled.

## Limits and validation

This is a small feedback backlog, not a log database. List/search scan the
namespace; `--limit` bounds output, not requests. Reads are not a whole-store
snapshot. Records including history are limited to 1 MiB. Local/SQLite storage
is single-host, not distributed filesystem coordination.

Automated tests cover lifecycle rules, setup/configuration, standalone skill
installation, local multi-process conflicts, SQLite, cloud request contracts,
pagination, SDK parameter compatibility, diagnostic probes, import/export and
partial batches. Tests use synthetic data and no cloud credentials.

**Live validation:** local filesystem/SQLite workflows and SDK dependency setup
are exercised. Real AWS/R2/GCS buckets are **not** claimed as live-tested by this
release; mock/SDK tests cannot prove a particular provider's server behaviour.
Run `doctor --write-test` against your approved destination before shared use.

## Development and releases

```bash
make check
# Optional official-SDK tests (otherwise skipped):
python3 -m pip install 'boto3>=1.40,<2' 'google-cloud-storage>=3,<4'
make test
```

The skill directory is self-contained; tests/docs outside it are not required
at runtime. CI runs supported Python versions and the cloud-SDK contract tests.
See [CONTRIBUTING.md](CONTRIBUTING.md) for release and adapter checks.

```text
skills/papercuts/
├── SKILL.md
├── papercuts                 # CLI launcher
├── doctor                    # convenience wrapper
├── papercuts_lib/            # lifecycle, config/setup and storage adapters
├── references/              # setup, storage and adapter documentation
├── LICENSE
└── NOTICE
tests/                       # synthetic tests; no production data
```

## License

[MIT](LICENSE). Independent, unofficial software; see [NOTICE](NOTICE).
