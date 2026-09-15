"""The additive model layer must not alter v1 engine, API or stored history."""
import json
import tempfile
import unittest
from pathlib import Path

from etl.engine import execute
from etl.serialization import pipeline_from_dict, pipeline_from_json, pipeline_to_dict, pipeline_to_json
from etl.spec import validate
from etl.store import Store

ROOT = Path(__file__).resolve().parents[1]


class ModelCompatibilityTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.spec = json.loads((ROOT / "examples/customers.json").read_text(encoding="utf-8"))
        self.spec["source"]["path"] = "input.csv"
        (self.root / "input.csv").write_bytes((ROOT / "examples/customers.csv").read_bytes())

    def test_validate_keeps_identity_and_model_round_trip_keeps_execution_behavior(self):
        self.assertIs(validate(self.spec), self.spec)
        restored = pipeline_to_dict(pipeline_from_dict(self.spec))
        self.assertEqual(execute(restored, self.root, limit=100), execute(self.spec, self.root, limit=100))
        for spec in (self.spec, restored):
            report = execute(spec, self.root, self.root / "out")
            self.assertEqual((report["processed"], report["valid"], report["invalid"]), (5, 3, 2))
            self.assertEqual(set(report), {"processed", "valid", "invalid", "sample", "preview", "directory"})

    def test_preexisting_v1_rows_and_snapshots_need_no_database_migration(self):
        db_path = self.root / "state.sqlite3"
        store = Store(db_path)
        original_json = json.dumps(self.spec, indent=4)
        historical_report = {"processed": 1, "valid": 0, "invalid": 1,
                             "sample": [{"record": 1, "values": {"code": ""}, "errors": {"code": ["required value is missing"]}}]}
        with store.connect() as db:
            # Simulate data written before the model layer existed.
            db.execute("INSERT INTO pipelines VALUES (?,?,?,?)", ("saved-v1", self.spec["name"], original_json, "2025-01-01"))
            db.execute("INSERT INTO runs(id,name,spec,started,status,report) VALUES (?,?,?,?,?,?)",
                       ("old-run", self.spec["name"], original_json, "2025-01-01", "completed", json.dumps(historical_report)))
            schema_before = list(db.execute("SELECT sql FROM sqlite_master WHERE type='table' ORDER BY name"))
        reopened = Store(db_path)
        self.assertEqual(reopened.pipelines()[0]["spec"], self.spec)
        old_run = reopened.run("old-run")
        self.assertEqual(old_run["report"], historical_report)
        self.assertEqual(pipeline_to_dict(pipeline_from_dict(old_run["spec"])), self.spec)
        self.assertEqual(pipeline_to_dict(pipeline_from_json(original_json)), self.spec)
        with reopened.connect() as db:
            self.assertEqual(db.execute("SELECT spec FROM runs WHERE id='old-run'").fetchone()[0], original_json)
            self.assertEqual(list(db.execute("SELECT sql FROM sqlite_master WHERE type='table' ORDER BY name")), schema_before)

    def test_saving_later_definition_does_not_modify_run_snapshot(self):
        store = Store(self.root / "state.sqlite3")
        captured = pipeline_from_dict(self.spec)
        saved_id = store.save(pipeline_to_dict(captured))
        run_id = store.create_run(pipeline_to_dict(captured))
        changed = pipeline_to_dict(captured)
        changed["columns"][0]["name"] = "new_name"
        changed["name"] = "Edited pipeline"
        store.save(changed, saved_id)
        self.assertEqual(store.run(run_id)["spec"], self.spec)
        self.assertEqual(pipeline_to_dict(captured), self.spec)
        self.assertEqual(pipeline_to_dict(pipeline_from_json(pipeline_to_json(captured))), self.spec)
        self.assertEqual(store.pipelines()[0]["spec"], changed)
