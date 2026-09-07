"""Guided setup never provisions cloud resources or handles secret values."""
import importlib.util
import json
import os
from pathlib import Path
import site
import subprocess
import sys
import uuid
import venv

from . import config as configuration
from .model import Conflict, PapercutsError
from .storage import build_storage

GUIDES = {
    "file": "Local files need no account or packages. Use a persistent private directory.\nFor ephemeral agents, mount a persistent volume or choose object storage.",
    "s3": "1. Choose/create a PRIVATE S3 bucket in your own AWS account. Block public access.\n2. Grant only ListBucket for the prefix and GetObject/PutObject for its objects.\n3. Local: configure AWS SSO/profile (aws configure sso; aws sso login).\n   Cloud: use an IAM role or workload identity; do not embed access keys.\n4. Other S3-compatible providers: supply their HTTPS API endpoint.\n   The provider MUST enforce conditional writes (If-Match and If-None-Match).",
    "r2": "1. Cloudflare Dashboard → R2 Object Storage → create/select a PRIVATE bucket.\n2. Under Manage R2 API Tokens, create a bucket-scoped Object Read & Write token.\n3. Put its S3 credentials in a private AWS credentials profile or your runner's\n   secret store (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY), never in chat/config.\n4. Copy the S3 API endpoint: https://ACCOUNT_ID.r2.cloudflarestorage.com.\n   Region is auto. This tool does not enable public access or create resources.",
    "gs": "1. Choose/create a PRIVATE Google Cloud Storage bucket. Enable uniform access\n   and public access prevention. Consider versioning/lifecycle costs.\n2. Grant storage.objects.list/get/create/delete for the prefix, plus\n   storage.buckets.get (to distinguish a missing bucket from a missing record).\n3. Local: run gcloud auth application-default login in your own terminal.\n   Cloud: use an attached service account or Workload Identity Federation.\n   gcloud auth login alone does NOT configure Application Default Credentials.",
}


def prompt(label, default=None):
    print(label + (f" [{default}]" if default else "") + ": ", end="", file=sys.stderr, flush=True)
    value = sys.stdin.readline()
    if not value:
        raise PapercutsError("Setup cancelled (end of input).")
    return value.strip() or default or ""


def install_dependencies(provider):
    package = "google-cloud-storage>=3,<4" if provider == "gs" else "boto3>=1.40,<2"
    runtime = configuration.home() / "runtime"
    if runtime.is_symlink():
        raise PapercutsError("Refusing a symlinked runtime directory.")
    runtime.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    print(f"Installing {package} into {runtime} using pip (network access).", file=sys.stderr)
    python = runtime / "bin" / "python"
    if not python.exists():
        venv.EnvBuilder(with_pip=True).create(runtime)
    subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check", package],
                   check=True, stdout=sys.stderr)
    locations = json.loads(subprocess.check_output(
        [str(python), "-c", "import json,site; print(json.dumps(site.getsitepackages()))"], text=True))
    for location in locations:
        site.addsitedir(location)
    importlib.invalidate_caches()


def doctor(config, *, offline=False, write_test=False):
    result = {"ok": True, "namespace": config["namespace"], "offline": offline,
              "storage": config.get("storage", "custom adapter"), "checks": ["configuration"]}
    if offline:
        if write_test:
            raise PapercutsError("--offline cannot be combined with --write-test.")
        result["limitations"] = ["No storage, credentials, dependencies or network were checked."]
        return result
    storage = build_storage(config)
    storage.list(f"{config['namespace']}/v1/records/")
    result["checks"].append("list")
    if not write_test:
        result["limitations"] = ["Listing succeeded; read/write permissions and conditional-write support are not verified. Use doctor --write-test explicitly."]
        return result
    key = f"{config['namespace']}/v1/diagnostics/{uuid.uuid4().hex}.txt"
    revision = None
    try:
        revision = storage.write(key, b"papercuts diagnostic v1\n", None)
        found = storage.read(key)
        if found is None or found[0] != b"papercuts diagnostic v1\n" or found[1] != revision:
            raise PapercutsError("Diagnostic read-after-write failed.")
        try:
            storage.write(key, b"must not overwrite\n", None)
        except Conflict:
            pass
        else:
            raise PapercutsError("Unsafe backend: create-only writes are not enforced. Do not use it.")
        stale = revision
        revision = storage.write(key, b"papercuts diagnostic v2\n", revision)
        try:
            storage.write(key, b"must not overwrite\n", stale)
        except Conflict:
            pass
        else:
            raise PapercutsError("Unsafe backend: stale-revision writes are not rejected. Do not use it.")
        found = storage.read(key)
        if found is None or found[0] != b"papercuts diagnostic v2\n" or found[1] != revision:
            raise PapercutsError("Diagnostic conditional update failed.")
        result["checks"].extend(["create", "read", "conditional-create", "conditional-update", "stale-write-rejected"])
    finally:
        if revision is not None:
            try:
                # Never delete a revision we did not successfully create.
                storage.delete(key, revision)
            except Exception as exc:
                print(f"Diagnostic cleanup failed ({type(exc).__name__}); remove only {key} after inspection.", file=sys.stderr)
                raise PapercutsError(f"Diagnostic cleanup failed; temporary object remains at {key}.") from exc
    result["checks"].append("delete-diagnostic")
    return result


