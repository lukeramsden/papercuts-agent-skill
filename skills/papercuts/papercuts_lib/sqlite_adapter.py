"""Optional reference adapter: SQLite on a private, single-host persistent disk."""
import contextlib
import os
from pathlib import Path
import sqlite3
import uuid

from .model import Conflict, MAX_BYTES, PapercutsError
from .storage import check_key


class SQLiteStorage:
    def __init__(self, path):
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            raise PapercutsError("SQLite adapter requires an absolute path.")
        if candidate.is_symlink():
            raise PapercutsError("SQLite database must not be a symlink.")
        self.path = candidate.resolve()

    @contextlib.contextmanager
    def _connection(self, writable=False):
        if writable:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                pass
            else:
                os.close(fd)
        elif not self.path.exists():
            yield None
            return
        connection = sqlite3.connect(self.path.as_uri() + ("?mode=rw" if writable else "?mode=ro"), uri=True, timeout=30)
        try:
            if writable:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("CREATE TABLE IF NOT EXISTS objects (key TEXT PRIMARY KEY, data BLOB NOT NULL, revision TEXT NOT NULL)")
            else:
                exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='objects'").fetchone()
                if not exists:
                    yield None
                    return
            yield connection
            if writable:
                connection.commit()
        except Exception:
            if writable:
                connection.rollback()
            raise
        finally:
            connection.close()

    def read(self, key):
        check_key(key)
        with self._connection() as connection:
            if connection is None:
                return None
            row = connection.execute("SELECT data, revision FROM objects WHERE key=?", (key,)).fetchone()
            if row is not None and len(row[0]) > MAX_BYTES:
                raise PapercutsError("Stored object exceeds 1 MiB.")
            return row

    def write(self, key, data, expected):
        check_key(key)
        revision = uuid.uuid4().hex
        with self._connection(writable=True) as connection:
            if expected is None:
                cursor = connection.execute("INSERT INTO objects VALUES (?, ?, ?) ON CONFLICT(key) DO NOTHING", (key, data, revision))
            else:
                cursor = connection.execute("UPDATE objects SET data=?, revision=? WHERE key=? AND revision=?", (data, revision, key, expected))
            if cursor.rowcount != 1:
                raise Conflict("Record changed. Read it again before retrying.")
        return revision

    def list(self, prefix):
        prefix = check_key(prefix.rstrip("/")) + "/"
        with self._connection() as connection:
            if connection is None:
                return []
            # Substring comparison avoids LIKE wildcard semantics for '_' in namespaces.
            return [row[0] for row in connection.execute(
                "SELECT key FROM objects WHERE substr(key, 1, ?) = ? ORDER BY key", (len(prefix), prefix))]

    def delete(self, key, expected):
        check_key(key)
        with self._connection(writable=True) as connection:
            cursor = connection.execute("DELETE FROM objects WHERE key=? AND revision=?", (key, expected))
            if cursor.rowcount != 1:
                raise Conflict("Object changed before deletion.")


def create(options):
    if set(options) != {"path"} or not isinstance(options["path"], str):
        raise PapercutsError("SQLite options must contain only an absolute 'path'.")
    return SQLiteStorage(options["path"])
