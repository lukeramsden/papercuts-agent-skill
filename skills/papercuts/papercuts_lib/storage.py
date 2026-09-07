"""Storage contract: absent reads return None; writes require an exact revision.

None means create-only, never unconditional overwrite. All other failures propagate.
Keys are relative, slash-separated paths. Plugins must provide the same semantics.
"""
import contextlib
import hashlib
import importlib
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import unquote, urlsplit

from .model import Conflict, MAX_BYTES, PapercutsError


def check_key(key):
    if not isinstance(key, str) or not key or any(
        not re.fullmatch(r"[A-Za-z0-9_.-]+", part) or part in (".", "..")
        for part in key.split("/")
    ):
        raise PapercutsError("Invalid storage key.")
    return key


class LocalStorage:
    """flock + atomic rename; intended for one host, not distributed/NFS locks."""
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()

    def _path(self, key):
        check_key(key)
        path = self.root.joinpath(key)
        # Reject symlinks beneath the chosen root, including dangling symlinks.
        current = self.root
        for part in key.split("/"):
            current = current / part
            if current.is_symlink():
                raise PapercutsError("Symlinks inside storage are not supported.")
        return path

    @contextlib.contextmanager
    def _lock(self):
        import fcntl
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self._path(".lock")
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def read(self, key):
        path = self._path(key)
        try:
            with path.open("rb") as stream:
                data = stream.read(MAX_BYTES + 1)
        except FileNotFoundError:
            return None
        if len(data) > MAX_BYTES:
            raise PapercutsError("Stored object exceeds the 1 MiB limit.")
        return data, hashlib.sha256(data).hexdigest()

    def write(self, key, data, expected):
        with self._lock():
            current = self.read(key)
            if (current[1] if current else None) != expected:
                raise Conflict("Record changed. Read it again before retrying.")
            path = self._path(key)
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd, tmp = tempfile.mkstemp(prefix=".write-", dir=path.parent)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(tmp, path)
                directory = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
        return hashlib.sha256(data).hexdigest()

    def list(self, prefix):
        path = self._path(prefix.rstrip("/"))
        if not path.exists():
            return []
        keys = []
        for directory, dirs, files in os.walk(path, followlinks=False):
            for name in dirs + files:
                if (Path(directory) / name).is_symlink():
                    raise PapercutsError("Symlinks inside storage are not supported.")
            for name in files:
                if not name.startswith("."):
                    keys.append((Path(directory) / name).relative_to(self.root).as_posix())
        return sorted(keys)

    def delete(self, key, expected):
        with self._lock():
            current = self.read(key)
            if current is None or current[1] != expected:
                raise Conflict("Object changed before deletion.")
            self._path(key).unlink()


def cloud_error(exc):
    response = getattr(exc, "response", {})
    code = str(response.get("Error", {}).get("Code", ""))
    status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    if code in ("PreconditionFailed", "ConditionalRequestConflict") or status in (409, 412):
        raise Conflict("Record changed. Read it again before retrying.") from exc
    # Avoid exposing SDK messages, which may contain credential/endpoint details.
    raise PapercutsError(f"S3 request failed ({code or type(exc).__name__}). Check credentials, bucket, endpoint and permissions.") from exc


