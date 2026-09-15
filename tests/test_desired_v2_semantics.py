"""Desired conservative ERP contract, NOT a version-2 implementation.

These assertions probe the CURRENT processor. Already-satisfied requirements
run normally. Known gaps are individual expected failures until the approved
semantic-change stage; an unexpected success requires reviewing that marker.
No v2 definitions are accepted by production code in Stage 1 or Stage 2.
"""
import unittest
from datetime import date
from decimal import Decimal

from etl.engine import convert
from tests.semantics_support import SemanticsTestCase, mapped


class DesiredV2AlreadySatisfiedTests(SemanticsTestCase):
    def test_codes_case_whitespace_and_multiline_text_are_preserved(self):
        for value in ("003", "  aBc003  ", "   ", 'First; "quoted"\r\nΔεύτερη γραμμή'):
            with self.subTest(value=value):
                self.assertEqual(mapped(value, type="string"), ({"value": value}, {}))

    def test_explicit_strict_integer_rejects_leading_zero_code(self):
        values, errors = mapped("003", type="int")
        self.assertEqual(values["value"], "003")
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
                self.assertEqual(values["value"], value)
                self.assertTrue(errors["value"])

    def test_decimal_conversion_never_round_trips_through_float(self):
        value = "12345678901234567890.1234567890123456789000"
        result = convert(value, "decimal")
        self.assertEqual(result.as_tuple(), Decimal(value).as_tuple())


class DesiredV2PendingTests(SemanticsTestCase):
    @unittest.expectedFailure
    def test_empty_string_remains_empty_without_explicit_transform(self):
        values, errors = mapped("", type="string")
        self.assertEqual(values, {"value": ""})
        self.assertEqual(errors, {})

    @unittest.expectedFailure
    def test_trim_alone_leaves_empty_string_after_conversion(self):
        self.assertEqual(mapped("   ", transforms=["trim"]), ({"value": ""}, {}))

    @unittest.expectedFailure
    def test_empty_to_null_then_trim_leaves_empty_string(self):
        self.assertEqual(mapped("   ", transforms=["empty_to_null", "trim"]), ({"value": ""}, {}))

    @unittest.expectedFailure
    def test_empty_integer_is_rejected_without_explicit_empty_to_null(self):
        _, errors = mapped("", type="int")
        self.assertIn("value", errors)

    @unittest.expectedFailure
    def test_empty_date_is_rejected_without_explicit_empty_to_null(self):
        _, errors = mapped("", type="date")
        self.assertIn("value", errors)

    @unittest.expectedFailure
    def test_empty_decimal_is_rejected_without_explicit_empty_to_null(self):
        _, errors = mapped("", type="decimal")
        self.assertIn("value", errors)

    @unittest.expectedFailure
    def test_preview_retains_null_distinct_from_empty_string(self):
        report = self.execute_rows([{"value": None}, {"value": ""}], limit=2)
        self.assertIsNone(report["sample"][0]["values"]["value"])
        self.assertEqual(report["sample"][1]["values"]["value"], "")

    @unittest.expectedFailure
    def test_rejected_diagnostics_keep_original_and_transformed_values(self):
        report = self.execute_rows([{"value": " bad "}],
                                   [{"name": "value", "source": "value", "type": "int", "transforms": ["trim"]}], limit=1)
        sample = report["sample"][0]
        # Shape may be adapted when RowResult is wired into the engine; these
        # assertions record the information the public diagnostics must retain.
        self.assertIn("original_values", sample)
        self.assertEqual(sample["original_values"], {"value": " bad "})
        self.assertEqual(sample["transformed_values"], {"value": "bad"})
        self.assertTrue(sample["errors"]["value"])
