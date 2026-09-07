# Storage, consistency and portability

## Pick a backend

| Backend | Good fit | Limits |
| --- | --- | --- |
| Local files | One developer, local agents, same-host parallel agents | Persistent disk required; locking is not for multiple hosts |
| Local files on a persistent runner volume | Container restarts without losing feedback | Volume must outlive the agent; same-host locking caveat still applies |
| S3 | Shared teams, CI, AWS-hosted agents | IAM, network, request/storage costs |
| R2 | Shared agents without tying storage to the compute cloud | Bucket-scoped credentials; verify S3 conditional-write support |
| GCS | GCP agents, attached service accounts / workload identity | ADC setup and generation-precondition support |
| Included SQLite adapter | A compact database on one persistent local host | Explicit adapter config; no additional packages |
| Trusted custom adapter | Existing Postgres, HTTP service, Azure Blob, etc. | You must implement and test the conditional-write contract |

No database server, hosted service, MCP server or agent-specific plugin is
required. Any agent that can run a command can use the CLI. A shell-free agent
can wrap the same `Papercuts` service and adapter API in its own approved tools.

## Data format and layout

```text
STORAGE_ROOT_OR_BUCKET_PREFIX/
└── NAMESPACE/
    └── v1/
        ├── records/
        │   └── pc_TIMESTAMP_RANDOM_UUID.md
        └── diagnostics/                 # temporary, explicit doctor probes only
```

Each record is Markdown with **JSON frontmatter**, a YAML-compatible mapping:

```markdown
---
{
  "schema_version": 1,
  "id": "pc_20260101T120000000000Z_0123456789abcdef0123456789abcdef",
  "created_at": "2026-01-01T12:00:00.000000Z",
  "updated_at": "2026-01-01T12:00:00.000000Z",
  "author": "example-agent",
  "source": "agent",
  "tags": ["docs"],
  "status": "open",
  "deferrals": 0,
  "history": []
}
---
The installation guide links to a removed configuration file.
```

JSON avoids an extra YAML dependency and safely quotes multiline metadata.
The parser accepts this defined format, **not arbitrary YAML frontmatter**.
IDs use UTC microseconds plus a full random UUID. User text is never a storage
key. Namespaces are explicit and stable, not guessed from branch names or
working directories.

Closing changes `status`, adds disposition/evidence/attribution and appends a
history event. Deferral adds the owner/reason and increments the count. Notes
append history without changing the original body. These are **application
history**, not a tamper-proof audit log: anyone with direct write permission
can alter the object. Enable provider versioning/retention if you need recovery.

An entry keeps one key for its entire lifetime. Open/closed are fields, not
inbox/archive folders. This avoids copy-then-delete failure windows and makes
closed records easy to find. The trade-off is that list/search currently scan
all records in the selected namespace, including closed ones.

## Concurrency and failures

- Create is conditional on absence. Updates are conditional on the revision
  read before the change. There are **no unconditional overwrites**.
- Local storage uses a per-root `flock`, SHA-256 revisions, fsync and atomic
  rename. Cooperating writers on one host serialize safely. It does not defend
  against another process with the same OS user's permissions deliberately
  changing symlinks or files outside the protocol. Existing symlinks under the
  configured root are rejected.
- S3/R2 use ETags with `If-Match` / `If-None-Match`. GCS uses object generations
  with `if_generation_match`, including `0` for create-only writes.
- Supply `--if-revision` from `show` to protect reasoning spanning multiple
  commands. A conflict is an instruction to **reread**, not blindly retry.
- List/search/export are per-object reads, not consistent whole-store snapshots.
  Concurrent edits may appear before or after their read. New arrivals during a
  paginated scan may not appear. Quiesce writers if a precise migration snapshot
  is required.
- Batches preflight all selected records and capture revisions, but are **not
  transactions across objects**. A change during a batch can stop it after
  earlier records committed. `completed` and `pending` are returned on stderr;
  pending includes the record whose result may be uncertain.
