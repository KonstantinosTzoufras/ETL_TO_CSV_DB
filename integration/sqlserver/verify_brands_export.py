"""Read-only BRANDS -> v2 execute -> temporary CSV, checked without retaining rows."""
import argparse
from collections import Counter
from datetime import date, datetime, time, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from time import perf_counter
from unittest.mock import patch
import csv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from etl.engine import execute
from etl.sources import SqlServerSource


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def connection_string():
    settings = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8-sig").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if separator:
            value = value.strip()
            if len(value) > 1 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            settings[key.strip()] = value
    server = settings["DB_HOST"]
    if settings.get("DB_PORT") and "," not in server:
        server += "," + settings["DB_PORT"]
    values = {"DRIVER": "ODBC Driver 18 for SQL Server", "SERVER": server,
              "DATABASE": settings["DB_NAME"], "UID": settings["DB_USER"], "PWD": settings["DB_PASS"],
              "Encrypt": "yes", "TrustServerCertificate": "yes", "APP": "ETL Stage 5 read-only BRANDS verification"}
    # Certificate trust override was explicitly authorized for this database.
    return ";".join(key + "={" + value.replace("}", "}}") + "}" for key, value in values.items())


def expected_text(value):
    """Independent test oracle; deliberately does not call exporter formatting."""
    if value is None:
        return "\\N"
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, (int, float, Decimal)):
        return str(value)
    raise AssertionError("Unexpected mapped native type")


