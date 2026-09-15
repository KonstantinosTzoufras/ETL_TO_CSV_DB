"""One generic template, CSV and existing BRANDS; SQL SELECT-only, 20 rows max."""
from contextlib import ExitStack
import csv
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from integration.sqlserver.verify_brands_export import connection_string, check
from etl.templates import TemplateStore, apply_template, bind_template
from etl.engine import execute, process_row
from etl.serialization import pipeline_from_dict
from etl.sources import SqlServerSource
from etl.exporters import OutputWriter


def verify():
    import pyodbc
    native_connect = pyodbc.connect
    audit = {"selects": 0, "batch_sizes": [], "cursor_closed": False, "connection_closed": False}
    class Cursor:
        def __init__(self, raw): self.raw = raw
        @property
        def description(self): return self.raw.description
        def execute(self, query):
            check(query == "SELECT * FROM [dbo].[BRANDS]", "Only the existing BRANDS SELECT is allowed")
            audit["selects"] += 1; self.raw.execute(query); return self
        def fetchmany(self, size):
            check(size == 20, "Sample must use a 20-row batch")
            rows = self.raw.fetchmany(size); audit["batch_sizes"].append(len(rows)); return rows
        def fetchall(self): raise AssertionError("fetchall is forbidden")
        def close(self): self.raw.close(); audit["cursor_closed"] = True
    class Connection:
        def __init__(self, raw): self.raw = raw
        @property
        def timeout(self): return self.raw.timeout
        @timeout.setter
        def timeout(self, value): self.raw.timeout = min(value, 15)
        def cursor(self): return Cursor(self.raw.cursor())
        def close(self): self.raw.close(); audit["connection_closed"] = True
    def connect(*args, **kwargs):
        kwargs["readonly"] = True
        return Connection(native_connect(*args, **kwargs))

    with tempfile.TemporaryDirectory(prefix="etl-template-acceptance-") as temporary:
        root = Path(temporary)
        store = TemplateStore(root / "templates")
        template = store.create({"format_version": 1, "processing_version": 2, "name": "Generic code and name",
            "fields": [{"output_name": "Code", "target_type": "string"},
                       {"output_name": "Name", "target_type": "string", "transforms": ["trim"]},
                       {"output_name": "Blank", "target_type": "string"},
                       {"output_name": "Missing", "target_type": "string"}]})
        def pipeline(source, code, name):
            spec = {"version": 2, "name": "Template acceptance", "source": source, "columns": [], "destination": {"kind": "csv"}}
            copied = apply_template(template, spec)
            return bind_template(copied["template"], copied["pipeline"], [{"source": code}, {"source": name}, {"literal": ""}, {"literal": None}])
        with (root / "input.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, delimiter=";"); writer.writerow(["code", "name", "extra"])
            writer.writerow(["003", " Ελλάδα\r\nnext ", "unused"])
        csv_spec = pipeline({"kind": "csv", "path": "input.csv"}, "code", "name")
        csv_report = execute(csv_spec, root, root / "runs")
        check(csv_report["valid"] == 1 and csv_report["sample"][0].converted_values["Code"] == "003", "CSV identifier changed")
        env = "ETL_SQL_TEMPLATE_ACCEPTANCE"
        sql_spec = pipeline({"kind": "sqlserver", "connection_env": env, "schema": "dbo", "table": "BRANDS"}, "CODE", "DESCR")
        # The generated definitions no longer depend on the template file.
        (root / "templates" / template.id / "1.json").unlink()
        model = pipeline_from_dict(sql_spec)
        output = root / "sql-output"; output.mkdir()
        count = 0
        with patch.dict(os.environ, {env: connection_string()}), patch.object(pyodbc, "connect", side_effect=connect), ExitStack() as stack:
            writer = OutputWriter(output, [c["name"] for c in sql_spec["columns"]], sql_spec["destination"], stack, version=2)
            stack.callback(writer.finish)
            stream = stack.enter_context(SqlServerSource(model.source, batch_size=20).open())
            iterator = iter(stream)
            for _ in range(20):
                row = next(iterator)
                result = process_row(model, row, {})
                check(result.valid, "Unexpected rejection")
                check(result.converted_values["Code"] == row.values["CODE"], "Code changed")
                check(result.converted_values["Blank"] == "" and result.converted_values["Missing"] is None, "Explicit literals changed")
                writer.write_result(result); count += 1
        with (output / "valid.csv").open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle, delimiter=";")
            check(next(reader) == ["Code", "Name", "Blank", "Missing"], "Unexpected output columns")
            check(sum(1 for _ in reader) == count, "Export row count drift")
        check(audit["batch_sizes"] == [20] and audit["cursor_closed"] and audit["connection_closed"], "Read lifecycle mismatch")
        return {"csv_valid_rows": csv_report["valid"], "csv_leading_zero_preserved": True,
                "sql_dataset": "dbo.BRANDS", "sql_valid_rows": count, "export_columns": 4,
                "same_template_used": True, "template_deleted_before_sql_execution": True,
                "database_write_statements": 0, "fetchall_calls": 0, **audit}


if __name__ == "__main__":
    try:
        result = verify()
    except Exception as error:
        print(json.dumps({"status": "failed", "error_type": type(error).__name__}))
        sys.exit(1)
    text = json.dumps(result, indent=2) + "\n"
    (Path(__file__).parent / "mapping_template_result.json").write_text(text, encoding="utf-8")
    print(text)
