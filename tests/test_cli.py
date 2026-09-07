import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from support import SKILL, MemoryStorage
from papercuts_lib.cli import BatchError, parser, sweep
from papercuts_lib.config import load, save, validate_config
from papercuts_lib.model import Conflict, PapercutsError
from papercuts_lib.service import Papercuts


class CLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("PAPERCUTS_")}
        self.env["PAPERCUTS_HOME"] = str(self.root / "config")
        self.tool = SKILL / "papercuts"

    def cli(self, *args, input=None, ok=True):
        result = subprocess.run([sys.executable, str(self.tool), *args], input=input, text=True,
                                capture_output=True, env=self.env, cwd=self.root)
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
        return result

    def setup_local(self):
        return self.cli("setup", "--storage", str(self.root / "records"), "--namespace", "test", "--yes")

    def test_first_use_setup_no_silent_fallback(self):
        self.cli("list", ok=False)
        result = json.loads(self.setup_local().stdout)
        self.assertTrue(result["configured"])
        self.assertTrue(json.loads(self.cli("doctor", "--write-test").stdout)["ok"])
        self.cli("setup", "--storage", str(self.root / "elsewhere"), "--yes", ok=False)
        path = self.root / "config/config.json"
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_setup_needs_confirmation(self):
        self.cli("setup", "--storage", str(self.root / "records"), ok=False)
        self.assertFalse((self.root / "config/config.json").exists())

    def test_basic_workflow(self):
        self.setup_local()
        record = json.loads(self.cli("add", "--source", "agent", "-t", "docs", input="bad link\nwith details").stdout)
        identifier = record["id"]
        self.assertEqual(json.loads(self.cli("list").stdout)["matched"], 1)
        shown = json.loads(self.cli("show", identifier).stdout)
        self.cli("note", identifier, "another occurrence", "--if-revision", shown["revision"])
        stale = self.cli("defer", identifier, "--blocked-on", "team", "--reason", "waiting", "--if-revision", shown["revision"], ok=False)
        self.assertEqual(stale.returncode, 3)
        self.cli("defer", identifier, "--blocked-on", "team", "--reason", "waiting")
        self.assertEqual(json.loads(self.cli("list", "--deferred").stdout)["matched"], 1)
        self.cli("close", identifier, "--as", "fixed", "--link", "https://example.com/pr/1")
        self.assertEqual(json.loads(self.cli("list").stdout)["matched"], 0)
        self.assertEqual(json.loads(self.cli("search", "occurrence").stdout)["matched"], 1)
        self.assertTrue(self.cli("show", identifier, "--markdown").stdout.startswith("---\n"))

    def test_sweep_skips_deferred_and_requires_selection(self):
        self.setup_local()
        one = json.loads(self.cli("add", "one").stdout)["id"]
        two = json.loads(self.cli("add", "two").stdout)["id"]
        self.cli("defer", two, "--blocked-on", "team", "--reason", "waiting")
        self.cli("sweep", "--as", "wontfix", "--reason", "no", "--yes", ok=False)
        preview = json.loads(self.cli("sweep", "--all", "--as", "wontfix", "--reason", "no", "--dry-run").stdout)
        self.assertEqual(preview["selected"], [one])
        self.assertEqual(preview["skipped"], [two])
        self.cli("sweep", "--all", "--as", "wontfix", "--reason", "no", ok=False)
        self.cli("sweep", "--all", "--as", "wontfix", "--reason", "no", "--yes")
        self.assertEqual(json.loads(self.cli("list").stdout)["records"][0]["id"], two)

    def test_export_import_idempotent(self):
        self.setup_local()
        one = json.loads(self.cli("add", "one").stdout)["id"]
        self.cli("close", one, "--as", "wontfix", "--reason", "upstream")
        self.cli("add", "two")
        exported = self.cli("export").stdout
        destination = self.root / "other-config.json"
        self.cli("--config", str(destination), "setup", "--storage", str(self.root / "other"), "--yes")
        result = json.loads(self.cli("--config", str(destination), "import", "-", "--dry-run", input=exported).stdout)
        self.assertEqual(len(result["create"]), 2)
        result = json.loads(self.cli("--config", str(destination), "import", "-", "--yes", input=exported).stdout)
        self.assertEqual(len(result["imported"]), 2)
        result = json.loads(self.cli("--config", str(destination), "import", "-", "--yes", input=exported).stdout)
        self.assertEqual(len(result["identical"]), 2)
        lines = exported.splitlines()
        altered = json.loads(lines[0])
        altered["body"] = "different"
        self.cli("import", "-", "--yes", input=json.dumps(altered), ok=False)

    def test_import_invalid_before_writes(self):
        self.setup_local()
        self.cli("import", "-", "--yes", input='{}\n', ok=False)
        self.assertEqual(json.loads(self.cli("list").stdout)["matched"], 0)

    def test_listing_limit_reports_truncation(self):
        self.setup_local()
        self.cli("add", "one")
        self.cli("add", "two")
        result = json.loads(self.cli("list", "--limit", "1").stdout)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["matched"], 2)
        self.assertEqual(len(result["records"]), 1)
        self.cli("list", "--limit", "-1", ok=False)

    def test_env_only_cloud_agent_local_volume(self):
        self.env["PAPERCUTS_STORAGE"] = str(self.root / "volume")
        self.env["PAPERCUTS_NAMESPACE"] = "project"
        self.env["PAPERCUTS_AUTHOR"] = "cloud-agent"
        record = json.loads(self.cli("add", "ephemeral agent with persistent volume").stdout)
        self.assertEqual(record["author"], "cloud-agent")
        self.assertEqual(json.loads(self.cli("path").stdout)["namespace"], "project")

    def test_standalone_skill_copy(self):
        installed = self.root / "installed"
        shutil.copytree(SKILL, installed, ignore=shutil.ignore_patterns("__pycache__"))
        self.tool = installed / "papercuts"
        self.setup_local()
        self.cli("add", "standalone")
        self.assertEqual(json.loads(self.cli("list").stdout)["matched"], 1)


