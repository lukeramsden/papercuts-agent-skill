import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from support import SKILL  # noqa: F401
from papercuts_lib.cli import parser
from papercuts_lib.model import PapercutsError
from papercuts_lib.setup import setup


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.environment = patch.dict(os.environ, {"PAPERCUTS_HOME": str(self.root / "config")}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.stderr = contextlib.redirect_stderr(io.StringIO())
        self.stderr.__enter__()
        self.addCleanup(self.stderr.__exit__, None, None, None)

    def test_interactive_local_wizard(self):
        args = parser().parse_args(["setup"])
        answers = ["local", str(self.root / "data"), "project", "yes"]
        with patch("sys.stdin.isatty", return_value=True), patch("papercuts_lib.setup.prompt", side_effect=answers):
            result = setup(args)
        self.assertTrue(result["configured"])
        saved = json.loads((self.root / "config/config.json").read_text())
        self.assertEqual(saved["namespace"], "project")
        self.assertEqual(saved["storage"], (self.root / "data").resolve().as_uri())

    def test_interactive_cancel_does_not_save(self):
        args = parser().parse_args(["setup"])
        answers = ["local", str(self.root / "data"), "project", "no"]
        with patch("sys.stdin.isatty", return_value=True), patch("papercuts_lib.setup.prompt", side_effect=answers), self.assertRaises(PapercutsError):
            setup(args)
        self.assertFalse((self.root / "config/config.json").exists())

    def test_failed_verification_does_not_replace_config(self):
        target = self.root / "config/config.json"
        target.parent.mkdir()
        target.write_text('{"original":true}')
        args = parser().parse_args(["setup", "--storage", str(self.root / "data"), "--yes", "--replace"])
        with patch("papercuts_lib.setup.doctor", side_effect=PapercutsError("access denied")), self.assertRaises(PapercutsError):
            setup(args)
        self.assertEqual(target.read_text(), '{"original":true}')

    def test_missing_cloud_sdk_no_silent_install(self):
        args = parser().parse_args(["setup", "--storage", "s3://synthetic-test-only", "--yes"])
        with patch("papercuts_lib.setup.importlib.util.find_spec", return_value=None), patch("papercuts_lib.setup.install_dependencies") as install, self.assertRaises(PapercutsError):
            setup(args)
        install.assert_not_called()
        self.assertFalse((self.root / "config/config.json").exists())

    def test_headless_skip_check_never_accesses_credentials(self):
        args = parser().parse_args(["setup", "--storage", "gs://synthetic-test-only", "--yes", "--skip-check"])
        with patch("papercuts_lib.setup.doctor", side_effect=AssertionError("credentials")):
            result = setup(args)
        self.assertEqual(result["verification"]["checks"], [])
        self.assertTrue(result["verification"]["limitations"])
