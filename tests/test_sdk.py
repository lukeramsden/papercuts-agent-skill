"""Optional tests against real SDK serializers/models, without network/auth."""
import inspect
import io
import unittest
from unittest.mock import MagicMock

from support import SKILL  # noqa: F401
from papercuts_lib.model import Conflict
from papercuts_lib.storage import GCSStorage, S3Storage

try:
    import boto3
    from botocore.response import StreamingBody
    from botocore.stub import Stubber
except ImportError:
    boto3 = None

try:
    from google.api_core.exceptions import NotFound, PreconditionFailed
    from google.cloud.storage import Blob, Bucket
except ImportError:
    Blob = None


@unittest.skipIf(boto3 is None, "Optional boto3 SDK not installed")
class S3SDKTests(unittest.TestCase):
    def setUp(self):
        self.client = boto3.client("s3", region_name="us-east-1", aws_access_key_id="synthetic",
                                   aws_secret_access_key="synthetic")
        self.stub = Stubber(self.client)
        self.stub.activate()
        self.addCleanup(self.stub.deactivate)
        self.store = S3Storage("synthetic-bucket", "prefix/", client=self.client)

    def test_conditional_put_and_delete_models(self):
        base = {"Bucket": "synthetic-bucket", "Key": "prefix/a.md", "Body": b"test",
                "ContentType": "text/markdown; charset=utf-8"}
        self.stub.add_response("put_object", {"ETag": '"first"'}, {**base, "IfNoneMatch": "*"})
        self.stub.add_response("put_object", {"ETag": '"second"'}, {**base, "IfMatch": '"first"'})
        self.stub.add_response("delete_object", {}, {"Bucket": "synthetic-bucket", "Key": "prefix/a.md", "IfMatch": '"second"'})
        self.assertEqual(self.store.write("a.md", b"test", None), '"first"')
        self.assertEqual(self.store.write("a.md", b"test", '"first"'), '"second"')
        self.store.delete("a.md", '"second"')
        self.stub.assert_no_pending_responses()

    def test_read_and_paginated_list_models(self):
        self.stub.add_response("get_object", {"ETag": '"rev"', "Body": StreamingBody(io.BytesIO(b"test"), 4)},
                               {"Bucket": "synthetic-bucket", "Key": "prefix/a.md"})
        self.assertEqual(self.store.read("a.md"), (b"test", '"rev"'))
        self.stub.add_response("list_objects_v2", {"IsTruncated": True, "NextContinuationToken": "page2", "Contents": [{"Key": "prefix/records/a.md"}]},
                               {"Bucket": "synthetic-bucket", "Prefix": "prefix/records/"})
        self.stub.add_response("list_objects_v2", {"IsTruncated": False, "Contents": [{"Key": "prefix/records/b.md"}]},
                               {"Bucket": "synthetic-bucket", "Prefix": "prefix/records/", "ContinuationToken": "page2"})
        self.assertEqual(self.store.list("records/"), ["records/a.md", "records/b.md"])
        self.stub.assert_no_pending_responses()

    def test_real_client_errors(self):
        self.stub.add_client_error("get_object", service_error_code="NoSuchKey", http_status_code=404)
        self.assertIsNone(self.store.read("a.md"))
        self.stub.add_client_error("put_object", service_error_code="PreconditionFailed", http_status_code=412)
        with self.assertRaises(Conflict):
            self.store.write("a.md", b"test", '"stale"')


@unittest.skipIf(Blob is None, "Optional GCS SDK not installed")
class GCSSDKTests(unittest.TestCase):
    def test_sdk_accepts_generation_conditions(self):
        for method in (Blob.upload_from_string, Blob.download_as_bytes, Blob.delete):
            self.assertIn("if_generation_match", inspect.signature(method).parameters)
            self.assertIn("timeout", inspect.signature(method).parameters)
        self.assertIn("timeout", inspect.signature(Blob.reload).parameters)
        self.assertIn("prefix", inspect.signature(Bucket.list_blobs).parameters)

    def test_real_exception_statuses(self):
        client = MagicMock()
        blob = client.bucket.return_value.blob.return_value
        store = GCSStorage("synthetic", client=client)
        blob.reload.side_effect = NotFound("synthetic")
        self.assertIsNone(store.read("a.md"))
        blob.upload_from_string.side_effect = PreconditionFailed("synthetic")
        with self.assertRaises(Conflict):
            store.write("a.md", b"test", "123")
