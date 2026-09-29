"""Atomic local safety copy, separate from committed estimates and sync."""
import hashlib
import json
import os
from pathlib import Path
import tempfile


class RecoveryFile:
    def __init__(self, store):
        if hasattr(store, "path"):
            database = Path(store.path)
            self.path = database.parent / "recovery" / (database.name + ".json")
        else:
            identity = json.dumps(store.config, sort_keys=True).encode()
            self.path = Path.home() / ".jhr-chiffrage" / "recovery" / (hashlib.sha256(identity).hexdigest() + ".json")

    def read(self):
        if not self.path.exists():
            return None
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("format") != 1:
            raise ValueError("Copie de secours illisible")
        return data

    def write(self, data):
        if not data:
            self.path.unlink(missing_ok=True)
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=".draft-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(dict(data, format=1), stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            Path(temporary).unlink(missing_ok=True)
