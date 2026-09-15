"""Domain immutability and lossless version-1 definition conversion."""
import copy
import json
import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from etl.models import (
    FieldError, FieldMapping, Pipeline, PipelineRun, RowResult, RunResult,
    SourceColumn, SourceDefinition, SourceRow, Transform, UNSET, ValidationRule,
)
from etl.serialization import pipeline_from_dict, pipeline_from_json, pipeline_to_dict, pipeline_to_json
from etl.spec import ConfigError

ROOT = Path(__file__).resolve().parents[1]


def definition():
    return {
        "version": 1, "name": "ERP · mappings",
        "source": {"kind": "csv", "path": "inputs/erp.csv"},
        "columns": [{"name": "code", "source": "Code"}],
        "destination": {"kind": "csv"},
    }


class V1ModelCodecTests(unittest.TestCase):
    def assert_round_trip(self, original):
        before = copy.deepcopy(original)
        model = pipeline_from_dict(original)
        self.assertEqual(pipeline_to_dict(model), before)
        self.assertEqual(json.loads(pipeline_to_json(model)), before)
        self.assertEqual(pipeline_to_dict(pipeline_from_json(json.dumps(before))), before)
        self.assertEqual(pipeline_to_dict(pipeline_from_json(json.dumps(before).encode())), before)
        self.assertEqual(original, before)
        return model

    def test_bundled_demo_round_trips(self):
        self.assert_round_trip(json.loads((ROOT / "examples/customers.json").read_text(encoding="utf-8")))

    def test_omitted_defaults_stay_omitted(self):
        model = self.assert_round_trip(definition())
        self.assertIsNone(model.columns[0].target_type)
        self.assertIsNone(model.columns[0].transforms)
        self.assertIs(model.columns[0].literal, UNSET)
        self.assertEqual(model.columns[0].validations, ())

    def test_explicit_defaults_false_and_empty_transforms_stay_present(self):
        spec = definition()
        spec["source"].update(delimiter=";", encoding="utf-8-sig")
        spec["destination"]["delimiter"] = ";"
        spec["columns"][0].update(type="string", required=False, transforms=[])
        model = self.assert_round_trip(spec)
        self.assertEqual(model.columns[0].target_type, "string")
        self.assertEqual(model.columns[0].transforms, ())
        self.assertIs(model.columns[0].validations[0].parameters["value"], False)

    def test_literal_null_empty_false_zero_and_strings_remain_distinct(self):
        for value in (None, "", "003", "   ", False, True, 0, 1, 0.0, -0.0, 1.25, 123456789012345678901234567890):
            with self.subTest(value=value):
                spec = definition()
                spec["columns"] = [{"name": "literal", "literal": value}]
                model = self.assert_round_trip(spec)
                result = pipeline_to_dict(model)["columns"][0]
                self.assertNotIn("source", result)
                self.assertIs(type(result["literal"]), type(value))
                self.assertIs(type(model.columns[0].literal), type(value))

    def test_all_v1_types_transforms_and_rules_round_trip(self):
        spec = definition()
        spec["columns"] = [
            {"name": kind, "source": "Value", "type": kind, "required": True,
             "max_length": 100, "transforms": ["trim", "empty_to_null", "lower", "upper", "trim"]}
            for kind in ("string", "int", "decimal", "float", "bool", "date", "datetime")
        ]
        model = self.assert_round_trip(spec)
        self.assertEqual([t.name for t in model.columns[0].transforms], ["trim", "empty_to_null", "lower", "upper", "trim"])
        self.assertEqual([r.name for r in model.columns[0].validations], ["required", "max_length"])

    def test_csv_and_sql_source_and_lookup_definitions_round_trip(self):
        sources = [
            {"kind": "csv", "path": "inputs/κόσμος.csv", "delimiter": "|", "encoding": "utf-8"},
            {"kind": "sqlserver", "connection_env": "ETL_SQL_MAIN", "schema": "dbo", "table": "A]B"},
        ]
        for source in sources:
            for lookup_source in sources:
                for destination in ({"kind": "xlsx"}, {"kind": "csv", "delimiter": "\t"}):
                    with self.subTest(source=source["kind"], lookup=lookup_source["kind"], destination=destination):
                        spec = definition()
                        spec["source"] = source
                        spec["destination"] = destination
                        spec["columns"][0]["lookup"] = {"source": lookup_source, "column": "CountryCode"}
                        model = self.assert_round_trip(spec)
                        self.assertIsInstance(model.columns[0].validations[0].parameters["source"], SourceDefinition)

    def test_quoted_decimal_and_multiline_literals_are_lossless(self):
        raw = "12345678901234567890.1234567890123456789000"
        spec = definition()
        spec["columns"] = [{"name": "amount", "literal": raw, "type": "decimal"},
                           {"name": "notes", "literal": '  Δοκιμή; "quoted"\r\nLine 2\n  '}]
        self.assert_round_trip(spec)

    def test_round_trip_is_structural_not_json_whitespace(self):
        original = json.dumps(definition(), indent=4, ensure_ascii=False)
        compact = pipeline_to_json(pipeline_from_json(original))
        self.assertNotEqual(compact, original)
        self.assertEqual(json.loads(compact), json.loads(original))

    def test_unsupported_version_is_rejected(self):
        spec = definition()
        spec["version"] = 3
        with self.assertRaisesRegex(ConfigError, "version must be 1"):
            pipeline_from_dict(spec)
        with self.assertRaisesRegex(ConfigError, "version must be 1"):
            pipeline_to_dict(replace(pipeline_from_dict(definition()), version=3))

    def test_unknown_v1_options_still_fail_validation(self):
        spec = definition()
        spec["columns"][0]["unknown"] = "must not disappear"
        with self.assertRaises(ConfigError):
            pipeline_from_dict(spec)

    def test_manually_constructed_models_serialize_through_v1_validation(self):
        model = Pipeline(
            name="Built in Python", source=SourceDefinition("csv", {"path": "input.csv"}),
            columns=[FieldMapping("code", source="Code", target_type="string", transforms=[Transform("trim")],
                                  validations=[ValidationRule("required", {"value": False})])],
            destination={"kind": "csv"},
        )
        spec = pipeline_to_dict(model)
        self.assertEqual(spec["columns"][0], {"name": "code", "source": "Code", "type": "string", "transforms": ["trim"], "required": False})
        self.assertEqual(pipeline_from_dict(spec), model)

    def test_unrepresentable_model_rules_do_not_silently_disappear(self):
        model = pipeline_from_dict(definition())
        bad_rules = [
            [ValidationRule("unknown", {})],
            [ValidationRule("required", {"value": True, "extra": 1})],
            [ValidationRule("required", {"value": True}), ValidationRule("required", {"value": False})],
        ]
        for rules in bad_rules:
            with self.subTest(rules=rules), self.assertRaises(ConfigError):
                pipeline_to_dict(replace(model, columns=[replace(model.columns[0], validations=rules)]))


