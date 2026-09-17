"""Stage-4 contracts: inspect native RowResults, not legacy projections."""
import csv
import json
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from etl.engine import execute, process_row, lookup_sets
from etl.models import RowResult, SourceRow
from etl.serialization import json_default, pipeline_from_dict, pipeline_to_dict
from etl.spec import ConfigError
from etl.store import Store
from tests.semantics_support import SemanticsTestCase, field


def definition(columns=None, version=2):
    return {"version": version, "name": "stages", "source": {"kind": "csv", "path": "input.csv"},
            "columns": columns or [field()], "destination": {"kind": "csv"}}


def process(value, lookups=None, **options):
    return process_row(pipeline_from_dict(definition([field(**options)])),
                       SourceRow(7, {"value": value}, 9, 11), lookups or {})


class ProcessingStageTests(SemanticsTestCase):
    def test_stage_separation_positions_and_successful_conversions_on_rejected_row(self):
        original = {"number": " 12 ", "bad": " 003 ", "memo": "a\r\nb", "unused": None}
        columns = [{"name": "count", "source": "number", "type": "int", "transforms": ["trim"]},
                   {"name": "bad", "source": "bad", "type": "int", "transforms": ["trim"]},
                   {"name": "memo", "source": "memo"}, {"name": "literal", "literal": "003"}]
        result = process_row(pipeline_from_dict(definition(columns)), SourceRow(7, original, 9, 11), {})
        original["number"] = "changed"
        self.assertIsInstance(result, RowResult)
        self.assertEqual(result.original_values["number"], " 12 ")
        self.assertEqual(dict(result.transformed_values), {"count": "12", "bad": "003", "memo": "a\r\nb", "literal": "003"})
        self.assertEqual(dict(result.converted_values), {"count": 12, "memo": "a\r\nb", "literal": "003"})
        self.assertEqual((result.source.number, result.source.line_start, result.source.line_end), (7, 9, 11))
        self.assertFalse(result.valid)
        self.assertEqual([(e.field, e.code, e.stage) for e in result.errors], [("bad", "invalid_type", "conversion")])
        self.assertTrue(result.errors[0].message)
        with self.assertRaises(TypeError):
            result.converted_values["count"] = 0

    def test_collects_independent_errors_in_stage_order(self):
        result = process("   ", required=True, max_length=1, type="int")
        self.assertEqual([e.code for e in result.errors], ["required", "max_length_exceeded", "invalid_type"])
        self.assertEqual([e.stage for e in result.errors], ["required", "max_length", "conversion"])
        self.assertEqual(dict(result.converted_values), {})

    def test_failed_conversion_never_calls_lookup(self):
        with patch("etl.engine.lookup_error", side_effect=AssertionError("dependent validation ran")):
            result = process("003", {"value": set()}, type="int")
        self.assertEqual([e.code for e in result.errors], ["invalid_type"])

    def test_successful_conversion_allows_lookup_despite_other_validation_errors(self):
        result = process("123", {"value": {456}}, max_length=2, type="int")
        self.assertEqual([e.code for e in result.errors], ["max_length_exceeded", "lookup_missing"])
        self.assertEqual(result.converted_values["value"], 123)

    def test_transform_order_and_null_empty_matrix(self):
        for kind in ("string", "int", "decimal", "date"):
            with self.subTest(kind=kind):
                self.assertIsNone(process(None, type=kind).converted_values["value"])
                empty = process("", type=kind)
                self.assertEqual(empty.valid, kind == "string")
                explicit = process("", type=kind, transforms=["empty_to_null"])
                self.assertTrue(explicit.valid)
                self.assertIsNone(explicit.converted_values["value"])
        forward = process(" \t ", transforms=["trim", "empty_to_null"])
        reverse = process(" \t ", transforms=["empty_to_null", "trim"])
        self.assertIsNone(forward.transformed_values["value"])
        self.assertEqual(reverse.converted_values["value"], "")
        self.assertEqual(process(" a\n b ", transforms=["trim"]).converted_values["value"], "a\n b")

    def test_nontext_transform_rejected_without_implicit_stringification(self):
        result = process(123, type="int", transforms=["trim"])
        self.assertEqual(result.transformed_values["value"], 123)
        self.assertEqual(dict(result.converted_values), {})
        self.assertEqual(result.errors[0].code, "invalid_transform_input")

    def test_decimal_native_precision_and_float_rejection(self):
        raw = "12345678901234567890.1234567890123456789000"
        for value in (raw, Decimal(raw), 123456789012345678901234567890):
            result = process(value, type="decimal")
            self.assertTrue(result.valid)
            self.assertEqual(result.converted_values["value"].as_tuple(), Decimal(value).as_tuple())
        result = process(0.1, type="decimal")
        self.assertFalse(result.valid)
        self.assertNotIn("value", result.converted_values)
        self.assertEqual(result.original_values["value"], 0.1)

    def test_binary_reaches_the_exporter_instead_of_a_python_repr(self):
        from pathlib import Path

        raw = bytes([0, 255])
        result = process(raw)
        self.assertTrue(result.valid)
        # V2 keeps the bytes so the exporter and diagnostics own the encoding;
        # str() here would silently write a Python repr into the export.
        self.assertEqual(result.converted_values["value"], raw)
        self.assertEqual(json.loads(json.dumps(dict(result.converted_values), default=json_default)),
                         {"value": {"$type": "bytes", "value": "AP8="}})
        report = self.execute_rows([{"value": raw}], version=2)
        with (Path(report["directory"]) / "valid.csv").open(encoding="utf-8-sig", newline="") as handle:
            self.assertEqual(list(csv.reader(handle, delimiter=";"))[1], ["base64:AP8="])
        legacy = self.execute_rows([{"value": raw}], version=1)
        with (Path(legacy["directory"]) / "valid.csv").open(encoding="utf-8-sig", newline="") as handle:
            self.assertEqual(list(csv.reader(handle, delimiter=";"))[1], [str(raw)])

    def test_explicit_case_transforms_only(self):
        self.assertEqual(process("aBc").converted_values["value"], "aBc")
        self.assertEqual(process("aBc", transforms=["upper", "lower"]).converted_values["value"], "abc")
        self.assertEqual(process("aBc", transforms=["lower", "upper"]).converted_values["value"], "ABC")

    def test_date_and_invalid_conversion_diagnostics(self):
        self.assertEqual(process("2024-02-29", type="date").converted_values["value"], date(2024, 2, 29))
        for kind, value in (("date", "2023-02-29"), ("date", "29/02/2024"), ("int", "1.0"),
                            ("int", "003"), ("decimal", "1,25"), ("float", "Infinity"), ("bool", "yes")):
            result = process(value, type=kind)
            self.assertEqual(result.errors[0].code, "invalid_type")
            self.assertNotIn("value", result.converted_values)
            self.assertEqual(result.transformed_values["value"], value)

    def test_preview_first_n_full_results_including_rejections_beyond_sample_limit(self):
        rows = [{"value": None if n % 7 == 0 else "" if n % 5 == 0 else str(n)} for n in range(137)]
        full_results = []
        full = self.execute_rows(rows, [field(type="int")], observed=full_results, version=2)
        self.assertEqual(len(full["sample"]), 20)
        self.assertEqual(len(full_results), 137)
        for limit in (1, 7, 100):
            preview_results = []
            preview = self.execute_rows(rows, [field(type="int")], limit=limit, observed=preview_results, version=2)
            self.assertEqual(preview_results, full_results[:limit])
            self.assertEqual(preview["sample"], preview_results)
            self.assertEqual(preview["invalid"], sum(not r.valid for r in preview_results))

    def test_real_csv_positions_rejected_json_and_shared_preview(self):
        (self.root / "input.csv").write_bytes(b'value;memo\n003;"a\nb"\n12;ok\n')
        spec = definition([field(type="int"), {"name": "memo", "source": "memo"}])
        preview = execute(spec, self.root, limit=1)
        full = execute(spec, self.root, self.root / "out")
        self.assertEqual(preview["sample"], full["sample"][:1])
        result = preview["sample"][0]
        self.assertEqual((result.source.number, result.source.line_start, result.source.line_end), (1, 2, 3))
        from pathlib import Path
        with (Path(full["directory"]) / "rejected.csv").open(encoding="utf-8-sig", newline="") as handle:
            rejected = next(csv.DictReader(handle, delimiter=";"))
        self.assertEqual(json.loads(rejected["source_json"]), {"value": "003", "memo": "a\nb"})
        stages = json.loads(rejected["mapped_json"])
        self.assertEqual(stages["transformed_values"]["value"], "003")
        self.assertNotIn("value", stages["converted_values"])
        self.assertEqual(json.loads(rejected["errors_json"])["value"][0]["stage"], "conversion")

    def test_lookup_reference_empty_is_not_null_and_uses_same_explicit_transforms(self):
        (self.root / "lookup.csv").write_text('key\n""\n 003 \n', encoding="utf-8")
        spec = definition([field(transforms=["trim"], lookup={"source": {"kind": "csv", "path": "lookup.csv"}, "column": "key"})])
        allowed = lookup_sets(spec, self.root)
        self.assertEqual(allowed, {"value": {"", "003"}})
        for value in (None, "", " 003 "):
            self.assertTrue(process_row(pipeline_from_dict(spec), SourceRow(1, {"value": value}), allowed).valid)
        spec["version"] = 1
        self.assertEqual(lookup_sets(spec, self.root), {"value": {"003"}})
        spec["version"] = 2
        spec["columns"][0]["type"] = "int"
        with self.assertRaisesRegex(ConfigError, "Lookup contains an invalid int"):
            lookup_sets(spec, self.root)

    def test_version_boundary_and_history_snapshot(self):
        store = Store(self.root / "history.sqlite")
        for version in (1, 2):
            spec = definition(version=version)
            self.assertEqual(pipeline_to_dict(pipeline_from_dict(spec)), spec)
            result = process_row(pipeline_from_dict(spec), SourceRow(1, {"value": ""}), {})
            self.assertEqual(result.converted_values["value"], None if version == 1 else "")
            saved = store.save(spec)
            run = store.create_run(spec)
            spec["version"] = 3 - version
            store.save(spec, saved)
            self.assertEqual(store.run(run)["spec"]["version"], version)
        native = process(Decimal("12345678901234567890.12300"), type="decimal")
        store.update_run(run, "completed", {"sample": [native]})
        encoded = store.run(run)["report"]["sample"][0]
        self.assertEqual(encoded["converted_values"]["value"], {"$type": "decimal", "value": "12345678901234567890.12300"})
        self.assertIsInstance(native.converted_values["value"], Decimal)
        self.assertEqual(json.loads(json.dumps(native, default=json_default)), encoded)