class ConfigTests(unittest.TestCase):
    def test_invalid_destinations(self):
        for storage in ("https://example.com", "s3://user:secret@bucket", "s3://bucket/../x", "file://host/tmp", "gs://bucket?token=secret"):
            with self.subTest(storage=storage), self.assertRaises(PapercutsError):
                validate_config({"schema_version": 1, "storage": storage})

    def test_endpoint_security(self):
        for endpoint in ("http://localhost:9000", "https://user:secret@example.com", "https://example.com?token=x", "https://example.com/path"):
            with self.assertRaises(PapercutsError):
                validate_config({"schema_version": 1, "storage": "s3://bucket", "endpoint": endpoint})
        with self.assertRaises(PapercutsError):
            validate_config({"schema_version": 1, "storage": "r2://bucket"})

    def test_namespace_and_secrets(self):
        for extra in ({"namespace": "../other"}, {"access_key": "secret"}, {"namespace": ""}):
            with self.assertRaises(PapercutsError):
                validate_config({"schema_version": 1, "storage": "/tmp/unused", **extra})

    def test_no_repository_config_autodiscovery(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"PAPERCUTS_HOME": temp}, clear=True):
            with self.assertRaises(PapercutsError):
                load()
            path = save({"schema_version": 1, "storage": str(Path(temp) / "data")})
            self.assertTrue(path.exists())
            with self.assertRaises(PapercutsError):
                save({"schema_version": 1, "storage": str(Path(temp) / "other")})


class BatchTests(unittest.TestCase):
    def test_preflight_and_partial_reporting(self):
        store = MemoryStorage()
        service = Papercuts(store)
        one, two = service.add("one")["id"], service.add("two")["id"]
        args = parser().parse_args(["sweep", "--all", "--as", "wontfix", "--reason", "upstream", "--yes"])
        original = store.write
        def fail_second(key, data, expected):
            if two in key:
                raise Conflict("raced")
            return original(key, data, expected)
        with patch.object(store, "write", side_effect=fail_second), self.assertRaises(BatchError) as caught:
            sweep(service, args)
        self.assertEqual(caught.exception.completed, [one])
        self.assertEqual(caught.exception.pending, [two])
        self.assertEqual(service.get(two)[0]["status"], "open")

    def test_preflight_evidence_prevents_any_writes(self):
        store = MemoryStorage()
        service = Papercuts(store)
        service.add("one")
        service.add("two")
        args = parser().parse_args(["sweep", "--all", "--as", "fixed", "--yes"])
        with self.assertRaises(PapercutsError):
            sweep(service, args)
        self.assertEqual(len(service.list()), 2)
