"""Conservative contracts now exercise explicitly version-2 processing.

Legacy characterization remains in test_legacy_semantics.py.
"""
from datetime import date
from decimal import Decimal

from etl.engine import process_row
from etl.conversions import convert_value
from etl.models import SourceRow
from etl.serialization import pipeline_from_dict, row_result_to_dict
from tests.semantics_support import SemanticsTestCase, field


def mapped(value, **options):
    # Every assertion explicitly selects v2; failed conversions have no value.
    result = process_row(pipeline_from_dict({
        "version": 2, "name": "v2 contract", "source": {"kind": "csv", "path": "fixture.csv"},
        "columns": [field(**options)], "destination": {"kind": "csv"},
    }), SourceRow(1, {"value": value}), {})
    errors = {}
    for error in result.errors:
        errors.setdefault(error.field, []).append(error.message)
    return dict(result.converted_values), errors



class DesiredV2AlreadySatisfiedTests(SemanticsTestCase):
    def test_codes_case_whitespace_and_multiline_text_are_preserved(self):
        for value in ("003", "  aBc003  ", "   ", 'First; "quoted"\r\nΔεύτερη γραμμή'):
            with self.subTest(value=value):
                self.assertEqual(mapped(value, type="string"), ({"value": value}, {}))

    def test_explicit_strict_integer_rejects_leading_zero_code(self):
        values, errors = mapped("003", type="int")
        self.assertNotIn("value", values)
        self.assertTrue(errors["value"])

    def test_optional_null_remains_null(self):
        self.assertEqual(mapped(None, type="string"), ({"value": None}, {}))
        self.assertEqual(mapped(None, type="int"), ({"value": None}, {}))

    def test_required_checks_without_mutating_whitespace(self):
        values, errors = mapped("   ", required=True)
        self.assertEqual(values["value"], "   ")
        self.assertTrue(errors["value"])

    def test_max_length_checks_without_truncating(self):
        values, errors = mapped("003", max_length=2)
        self.assertEqual(values["value"], "003")
        self.assertTrue(errors["value"])

    def test_explicit_trim_then_empty_to_null_produces_null(self):
        self.assertEqual(mapped("   ", transforms=["trim", "empty_to_null"]), ({"value": None}, {}))

    def test_dates_require_explicit_type_and_strict_format(self):
        self.assertEqual(mapped("2024-02-29"), ({"value": "2024-02-29"}, {}))
        self.assertEqual(mapped("2024-02-29", type="date"), ({"value": date(2024, 2, 29)}, {}))
        for value in ("2023-02-29", "2024-2-29", "29/02/2024"):
            with self.subTest(value=value):
                values, errors = mapped(value, type="date")
                self.assertNotIn("value", values)
                self.assertTrue(errors["value"])

    def test_decimal_conversion_never_round_trips_through_float(self):
        value = "12345678901234567890.1234567890123456789000"
        result = convert_value(value, "decimal", version=2)
        self.assertEqual(result.as_tuple(), Decimal(value).as_tuple())


class DesiredV2ImplementedTests(SemanticsTestCase):
    def test_empty_string_remains_empty_without_explicit_transform(self):
        values, errors = mapped("", type="string")
        self.assertEqual(values, {"value": ""})
        self.assertEqual(errors, {})

    def test_trim_alone_leaves_empty_string_after_conversion(self):
        self.assertEqual(mapped("   ", transforms=["trim"]), ({"value": ""}, {}))

    def test_empty_to_null_then_trim_leaves_empty_string(self):
        self.assertEqual(mapped("   ", transforms=["empty_to_null", "trim"]), ({"value": ""}, {}))

    def test_empty_integer_is_rejected_without_explicit_empty_to_null(self):
        _, errors = mapped("", type="int")
        self.assertIn("value", errors)

    def test_empty_date_is_rejected_without_explicit_empty_to_null(self):
        _, errors = mapped("", type="date")
        self.assertIn("value", errors)

    def test_empty_decimal_is_rejected_without_explicit_empty_to_null(self):
        _, errors = mapped("", type="decimal")
        self.assertIn("value", errors)

    def test_preview_retains_null_distinct_from_empty_string(self):
        report = self.execute_rows([{"value": None}, {"value": ""}], limit=2, version=2)
        self.assertIsNone(report["sample"][0].converted_values["value"])
        self.assertEqual(report["sample"][1].converted_values["value"], "")

    def test_rejected_diagnostics_keep_original_and_transformed_values(self):
        report = self.execute_rows([{"value": " bad "}],
                                   [{"name": "value", "source": "value", "type": "int", "transforms": ["trim"]}], limit=1, version=2)
        sample = row_result_to_dict(report["sample"][0])
        # Shape may be adapted when RowResult is wired into the engine; these
        # assertions record the information the public diagnostics must retain.
        self.assertIn("original_values", sample)
        self.assertEqual(sample["original_values"], {"value": " bad "})
        self.assertEqual(sample["transformed_values"], {"value": "bad"})
        self.assertTrue(sample["errors"]["value"])
