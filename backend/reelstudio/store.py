import json
from pathlib import Path
import sqlite3
from threading import RLock
from uuid import uuid4
from datetime import datetime, timezone


def uid() -> str:
    return uuid4().hex


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    """Atomic document persistence for a single-user local workstation."""

    def __init__(self, root: Path):
        self.root = root.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        self.db = sqlite3.connect(self.root / "studio.sqlite3", check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS documents (kind TEXT, id TEXT, body TEXT, PRIMARY KEY(kind,id))")
        self.db.commit()

    def recover_interrupted(self):
        """Call at application startup, never when opening a read-only inspection."""
        for job in self.all("jobs"):
            if job["status"] in ("queued", "running"):
                self.save("jobs", {**job, "status": "failed", "error": "Processing was interrupted by a restart. Your last saved revision is intact.", "message": "Interrupted"})

    def get(self, kind: str, id: str) -> dict:
        with self.lock:
            row = self.db.execute("SELECT body FROM documents WHERE kind=? AND id=?", (kind, id)).fetchone()
            if not row:
                raise KeyError(id)
            return json.loads(row[0])

    def save(self, kind: str, document: dict):
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO documents VALUES (?,?,?)", (kind, document["id"], json.dumps(document, allow_nan=False)))
            self.db.commit()

    def all(self, kind: str) -> list[dict]:
        with self.lock:
            return [json.loads(r[0]) for r in self.db.execute("SELECT body FROM documents WHERE kind=? ORDER BY rowid DESC", (kind,))]

    def project_dir(self, id: str) -> Path:
        if len(id) != 32 or any(c not in "0123456789abcdef" for c in id):
            raise ValueError("Invalid project ID")
        return self.root / "projects" / id

    def busy(self, project: str) -> bool:
        return any(j.get("project_id") == project and j["status"] in ("queued", "running") for j in self.all("jobs"))

    def close(self):
        self.db.close()
