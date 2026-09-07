import concurrent.futures
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock

from support import SKILL  # noqa: F401
from papercuts_lib.model import Conflict, PapercutsError
from papercuts_lib.storage import GCSStorage, LocalStorage, S3Storage


def attempt_update(root, revision, value):
    try:
        LocalStorage(root).write("records/test.md", value, revision)
        return True
    except Conflict:
        return False


class LocalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = LocalStorage(Path(self.temp.name) / "data")

    def test_contract(self):
        self.assertIsNone(self.store.read("records/a.md"))
        self.assertEqual(self.store.list("records/"), [])
        revision = self.store.write("records/a.md", b"one", None)
        self.assertEqual(self.store.read("records/a.md"), (b"one", revision))
        with self.assertRaises(Conflict):
            self.store.write("records/a.md", b"two", None)
        second = self.store.write("records/a.md", b"two", revision)
        with self.assertRaises(Conflict):
            self.store.write("records/a.md", b"three", revision)
        with self.assertRaises(Conflict):
            self.store.delete("records/a.md", revision)
        self.assertEqual(self.store.list("records/"), ["records/a.md"])
        self.store.delete("records/a.md", second)
        self.assertIsNone(self.store.read("records/a.md"))

    def test_permissions(self):
        self.store.write("records/a.md", b"one", None)
        self.assertEqual((self.store.root / "records/a.md").stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.store.root.stat().st_mode & 0o777, 0o700)

    def test_traversal_and_symlinks(self):
        for key in ("../x", "/tmp/x", "a//b", "a/../b", "a\\b", "a/*"):
            with self.assertRaises(PapercutsError):
                self.store.read(key)
        self.store.root.mkdir()
        (self.store.root / "link").symlink_to(self.temp.name)
        with self.assertRaises(PapercutsError):
            self.store.write("link/x", b"x", None)
        with self.assertRaises(PapercutsError):
            self.store.list("link/")

    def test_atomic_replace_failure_preserves_old(self):
        from unittest.mock import patch
        rev = self.store.write("records/a.md", b"one", None)
        with patch("papercuts_lib.storage.os.replace", side_effect=OSError("full")), self.assertRaises(OSError):
            self.store.write("records/a.md", b"two", rev)
        self.assertEqual(self.store.read("records/a.md"), (b"one", rev))
        self.assertEqual(self.store.list("records/"), ["records/a.md"])

    def test_multiprocess_conflict(self):
        revision = self.store.write("records/test.md", b"original", None)
        with concurrent.futures.ProcessPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(attempt_update, [str(self.store.root)] * 8,
                                    [revision] * 8, [str(i).encode() for i in range(8)]))
        self.assertEqual(sum(results), 1)


class S3Error(Exception):
    def __init__(self, code, status):
        self.response = {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}}


class S3Tests(unittest.TestCase):
    def setUp(self):
        self.client = MagicMock()
        self.store = S3Storage("example", "prefix/", client=self.client)

    def test_read(self):
        body = io.BytesIO(b"value")
        self.client.get_object.return_value = {"Body": body, "ETag": '"rev"'}
        self.assertEqual(self.store.read("x.md"), (b"value", '"rev"'))
        self.assertTrue(body.closed)
        self.client.get_object.assert_called_once_with(Bucket="example", Key="prefix/x.md")

    def test_missing_vs_error(self):
        self.client.get_object.side_effect = S3Error("NoSuchKey", 404)
        self.assertIsNone(self.store.read("x.md"))
        for code, status in (("NoSuchBucket", 404), ("404", 404), ("AccessDenied", 403)):
            self.client.get_object.side_effect = S3Error(code, status)
            with self.assertRaises(PapercutsError):
                self.store.read("x.md")

    def test_conditional_writes(self):
        self.client.put_object.return_value = {"ETag": '"next"'}
        self.store.write("x.md", b"x", None)
        self.assertEqual(self.client.put_object.call_args.kwargs["IfNoneMatch"], "*")
        self.store.write("x.md", b"y", '"old"')
        self.assertEqual(self.client.put_object.call_args.kwargs["IfMatch"], '"old"')
        for code, status in (("PreconditionFailed", 412), ("ConditionalRequestConflict", 409)):
            self.client.put_object.side_effect = S3Error(code, status)
            with self.assertRaises(Conflict):
                self.store.write("x.md", b"z", '"old"')

    def test_pagination(self):
        self.client.get_paginator.return_value.paginate.return_value = [
            {"Contents": [{"Key": "prefix/records/a.md"}]}, {}, {"Contents": [{"Key": "prefix/records/b.md"}]}]
        self.assertEqual(self.store.list("records/"), ["records/a.md", "records/b.md"])

    def test_listing_error(self):
        self.client.get_paginator.return_value.paginate.side_effect = S3Error("AccessDenied", 403)
        with self.assertRaises(PapercutsError):
            self.store.list("records/")

    def test_delete_conditional(self):
        self.store.delete("x.md", '"rev"')
        self.assertEqual(self.client.delete_object.call_args.kwargs["IfMatch"], '"rev"')


class GCSErr(Exception):
    def __init__(self, code):
        self.code = code


class GCSTests(unittest.TestCase):
    def setUp(self):
        self.client = MagicMock()
        self.bucket = self.client.bucket.return_value
        self.blob = self.bucket.blob.return_value
        self.blob.size = 10
        self.blob.generation = 42
        self.store = GCSStorage("example", "prefix/", client=self.client)

    def test_generation_read(self):
        self.blob.download_as_bytes.return_value = b"value"
        self.assertEqual(self.store.read("x.md"), (b"value", "42"))
        self.blob.download_as_bytes.assert_called_once_with(if_generation_match=42, timeout=30)

    def test_read_missing_bucket_is_not_missing_record(self):
        self.blob.reload.side_effect = GCSErr(404)
        self.assertIsNone(self.store.read("x.md"))
        self.bucket.reload.side_effect = GCSErr(404)
        with self.assertRaises(PapercutsError):
            self.store.read("x.md")

    def test_generation_writes(self):
        self.store.write("x.md", b"x", None)
        self.assertEqual(self.blob.upload_from_string.call_args.kwargs["if_generation_match"], 0)
        self.store.write("x.md", b"y", "41")
        self.assertEqual(self.blob.upload_from_string.call_args.kwargs["if_generation_match"], 41)
        self.blob.upload_from_string.side_effect = GCSErr(412)
        with self.assertRaises(Conflict):
            self.store.write("x.md", b"z", "41")

    def test_pagination_and_prefix(self):
        blobs = [MagicMock(), MagicMock()]
        blobs[0].name, blobs[1].name = "prefix/records/a.md", "prefix/records/b.md"
        self.bucket.list_blobs.return_value = iter(blobs)
        self.assertEqual(self.store.list("records/"), ["records/a.md", "records/b.md"])

    def test_delete(self):
        self.store.delete("x.md", "41")
        self.blob.delete.assert_called_once_with(if_generation_match=41, timeout=30)
