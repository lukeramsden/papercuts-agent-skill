import copy
import unittest
from unittest.mock import patch

from support import MemoryStorage
from papercuts_lib.model import Conflict, PapercutsError, decode, encode, valid_id
from papercuts_lib.service import Papercuts
from papercuts_lib.setup import doctor


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStorage()
        self.service = Papercuts(self.store, author="Test", source="agent")
        self.record = self.service.add('Tool says "wrong"\n---\nUnicode: café 🐛', ["docs", "tooling"])
        self.id = self.record["id"]

    def test_markdown_roundtrip(self):
        self.assertEqual(decode(encode(self.record)), self.record)

    def test_metadata_injection_is_data(self):
        record = copy.deepcopy(self.record)
        record["author"] = 'a\nstatus: closed\n---'
        self.assertEqual(decode(encode(record)), record)

    def test_invalid_records(self):
        for data in (b"not markdown", b"---\n{}\n---\nx", b"---\n[]\n---\nx", b"\xff"):
            with self.subTest(data=data), self.assertRaises(PapercutsError):
                decode(data)

    def test_size_limit(self):
        record = dict(self.record, body="x" * 1024 * 1024)
        with self.assertRaises(PapercutsError):
            encode(record)

    def test_id_traversal(self):
        for value in ("../x", "a/b", "*", "", None, self.id + "/x"):
            with self.subTest(value=value), self.assertRaises(PapercutsError):
                valid_id(value)

    def test_empty_add(self):
        with self.assertRaises(PapercutsError):
            self.service.add(" \n")

    def test_reserved_tag(self):
        with self.assertRaises(PapercutsError):
            self.service.add("x", ["DeFeRrEd"])

    def test_search_and_filters(self):
        self.service.note(self.id, "another occurrence in CI")
        self.assertEqual(len(self.service.list(query="OCCURRENCE", tags=["docs"])), 1)
        self.assertEqual(self.service.list(tags=["missing"]), [])
        self.service.defer(self.id, "tools-team", "waiting on release")
        self.assertEqual(len(self.service.list(min_deferrals=1, blocked_on="tools-team")), 1)
        self.assertEqual(self.service.list(blocked_on="other"), [])

    def test_namespaces(self):
        other = Papercuts(self.store, "other")
        self.assertEqual(other.list(), [])
        with self.assertRaises(PapercutsError):
            other.get(self.id)

    def test_defer_guards(self):
        self.service.defer(self.id, "team", "Waiting on release.")
        with self.assertRaises(PapercutsError):
            self.service.defer(self.id, "team", "waiting ON release!")
        self.service.defer(self.id, "team", "release landed; validating")
        with self.assertRaises(PapercutsError):
            self.service.defer(self.id, "team", "waiting on release")
        self.service.defer(self.id, "team", "validation found another issue")
        with self.assertRaises(PapercutsError):
            self.service.defer(self.id, "team", "fourth reason")
        self.assertEqual(self.service.get(self.id)[0]["deferrals"], 3)

    def test_deferral_requires_owner_and_meaning(self):
        for owner, reason in (("", "x"), ("team", ""), ("team", "!!!")):
            with self.assertRaises(PapercutsError):
                self.service.defer(self.id, owner, reason)

    def test_disposition_evidence(self):
        for disposition in ("fixed", "escalated", "wontfix", "duplicate", "invalid"):
            with self.subTest(disposition=disposition), self.assertRaises(PapercutsError):
                self.service.close(self.id, disposition)
        self.assertEqual(self.service.get(self.id)[0]["status"], "open")

    def test_terminal_close_and_notes(self):
        self.service.close(self.id, "fixed", link="urn:git:commit:abcdef")
        self.assertEqual(self.service.list(), [])
        self.assertEqual(len(self.service.list(status="closed")), 1)
        with self.assertRaises(PapercutsError):
            self.service.defer(self.id, "team", "reason")
        with self.assertRaises(PapercutsError):
            self.service.close(self.id, "wontfix", reason="no")
        updated = self.service.note(self.id, "confirmed in release")
        self.assertEqual(updated["status"], "closed")

    def test_duplicate(self):
        other = self.service.add("canonical")
        self.service.close(self.id, "duplicate", duplicate_of=other["id"])
        third = self.service.add("third")
        with self.assertRaises(PapercutsError):
            self.service.close(third["id"], "duplicate", duplicate_of=self.id)
        with self.assertRaises(PapercutsError):
            self.service.close(other["id"], "duplicate", duplicate_of=other["id"])

    def test_reject_executable_or_credential_link(self):
        for link in ("javascript:alert(1)", "file:///tmp/x", "https://user:secret@example.com", "https://"):
            with self.assertRaises(PapercutsError):
                self.service.close(self.id, "fixed", link=link)

    def test_dry_run_unchanged(self):
        original = self.service.get(self.id)
        self.service.defer(self.id, "team", "reason", dry_run=True)
        self.service.close(self.id, "wontfix", reason="no", dry_run=True)
        self.assertEqual(self.service.get(self.id), original)

    def test_stale_revision(self):
        _, revision = self.service.get(self.id)
        self.service.note(self.id, "changed")
        with self.assertRaises(Conflict):
            self.service.close(self.id, "wontfix", reason="no", expected=revision)
        self.assertEqual(self.service.get(self.id)[0]["status"], "open")

    def test_write_race(self):
        original_write = self.store.write
        def racing_write(key, data, expected):
            original_write(key, encode(dict(self.record, body="concurrent edit")), expected)
            return original_write(key, data, expected)
        with patch.object(self.store, "write", side_effect=racing_write), self.assertRaises(Conflict):
            self.service.defer(self.id, "team", "reason")
        self.assertEqual(self.service.get(self.id)[0]["deferrals"], 0)

    def test_key_record_mismatch(self):
        other = self.service.add("other")
        self.store.objects[self.service.key(self.id)] = encode(other), "999"
        with self.assertRaises(PapercutsError):
            self.service.get(self.id)

    def test_storage_failures_not_empty(self):
        with patch.object(self.store, "list", side_effect=PapercutsError("auth failed")):
            with self.assertRaises(PapercutsError):
                self.service.list()

    def test_doctor_write_probe_and_cleanup(self):
        with patch("papercuts_lib.setup.build_storage", return_value=self.store):
            result = doctor({"namespace": "default", "storage": "file:///unused"}, write_test=True)
        self.assertIn("stale-write-rejected", result["checks"])
        self.assertEqual(len(self.store.objects), 1)

    def test_doctor_offline_never_builds_adapter(self):
        with patch("papercuts_lib.setup.build_storage", side_effect=AssertionError("network")):
            self.assertTrue(doctor({"namespace": "x"}, offline=True)["ok"])
            with self.assertRaises(PapercutsError):
                doctor({"namespace": "x"}, offline=True, write_test=True)

    def test_doctor_detects_unsafe_provider(self):
        def unsafe_write(key, data, expected):
            self.store.counter += 1
            self.store.objects[key] = data, str(self.store.counter)
            return str(self.store.counter)
        with patch("papercuts_lib.setup.build_storage", return_value=self.store), patch.object(self.store, "write", side_effect=unsafe_write):
            with self.assertRaises(PapercutsError):
                doctor({"namespace": "default"}, write_test=True)
