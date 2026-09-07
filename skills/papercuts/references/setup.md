# Setup: local developers and cloud agents

Run commands from the installed skill directory, or use the tool's absolute
path. Setup never asks for access keys, tokens, credential JSON contents or
browser callback codes. Authenticate through the cloud provider's normal tools
in your own terminal, or your agent runner's identity/secret facilities.

## Guided first use

```bash
./papercuts setup
```

The wizard asks:

1. Local files, S3, R2, or GCS?
2. Which persistent directory or private bucket/prefix?
3. Which namespace (usually a project or team)?
4. For R2, which HTTPS S3 API endpoint? For S3/R2, which optional AWS profile?
5. If needed, may it install the cloud SDK in a private virtual environment?
6. Does the displayed destination look correct?

For cloud choices it prints account/bucket/authentication steps before asking
for the destination. Finish those steps in another terminal or provider Console.
It does not open browsers, sign in on your behalf, provision resources, create
keys, or modify IAM. Some manual Console work is expected.

Setup validates a listing before saving. Missing permissions or credentials fail
without saving a config. Listing does **not** prove write access or safe
concurrency. After saving, explicitly verify those:

```bash
./papercuts doctor --write-test
```

This creates a random object under `NAMESPACE/v1/diagnostics/`, verifies reads,
create-only writes, conditional updates and stale-write rejection, and deletes
only the exact revision it wrote. A failed cleanup is reported with its key;
inspect and remove that diagnostic object yourself. Diagnostics are not records.
Provider versioning/soft-delete can retain noncurrent diagnostic versions.

## Local files: no third-party packages

```bash
./papercuts setup --storage "$HOME/.local/share/papercuts" --namespace my-project --yes
./papercuts doctor --write-test
```

Relative paths are resolved at setup time and saved as absolute `file:///` URIs,
so invoking the installed skill elsewhere cannot change the inbox. A directory
on a persistent agent volume also works. The default interactive directory is
`$XDG_DATA_HOME/papercuts`, or `~/.local/share/papercuts`.

New directories/files use modes 700/600. Existing parent directory permissions
are not changed. Data is plaintext; use normal disk encryption/backups. Local
locking is for a **single host**. Do not use NFS, object-store FUSE mounts or
cloud-sync folders for concurrent multi-host writers.

## AWS S3

1. Choose or create a bucket in your AWS account. Enable **Block Public Access**.
2. Choose a dedicated prefix, such as `papercuts/`. Consider encryption,
   versioning, retention and storage/list/read costs.
3. Grant `s3:ListBucket` on that bucket, restricted by `s3:prefix`, and
   `s3:GetObject` / `s3:PutObject` on the selected prefix's objects. Diagnostics
   also need `s3:DeleteObject` under `PREFIX/NAMESPACE/v1/diagnostics/*`.
   Record operations never delete objects. KMS-encrypted buckets may need
   additional key permissions; do not grant broader access blindly.
4. Locally, configure an approved SSO profile in your own terminal:

   ```bash
   aws configure sso --profile papercuts
   aws sso login --profile papercuts
   ```

5. Set up the tool:

   ```bash
   ./papercuts setup --storage s3://YOUR-BUCKET/papercuts \
     --namespace my-project --region us-east-1 --profile papercuts \
     --install-deps --yes
   ./papercuts doctor --write-test
   ```

Use the bucket's actual region. The AWS CLI is useful for local authentication,
not needed for data operations. The SDK uses the normal AWS credential chain:
profiles/SSO, environment credentials, web identity, container credentials or
instance roles. For cloud agents, prefer a scoped IAM role / OIDC federation and
omit a local `--profile`. Do not copy a developer's SSO cache into a runner image.

## Cloudflare R2

1. In Cloudflare Dashboard, open **R2 Object Storage** and choose/create a
   private bucket. Do not enable an `r2.dev` public URL or a public custom domain.
2. Under **Manage R2 API Tokens**, create credentials with **Object Read & Write**
   limited to the intended bucket. These are S3 access-key credentials, not an
   ordinary Cloudflare bearer API token.
