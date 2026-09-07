"""Portable Markdown records; JSON frontmatter is a YAML-compatible mapping."""
import copy
import datetime as dt
import json
import re
import uuid


class PapercutsError(Exception):
    pass


class Conflict(PapercutsError):
    pass


ID_RE = re.compile(r"pc_[0-9]{8}T[0-9]{12}Z_[0-9a-f]{32}\Z")
DISPOSITIONS = ("fixed", "escalated", "wontfix", "duplicate")
MAX_BYTES = 1024 * 1024


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def new_id():
    return "pc_" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ_") + uuid.uuid4().hex


def valid_id(value):
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise PapercutsError("Invalid papercut ID; use an exact ID returned by add/list.")
    return value


def text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise PapercutsError(f"{label} must not be empty.")
    return value.strip()


def normal_reason(value):
    return " ".join(re.findall(r"\w+", value.casefold(), flags=re.UNICODE))


def validate(record):
    if not isinstance(record, dict) or record.get("schema_version") != 1:
        raise PapercutsError("Unsupported record schema (expected version 1).")
    valid_id(record.get("id"))
    for field in ("created_at", "updated_at", "author", "source", "body"):
        text(record.get(field), field)
    for field in ("created_at", "updated_at"):
        try:
            parsed = dt.datetime.fromisoformat(record[field].replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError()
        except ValueError as exc:
            raise PapercutsError(f"Invalid {field} timestamp.") from exc
    if record.get("status") not in ("open", "closed"):
        raise PapercutsError("Invalid record status.")
    if not isinstance(record.get("tags"), list) or any(not isinstance(t, str) for t in record["tags"]):
        raise PapercutsError("tags must be a list of strings.")
    if not isinstance(record.get("history"), list) or any(not isinstance(e, dict) for e in record["history"]):
        raise PapercutsError("history must be a list of objects.")
    if type(record.get("deferrals")) is not int or not 0 <= record["deferrals"] <= 3:
        raise PapercutsError("Invalid deferral count.")
    if record["status"] == "closed":
        if record.get("disposition") not in DISPOSITIONS:
            raise PapercutsError("Closed record needs a disposition.")
        for field in ("closed_at", "closed_by"):
            text(record.get(field), field)
        if record["disposition"] in ("fixed", "escalated"):
            text(record.get("disposition_link"), "disposition_link")
        elif record["disposition"] == "wontfix":
            text(record.get("disposition_reason"), "disposition_reason")
        else:
            valid_id(record.get("duplicate_of"))
            if record["duplicate_of"] == record["id"]:
                raise PapercutsError("A record cannot duplicate itself.")
    if record["deferrals"]:
        for field in ("blocked_on", "deferred_at", "defer_reason"):
            text(record.get(field), field)
    return record


def encode(record):
    validate(record)
    metadata = copy.deepcopy(record)
    body = metadata.pop("body")
    data = ("---\n" + json.dumps(metadata, ensure_ascii=False, indent=2) + "\n---\n" + body + "\n").encode()
    if len(data) > MAX_BYTES:
        raise PapercutsError("Record exceeds the 1 MiB limit; summarise logs rather than uploading them.")
    return data


def decode(data):
    if len(data) > MAX_BYTES:
        raise PapercutsError("Record exceeds the 1 MiB limit.")
    try:
        content = data.decode("utf-8")
        if not content.startswith("---\n"):
            raise ValueError("missing frontmatter")
        header, body = content[4:].split("\n---\n", 1)
        record = json.loads(header)
        if not isinstance(record, dict):
            raise ValueError("metadata must be an object")
        record["body"] = body.removesuffix("\n")
        return validate(record)
    except (UnicodeError, ValueError, TypeError) as exc:
        raise PapercutsError("Invalid papercut Markdown/JSON frontmatter.") from exc
