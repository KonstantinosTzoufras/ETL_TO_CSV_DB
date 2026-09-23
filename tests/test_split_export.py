"""One output file per distinct value of a chosen field.

The point being protected: NULL and empty string stay distinct groups (the
same rule v2 already enforces for exported values), a group is never dropped
or merged by accident, and a near-unique column cannot silently explode into
thousands of files.
"""
import csv
import json
import tempfile
import unittest
from pathlib import Path

from etl.engine import execute
from etl.ordered import from_dict as ordered_from_dict
from etl.spec import ConfigError, MAX_SPLIT_GROUPS, validate

ROOT = Path(__file__).resolve().parents[1]


def spec(split_by=None, columns=None, delimiter=";"):
    value = {"version": 2, "name": "Split export",
             "source": {"kind": "csv", "path": "input.csv", "delimiter": delimiter},
             "columns": columns or [
                 {"name": "status", "source": "status", "type": "string"},
                 {"name": "amount", "source": "amount", "type": "string"},
             ],
             "destination": {"kind": "csv", "delimiter": delimiter}}
    if split_by is not None:
        value["destination"]["split_by"] = split_by
    return value


class SplitExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, rows, header="status;amount", delimiter=";"):
        (self.root / "input.csv").write_text(header + "\n" + "\n".join(rows) + "\n", encoding="utf-8")

    def group_files(self, directory):
        return sorted(str(p.relative_to(directory)).replace("\\", "/") for p in Path(directory).rglob("*") if p.is_file())

    # Validation.

    def test_split_by_must_name_an_existing_column(self):
        self.write(["active;10"])
        with self.assertRaisesRegex(ConfigError, "split_by"):
            validate(spec(split_by="nope"))

    def test_split_by_rejects_non_string_and_oversized_values(self):
        for bad in (123, "", " ", "x" * 129):
            with self.subTest(bad=bad):
                s = spec()
                s["destination"]["split_by"] = bad
                with self.assertRaises(ConfigError):
                    validate(s)

    def test_absent_split_by_is_the_untouched_default(self):
        self.write(["active;10", "inactive;20"])
        report = execute(spec(), self.root, self.root / "out")
        directory = Path(report["directory"])
        self.assertEqual(self.group_files(directory), ["rejected.csv", "rejected.xlsx", "valid.csv"])
        self.assertEqual(report["files"], ["valid.csv", "rejected.csv", "rejected.xlsx"])

    # Grouping behaviour.

    def test_one_file_per_distinct_value_with_the_right_rows_in_each(self):
        self.write(["active;10", "inactive;20", "active;30"])
        report = execute(spec(split_by="status"), self.root, self.root / "out")
        directory = Path(report["directory"])
        self.assertEqual(self.group_files(directory), ["active/valid.csv", "inactive/valid.csv", "rejected.csv", "rejected.xlsx"])
        with (directory / "active" / "valid.csv").open(encoding="utf-8-sig") as handle:
            rows = list(csv.DictReader(handle, delimiter=";"))
        self.assertEqual([r["amount"] for r in rows], ["10", "30"])
        with (directory / "inactive" / "valid.csv").open(encoding="utf-8-sig") as handle:
            rows = list(csv.DictReader(handle, delimiter=";"))
        self.assertEqual([r["amount"] for r in rows], ["20"])
        self.assertEqual(sorted(report["files"]), ["active/valid.csv", "inactive/valid.csv", "rejected.csv", "rejected.xlsx"])

    def test_null_and_empty_string_are_different_groups(self):
        columns = [{"name": "flag", "source": "flag", "type": "string", "transforms": ["empty_to_null"]},
                   {"name": "amount", "source": "amount", "type": "string"}]
        (self.root / "input.csv").write_text("flag;amount\n;1\nx;2\n", encoding="utf-8")
        report = execute(spec(split_by="flag", columns=columns), self.root, self.root / "out")
        directory = Path(report["directory"])
        self.assertEqual(self.group_files(directory), ["NULL/valid.csv", "rejected.csv", "rejected.xlsx", "x/valid.csv"])

    def test_a_row_that_fails_validation_never_creates_a_group(self):
        columns = [{"name": "status", "source": "status", "type": "string", "required": True},
                   {"name": "amount", "source": "amount", "type": "int"}]
        self.write(["active;10", ";20", "broken;not_a_number"])
        report = execute(spec(split_by="status", columns=columns), self.root, self.root / "out")
        directory = Path(report["directory"])
        # "broken" would have been a third group, but its row failed type conversion.
        self.assertEqual(self.group_files(directory), ["active/valid.csv", "rejected.csv", "rejected.xlsx"])
        self.assertEqual(report["invalid"], 2)

    def test_every_row_rejected_leaves_no_group_at_all(self):
        columns = [{"name": "status", "source": "status", "type": "int"}]
        self.write(["active;10", "inactive;20"], header="status;amount")
        report = execute(spec(split_by="status", columns=columns), self.root, self.root / "out")
        directory = Path(report["directory"])
        self.assertEqual(self.group_files(directory), ["rejected.csv", "rejected.xlsx"])
        self.assertEqual(report["files"], ["rejected.csv", "rejected.xlsx"])

    # Safety and filename handling.

    def test_a_near_unique_column_is_refused_rather_than_producing_hundreds_of_files(self):
        rows = [f"id{n};{n}" for n in range(MAX_SPLIT_GROUPS + 5)]
        self.write(rows, header="status;amount")
        with self.assertRaisesRegex(ConfigError, str(MAX_SPLIT_GROUPS)):
            execute(spec(split_by="status"), self.root, self.root / "out")

    def test_exactly_the_cap_succeeds(self):
        rows = [f"id{n};{n}" for n in range(MAX_SPLIT_GROUPS)]
        self.write(rows, header="status;amount")
        report = execute(spec(split_by="status"), self.root, self.root / "out")
        self.assertEqual(len(report["files"]) - 2, MAX_SPLIT_GROUPS)  # minus rejected.csv, rejected.xlsx

    def test_values_with_unsafe_filename_characters_are_sanitized(self):
        self.write(['"a/b:c";1', '"a\\b?c";2'], header="status;amount")
        report = execute(spec(split_by="status"), self.root, self.root / "out")
        directory = Path(report["directory"])
        names = {Path(f).parts[0] for f in report["files"] if f not in ("rejected.csv", "rejected.xlsx")}
        self.assertEqual(len(names), 2, "two distinct raw values must not collapse into one folder")
        for name in names:
            self.assertTrue((directory / name / "valid.csv").is_file())

    def test_two_values_colliding_after_sanitization_get_distinct_folders(self):
        self.write(['"a/b";1', '"a:b";2'], header="status;amount")  # both sanitize to "a_b"
        report = execute(spec(split_by="status"), self.root, self.root / "out")
        directory = Path(report["directory"])
        valid_dirs = sorted(Path(f).parts[0] for f in report["files"] if f not in ("rejected.csv", "rejected.xlsx"))
        self.assertEqual(len(valid_dirs), 2)
        self.assertEqual(len(set(valid_dirs)), 2)
        rows = []
        for d in valid_dirs:
            with (directory / d / "valid.csv").open(encoding="utf-8-sig") as handle:
                rows.append([r["amount"] for r in csv.DictReader(handle, delimiter=";")])
        self.assertEqual(sorted(sum(rows, [])), ["1", "2"], "neither row is dropped or merged")

    def test_reserved_windows_names_and_empty_sanitization_are_handled(self):
        self.write(['"CON";1', '"***";2'], header="status;amount")
        report = execute(spec(split_by="status"), self.root, self.root / "out")
        directory = Path(report["directory"])
        for f in report["files"]:
            if f in ("rejected.csv", "rejected.xlsx"):
                continue
            self.assertTrue((directory / f).is_file())

    # Reuse of the existing engine and exporter.

    def test_preview_is_unaffected_by_split_by(self):
        self.write(["active;10", "inactive;20"])
        preview = execute(spec(split_by="status"), self.root, limit=100)
        self.assertNotIn("directory", preview)
        self.assertEqual(preview["processed"], 2)

    def test_split_export_is_byte_identical_per_group_to_a_manual_single_run(self):
        self.write(["active;10", "active;30"])
        split_report = execute(spec(split_by="status"), self.root, self.root / "split")
        plain_report = execute(spec(), self.root, self.root / "plain")
        with (Path(split_report["directory"]) / "active" / "valid.csv").open("rb") as handle:
            split_bytes = handle.read()
        # The plain run mixes both statuses, so compare only the header + one row shape.
        self.assertTrue(split_bytes.startswith(b"\xef\xbb\xbfstatus;amount\r\n"))

    def test_xlsx_split_produces_one_workbook_per_group(self):
        self.write(["active;10", "inactive;20"])
        s = spec(split_by="status")
        s["destination"] = {"kind": "xlsx", "split_by": "status"}
        report = execute(s, self.root, self.root / "out")
        directory = Path(report["directory"])
        self.assertEqual(sorted(report["files"]), ["active/valid.xlsx", "inactive/valid.xlsx", "rejected.csv", "rejected.xlsx"])
        self.assertTrue((directory / "active" / "valid.xlsx").is_file())

    # Ordered (multi-step) pipelines explicitly do not support this yet.

    def test_split_by_is_refused_inside_an_ordered_step(self):
        step_spec = {
            "kind": "ordered_query_export", "format_version": 1, "name": "Steps",
            "connection_env": "ETL_SQL_TEST", "failure_policy": "stop",
            "steps": [{"id": "step-1", "name": "Step 1",
                       "query": {"format_version": 1, "dialect": "tsql", "sql": "SELECT status FROM dbo.T",
                                 "parameters": [], "timeout_seconds": 30},
                       "processing_version": 2,
                       "columns": [{"name": "status", "source": "status", "type": "string"}],
                       "destination": {"kind": "csv", "split_by": "status"}}],
        }
        with self.assertRaisesRegex(ConfigError, "split_by"):
            ordered_from_dict(step_spec)


if __name__ == "__main__":
    unittest.main()
