from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from pathlib import Path
import csv
import json
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from etl.engine import execute
from etl.models import Transform
from etl.spec import ConfigError
from etl.templates import (MappingTemplate, TemplateField, TemplateStore, apply_template,
                           bind_template, template_from_dict, template_to_dict)


def blueprint():
    return {"format_version": 1, "name": "Reusable target", "processing_version": 2,
            "fields": [{"output_name": "Code", "target_type": "string", "required": True},
                       {"output_name": "Name", "target_type": "string", "transforms": ["trim", "empty_to_null"]},
                       {"output_name": "VAT", "target_type": "string"}]}


def draft(kind="csv", version=2):
    source = {"kind": "csv", "path": "input.csv"} if kind == "csv" else {"kind": "sqlserver", "connection_env": "ETL_SQL_TEST", "schema": "dbo", "table": "AnyTable"}
    return {"version": version, "name": "Target pipeline", "source": source, "columns": [], "destination": {"kind": "csv"}}


class TemplateTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.store = TemplateStore(self.root / "templates")
        self.template = self.store.create(blueprint())

    def bind(self, bindings=None, spec=None):
        return bind_template(template_to_dict(self.template), spec or draft(), bindings if bindings is not None else [{"source": "code"}, {"source": "name"}, {"literal": ""}])

    def test_models_recursively_immutable_and_roundtrip(self):
        fields = [TemplateField("Code", "string", [Transform("trim")])]
        model = MappingTemplate("a" * 32, "Target", 1, fields)
        fields.clear()
        self.assertEqual(len(model.fields), 1)
        self.assertEqual(model.fields[0].transforms, (Transform("trim"),))
        with self.assertRaises(FrozenInstanceError):
            model.fields[0].required = True
        self.assertEqual(template_from_dict(json.loads(json.dumps(template_to_dict(self.template)))), self.template)

    def test_strictly_reject_source_and_credential_properties_at_each_level(self):
        for key in ("source", "table", "schema", "column", "connection_env", "credentials", "password", "literal", "lookup"):
            with self.subTest(key=key):
                value = blueprint(); value[key] = "forbidden"
                with self.assertRaises(ConfigError): self.store.create(value)
                value = blueprint(); value["fields"][0][key] = "forbidden"
                with self.assertRaises(ConfigError): self.store.create(value)

    def test_reject_bad_names_rules_versions_and_types(self):
        cases = [{"target_type": "guess"}, {"target_type": []}, {"output_name": " "}, {"required": 1},
                 {"transforms": ["infer_date"]}, {"transforms": [None]}, {"lookup_required": "yes"}, {"max_length": True}, {"max_length": 0}]
        for change in cases:
            value = blueprint(); value["fields"][0].update(change)
            with self.subTest(change=change), self.assertRaises(ConfigError): self.store.create(value)
        for change in ({"processing_version": True}, {"processing_version": 3}, {"format_version": 2}, {"fields": []}):
            with self.assertRaises(ConfigError): self.store.create({**blueprint(), **change})
        value = blueprint(); value["fields"][1]["output_name"] = "code"
        with self.assertRaises(ConfigError): self.store.create(value)

    def test_defaults_and_order(self):
        value = blueprint(); del value["processing_version"]
        model = self.store.create(value)
        self.assertEqual(model.processing_version, 2)
        self.assertEqual([f.output_name for f in model.fields], ["Code", "Name", "VAT"])
        self.assertEqual(self.bind()["columns"][1]["transforms"], ["trim", "empty_to_null"])

    def test_storage_immutable_revisions_stale_edit_and_restart(self):
        original_path = self.root / "templates" / self.template.id / "1.json"
        original_bytes = original_path.read_bytes()
        value = blueprint(); value["name"] = "Revision two"; value["fields"][0]["required"] = False
        second = self.store.revise(self.template.id, 1, value)
        self.assertEqual(second.revision, 2)
        self.assertEqual(original_path.read_bytes(), original_bytes)
        with self.assertRaises(ConfigError): self.store.revise(self.template.id, 1, value)
        restarted = TemplateStore(self.root / "templates")
        self.assertEqual(restarted.read(self.template.id, 1), self.template)
        self.assertEqual([r["revision"] for r in restarted.list()], [1, 2])
        self.assertEqual(list(original_path.parent.glob("*.tmp")), [])

    def test_concurrent_revision_never_overwrites(self):
        def revise(_):
            try:
                return self.store.revise(self.template.id, 1, blueprint()).revision
            except ConfigError:
                return "conflict"
        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertCountEqual(list(executor.map(revise, range(2))), [2, "conflict"])

    def test_paths_and_atomic_failure(self):
        with self.assertRaises(ConfigError): self.store.read("../outside", 1)
        with self.assertRaises(ConfigError): self.store.read(self.template.id, True)
        with patch("etl.templates.os.link", side_effect=OSError("simulated publication failure")):
            with self.assertRaises(OSError): self.store.revise(self.template.id, 1, blueprint())
        self.assertEqual(len(self.store.list()), 1)
        self.assertFalse(list((self.root / "templates" / self.template.id).glob("*.tmp")))

    def test_apply_copies_without_any_name_matching(self):
        value = draft()
        pending = apply_template(self.template, value)
        self.assertEqual(pending["bindings"], [None, None, None])
        self.assertEqual(pending["pipeline"]["columns"], [])
        pending["template"]["fields"][0]["output_name"] = "Changed"
        pending["pipeline"]["name"] = "Changed"
        self.assertEqual(self.template.fields[0].output_name, "Code")
        self.assertEqual(value["name"], "Target pipeline")

    def test_existing_mappings_and_version_mismatches_block(self):
        value = draft(); value["columns"] = [{"name": "existing", "literal": "x"}]
        for spec in (value, draft(version=1), draft(version=True)):
            with self.assertRaises(ConfigError): apply_template(self.template, spec)
        legacy = blueprint(); legacy["processing_version"] = 1
        model = self.store.create(legacy)
        self.assertEqual(apply_template(model, draft(version=1))["pipeline"]["version"], 1)
        with self.assertRaises(ConfigError): bind_template(template_to_dict(model), draft(), [{"literal": None}] * 3)

    def test_optional_and_required_unmapped_both_block(self):
        for bindings in ([None, {"literal": "x"}, {"literal": ""}], [{"source": "Code"}, {"literal": "x"}, None], [{"source": ""}, {"literal": "x"}, {"literal": ""}]):
            with self.assertRaises(ConfigError): self.bind(bindings)
        with self.assertRaises(ConfigError): self.bind([{"source": "code", "literal": "x"}] * 3)

    def test_null_empty_whitespace_and_multiline_literals_are_explicit(self):
        for value in (None, "", " \t ", "Ελλάδα\r\nnext", True, 123):
            pipeline = self.bind([{"literal": "003"}, {"literal": value}, {"literal": ""}])
            self.assertEqual(pipeline["columns"][1]["literal"], value)
            self.assertEqual(pipeline["columns"][2]["literal"], "")

    def test_lookup_required_until_existing_rule_is_supplied(self):
        value = blueprint(); value["fields"][0]["lookup_required"] = True
        template = self.store.create(value)
        bindings = [{"source": "code"}, {"literal": "name"}, {"literal": ""}]
        with self.assertRaises(ConfigError): bind_template(template_to_dict(template), draft(), bindings)
        bindings[0]["lookup"] = {"source": {"kind": "csv", "path": "lookup.csv"}, "column": "code"}
        generated = bind_template(template_to_dict(template), draft(), bindings)
        self.assertEqual(generated["columns"][0]["lookup"], bindings[0]["lookup"])
        self.assertNotIn("lookup", template_to_dict(template)["fields"][0])
        bindings[0]["lookup"]["source"]["credentials"] = "forbidden"
        with self.assertRaises(ConfigError): bind_template(template_to_dict(template), draft(), bindings)

    def test_copied_definition_works_after_template_edit_and_removal(self):
        pending = apply_template(self.template, draft())
        value = blueprint(); value["fields"][0]["transforms"] = ["upper"]
        self.store.revise(self.template.id, 1, value)
        # Remove only our two explicit fixture files, never a project directory.
        for revision in (1, 2):
            (self.root / "templates" / self.template.id / f"{revision}.json").unlink()
        generated = bind_template(pending["template"], pending["pipeline"], [{"source": "code"}, {"source": "name"}, {"literal": ""}])
        self.assertEqual(generated["columns"][0]["transforms"], [])
        self.assertNotIn("template", generated)
        self.assertEqual(set(generated), {"version", "name", "source", "columns", "destination"})

    def test_csv_generated_pipeline_equals_manual_execution_and_export(self):
        with (self.root / "input.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, delimiter=";"); writer.writerow(["code", "name", "extra"])
            writer.writerow(["003", " Ελλάδα\r\nnext ", "unused"]); writer.writerow(["004", "   ", "unused"])
        generated = self.bind()
        manual = {**draft(), "columns": [{"name": "Code", "type": "string", "source": "code", "required": True, "transforms": []},
                                          {"name": "Name", "type": "string", "source": "name", "required": False, "transforms": ["trim", "empty_to_null"]},
                                          {"name": "VAT", "type": "string", "literal": "", "required": False, "transforms": []}]}
        self.assertEqual(generated, manual)
        preview = execute(generated, self.root, limit=2)
        full = execute(manual, self.root, self.root / "runs")
        self.assertEqual(preview["sample"], full["sample"])
        self.assertEqual(preview["sample"][0].converted_values["Code"], "003")
        self.assertIsNone(preview["sample"][1].converted_values["Name"])
        self.assertNotIn("extra", preview["sample"][0].converted_values)
        self.assertIn("003", (Path(full["directory"]) / "valid.csv").read_text(encoding="utf-8-sig"))

    def test_same_template_sqlserver_pipeline_with_existing_batched_adapter(self):
        pipeline = self.bind(spec=draft("sqlserver"))
        with patch.dict("os.environ", {"ETL_SQL_TEST": "test-only"}), patch("pyodbc.connect") as connect:
            cursor = connect.return_value.cursor.return_value
            cursor.description = [(name, str, None, None, None, None, True) for name in ("code", "name", "extra")]
            cursor.fetchmany.side_effect = [[("003", " Name ", "unused")], []]
            result = execute(pipeline, self.root, limit=1)
            cursor.execute.assert_called_once_with("SELECT * FROM [dbo].[AnyTable]")
            cursor.fetchall.assert_not_called()
        self.assertEqual(result["sample"][0].converted_values, {"Code": "003", "Name": "Name", "VAT": ""})


if __name__ == "__main__":
    unittest.main()