3. Save them in a dedicated private AWS credentials profile using your own
   terminal (`aws configure --profile papercuts-r2`), or inject
   `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` from your runner's secret store.
   Never paste them into chat or `config.json`.
4. Copy the bucket's S3 API endpoint. Use the endpoint Cloudflare gives you,
   including a jurisdiction-specific endpoint if applicable.

   ```bash
   ./papercuts setup --storage r2://YOUR-BUCKET/papercuts \
     --endpoint https://ACCOUNT_ID.r2.cloudflarestorage.com \
     --namespace my-project --profile papercuts-r2 --install-deps --yes
   ./papercuts doctor --write-test
   ```

R2 uses region `auto`. `--profile` is optional when the runner injects credentials.
The SDK signs requests; the tool does not manage or refresh Cloudflare API tokens.
Token rotation/revocation is managed by you or the runner.

## Other S3-compatible providers

Use `s3://` with an explicitly trusted HTTPS endpoint, for example a private
MinIO service with a valid TLS certificate:

```bash
./papercuts setup --storage s3://YOUR-BUCKET/papercuts \
  --endpoint https://objects.example.com --region us-east-1 \
  --namespace my-project --install-deps --yes
./papercuts doctor --write-test
```

Compatibility requires `ListObjectsV2` pagination, `GetObject`, conditional
`PutObject` using **both** `If-None-Match: *` and `If-Match`, and conditional
`DeleteObject` for the diagnostic. “S3-compatible” alone is not proof. If a
provider rejects or ignores these conditions, do not use it for this skill.
Do not disable certificate verification or remove conditions to make it work.

## Google Cloud Storage

1. Choose/create a private GCS bucket. Enable **uniform bucket-level access**
   and **public access prevention**.
2. Grant `storage.objects.list`, `storage.objects.get`, `storage.objects.create`
   and `storage.objects.delete` for the chosen data scope. GCS requires delete
   permission to replace an existing object even though records are never
   explicitly deleted. Also grant `storage.buckets.get` so a missing bucket
   cannot be mistaken for a missing record. Prefer a dedicated bucket/custom
   role where appropriate; consult your organisation's IAM policy.
3. Locally, configure **Application Default Credentials (ADC)**:

   ```bash
   gcloud auth application-default login
   ```

   `gcloud auth login` alone is not sufficient. The SDK may also need an explicit
   project/quota project (`GOOGLE_CLOUD_PROJECT`, or your ADC configuration).
   Service-account impersonation is an option if approved by your organisation.
4. Set up and test:

   ```bash
   ./papercuts setup --storage gs://YOUR-BUCKET/papercuts \
     --namespace my-project --install-deps --yes
   ./papercuts doctor --write-test
   ```

For cloud agents, prefer an attached service account or Workload Identity
Federation. If an approved runner supplies ADC through
`GOOGLE_APPLICATION_CREDENTIALS`, this should be a private file path, not JSON
in chat. Avoid long-lived service-account keys where federation is available.

## Ephemeral/cloud-agent configuration

Bake the skill and provider SDK into your image; inject identity at runtime.
A storage URI plus namespace can replace a saved config:

```bash
export PAPERCUTS_STORAGE=s3://YOUR-BUCKET/papercuts
export PAPERCUTS_NAMESPACE=my-project
export PAPERCUTS_REGION=us-east-1
export PAPERCUTS_AUTHOR=ci-agent
export PAPERCUTS_SOURCE=agent
# Credentials come from the runner's IAM role, not these variables.
/path/to/skill/papercuts doctor
/path/to/skill/papercuts add 'The lint command points to a removed configuration file.'
```

Equivalent variables exist for `PAPERCUTS_ENDPOINT` and `PAPERCUTS_PROFILE`.
For GCS use `gs://`; for R2 use `r2://` plus `PAPERCUTS_ENDPOINT` and region `auto`.
Environment overrides apply over saved config; use a separate empty
`PAPERCUTS_HOME` when switching provider/trust boundaries so old profile/endpoint
settings are not inherited. No credentials are stored by this tool.

