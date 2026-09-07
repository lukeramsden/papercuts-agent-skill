import json
import os
import re
import unittest

from support import ROOT, SKILL
from papercuts_lib.cli import VERSION


class PackagingTests(unittest.TestCase):
    def test_metadata(self):
        content = (SKILL / "SKILL.md").read_text()
        self.assertTrue(content.startswith("---\n"))
        metadata = content.split("---\n", 2)[1]
        fields = dict(line.split(": ", 1) for line in metadata.strip().splitlines())
        self.assertEqual(fields["name"], "papercuts")
        self.assertLessEqual(len(json.loads(fields["description"])), 1024)
        self.assertEqual(fields["license"], "MIT")

    def test_relative_documentation_links(self):
        for path in ROOT.rglob("*.md"):
            if ".git" in path.parts:
                continue
            for link in re.findall(r"\]\(([^)]+)\)", path.read_text()):
                if "://" not in link and not link.startswith("#"):
                    target = path.parent / link.split("#", 1)[0]
                    self.assertTrue(target.exists(), f"Broken link in {path.relative_to(ROOT)}: {link}")

    def test_licences_travel_with_skill(self):
        for name in ("LICENSE", "NOTICE"):
            self.assertEqual((SKILL / name).read_bytes(), (ROOT / name).read_bytes())

    def test_executable_launchers_and_version(self):
        for name in ("papercuts", "doctor"):
            self.assertTrue(os.access(SKILL / name, os.X_OK))
        self.assertIn(f"## {VERSION}", (ROOT / "CHANGELOG.md").read_text())

    def test_project_onboarding_instructions_and_portable_note(self):
        content = (SKILL / "SKILL.md").read_text()
        self.assertIn("first invocation in a project", content)
        self.assertIn("root `AGENTS.md`", content)
        self.assertIn("do not append duplicates", content)
        self.assertIn("rev-parse --show-toplevel", content)
        self.assertIn("project is read-only", content)
        self.assertIn("storage setup is separate", (ROOT / "README.md").read_text().lower())
        note = re.search(r"```markdown\n(.*?)\n```", content, re.DOTALL).group(1)
        self.assertIn("## Papercuts (log the friction you hit)", note)
        self.assertIn("use the `papercuts` skill", note)
        self.assertIn('add --source agent "<what tripped you up>"', note)
        self.assertIn("Never re-log deferred", note)
        self.assertNotIn("./papercuts", note)
        self.assertLess(len(note.split()), 180)

    def test_no_absolute_developer_paths(self):
        for path in SKILL.rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                content = path.read_text()
                self.assertNotIn("/Users/", content)
