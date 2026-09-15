import csv
from contextlib import ExitStack
from datetime import date, datetime
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from etl.diagnostics import present_result, rejected_page, value_cell
from etl.engine import process_row
from etl.exporters import RejectedWriter
from etl.models import FieldError, RowResult, SourceRow
from etl.serialization import pipeline_from_dict
from etl.spec import ConfigError


def definition(version=2):
    return {"version": version, "name": "Diagnostics fixture", "source": {"kind": "csv", "path": "gone.csv"},
            "columns": [{"name": "identifier", "source": "code", "type": "string", "transforms": ["trim"]},
                        {"name": "bad", "source": "number", "type": "int", "max_length": 2},
                        {"name": "required", "source": "empty", "required": True}],
            "destination": {"kind": "csv", "delimiter": ",", "encoding": "utf-8"} if version == 2 else {"kind": "csv"}}


class DiagnosticPresentationTests(unittest.TestCase):
    def result(self):
        spec = definition()
        result = process_row(pipeline_from_dict(spec), SourceRow(7, {"code": " 003 ", "number": "003", "empty": ""}, 9, 11), {})
        return spec, result

    def test_rejected_stages_positions_multiple_errors_and_missing_conversion(self):
        spec, result = self.result()
        view = present_result(result, spec)
        self.assertFalse(view["valid"])
        self.assertEqual(view["summary"], "Rejected: 2 fields, 3 errors")
        self.assertEqual(view["record"], "7")
        self.assertEqual(view["source_position"], {"line_start": 9, "line_end": 11})
        code, number, required = view["fields"]
        self.assertEqual(code["original"]["text"], " 003 ")
        self.assertEqual(code["transformed"]["text"], "003")
        self.assertIn("Transformed value differs", code["states"])
        self.assertIn("Successfully converted", code["states"])
        self.assertEqual([e["stage"] for e in number["errors"]], ["max_length", "conversion"])
        self.assertIn("Validation failure", number["states"])
        self.assertIn("Conversion failure", number["states"])
        self.assertFalse(number["converted"]["available"])
        self.assertNotIn("text", number["converted"])
        self.assertTrue(required["converted"]["available"])
        self.assertEqual(required["converted"]["text"], "")

    def test_valid_row_unchanged_and_null_conversion_are_successful(self):
        spec = definition()
        spec["columns"] = [{"name": "identifier", "source": "code"}, {"name": "nothing", "literal": None}]
        result = process_row(pipeline_from_dict(spec), SourceRow(1, {"code": "003"}), {})
        view = present_result(result, spec)
        self.assertTrue(view["valid"])
        self.assertEqual(view["summary"], "Valid")
        self.assertIn("Unchanged", view["fields"][0]["states"])
        self.assertEqual(view["fields"][1]["origin"], "Literal from pipeline snapshot")
        self.assertTrue(view["fields"][1]["converted"]["available"])
        self.assertEqual(view["fields"][1]["converted"]["label"], "NULL")

    def test_null_empty_whitespace_multiline_unicode_and_exact_scalars(self):
        self.assertEqual(value_cell(None)["label"], "NULL")
        self.assertEqual(value_cell("")["label"], "Empty string (length 0)")
        self.assertEqual(value_cell(" \t ")["label"], "Whitespace-only string (length 3)")
        content = ' Ελληνικά\r\n"text"\n '
        self.assertEqual(value_cell(content)["text"], content)
        self.assertEqual(json.loads(value_cell(content)["escaped"]), content)
        for value, kind, text in ((Decimal("12345678901234567890.4500"), "decimal", "12345678901234567890.4500"),
                                  (date(2024, 2, 29), "date", "2024-02-29"),
                                  (datetime(2024, 2, 29, 12, 30, 1, 123456), "datetime", "2024-02-29T12:30:01.123456"),
                                  (9223372036854775807, "integer", "9223372036854775807"),
                                  (True, "boolean", "true")):
            with self.subTest(kind=kind):
                cell = value_cell(value)
                self.assertEqual((cell["type"], cell["text"]), (kind, text))
                self.assertEqual(json.loads(json.dumps(cell))["text"], text)

    def test_no_processing_during_presentation(self):
        spec, result = self.result()
        with patch("etl.engine.process_row", side_effect=AssertionError("must not process")), patch("etl.sources.create_source", side_effect=AssertionError("must not read")):
            self.assertFalse(present_result(result, spec)["valid"])

    def test_transform_failure_and_sql_execution_relative_positions(self):
        spec = definition()
        spec["source"] = {"kind": "sqlserver", "connection_env": "ETL_SQL_TEST", "schema": "dbo", "table": "T"}
        spec["columns"] = [{"name": "code", "source": "code"}]
        result = RowResult(SourceRow(2, {"code": "003"}), {"code": "003"}, {},
                           (FieldError("code", "invalid_transform_input", "failed", "transform"),))
        view = present_result(result, spec)
        self.assertEqual(view["source_kind"], "sqlserver")
        self.assertEqual(view["source_position"], {"line_start": None, "line_end": None})
        self.assertIn("Transform failure", view["fields"][0]["states"])
        self.assertEqual(view["fields"][0]["converted"]["label"], "Conversion not completed")


class StoredDiagnosticTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.data = Path(temp.name)
        self.directory = self.data / "runs" / "output"
        self.directory.mkdir(parents=True)
        self.spec = definition()
        self.run = {"id": "saved-run", "name": self.spec["name"], "status": "completed", "spec": self.spec, "report": {"directory": str(self.directory)}}

    def write(self, count=3):
        with ExitStack() as stack:
            writer = RejectedWriter(self.directory, self.spec["destination"], stack, version=self.spec["version"])
            for i in range(count):
                result = process_row(pipeline_from_dict(self.spec), SourceRow(i + 1, {"code": ' 003\n"Ελλάδα" ', "number": "bad", "empty": ""}, i + 2, i + 3), {})
                writer.write_result(result)

    def page(self, **kwargs):
        return rejected_page(self.run, self.data, "secret", **kwargs)

    def test_existing_v2_file_snapshot_paging_without_source(self):
        self.write()
        with patch("etl.engine.execute", side_effect=AssertionError("must not execute")), patch("etl.sources.create_source", side_effect=AssertionError("must not read")):
            first = self.page(limit=2)
            second = self.page(limit=2, cursor=first["next_cursor"])
        self.assertEqual([r["record"] for r in first["rows"]], ["1", "2"])
        self.assertEqual([r["record"] for r in second["rows"]], ["3"])
        self.assertIsNone(second["next_cursor"])
        field = first["rows"][0]["fields"][0]
        self.assertEqual(field["name"], "identifier")
        self.assertEqual(field["original"]["text"], ' 003\n"Ελλάδα" ')
        self.assertEqual(field["converted"]["text"], '003\n"Ελλάδα"')

    def test_legacy_evidence_is_not_reconstructed(self):
        self.spec.clear(); self.spec.update(definition(1))
        self.write()
        page = self.page()
        self.assertTrue(page["legacy"])
        field = page["rows"][0]["fields"][1]
        self.assertFalse(field["transformed"]["available"])
        self.assertFalse(field["converted"]["available"])
        self.assertTrue(field["legacy_value"]["available"])
        self.assertTrue(all(e["stage"] is None and e["code"] is None for e in field["errors"]))

    def test_exact_stored_decimal_date_bigint_and_null(self):
        values = {"decimal": Decimal("12345678901234567890.4500"), "date": date(2024, 2, 29), "big": 9223372036854775807, "null": None, "empty": ""}
        self.spec["columns"] = [{"name": name, "source": name} for name in values]
        result = RowResult(SourceRow(1, values), values, values, (FieldError("empty", "required", "required", "required"),))
        with ExitStack() as stack:
            RejectedWriter(self.directory, self.spec["destination"], stack, version=2).write_result(result)
        view = self.page()["rows"][0]
        for field in view["fields"]:
            self.assertEqual(field["converted"], value_cell(values[field["name"]]))

    def test_empty_file_missing_failed_run_path_escape_and_bad_cursor(self):
        self.write(0)
        self.assertEqual(self.page()["rows"], [])
        for change in ({"status": "failed"}, {"report": {"directory": str(self.data.parent)}}):
            with self.assertRaises(ConfigError):
                rejected_page({**self.run, **change}, self.data, "secret")
        with self.assertRaises(ConfigError):
            self.page(cursor="not-a-cursor")
        with self.assertRaises(ConfigError):
            self.page(limit=101)
        (self.directory / "rejected.csv").unlink()
        with self.assertRaises(ConfigError):
            self.page()

    def test_cursor_bound_to_run_file_and_secret(self):
        self.write()
        cursor = self.page(limit=1)["next_cursor"]
        for run, secret in (({**self.run, "id": "other"}, "secret"), (self.run, "wrong")):
            with self.assertRaises(ConfigError):
                rejected_page(run, self.data, secret, cursor=cursor)
        with (self.directory / "rejected.csv").open("a", encoding="utf-8") as handle:
            handle.write("\n")
        with self.assertRaises(ConfigError):
            self.page(cursor=cursor)

    def test_malformed_file_fails_without_partial_page(self):
        self.write()
        with (self.directory / "rejected.csv").open("a", encoding="utf-8") as handle:
            handle.write("broken\n")
        with self.assertRaises(ConfigError):
            self.page()

    def test_page_byte_budget_preserves_resume_record(self):
        self.write()
        one = self.page(limit=1)
        size = len(json.dumps(one["rows"][0], ensure_ascii=False).encode("utf-8"))
        with patch("etl.diagnostics.MAX_PAGE_BYTES", size + 20):
            first = self.page()
            second = self.page(cursor=first["next_cursor"])
        self.assertEqual([r["record"] for r in first["rows"]], ["1"])
        self.assertEqual([r["record"] for r in second["rows"]], ["2"])


if __name__ == "__main__":
    unittest.main()
