"""SQLite persistence for definitions and execution snapshots."""
from .serialization import json_default
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .ordered import validate_definition as validate
from .spec import ConfigError


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
                CREATE TABLE IF NOT EXISTS binding_profiles (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, template_id TEXT NOT NULL,
                    source_key TEXT NOT NULL, profile TEXT NOT NULL, updated TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS binding_profiles_template
                    ON binding_profiles(template_id);
            """)
        # Provenance is informational only and is deliberately kept out of the
        # pipeline spec, so that nothing downstream can read it and act on it.
        with self.connect() as db:
            if "provenance" not in {row["name"] for row in db.execute("PRAGMA table_info(pipelines)")}:
                db.execute("ALTER TABLE pipelines ADD COLUMN provenance TEXT")

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
            return [dict(row) | {"spec": json.loads(row["spec"]), "provenance": json.loads(row["provenance"]) if row["provenance"] else None}
                    for row in db.execute("SELECT * FROM pipelines ORDER BY updated DESC")]

    def save(self, spec, pipeline_id=None, provenance=None):
        validate(spec)
        pipeline_id = pipeline_id or uuid.uuid4().hex
        stamp = json.dumps(provenance) if provenance else None
        with self.connect() as db:
            db.execute("INSERT INTO pipelines(id,name,spec,updated,provenance) VALUES (?, ?, ?, ?, ?) "
                       "ON CONFLICT(id) DO UPDATE SET name=excluded.name, spec=excluded.spec, "
                       "updated=excluded.updated, provenance=excluded.provenance",
                       (pipeline_id, spec["name"], json.dumps(spec), now(), stamp))
        return pipeline_id

    def delete_pipeline(self, pipeline_id):
        """Remove only a saved definition; run snapshots and files are independent."""
        with self.connect() as db:
            return db.execute("DELETE FROM pipelines WHERE id=?", (pipeline_id,)).rowcount == 1

    # Binding profiles. Mutable by design: nothing is generated from a profile
    # that does not also carry the result, so there is no past state to preserve.

    def list_profiles(self, template_id):
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT id, name, source_key, updated FROM binding_profiles WHERE template_id=? ORDER BY updated DESC",
                (template_id,))]

    def read_profile(self, profile_id):
        with self.connect() as db:
            row = db.execute("SELECT profile FROM binding_profiles WHERE id=?", (profile_id,)).fetchone()
        if row is None:
            raise ConfigError("Binding profile not found")
        return json.loads(row["profile"])

    def save_profile(self, profile, expected_updated=None):
        """Overwrite guarded by the timestamp the caller loaded.

        A concurrent edit is refused rather than lost; the caller reloads and
        decides, exactly as a stale template revision is handled.
        """
        from .binding_profiles import key_text
        with self.connect() as db:
            row = db.execute("SELECT updated FROM binding_profiles WHERE id=?", (profile["id"],)).fetchone()
            if row is not None and expected_updated != row["updated"]:
                raise ConfigError("This binding profile changed elsewhere; reload it before saving")
            if row is None and expected_updated is not None:
                raise ConfigError("Binding profile not found")
            db.execute("INSERT INTO binding_profiles VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                       "name=excluded.name, source_key=excluded.source_key, profile=excluded.profile, updated=excluded.updated",
                       (profile["id"], profile["name"], profile["template_id"],
                        key_text(profile["source_key"]), json.dumps(profile, ensure_ascii=False), profile["updated"]))
        return profile

    def delete_profile(self, profile_id):
        with self.connect() as db:
            if db.execute("DELETE FROM binding_profiles WHERE id=?", (profile_id,)).rowcount != 1:
                raise ConfigError("Binding profile not found")

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