For image builds without runtime identity, explicit provisioning is possible:

```bash
./papercuts setup --storage gs://YOUR-BUCKET/papercuts --namespace my-project \
  --yes --install-deps --skip-check
```

`--skip-check` saves **unverified** configuration; run `doctor` and a write probe
with the runtime identity before treating the store as ready. Do not use this
flag to conceal an actual auth/access error.

## Configuration and dependency locations

- Config: `$PAPERCUTS_CONFIG`, or `$PAPERCUTS_HOME/config.json`.
- Home: `$PAPERCUTS_HOME`, or `$XDG_CONFIG_HOME/papercuts`, or `~/.config/papercuts`.
- Runtime: `$PAPERCUTS_HOME/runtime/` (using the resolved home above).
- `--config FILE` is a **global flag**, before the command.
- Author/source: command flags on `add`, then `PAPERCUTS_AUTHOR` /
  `PAPERCUTS_SOURCE`, then saved defaults; final defaults are OS username and `cli`.
  Set source/author in config or environment for note/close/defer attribution too.

`--install-deps` uses Python's `venv` and pip to install `boto3>=1.40,<2` for
S3/R2, or `google-cloud-storage>=3,<4` for GCS. It needs network access and your
normal trust in that package registry. It never uses sudo or modifies system
Python. Dependency versions are bounded, not fully locked; use your own locked
image/environment where supply-chain reproducibility is required.

The launcher automatically uses that home runtime if present. On minimal Linux,
you may need your distribution's Python `venv` package. After replacing the base
Python installation, a virtual environment may need rebuilding. You can also
install the relevant SDK into your own environment instead; do not create the
home runtime in that case.

A configuration is never auto-loaded from a repository. To change destinations:

```bash
./papercuts --config /private/other-config.json setup --storage /private/other-data --yes
./papercuts --config /private/other-config.json list
```

`setup --replace` intentionally replaces a config, **not the data**. Existing
records remain where they were. For migration, use export/import as described
in [storage.md](storage.md). Setup ignores environment overrides while writing
its explicit config; subsequent commands apply them, so unset stale overrides.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| Not configured | Run `setup`, or provide `PAPERCUTS_STORAGE`; no implicit inbox is created. |
| Missing SDK | Repeat cloud setup with `--install-deps` (`--replace` if already configured), or install the SDK in your own Python environment. |
| AWS credentials missing/expired | Reauthenticate the approved profile or fix the runner role/identity. Do not request secrets in chat. |
| GCS DefaultCredentialsError | Configure ADC, not just gcloud CLI login; check project and runner identity. |
| AccessDenied / 403 | Check intended bucket/prefix, identity and scoped permissions. Do not make the bucket public. |
| S3 redirect/region error | Supply the bucket's actual `--region`; check the provider endpoint. |
| Conditional writes unsupported | Do not use this provider with concurrent writers; choose a conforming backend/adapter. |
| Conflict / exit 3 | Read the record again. Reconcile competing changes before retrying. |
| Timeout/interruption during a write | Its result may be unknown. Inspect/search before retrying; `add` is not automatically idempotent. |
| Local PermissionError | Check private directory ownership and volume permissions; no sudo/chmod-to-world-writable workaround. |
| Diagnostic cleanup failed | Inspect and remove only the reported diagnostic key; it may require delete permission. |

## Provider references

- [AWS SDK credentials](https://boto3.amazonaws.com/v1/documentation/api/latest/guide/credentials.html)
- [S3 conditional writes](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html)
- [R2 S3 API compatibility](https://developers.cloudflare.com/r2/api/s3/api/)
- [R2 S3 credentials](https://developers.cloudflare.com/r2/api/tokens/)
- [Google Application Default Credentials](https://cloud.google.com/docs/authentication/application-default-credentials)
- [GCS request preconditions](https://cloud.google.com/storage/docs/request-preconditions)