def digest_row(digest, values):
    digest.update(json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    digest.update(b"\n")


def verify():
    import pyodbc
    audits = []
    raw_connect = pyodbc.connect

    class Cursor:
        def __init__(self, raw, audit):
            self.raw, self.audit = raw, audit

        @property
        def description(self):
            return self.raw.description

        def execute(self, query):
            check(query == "SELECT * FROM [dbo].[BRANDS]", "Only the BRANDS source SELECT is allowed")
            self.audit["selects"] += 1
            self.raw.execute(query)
            return self

        def fetchmany(self, size):
            check(size == 1000, "Expected unchanged production batch size")
            rows = self.raw.fetchmany(size)
            self.audit["batch_rows"].append(len(rows))
            return rows

        def fetchall(self):
            raise AssertionError("fetchall forbidden")

        def close(self):
            self.raw.close()
            self.audit["cursor_closed"] = True

    class Connection:
        def __init__(self, raw, audit):
            self.raw, self.audit = raw, audit

        @property
        def timeout(self):
            return self.raw.timeout

        @timeout.setter
        def timeout(self, value):
            self.raw.timeout = min(value, 15)

        def cursor(self):
            return Cursor(self.raw.cursor(), self.audit)

        def close(self):
            self.raw.close()
            self.audit["connection_closed"] = True

    def connect(*args, **kwargs):
        kwargs["readonly"] = True
        audit = {"selects": 0, "batch_rows": [], "cursor_closed": False, "connection_closed": False}
        audits.append(audit)
        return Connection(raw_connect(*args, **kwargs), audit)

    source = {"kind": "sqlserver", "connection_env": "ETL_SQL_BRANDS_VERIFY", "schema": "dbo", "table": "BRANDS"}
    type_map = {"str": "string", "int": "int", "Decimal": "decimal", "float": "float", "bool": "bool", "date": "date", "datetime": "datetime"}
    expected_digest, actual_digest = hashlib.sha256(), hashlib.sha256()
    observations = Counter()
    started = perf_counter()
    with patch.dict(os.environ, {"ETL_SQL_BRANDS_VERIFY": connection_string()}), patch.object(pyodbc, "connect", side_effect=connect):
        schema = SqlServerSource(source).read_schema()
        check(all(c.native_type in type_map for c in schema), "Unexpected source type; review mapping explicitly")
        columns = [{"name": c.name, "source": c.name, "type": type_map[c.native_type]} for c in schema]
        names = [c["name"] for c in columns]
        spec = {"version": 2, "name": "Stage 5 BRANDS verification", "source": source, "columns": columns,
                "destination": {"kind": "csv", "delimiter": ";", "encoding": "utf-8-sig", "null_value": "\\N", "formula_policy": "preserve"}}

        def observe(result):
            check(result.valid, "Unexpected rejected row; review source changes")
            values = []
            for name in names:
                original, converted = result.original_values[name], result.converted_values[name]
                check(original == result.transformed_values[name] == converted, "Unexpected processing change")
                if isinstance(original, Decimal):
                    check(original.as_tuple() == converted.as_tuple(), "Decimal scale or precision changed")
                    observations["exact_decimal_cells"] += 1
                if original is None:
                    observations["null_cells"] += 1
                elif isinstance(original, str):
                    observations["empty_string_cells"] += int(original == "")
                    observations["unicode_cells"] += int(any(ord(char) > 127 for char in original))
                    observations["multiline_cells"] += int("\r" in original or "\n" in original)
                values.append(expected_text(converted))
            digest_row(expected_digest, values)

        # Newly-created, isolated workspace child; cleanup cannot target user data.
        with tempfile.TemporaryDirectory(prefix="brands-stage5-", dir=ROOT) as temporary:
            temporary_root = Path(temporary).resolve()
            check(temporary_root.parent == ROOT.resolve(), "Unexpected temporary directory location")
            report = execute(spec, ROOT, temporary_root, on_row=observe)
            check((report["processed"], report["valid"], report["invalid"]) == (423, 423, 0), "Expected exactly 423 valid BRANDS rows; dataset may have changed")
            path = Path(report["directory"]) / "valid.csv"
            with path.open("rb") as handle:
                check(handle.read(3) == b"\xef\xbb\xbf", "Missing configured UTF-8 BOM")
            output_rows = 0
            with path.open(encoding="utf-8-sig", newline="") as handle:
                reader = csv.reader(handle, delimiter=";", strict=True)
                check(next(reader) == names, "Header order/count differs from mapping")
                for row in reader:
                    check(len(row) == len(names), "CSV field-count drift")
                    digest_row(actual_digest, row)
                    output_rows += 1
            check(output_rows == 423, "CSV row-count drift")
            check(actual_digest.digest() == expected_digest.digest(), "CSV values differ from exact native representations")
            with (path.parent / "rejected.csv").open(encoding="utf-8-sig", newline="") as handle:
                reader = csv.reader(handle, delimiter=";", strict=True)
                next(reader)
                check(next(reader, None) is None, "Unexpected rejected export rows")
            csv_bytes = path.stat().st_size
        check(not temporary_root.exists(), "Temporary business-data files were not removed")

    check(len(audits) == 2, "Expected schema inspection and one full execution")
    check(audits[0]["batch_rows"] == [], "Schema inspection fetched data")
    check(audits[1]["batch_rows"] == [423, 0], "Unexpected batch counts")
    check(all(a["selects"] == 1 and a["cursor_closed"] and a["connection_closed"] for a in audits), "SQL resource leak or unexpected query")
    return {"verified_at_utc": datetime.now(timezone.utc).isoformat(), "status": "passed", "table": "dbo.BRANDS",
            "input_rows": report["processed"], "output_rows": output_rows, "rejected_rows": report["invalid"],
            "mapping_columns": len(names), "header_columns": len(names), "csv_bytes": csv_bytes,
            "observations": dict(observations), "all_cells_match_exact_representations": True,
            "encoding": "utf-8-sig", "delimiter": ";", "null_value": "\\N", "formula_policy": "preserve",
            "sql_audit": audits, "database_writes": 0, "temporary_csv_removed": True,
            "seconds": round(perf_counter() - started, 3)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, help="Optional sanitized summary JSON; contains no source rows or credentials")
    args = parser.parse_args()
    try:
        summary = verify()
        payload = json.dumps(summary, indent=2)
        if args.report:
            args.report.write_text(payload + "\n", encoding="utf-8")
        print(payload)
    except Exception as error:
        # Raw driver diagnostics may include credentials or connection details.
        print(f"FAILED ({type(error).__name__}): " + (str(error) if isinstance(error, AssertionError) else "check connection, fixture and dependencies"), file=sys.stderr)
        sys.exit(1)
