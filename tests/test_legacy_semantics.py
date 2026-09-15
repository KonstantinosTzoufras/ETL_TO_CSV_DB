"""Characterize version 1, including behavior intentionally deferred for v2.

These tests must stay green during the structural refactor. They are not an
endorsement of implicit empty-to-null conversion or lossy diagnostics.
"""
import csv
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from etl.engine import convert, transform
from tests.semantics_support import SemanticsTestCase, field, mapped


class LegacyV1SemanticsTests(SemanticsTestCase):
    def test_leading_zero_string_is_preserved_exactly(self):
        self.assertEqual(mapped("003", type="string"), ({"value": "003"}, {}))
        self.assertEqual(mapped("003"), ({"value": "003"}, {}))

    def test_leading_zero_integer_is_rejected_without_replacement(self):
        values, errors = mapped("003", type="int")
        self.assertEqual(values["value"], "003")
        self.assertIn("without leading zeros", errors["value"][0])

    def test_none_remains_none_for_every_optional_type(self):
        for kind in ("string", "int", "decimal", "float", "bool", "date", "datetime"):
            with self.subTest(kind=kind):
                self.assertEqual(mapped(None, type=kind), ({"value": None}, {}))

    def test_empty_string_is_implicitly_null_for_every_optional_type(self):
        for kind in ("string", "int", "decimal", "float", "bool", "date", "datetime"):
            with self.subTest(kind=kind):
                self.assertEqual(mapped("", type=kind), ({"value": None}, {}))

    def test_whitespace_and_case_remain_unchanged_without_transforms(self):
        for value in ("   ", "\t\n", "  aBc003  "):
            with self.subTest(value=value):
                self.assertEqual(mapped(value), ({"value": value}, {}))

    def test_trim_only_changes_edges_but_conversion_then_nulls_empty(self):
        self.assertEqual(transform("  A  B\nC \t", {"transforms": ["trim"]}), "A  B\nC")
        self.assertEqual(transform("   ", {"transforms": ["trim"]}), "")
        self.assertEqual(mapped("   ", transforms=["trim"]), ({"value": None}, {}))

    def test_empty_to_null_matches_only_empty_text(self):
        for value, expected in ((None, None), ("", None), ("   ", "   "), ("003", "003")):
            with self.subTest(value=value):
                self.assertEqual(transform(value, {"transforms": ["empty_to_null"]}), expected)

    def test_transform_order_is_distinct_before_conversion_but_collapsed_after(self):
        first = ["trim", "empty_to_null"]
        second = ["empty_to_null", "trim"]
        self.assertIsNone(transform("   ", {"transforms": first}))
        self.assertEqual(transform("   ", {"transforms": second}), "")
        self.assertEqual(mapped("   ", transforms=first), ({"value": None}, {}))
        self.assertEqual(mapped("   ", transforms=second), ({"value": None}, {}))

    def test_required_rejects_null_empty_and_whitespace_without_trimming_output(self):
        for value in (None, "", "   ", "\t\n"):
            with self.subTest(value=value):
                values, errors = mapped(value, required=True)
                self.assertEqual(errors, {"value": ["required value is missing"]})
                self.assertEqual(values["value"], None if value in (None, "") else value)
        self.assertEqual(mapped(" 003 ", required=True), ({"value": " 003 "}, {}))

    def test_max_length_runs_after_transforms_and_before_conversion(self):
        values, errors = mapped(" 003 ", max_length=3)
        self.assertEqual(values["value"], " 003 ")
        self.assertIn("exceeds 3", errors["value"][0])
        self.assertEqual(mapped(" 003 ", transforms=["trim"], max_length=3), ({"value": "003"}, {}))
        # Decimal conversion removes the plus sign, but length is checked first.
        values, errors = mapped("+12", type="decimal", max_length=2)
        self.assertEqual(values["value"], Decimal("12"))
        self.assertEqual(errors, {"value": ["exceeds 2 characters"]})
        self.assertEqual(mapped(None, max_length=1), ({"value": None}, {}))

    def test_strict_dates_are_explicit_and_not_inferred_for_strings(self):
        self.assertEqual(convert("2024-02-29", "date"), date(2024, 2, 29))
        self.assertEqual(mapped("2024-02-29"), ({"value": "2024-02-29"}, {}))
        for value in ("2023-02-29", "2025-02-30", "29/02/2024", "20240229", "2024-2-29", " 2024-02-29 "):
            with self.subTest(value=value):
                values, errors = mapped(value, type="date")
                self.assertEqual(values["value"], value)
                self.assertIn("value", errors)
        self.assertEqual(mapped(" 2024-02-29 ", type="date", transforms=["trim"]), ({"value": date(2024, 2, 29)}, {}))

    def test_decimal_preserves_text_and_native_decimal_precision_and_scale(self):
        raw = "12345678901234567890.1234567890123456789000"
        for value in (raw, Decimal(raw)):
            with self.subTest(value=value):
                result = convert(value, "decimal")
                self.assertIsInstance(result, Decimal)
                self.assertEqual(result.as_tuple(), Decimal(raw).as_tuple())

    def test_multiline_text_is_preserved_through_processing_and_csv_export(self):
        raw = '  First; "quoted"\r\nΔεύτερη γραμμή\nLast  '
        report = self.execute_rows([{"value": raw}])
        self.assertEqual(report["sample"][0]["values"]["value"], raw)
        with (Path(report["directory"]) / "valid.csv").open(encoding="utf-8-sig", newline="") as handle:
            self.assertEqual(list(csv.reader(handle, delimiter=";")), [["value"], [raw]])

    def test_failed_type_conversions_retain_input_and_field_errors(self):
        for kind, value in (("int", "12.3"), ("int", "abc"), ("decimal", "1,25"), ("float", "NaN"), ("bool", "yes"), ("datetime", "2024-02-29")):
            with self.subTest(kind=kind, value=value):
                values, errors = mapped(value, type=kind)
                self.assertEqual(values, {"value": value})
                self.assertTrue(errors["value"])

    def test_required_and_type_errors_accumulate_in_order(self):
        values, errors = mapped(" ", required=True, type="int")
        self.assertEqual(values, {"value": " "})
        self.assertEqual(errors["value"][0], "required value is missing")
        self.assertIn("integer", errors["value"][1])

    def test_rejections_keep_originals_but_mapped_values_mix_processing_stages(self):
        original = {"number": " 12 ", "bad": " x ", "empty": "", "null": None}
        columns = [
            {"name": "number", "source": "number", "type": "int", "transforms": ["trim"]},
            {"name": "bad", "source": "bad", "type": "int", "transforms": ["trim"]},
            {"name": "empty", "source": "empty"}, {"name": "null", "source": "null"},
        ]
        report = self.execute_rows([original], columns)
        with (Path(report["directory"]) / "rejected.csv").open(encoding="utf-8-sig", newline="") as handle:
            row = next(csv.DictReader(handle, delimiter=";"))
        self.assertEqual(row["record_number"], "1")
        self.assertEqual(json.loads(row["source_json"]), original)
        self.assertEqual(json.loads(row["mapped_json"]), {"number": 12, "bad": "x", "empty": None, "null": None})
        self.assertEqual(set(json.loads(row["errors_json"])), {"bad"})
        self.assertNotIn("transformed_json", row)
        sample = report["sample"][0]
        self.assertNotIn("original_values", sample)
        self.assertEqual(sample["values"]["empty"], sample["values"]["null"])

    def test_preview_matches_first_n_full_processing_results_including_rejections(self):
        rows = [{"value": "bad" if i % 3 == 0 else str(i)} for i in range(137)]
        for limit in (1, 7, 100):
            with self.subTest(limit=limit):
                full_results, preview_results = [], []
                full = self.execute_rows(rows, [field(type="int")], observed=full_results)
                preview = self.execute_rows(rows, [field(type="int")], limit=limit, observed=preview_results)
                self.assertEqual(full["processed"], len(rows))
                self.assertEqual(len(preview_results), limit)
                self.assertEqual(preview_results, full_results[:limit])
                self.assertEqual(preview["processed"], limit)
                self.assertEqual(preview["invalid"], sum(bool(result[1][1]) for result in full_results[:limit]))
                self.assertEqual(preview["valid"] + preview["invalid"], limit)
                self.assertNotIn("directory", preview)
