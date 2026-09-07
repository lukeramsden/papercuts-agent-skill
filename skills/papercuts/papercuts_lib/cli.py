import argparse
import datetime as dt
import getpass
import json
import subprocess
import sys
from pathlib import Path

from . import config
from .model import Conflict, DISPOSITIONS, MAX_BYTES, PapercutsError, decode, encode, validate, valid_id
from .service import Papercuts
from .setup import doctor, setup
from .storage import build_storage

VERSION = "0.1.1"


class BatchError(PapercutsError):
    def __init__(self, message, completed, pending):
        super().__init__(message)
        self.completed, self.pending = completed, pending


def output(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def nonnegative(value):
    try:
        number = int(value)
        if number < 0:
            raise ValueError()
        return number
    except ValueError:
        raise argparse.ArgumentTypeError("Expected a nonnegative integer.") from None


def parser():
    root = argparse.ArgumentParser(description="Capture and resolve small developer/agent annoyances. Data commands emit JSON.")
    root.add_argument("--version", action="version", version=VERSION)
    root.add_argument("--config", help="Explicit config file (otherwise PAPERCUTS_CONFIG / PAPERCUTS_HOME)")
    commands = root.add_subparsers(dest="command", required=True)
    p = commands.add_parser("setup", help="Guided storage and authentication setup")
    p.add_argument("--storage", help="Local path or file:///..., s3://..., r2://..., gs://...")
    for flag in ("namespace", "endpoint", "region", "profile", "author", "source"):
        p.add_argument("--" + flag)
    for flag in ("yes", "replace", "skip-check", "install-deps"):
        p.add_argument("--" + flag, action="store_true")
    p = commands.add_parser("doctor", help="Read-only diagnosis; optional explicit write probe")
    p.add_argument("--offline", action="store_true")
    p.add_argument("--write-test", action="store_true")
    commands.add_parser("path", help="Show selected storage, namespace and configuration path")
    p = commands.add_parser("add", help="Log friction; stdin is used if the message is omitted")
    p.add_argument("message", nargs="?")
    p.add_argument("-t", "--tag", action="append", default=[])
    p.add_argument("--author")
    p.add_argument("--source")
    for command in ("list", "search"):
        p = commands.add_parser(command)
        if command == "search":
            p.add_argument("query", help="Case-insensitive substring in body, tags and notes/history")
        else:
            p.add_argument("--query")
        p.add_argument("--status", choices=("open", "closed", "all"), default="all" if command == "search" else "open")
        p.add_argument("-t", "--tag", action="append", default=[])
        p.add_argument("--deferred", action="store_true")
        p.add_argument("--min-deferrals", type=nonnegative, default=0)
        p.add_argument("--blocked-on")
        p.add_argument("--limit", type=nonnegative, default=50, help="Output limit; 0 = all. Does not limit storage scanning.")
    p = commands.add_parser("show")
    p.add_argument("id")
    p.add_argument("--markdown", action="store_true")
    p = commands.add_parser("note", help="Append evidence or another occurrence, without creating a duplicate")
    p.add_argument("id")
    p.add_argument("message", nargs="?")
    p.add_argument("--if-revision")
    p = commands.add_parser("defer")
    p.add_argument("id")
    p.add_argument("--blocked-on", required=True)
    p.add_argument("--reason", required=True)
    p.add_argument("--if-revision")
    p.add_argument("--dry-run", action="store_true")
    for command in ("close", "sweep"):
        p = commands.add_parser(command, help="Close with evidence" if command == "close" else "Preview/close an explicit batch; never silently archive unresolved work")
        if command == "close":
            p.add_argument("id")
            p.add_argument("--if-revision")
        else:
            p.add_argument("--id", action="append", default=[])
            p.add_argument("--all", action="store_true", help="Explicitly select all matching open records")
            p.add_argument("--before", help="Created before this ISO 8601 timestamp (timezone required)")
            p.add_argument("-t", "--tag", action="append", default=[])
            p.add_argument("--include-deferred", action="store_true")
            p.add_argument("--yes", action="store_true", help="Required for actual batch writes")
        p.add_argument("--as", dest="disposition", choices=DISPOSITIONS, required=True)
        p.add_argument("--reason")
        p.add_argument("--link")
        p.add_argument("--duplicate-of")
        p.add_argument("--dry-run", action="store_true")
    commands.add_parser("export", help="Export every record as JSONL to stdout (private data)")
    p = commands.add_parser("import", help="Create-only JSONL import; never overwrite existing records")
    p.add_argument("file", help="JSONL export file; '-' for stdin")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--yes", action="store_true")
    return root


def message(value):
    if value is not None:
        return value
    if sys.stdin.isatty():
        raise PapercutsError("Provide a quoted message or pipe a sanitised body on stdin.")
    data = sys.stdin.buffer.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise PapercutsError("Input exceeds 1 MiB; summarise it first.")
    try:
        return data.decode("utf-8")
    except UnicodeError as exc:
        raise PapercutsError("Input must be UTF-8 text.") from exc


def close_options(args):
    return dict(reason=args.reason, link=args.link, duplicate_of=args.duplicate_of)


def sweep(service, args):
    if bool(args.id) == bool(args.all):
        raise PapercutsError("Choose either explicit --id values or --all, not both.")
    if not args.dry_run and not args.yes:
        raise PapercutsError("Batch writes require --yes; preview with --dry-run first.")
    before = None
    if args.before:
        try:
            before = dt.datetime.fromisoformat(args.before.replace("Z", "+00:00"))
            if before.tzinfo is None:
                raise ValueError()
        except ValueError as exc:
            raise PapercutsError("--before must be ISO 8601 with a timezone.") from exc
    records = [service.get(valid_id(i))[0] for i in dict.fromkeys(args.id)] if args.id else service.list()
    chosen, skipped, revisions = [], [], {}
    for record in records:
        identifier = record["id"]
        record, revision = service.get(identifier)
        service.require_open(record)
        if ((record["deferrals"] and not args.include_deferred)
                or not set(args.tag).issubset(record["tags"])
                or (before is not None and dt.datetime.fromisoformat(record["created_at"].replace("Z", "+00:00")) >= before)):
            skipped.append(identifier)
            continue
        chosen.append(identifier)
        # Filter and update against the SAME revision, including deferral status.
        revisions[identifier] = revision
    if args.duplicate_of in chosen:
        raise PapercutsError("Duplicate target cannot be part of the batch.")
    for identifier in chosen:
        service.close(identifier, args.disposition, expected=revisions[identifier], dry_run=True, **close_options(args))
    if args.dry_run:
        return {"dry_run": True, "selected": chosen, "skipped": skipped, "disposition": args.disposition}
    completed = []
    for index, identifier in enumerate(chosen):
        try:
            service.close(identifier, args.disposition, expected=revisions[identifier], **close_options(args))
        except Exception as exc:
            raise BatchError("Batch stopped. Completed IDs are committed; inspect remaining IDs before retrying.", completed, chosen[index:]) from exc
        completed.append(identifier)
    return {"closed": completed, "skipped": skipped, "disposition": args.disposition}


def import_records(service, args):
    if not args.dry_run and not args.yes:
        raise PapercutsError("Import requires --yes; preview with --dry-run first.")
    records = {}
    stream = sys.stdin.buffer if args.file == "-" else Path(args.file).expanduser().open("rb")
    try:
        while True:
            line = stream.readline(MAX_BYTES + 1)
            if not line:
                break
            if len(line) > MAX_BYTES:
                raise PapercutsError("Import line exceeds 1 MiB.")
            if not line.strip():
                continue
            try:
                record = validate(json.loads(line))
            except (ValueError, UnicodeError) as exc:
                raise PapercutsError("Invalid JSONL import.") from exc
            identifier = record["id"]
            if identifier in records:
                raise PapercutsError("Duplicate ID in import file.")
            records[identifier] = encode(record)
    finally:
        if args.file != "-":
            stream.close()
    create, skipped = [], []
    for identifier, data in records.items():
        existing = service.storage.read(service.key(identifier))
        if existing is not None:
            if decode(existing[0]) != decode(data):
                raise Conflict(f"Import would overwrite a different existing record: {identifier}")
            skipped.append(identifier)
        else:
            create.append(identifier)
        record = decode(data)
        target = record.get("duplicate_of")
        if target and target not in records:
            service.get(target)
    if args.dry_run:
        return {"dry_run": True, "create": create, "identical": skipped}
    completed = []
    for index, identifier in enumerate(create):
        try:
            service.storage.write(service.key(identifier), records[identifier], None)
        except Exception as exc:
            raise BatchError("Import stopped; repeat the same file to skip identical records and resume.", completed, create[index:]) from exc
        completed.append(identifier)
    return {"imported": completed, "identical": skipped}


def run(args):
    if args.command == "setup":
        return setup(args)
    cfg = config.load(args.config)
    if args.command == "path":
        return {"config": str(config.config_path(args.config)), "storage": cfg.get("storage", "custom adapter"),
                "namespace": cfg["namespace"], "records_prefix": f"{cfg['namespace']}/v1/records/"}
    if args.command == "doctor":
        return doctor(cfg, offline=args.offline, write_test=args.write_test)
    storage = build_storage(cfg)
    service = Papercuts(storage, cfg["namespace"],
                        getattr(args, "author", None) or cfg.get("author") or getpass.getuser(),
                        getattr(args, "source", None) or cfg.get("source", "cli"))
    command = args.command
    if command == "add":
        return service.add(message(args.message), args.tag)
    if command in ("list", "search"):
        records = service.list(status=args.status, tags=args.tag, query=args.query,
                               min_deferrals=max(args.min_deferrals, 1 if args.deferred else 0),
                               blocked_on=args.blocked_on)
        selected = records[:args.limit] if args.limit else records
        return {"records": selected, "matched": len(records), "truncated": len(selected) < len(records),
                "consistency": "Per-record reads, not a transaction-wide snapshot."}
    if command == "show":
        record, revision = service.get(args.id)
        if args.markdown:
            sys.stdout.buffer.write(encode(record))
            return None
        return {"record": record, "revision": revision}
    if command == "note":
        return service.note(args.id, message(args.message), args.if_revision)
    if command == "defer":
        return {"dry_run": args.dry_run, "record": service.defer(args.id, args.blocked_on, args.reason,
                expected=args.if_revision, dry_run=args.dry_run)}
    if command == "close":
        return {"dry_run": args.dry_run, "record": service.close(args.id, args.disposition,
                expected=args.if_revision, dry_run=args.dry_run, **close_options(args))}
    if command == "sweep":
        return sweep(service, args)
    if command == "export":
        # Collect/validate before emitting; remote errors must not look like a complete export.
        for record in service.list(status="all"):
            print(json.dumps(record, ensure_ascii=False))
        return None
    if command == "import":
        return import_records(service, args)
    raise PapercutsError("Unknown command.")


def main():
    args = parser().parse_args()
    try:
        result = run(args)
        if result is not None:
            output(result)
        return 0
    except (PapercutsError, OSError, subprocess.CalledProcessError) as exc:
        result = {"error": str(exc) if isinstance(exc, PapercutsError) else f"{type(exc).__name__}: operation failed; check local paths, permissions and dependency installation.",
                  "type": type(exc).__name__}
        if isinstance(exc, BatchError):
            result.update(completed=exc.completed, pending=exc.pending)
        print(json.dumps(result), file=sys.stderr)
        return 3 if isinstance(exc, Conflict) else 1
    except KeyboardInterrupt:
        print('{"error":"Interrupted; an in-flight cloud write may have committed. Inspect the record before retrying."}', file=sys.stderr)
        return 130
    except Exception as exc:
        # SDK credential initialization errors must not leak raw credential data/tracebacks.
        print(json.dumps({"error": "Unexpected failure; check doctor, SDK credentials and configuration. No automatic fallback was used.", "type": type(exc).__name__}), file=sys.stderr)
        return 1