class DomainImmutabilityTests(unittest.TestCase):
    def test_mutating_input_tree_cannot_change_definition(self):
        spec = definition()
        spec["columns"][0].update(transforms=["trim"], lookup={"source": {"kind": "csv", "path": "lookup.csv"}, "column": "Code"})
        expected = copy.deepcopy(spec)
        model = pipeline_from_dict(spec)
        spec["name"] = "Changed"
        spec["source"]["path"] = "changed.csv"
        spec["destination"]["kind"] = "xlsx"
        spec["columns"][0]["transforms"].append("upper")
        spec["columns"][0]["lookup"]["source"]["path"] = "changed.csv"
        spec["columns"].clear()
        self.assertEqual(pipeline_to_dict(model), expected)

    def test_model_does_not_expose_mutable_definition_containers(self):
        spec = definition()
        spec["columns"][0].update(transforms=["trim"], lookup={"source": {"kind": "csv", "path": "lookup.csv"}, "column": "Code"})
        model = pipeline_from_dict(spec)
        with self.assertRaises(FrozenInstanceError):
            model.name = "Changed"
        with self.assertRaises(TypeError):
            model.destination["kind"] = "xlsx"
        with self.assertRaises(TypeError):
            model.source.options["path"] = "changed.csv"
        with self.assertRaises(TypeError):
            model.columns[0] = model.columns[0]
        with self.assertRaises(TypeError):
            model.columns[0].validations[0].parameters["source"].options["path"] = "changed.csv"
        with self.assertRaises(FrozenInstanceError):
            model.columns[0].transforms[0].name = "upper"
        self.assertFalse(hasattr(model, "__dict__"))

    def test_each_serialization_returns_a_fresh_mutable_tree(self):
        spec = definition()
        spec["columns"][0]["transforms"] = ["trim"]
        model = pipeline_from_dict(spec)
        first, second = pipeline_to_dict(model), pipeline_to_dict(model)
        first["source"]["path"] = "changed.csv"
        first["columns"][0]["transforms"].append("upper")
        self.assertEqual(second, spec)
        self.assertEqual(pipeline_to_dict(model), spec)

    def test_direct_constructor_copies_and_freezes_lists_and_nested_mappings(self):
        parameters = {"value": ["a", {"nested": ["b"]}]}
        rule = ValidationRule("fixture", parameters)
        parameters["value"][1]["nested"].append("changed")
        self.assertEqual(rule.parameters["value"][1]["nested"], ("b",))
        with self.assertRaises(TypeError):
            rule.parameters["value"][1]["nested"] = ("changed",)

    def test_result_models_preserve_distinct_stages_and_native_values(self):
        native = {"null": None, "empty": "", "code": "003", "amount": Decimal("12.3400"),
                  "date": date(2024, 2, 29), "time": datetime(2024, 2, 29, 12), "nested": {"items": [1, 2]}}
        source = SourceRow(7, native, line_start=9, line_end=11)
        transformed = {"value": "bad"}
        errors = [FieldError("value", "invalid_type", "Expected integer", "conversion")]
        result = RowResult(source, transformed, {}, errors)
        transformed["value"] = "changed"
        errors.clear()
        native["nested"]["items"].append(3)
        self.assertFalse(result.valid)
        self.assertEqual(result.source.number, 7)
        self.assertEqual((result.source.line_start, result.source.line_end), (9, 11))
        self.assertIsNone(result.original_values["null"])
        self.assertEqual(result.original_values["empty"], "")
        self.assertEqual(result.original_values["amount"].as_tuple(), Decimal("12.3400").as_tuple())
        self.assertEqual(result.original_values["nested"]["items"], (1, 2))
        self.assertEqual(result.transformed_values["value"], "bad")
        self.assertEqual(result.errors[0].code, "invalid_type")
        with self.assertRaises(TypeError):
            result.original_values["code"] = "3"
        with self.assertRaises(TypeError):
            result.transformed_values["value"] = "changed"
        with self.assertRaises(FrozenInstanceError):
            result.errors[0].message = "changed"

    def test_pipeline_run_captures_definition_and_run_sample_is_immutable(self):
        spec = definition()
        run = PipelineRun("run-id", pipeline_from_dict(spec), "queued", "2026-09-15T00:00:00Z")
        spec["columns"][0]["source"] = "Changed"
        self.assertEqual(run.pipeline.columns[0].source, "Code")
        row = RowResult(SourceRow(1, {"code": "003"}), {"code": "003"}, {"code": "003"})
        samples = [row]
        result = RunResult(1, 1, 0, samples, preview=True)
        samples.clear()
        self.assertEqual(result.sample, (row,))
        self.assertTrue(result.sample[0].valid)
        with self.assertRaises(FrozenInstanceError):
            run.status = "completed"
        with self.assertRaises(FrozenInstanceError):
            SourceColumn("Code", "nvarchar", True).nullable = False

    def test_unsupported_mutable_objects_are_rejected_instead_of_retained(self):
        with self.assertRaises(TypeError):
            SourceRow(1, {"bytes": bytearray(b"mutable")})
        with self.assertRaises(TypeError):
            SourceRow(1, {"object": object()})

    def test_field_source_and_literal_are_mutually_exclusive_including_null(self):
        with self.assertRaises(ValueError):
            FieldMapping("value")
        with self.assertRaises(ValueError):
            FieldMapping("value", source="Code", literal=None)
        self.assertIsNone(FieldMapping("value", literal=None).literal)
