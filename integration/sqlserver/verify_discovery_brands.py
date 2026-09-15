"""Existing dbo.BRANDS only: SELECT-only discovery acceptance, no fixtures/writes.

Run from the workspace with .venv/Scripts/python.exe integration/sqlserver/verify_discovery_brands.py.
Uses the existing authorized .env connection helper. Never prints source values or credentials.
"""
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import sys
from time import perf_counter
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from integration.sqlserver.verify_brands_export import connection_string, check
from etl.discovery import SqlServerDiscovery, to_dict
from etl.serialization import json_default
from etl.spec import source_spec


def verify():
    import pyodbc
    original_connect = pyodbc.connect
    audits = []

    class Cursor:
        def __init__(self, raw, audit):
            self.raw, self.audit = raw, audit
            self.data = False

        @property
        def description(self):
            return self.raw.description

        def execute(self, sql, *params):
            self.data = sql == "SELECT * FROM [dbo].[BRANDS]"
            catalogs = ("sys.schemas", "sys.tables", "sys.views", "sys.objects", "sys.columns")
            check(self.data or (sql.startswith("SELECT ") and any("FROM " + c in sql for c in catalogs)), "Unexpected SQL")
            check(";" not in sql and " JOIN " not in sql.upper() and "COUNT(" not in sql.upper(), "Out-of-scope SQL")
            self.audit["selects"] += 1
            self.raw.execute(sql, *params)
            return self

        def fetchone(self):
            check(not self.data, "Data rows must be batched")
            return self.raw.fetchone()

        def fetchmany(self, size):
            check(1 <= size <= 101, "Unbounded batch")
            if self.data:
                check(size == 20, "Expected 20-row source sample")
                self.audit["data_fetch_sizes"].append(size)
            return self.raw.fetchmany(size)

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
        audit = {"selects": 0, "data_fetch_sizes": [], "cursor_closed": False, "connection_closed": False}
        audits.append(audit)
        return Connection(original_connect(*args, **kwargs), audit)

    def pages(method, namespace=()):
        cursor = None
        while True:
            page = method(namespace, cursor=cursor, limit=100)
            yield from page.items
            cursor = page.next_cursor
            if cursor is None:
                break

    started = perf_counter()
    env = "ETL_SQL_DISCOVERY_ACCEPTANCE"
    with patch.dict(os.environ, {env: connection_string()}), patch.object(pyodbc, "connect", side_effect=connect):
        discovery = SqlServerDiscovery(env, ROOT)
        check(any(n == ("dbo",) for n in pages(discovery.list_namespaces)), "dbo not visible")
        dataset = next((d for d in pages(discovery.list_datasets, ("dbo",)) if d.name == "BRANDS"), None)
        check(dataset is not None and dataset.kind == "table", "BRANDS table not found")
        source = discovery.configure(dataset, {})
        source_spec(to_dict(source))
        check(set(source.options) == {"connection_env", "schema", "table"}, "Unexpected source options")
        columns = discovery.inspect(source)
        check(len(columns) == 173, "BRANDS schema changed")
        sample = discovery.sample(source, limit=20)
        check(len(sample.rows) == 20 and sample.stop_reason == "row_limit", "Sample limit mismatch")
        check([c.column.name for c in columns] == [c.name for c in sample.columns], "Schema mismatch")
        check([r.number for r in sample.rows] == list(range(1, 21)), "Record positions mismatch")
        check(all(r.line_start is None and r.line_end is None for r in sample.rows), "SQL cannot expose physical lines")
        encoded = json.loads(json.dumps(to_dict(sample), default=json_default, ensure_ascii=False))
        decimals = unicode_values = 0
        for row, wire in zip(sample.rows, encoded["rows"]):
            for name, value in row.values.items():
                if isinstance(value, Decimal):
                    check(wire["values"][name] == {"$type": "decimal", "value": str(value)}, "Decimal changed at JSON boundary")
                    decimals += 1
                elif isinstance(value, str):
                    check(wire["values"][name] == value, "Source text changed")
                    unicode_values += any(ord(c) > 127 for c in value)
                elif value is None:
                    check(wire["values"][name] is None, "NULL changed")
        check(decimals > 0 and unicode_values > 0, "Expected representative native values")
    check(all(a["cursor_closed"] and a["connection_closed"] for a in audits), "Resource leak")
    check([size for a in audits for size in a["data_fetch_sizes"]] == [20], "Sample performed extra data fetches")
    return {"verified_at_utc": datetime.now(timezone.utc).isoformat(), "dataset": "dbo.BRANDS",
            "columns": len(columns), "declared_types_available": sum(c.declared_type is not None for c in columns),
            "sample_rows": len(sample.rows), "stop_reason": sample.stop_reason,
            "decimal_values_verified": decimals, "unicode_values_verified": unicode_values,
            "data_fetch_sizes": [20], "fetchall_calls": 0, "selects": sum(a["selects"] for a in audits),
            "database_write_statements": 0, "all_resources_closed": True,
            "elapsed_seconds": round(perf_counter() - started, 3)}


if __name__ == "__main__":
    try:
        result = verify()
    except Exception as error:
        print(json.dumps({"status": "failed", "error_type": type(error).__name__, "code": getattr(error, "code", None)}))
        sys.exit(1)
    text = json.dumps(result, indent=2) + "\n"
    (Path(__file__).parent / "brands_discovery_result.json").write_text(text, encoding="utf-8")
    print(text)
