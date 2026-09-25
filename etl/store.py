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
                CREATE TABLE IF NOT EXISTS pipeline_exports (
                    pipeline_id TEXT PRIMARY KEY, table_name TEXT UNIQUE NOT NULL, updated TEXT NOT NULL
                );
            """)
        # Provenance is informational only and is deliberately kept out of the
        # pipeline spec, so that nothing downstream can read it and act on it.
        with self.connect() as db:
            if "provenance" not in {row["name"] for row in db.execute("PRAGMA table_info(pipelines)")}:
                db.execute("ALTER TABLE pipelines ADD COLUMN provenance TEXT")
        # 'server' for every run before remote execution existed; existing rows
        # keep meaning exactly what they always meant.
        with self.connect() as db:
            if "execution_target" not in {row["name"] for row in db.execute("PRAGMA table_info(runs)")}:
                db.execute("ALTER TABLE runs ADD COLUMN execution_target TEXT NOT NULL DEFAULT 'server'")

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

    def pipeline(self, pipeline_id):
        with self.connect() as db:
            row = db.execute("SELECT id FROM pipelines WHERE id=?", (pipeline_id,)).fetchone()
        return row["id"] if row else None

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

    def claim_export_table(self, pipeline_id, desired_name):
        """The table name a pipeline's database export writes to.

        The first successful call for a pipeline_id claims desired_name and
        keeps it permanently: renaming the pipeline afterward does not rename
        the table, so nothing that already points at it breaks quietly. A
        desired_name already claimed by a DIFFERENT pipeline is refused
        outright, before any DDL runs - two pipelines never share one table.
        Callers that need to know whether this specific call is the one doing
        the claiming (to decide whether a same-named real table is safe to
        find) should check export_table_for(pipeline_id) first - it returns
        None exactly when this call is about to make a brand-new claim.
        """
        with self.connect() as db:
            row = db.execute("SELECT table_name FROM pipeline_exports WHERE pipeline_id=?", (pipeline_id,)).fetchone()
            if row is not None:
                return row["table_name"]
            clash = db.execute("SELECT pipeline_id FROM pipeline_exports WHERE table_name=?", (desired_name,)).fetchone()
            if clash is not None:
                raise ConfigError(f"Table name '{desired_name}' is already used by another pipeline; rename this pipeline and run again")
            db.execute("INSERT INTO pipeline_exports(pipeline_id,table_name,updated) VALUES (?,?,?)", (pipeline_id, desired_name, now()))
            return desired_name

    def export_table_for(self, pipeline_id):
        with self.connect() as db:
            row = db.execute("SELECT table_name FROM pipeline_exports WHERE pipeline_id=?", (pipeline_id,)).fetchone()
        return row["table_name"] if row else None

    def create_run(self, spec, execution_target="server"):
        validate(spec)
        run_id = uuid.uuid4().hex
        with self.connect() as db:
            db.execute("INSERT INTO runs(id,name,spec,started,status,execution_target) VALUES(?,?,?,?,?,?)",
                       (run_id, spec["name"], json.dumps(spec), now(), "queued", execution_target))
        return run_id

    def update_run(self, run_id, status, report=None, error=None):
        with self.connect() as db:
            db.execute("UPDATE runs SET status=?, report=COALESCE(?,report), error=?, finished=? WHERE id=?", (status, json.dumps(report, default=json_default) if report is not None else None, error, now() if status in {"completed", "completed_with_errors", "failed", "interrupted"} else None, run_id))

    def finish_run(self, run_id, status, report=None, error=None):
        """Guarded terminal transition, out of queued/running only. Returns
        whether this call was the one that made the transition - callers that
        release a shared resource on completion (a remote run's server-side
        slot) must only do so when this is True, so a race between two
        observers of the same run (e.g. a timeout racing a late status
        arriving) cannot release it twice."""
        with self.connect() as db:
            cursor = db.execute(
                "UPDATE runs SET status=?, report=COALESCE(?,report), error=?, finished=? WHERE id=? AND status IN ('queued','running')",
                (status, json.dumps(report, default=json_default) if report is not None else None, error, now(), run_id))
            return cursor.rowcount == 1

    def mark_running(self, run_id):
        """Queued -> running only. A remote dispatch that finishes after the
        poller already timed out/failed the same run_id must not resurrect
        it - if this returns False, the run already has its final outcome
        and the caller has nothing further to do."""
        with self.connect() as db:
            cursor = db.execute("UPDATE runs SET status='running' WHERE id=? AND status='queued'", (run_id,))
            return cursor.rowcount == 1

    def runs(self):
        with self.connect() as db:
            return [dict(row) | {"report": json.loads(row["report"]), "spec": json.loads(row["spec"])} for row in db.execute("SELECT * FROM runs ORDER BY started DESC, rowid DESC LIMIT 100")]

    def run(self, run_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            return None
        return dict(row) | {"report": json.loads(row["report"]), "spec": json.loads(row["spec"])}