class S3Storage:
    def __init__(self, bucket, prefix="", *, endpoint=None, region=None, profile=None, client=None):
        self.bucket, self.prefix = bucket, prefix
        if client is not None:
            self.client = client
        else:
            try:
                import boto3
                from botocore.config import Config
            except ImportError as exc:
                raise PapercutsError("S3/R2 dependencies missing. Run setup --install-deps, or install boto3>=1.40,<2 in your Python environment.") from exc
            self.client = boto3.Session(profile_name=profile).client(
                "s3", endpoint_url=endpoint, region_name=region or "us-east-1",
                config=Config(connect_timeout=10, read_timeout=30, retries={"max_attempts": 3, "mode": "standard"}),
            )

    def _key(self, key):
        check_key(key)
        return self.prefix + key

    def read(self, key):
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=self._key(key))
        except Exception as exc:
            # NoSuchBucket and generic 404s are NOT missing records.
            if getattr(exc, "response", {}).get("Error", {}).get("Code") == "NoSuchKey":
                return None
            cloud_error(exc)
        with contextlib.closing(response["Body"]) as stream:
            data = stream.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise PapercutsError("Stored object exceeds the 1 MiB limit.")
        return data, response["ETag"]

    def write(self, key, data, expected):
        condition = {"IfNoneMatch": "*"} if expected is None else {"IfMatch": expected}
        try:
            result = self.client.put_object(Bucket=self.bucket, Key=self._key(key), Body=data,
                                           ContentType="text/markdown; charset=utf-8", **condition)
            return result["ETag"]
        except Exception as exc:
            cloud_error(exc)

    def list(self, prefix):
        keys = []
        try:
            pages = self.client.get_paginator("list_objects_v2").paginate(
                Bucket=self.bucket, Prefix=self._key(prefix.rstrip("/")) + "/")
            for page in pages:
                keys.extend(item["Key"][len(self.prefix):] for item in page.get("Contents", []))
        except Exception as exc:
            cloud_error(exc)
        return sorted(keys)

    def delete(self, key, expected):
        try:
            self.client.delete_object(Bucket=self.bucket, Key=self._key(key), IfMatch=expected)
        except Exception as exc:
            cloud_error(exc)


class GCSStorage:
    def __init__(self, bucket, prefix="", *, client=None):
        if client is None:
            try:
                from google.cloud import storage
            except ImportError as exc:
                raise PapercutsError("GCS dependencies missing. Run setup --install-deps, or install google-cloud-storage>=3,<4.") from exc
            client = storage.Client()
        self.bucket = client.bucket(bucket)
        self.prefix = prefix

    def _blob(self, key):
        return self.bucket.blob(self.prefix + check_key(key))

    @staticmethod
    def _error(exc):
        code = getattr(exc, "code", None)
        if code in (409, 412):
            raise Conflict("Record changed. Read it again before retrying.") from exc
        raise PapercutsError(f"GCS request failed ({code or type(exc).__name__}). Check ADC credentials, bucket and permissions.") from exc

    def read(self, key):
        blob = self._blob(key)
        try:
            blob.reload(timeout=30)
            if blob.size > MAX_BYTES:
                raise PapercutsError("Stored object exceeds the 1 MiB limit.")
            generation = blob.generation
            data = blob.download_as_bytes(if_generation_match=generation, timeout=30)
            return data, str(generation)
        except PapercutsError:
            raise
        except Exception as exc:
            if getattr(exc, "code", None) == 404:
                # Distinguish a missing bucket from a missing object.
                try:
                    self.bucket.reload(timeout=30)
                except Exception as bucket_exc:
                    self._error(bucket_exc)
                return None
            self._error(exc)

    def write(self, key, data, expected):
        blob = self._blob(key)
        try:
            blob.upload_from_string(data, content_type="text/markdown; charset=utf-8",
                                    if_generation_match=0 if expected is None else int(expected), timeout=30)
            return str(blob.generation)
        except Exception as exc:
            self._error(exc)

    def list(self, prefix):
        try:
            return sorted(blob.name[len(self.prefix):] for blob in self.bucket.list_blobs(
                prefix=self.prefix + check_key(prefix.rstrip("/")) + "/", timeout=30))
        except Exception as exc:
            self._error(exc)

    def delete(self, key, expected):
        try:
            self._blob(key).delete(if_generation_match=int(expected), timeout=30)
        except Exception as exc:
            self._error(exc)


def build_storage(config):
    if config.get("adapter"):
        # Explicit user-owned config only. Never discover plugins in a repository.
        try:
            module, name = config["adapter"].split(":", 1)
            return getattr(importlib.import_module(module), name)(config.get("options", {}))
        except Exception as exc:
            raise PapercutsError(f"Could not load trusted adapter ({type(exc).__name__}).") from exc
    uri = config["storage"]
    parsed = urlsplit(uri)
    if parsed.scheme == "file":
        return LocalStorage(unquote(parsed.path))
    prefix = parsed.path.strip("/")
    prefix = prefix + "/" if prefix else ""
    if parsed.scheme in ("s3", "r2"):
        return S3Storage(parsed.netloc, prefix, endpoint=config.get("endpoint"),
                         region=config.get("region"), profile=config.get("profile"))
    if parsed.scheme == "gs":
        return GCSStorage(parsed.netloc, prefix)
    raise PapercutsError("Unsupported storage URI.")
