import copy
from urllib.parse import urlsplit

from .model import DISPOSITIONS, PapercutsError, decode, encode, new_id, normal_reason, now, text, valid_id


class Papercuts:
    def __init__(self, storage, namespace="default", author="unknown", source="cli"):
        self.storage = storage
        self.prefix = f"{namespace}/v1/records/"
        self.author = text(author, "author")
        self.source = text(source, "source")

    def key(self, identifier):
        return self.prefix + valid_id(identifier) + ".md"

    def get(self, identifier):
        result = self.storage.read(self.key(identifier))
        if result is None:
            raise PapercutsError(f"Papercut not found: {identifier}")
        data, revision = result
        record = decode(data)
        if record["id"] != identifier:
            raise PapercutsError("Record ID does not match its storage key.")
        return record, revision

    def add(self, body, tags=()):
        tags = sorted({text(t, "tag") for t in tags})
        if any(t.casefold() == "deferred" for t in tags):
            raise PapercutsError("Do not re-log deferred work. Use defer or note on the existing ID.")
        stamp = now()
        record = dict(schema_version=1, id=new_id(), created_at=stamp, updated_at=stamp,
                      author=self.author, source=self.source, tags=tags, status="open",
                      deferrals=0, body=text(body, "message"), history=[])
        self.storage.write(self.key(record["id"]), encode(record), None)
        return record

    def list(self, *, status="open", tags=(), query=None, min_deferrals=0, blocked_on=None):
        records = []
        for key in self.storage.list(self.prefix):
            if not key.endswith(".md"):
                continue
            identifier = key[len(self.prefix):-3]
            record, _ = self.get(identifier)
            if status != "all" and record["status"] != status:
                continue
            if not set(tags).issubset(record["tags"]) or record["deferrals"] < min_deferrals:
                continue
            if blocked_on is not None and record.get("blocked_on") != blocked_on:
                continue
            if query:
                searchable = "\n".join([record["body"], *record["tags"], *[
                    e.get("reason", "") + " " + e.get("body", "") for e in record["history"]]])
                if query.casefold() not in searchable.casefold():
                    continue
            records.append(record)
        return sorted(records, key=lambda r: (r["created_at"], r["id"]))

    @staticmethod
    def require_open(record):
        if record["status"] != "open":
            raise PapercutsError("Papercut is already closed. Terminal dispositions cannot be overwritten or reopened.")

    def _change(self, identifier, expected, action, mutate, dry_run=False):
        record, revision = self.get(identifier)
        if expected is not None and revision != expected:
            from .model import Conflict
            raise Conflict("Record changed since the supplied revision. Read it again.")
        record = copy.deepcopy(record)
        event = mutate(record) or {}
        record["updated_at"] = now()
        record["history"].append(dict(action=action, at=record["updated_at"], author=self.author,
                                      source=self.source, **event))
        data = encode(record)
        if not dry_run:
            self.storage.write(self.key(identifier), data, revision)
        return record

    def note(self, identifier, body, expected=None):
        body = text(body, "note")
        return self._change(identifier, expected, "note", lambda record: {"body": body})

    def defer(self, identifier, owner, reason, expected=None, dry_run=False):
        owner, reason = text(owner, "blocked-on"), text(reason, "reason")
        if not normal_reason(reason):
            raise PapercutsError("Deferral reason must contain words.")

        def mutate(record):
            self.require_open(record)
            if record["deferrals"] >= 3:
                raise PapercutsError("Already deferred three times. Escalate or make a wontfix decision.")
            if any(e.get("action") == "defer" and normal_reason(e.get("reason", "")) == normal_reason(reason)
                   for e in record["history"]):
                raise PapercutsError("Already deferred for that reason. Add evidence with note, or close it.")
            record.update(deferrals=record["deferrals"] + 1, blocked_on=owner,
                          defer_reason=reason, deferred_at=now())
            return {"blocked_on": owner, "reason": reason}
        return self._change(identifier, expected, "defer", mutate, dry_run)

    def close(self, identifier, disposition, *, reason=None, link=None, duplicate_of=None,
              expected=None, dry_run=False):
        if disposition not in DISPOSITIONS:
            raise PapercutsError("Disposition must be fixed, escalated, wontfix or duplicate.")
        if reason is not None:
            reason = text(reason, "reason")
        if disposition in ("fixed", "escalated"):
            link = text(link, "link")
            parsed = urlsplit(link)
            # Allow issue tracker / commit URIs, but never executable or credential-bearing links.
            if parsed.scheme not in ("https", "http", "urn") or (parsed.scheme != "urn" and not parsed.netloc) or parsed.username or parsed.password:
                raise PapercutsError("Use an http(s) evidence link or urn (for example urn:git:commit:HASH), without credentials.")
        if disposition == "wontfix":
            reason = text(reason, "reason")
        if disposition == "duplicate":
            valid_id(duplicate_of)
            if identifier == duplicate_of:
                raise PapercutsError("A papercut cannot duplicate itself.")
            target, _ = self.get(duplicate_of)
            if target.get("disposition") == "duplicate":
                raise PapercutsError("Point to the canonical papercut, not another duplicate.")

        def mutate(record):
            self.require_open(record)
            record.update(status="closed", disposition=disposition, closed_at=now(), closed_by=self.author)
            if reason:
                record["disposition_reason"] = reason
            if link:
                record["disposition_link"] = link
            if disposition == "duplicate":
                record["duplicate_of"] = duplicate_of
            return {k: v for k, v in {"disposition": disposition, "reason": reason, "link": link,
                                      "duplicate_of": duplicate_of}.items() if v is not None}
        return self._change(identifier, expected, "close", mutate, dry_run)
