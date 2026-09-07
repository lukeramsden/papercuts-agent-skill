from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "papercuts"
sys.path.insert(0, str(SKILL))

from papercuts_lib.model import Conflict


class MemoryStorage:
    def __init__(self):
        self.objects = {}
        self.counter = 0

    def read(self, key):
        return self.objects.get(key)

    def write(self, key, data, expected):
        current = self.read(key)
        if (current[1] if current else None) != expected:
            raise Conflict("changed")
        self.counter += 1
        self.objects[key] = data, str(self.counter)
        return str(self.counter)

    def list(self, prefix):
        return sorted(k for k in self.objects if k.startswith(prefix))

    def delete(self, key, expected):
        current = self.read(key)
        if current is None or current[1] != expected:
            raise Conflict("changed")
        del self.objects[key]