- Duplicate references are checked when closing, not protected by cross-record
  transactions. Concurrent canonicalisation needs coordination; do not have two
  agents designate each other's records as duplicates simultaneously.
- Provider SDKs use bounded retries/timeouts. A timeout, disconnect or process
  interruption during a write can leave its result unknown. Read/search before
  retrying. An `add` retry generates a new ID, so it can create a duplicate.
- Auth, permission, network and missing-bucket failures propagate as errors,
  never a clean inbox or a silent local fallback.

There is no delete/reopen command for records. This prevents accidental history
loss, but is not a retention policy. Administrators must manage deletion,
backup, legal retention and provider lifecycle rules directly when needed.

## Size, search and cost

A record including history is limited to 1 MiB. Prefer a short explanation and
safe evidence link over raw logs. Too many repeated notes can eventually hit
that limit; summarise the investigation externally rather than endlessly
appending output.

The current implementation is intended for small feedback backlogs, not an
observability datastore. List/search fetch every record in a namespace;
`--limit` bounds output, **not API requests**. Search is a case-insensitive
substring over body/tags/notes/reasons. It is not semantic search, and it does
not guarantee duplicate detection. Use namespace separation and an indexed
adapter/service if the number of objects or request costs become material.

## Export, import and changing providers

Export is portable schema-v1 JSONL, not a dump of SDK metadata or revisions.
It includes open and closed records, IDs, history and attribution. These are
private user data: do not commit or publish exports. Use an owner-only directory
and restrictive umask, because shell redirection controls the export file mode.

```bash
umask 077
./papercuts --config /private/source.json export > /private/papercuts-backup.jsonl
# Check exit status: do not treat a failed/interrupted export as a backup.
./papercuts --config /private/destination.json import /private/papercuts-backup.jsonl --dry-run
./papercuts --config /private/destination.json import /private/papercuts-backup.jsonl --yes
```

Import validates the entire input before writing, preserves IDs/content, skips
identical existing records, and refuses to overwrite different records. Writes
are create-only. A partial import can be resumed with the same file. Import and
export hold the selected records in memory; split very large migrations or use
a purpose-built storage migration tool.

Pause writers for a definitive migration, export, import into the new namespace,
compare counts and selected records, then switch configs. Changing a config does
not move or delete any data. Runtime ETags/generations are intentionally different
at the destination; obtain fresh revisions with `show`.

This is a versioned generic format, not a drop-in reader for unrelated/legacy
papercut tools with YAML metadata or inbox/archive layouts. Convert such data
explicitly into schema-v1 JSONL; do not point this tool at a legacy prefix and
assume it has imported those records.

## Other useful storage patterns

The SQLite reference adapter is included; other items below are integration
patterns or future adapter options, **not additional built-in backends**:

- **Git-backed files:** keep local storage in a private repository for reviewable
  history; commit/push only when approved. Ignore `.lock` and `.write-*`. Git is
  transport/history, not a distributed lock. Synchronise with one writer or
  reconcile conflicts; never assume simultaneous Git clones share CAS semantics.
- **SQLite:** the [included adapter](adapters.md) uses transactions and
  revision-conditioned updates on one local host/agent volume. It does not
  provide indexed search. Do not place a live database on object storage or
  treat it as a multi-host database.
- **Postgres:** good for many agents, tenant-aware access, indexed searches and
  reporting. A trusted adapter can store Markdown bytes plus a revision column;
  use `INSERT ... ON CONFLICT DO NOTHING` and an expected-revision `UPDATE`.
- **HTTP service:** useful for sandboxes that cannot hold cloud SDK credentials.
  Use a narrow authenticated API with per-tenant authorisation, list pagination,
  ETags and conditional PUT. Do not proxy arbitrary URLs or credentials.
- **Azure Blob:** map revisions to ETags and implement the corresponding
  conditional upload/delete semantics.
- **Issue trackers:** useful for escalation targets and human notifications.
  Treat the tracker as an adapter only if it can enforce real atomic revision
  checks; a read-then-edit API without them is insufficient.

See [adapters.md](adapters.md) for the executable adapter interface.
