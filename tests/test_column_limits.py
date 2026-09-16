import csv
import tempfile
import unittest
from pathlib import Path

from etl.engine import execute
from etl.spec import MAX_OUTPUT_COLUMNS, ConfigError, validate
from etl.templates import template_from_dict


class ColumnLimitTests(unittest.TestCase):
    def spec(self, count):
        return {"version": 2, "name": "Wide dataset", "source": {"kind": "csv", "path": "wide.csv", "delimiter": ";"},
                "columns": [{"name": f"field_{i}", "source": f"field_{i}", "type": "string"} for i in range(count)],
                "destination": {"kind": "csv", "delimiter": ";"}}

    def test_wide_csv_preview_and_export(self):
        spec = self.spec(300)
        names = [c["name"] for c in spec["columns"]]
        values = ["003", "", "Greek Ελληνικά", "line1\nline2"] + [str(i) for i in range(296)]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with (root / "wide.csv").open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle, delimiter=";")
                writer.writerow(names)
                writer.writerow(values)
            preview = execute(spec, root, limit=100)
            report = execute(spec, root, root / "output")
            self.assertEqual(preview["sample"], report["sample"])
            self.assertEqual((report["valid"], report["invalid"]), (1, 0))
            with (Path(report["directory"]) / "valid.csv").open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.reader(handle, delimiter=";"))
            self.assertEqual(rows, [names, values])

    def test_pipeline_and_template_share_boundary(self):
        validate(self.spec(MAX_OUTPUT_COLUMNS))
        with self.assertRaises(ConfigError):
            validate(self.spec(MAX_OUTPUT_COLUMNS + 1))
        template = {"id": "a" * 32, "revision": 1, "name": "Wide template", "format_version": 1, "processing_version": 2,
                    "fields": [{"output_name": f"field_{i}", "target_type": "string"} for i in range(MAX_OUTPUT_COLUMNS)]}
        self.assertEqual(len(template_from_dict(template).fields), MAX_OUTPUT_COLUMNS)
        template["fields"].append({"output_name": "too_many", "target_type": "string"})
        with self.assertRaises(ConfigError):
            template_from_dict(template)