def setup(args):
    interactive = sys.stdin.isatty() and not args.yes
    target = configuration.config_path(args.config)
    if target.exists() and not args.replace:
        raise PapercutsError("Configuration already exists. Run doctor, or setup --replace to change it. This does not migrate existing data.")
    storage = args.storage
    if not storage:
        if not interactive:
            raise PapercutsError("Noninteractive setup needs --storage and --yes. Read references/setup.md for credentials.")
        print("Papercuts setup\nNo credentials are requested or saved. Entries may contain private project details.", file=sys.stderr)
        provider = prompt("Storage: local, s3, r2, or gcs", "local").lower()
        provider = {"local": "file", "gcs": "gs"}.get(provider, provider)
        if provider not in GUIDES:
            raise PapercutsError("Choose local, s3, r2, or gcs.")
        print("\n" + GUIDES[provider] + "\n", file=sys.stderr)
        if provider == "file":
            default = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "papercuts"
            storage = prompt("Persistent local directory", str(default))
        else:
            bucket = prompt("Bucket name (no credentials)")
            prefix = prompt("Optional bucket prefix", "papercuts")
            storage = f"{provider}://{bucket}/{prefix}"
    storage = configuration.normalise_storage(storage)
    provider = storage.split(":", 1)[0]
    if not interactive:
        if not args.yes:
            raise PapercutsError("Noninteractive setup requires --yes to confirm the destination.")
        print(GUIDES[provider], file=sys.stderr)
    cfg = {"schema_version": 1, "storage": storage,
           "namespace": args.namespace or (prompt("Namespace (project/team; not an access-control boundary)", "default") if interactive else "default")}
    for field in ("endpoint", "region", "profile", "author", "source"):
        value = getattr(args, field, None)
        if value:
            cfg[field] = value
    if provider == "r2":
        cfg["region"] = args.region or "auto"
        if not cfg.get("endpoint") and interactive:
            cfg["endpoint"] = prompt("R2 HTTPS S3 API endpoint")
    if provider in ("s3", "r2") and interactive and not cfg.get("profile"):
        profile = prompt("AWS profile (blank for default credential chain)")
        if profile:
            cfg["profile"] = profile
    cfg = configuration.validate_config(cfg)
    if provider != "file":
        missing = False
        try:
            missing = importlib.util.find_spec("google.cloud.storage" if provider == "gs" else "boto3") is None
        except ModuleNotFoundError:
            missing = True
        install = args.install_deps
        if missing and interactive and not install:
            install = prompt("Install cloud Python dependencies in a private virtual environment? yes/no", "no").lower() == "yes"
        if install:
            install_dependencies(provider)
        elif missing and not args.skip_check:
            raise PapercutsError("Cloud dependencies missing. Repeat setup with --install-deps, or install the provider SDK yourself. No config was saved.")
    print(f"Destination: {storage}\nNamespace: {cfg['namespace']}\nConfig: {target}", file=sys.stderr)
    if interactive and prompt("Save this configuration? yes/no", "no").lower() != "yes":
        raise PapercutsError("Setup cancelled; configuration not saved.")
    checks = {"ok": True, "checks": [], "limitations": ["Storage check explicitly skipped."]}
    if not args.skip_check:
        checks = doctor(cfg)
    configuration.save(cfg, args.config, replace=args.replace)
    return {"configured": True, "config": str(target), "storage": storage, "namespace": cfg["namespace"],
            "verification": checks, "next": "Run papercuts doctor --write-test to verify conditional writes (creates and removes one diagnostic object)."}
