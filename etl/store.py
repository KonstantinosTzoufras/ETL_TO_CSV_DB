"""SQLite persistence for definitions and execution snapshots."""
from .serialization import json_default
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .ordered import validate_definition as validate


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS pipelines (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, spec TEXT NOT NULL, updated TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, spec TEXT NOT NULL,
                    started TEXT NOT NULL, finished TEXT, status TEXT NOT NULL,
                    report TEXT NOT NULL DEFAULT '{}', error TEXT
                );
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def pipelines(self):
        with self.connect() as db:
            return [dict(row) | {"spec": json.loads(row["spec"])} for row in db.execute("SELECT * FROM pipelines ORDER BY updated DESC")]

    def save(self, spec, pipeline_id=None):
        validate(spec)
        pipeline_id = pipeline_id or uuid.uuid4().hex
        with self.connect() as db:
            db.execute("INSERT INTO pipelines VALUES (?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET name=excluded.name, spec=excluded.spec, updated=excluded.updated", (pipeline_id, spec["name"], json.dumps(spec), now()))
        return pipeline_id

    def delete_pipeline(self, pipeline_id):
        """Remove only a saved definition; run snapshots and files are independent."""
        with self.connect() as db:
            return db.execute("DELETE FROM pipelines WHERE id=?", (pipeline_id,)).rowcount == 1

    def create_run(self, spec):
        validate(spec)
        run_id = uuid.uuid4().hex
        with self.connect() as db:
            db.execute("INSERT INTO runs(id,name,spec,started,status) VALUES(?,?,?,?,?)", (run_id, spec["name"], json.dumps(spec), now(), "queued"))
        return run_id

    def update_run(self, run_id, status, report=None, error=None):
        with self.connect() as db:
            db.execute("UPDATE runs SET status=?, report=COALESCE(?,report), error=?, finished=? WHERE id=?", (status, json.dumps(report, default=json_default) if report is not None else None, error, now() if status in {"completed", "completed_with_errors", "failed", "interrupted"} else None, run_id))

    def runs(self):
        with self.connect() as db:
            return [dict(row) | {"report": json.loads(row["report"]), "spec": json.loads(row["spec"])} for row in db.execute("SELECT * FROM runs ORDER BY started DESC, rowid DESC LIMIT 100")]

    def run(self, run_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            return None
        return dict(row) | {"report": json.loads(row["report"]), "spec": json.loads(row["spec"])}
