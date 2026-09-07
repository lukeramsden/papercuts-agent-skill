import concurrent.futures
from pathlib import Path
import tempfile
import unittest

from support import SKILL  # noqa: F401
from papercuts_lib.model import Conflict, PapercutsError
from papercuts_lib.service import Papercuts
from papercuts_lib.sqlite_adapter import SQLiteStorage, create
from papercuts_lib.storage import build_storage


def sqlite_update(path, revision, value):
    try:
        SQLiteStorage(path).write("records/a.md", value, revision)
        return True
    except Conflict:
        return False


class SQLiteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "private" / "test.db"
        self.store = SQLiteStorage(str(self.path))

    def test_absent_reads_do_not_create(self):
        self.assertIsNone(self.store.read("records/a.md"))
        self.assertEqual(self.store.list("records/"), [])
        self.assertFalse(self.path.exists())

    def test_contract(self):
        revision = self.store.write("records/a.md", b"one", None)
        self.assertEqual(self.store.read("records/a.md"), (b"one", revision))
        with self.assertRaises(Conflict):
            self.store.write("records/a.md", b"two", None)
        next_revision = self.store.write("records/a.md", b"two", revision)
        with self.assertRaises(Conflict):
            self.store.write("records/a.md", b"three", revision)
        with self.assertRaises(Conflict):
            self.store.delete("records/a.md", revision)
        self.assertEqual(self.store.read("records/a.md"), (b"two", next_revision))
        self.store.delete("records/a.md", next_revision)
        self.assertIsNone(self.store.read("records/a.md"))

    def test_prefix_not_sql_wildcard(self):
        self.store.write("a_b/records/a.md", b"a", None)
        self.store.write("axb/records/b.md", b"b", None)
        self.assertEqual(self.store.list("a_b/"), ["a_b/records/a.md"])

    def test_permissions(self):
        self.store.write("records/a.md", b"a", None)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)

    def test_factory_load_and_lifecycle(self):
        store = build_storage({"adapter": "papercuts_lib.sqlite_adapter:create", "options": {"path": str(self.path)}})
        service = Papercuts(store)
        identifier = service.add("sqlite friction")["id"]
        service.defer(identifier, "team", "waiting for release")
        service.close(identifier, "wontfix", reason="upstream")
        self.assertEqual(service.list(), [])
        self.assertEqual(len(service.list(status="closed")), 1)

    def test_options(self):
        for options in ({}, {"path": "relative"}, {"path": str(self.path), "secret": "x"}):
            with self.assertRaises(PapercutsError):
                create(options)

    def test_concurrent_cas(self):
        revision = self.store.write("records/a.md", b"one", None)
        with concurrent.futures.ProcessPoolExecutor(max_workers=4) as pool:
            result = list(pool.map(sqlite_update, [str(self.path)] * 8, [revision] * 8,
                                   [str(i).encode() for i in range(8)]))
        self.assertEqual(sum(result), 1)
