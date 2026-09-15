"""Opt-in live SQL verification. Does not create or modify database objects."""
import argparse
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
import json
import os
from pathlib import Path
import sys
from time import perf_counter
import tracemalloc
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from etl.engine import execute, process_row
from etl.serialization import json_default, pipeline_from_dict
from etl.sources import SqlServerSource


def check(condition, message):
    if not condition:
        raise AssertionError(message)


@dataclass
class Audit:
    connections: int = 0
    cursors: int = 0
    connections_closed: int = 0
    cursors_closed: int = 0
    fetchmany_calls: int = 0
    returned_rows: int = 0
    max_batch_rows: int = 0
    empty_batches: int = 0
    fetchall_calls: int = 0
    expected_batch: int = 2

    def verify(self, expected_rows):
        check(self.fetchall_calls == 0, "fetchall was attempted")
        check(self.returned_rows == expected_rows, "Unexpected number of fetched rows")
        check(self.fetchmany_calls >= 2, "Expected multiple fetchmany calls including EOF")
        check(self.max_batch_rows <= self.expected_batch, "Batch exceeded requested bound")
        check(self.empty_batches == 1, "Full iteration must read one empty EOF batch")
        check(self.connections == self.connections_closed == 1, "Connection leaked or unexpected extra connection")
        check(self.cursors == self.cursors_closed == 1, "Cursor leaked or unexpected extra cursor")


class AuditedCursor:
    def __init__(self, cursor, audit):
        self.raw, self.audit = cursor, audit
        audit.cursors += 1

    @property
    def description(self):
        return self.raw.description

    def execute(self, query):
        check(query == "SELECT * FROM [dbo].[ETLStage4Fixture]", "Unexpected query semantics")
        self.raw.execute(query)
        return self

    def fetchmany(self, size):
        check(size == self.audit.expected_batch, "Unexpected fetchmany request size")
        self.audit.fetchmany_calls += 1
        rows = self.raw.fetchmany(size)
        self.audit.returned_rows += len(rows)
        self.audit.max_batch_rows = max(self.audit.max_batch_rows, len(rows))
        self.audit.empty_batches += int(not rows)
        return rows

    def fetchall(self):
        self.audit.fetchall_calls += 1
        raise AssertionError("fetchall is forbidden in this verification")

    def fetchone(self):
        raise AssertionError("Expected fetchmany, not fetchone")

    def __iter__(self):
        raise AssertionError("Expected explicit fetchmany, not cursor iteration")

    def close(self):
        self.raw.close()
        self.audit.cursors_closed += 1


class AuditedConnection:
    def __init__(self, connection, audit):
        self.raw, self.audit = connection, audit
        audit.connections += 1

    @property
    def timeout(self):
        return self.raw.timeout

    @timeout.setter
    def timeout(self, value):
        self.raw.timeout = value

    def cursor(self):
        return AuditedCursor(self.raw.cursor(), self.audit)

    def close(self):
        self.raw.close()
        self.audit.connections_closed += 1


@contextmanager
def audit_driver(batch_size):
    # The real pyodbc connection/cursor still perform the I/O. Only this
    # standalone test process is patched; production source code is untouched.
    import pyodbc
    connect = pyodbc.connect
    audit = Audit(expected_batch=batch_size)
    def audited_connect(*args, **kwargs):
        return AuditedConnection(connect(*args, **kwargs), audit)
    with patch.object(pyodbc, "connect", side_effect=audited_connect):
        yield audit


ERRORS = {
    1: {("code_as_int", "invalid_type", "conversion")},
    2: set(),
    3: {("code_as_int", "invalid_type", "conversion"), ("date_from_text", "invalid_type", "conversion"),
        ("required_probe", "required", "required")},
    4: set(),
    5: {("code_as_int", "invalid_type", "conversion"), ("date_from_text", "invalid_type", "conversion"),
        ("required_probe", "required", "required"), ("required_probe", "max_length_exceeded", "max_length")},
    6: {("date_from_text", "invalid_type", "conversion"), ("required_probe", "required", "required")},
    7: set(),
}
AMOUNTS = {
    1: "12345678901234567890.123456789012345678", 2: "-0.000000000000000001",
    3: "0.000000000000000000", 5: "1.230000000000000000",
    6: "99999999999999999999.999999999999999999", 7: "3.140000000000000000",
}


def verify_result(result):
    original, transformed, converted = result.original_values, result.transformed_values, result.converted_values
    case = original["case_id"]
    check(case in ERRORS, "Unknown fixture case")
    check(case == 1 + (original["row_id"] - 1) % 7, "Fixture row_id/case_id mismatch")
    check({(e.field, e.code, e.stage) for e in result.errors} == ERRORS[case], f"Unexpected field errors for case {case}")
    check(result.valid == (not ERRORS[case]), "Invalid row classification")
    check(result.source.line_start is None and result.source.line_end is None, "SQL rows must not claim physical line positions")
    for name in ("code", "greek_text", "memo", "optional_text", "date_text"):
        check(original[name] == transformed[name] == converted[name], f"Unexpected normalization: {name}")
    for name, kind in (("native_bigint", int), ("native_int", int), ("enabled", bool),
                       ("native_date", date), ("native_datetime", datetime)):
        value = original[name]
        check(value is None or type(value) is kind, f"Unexpected driver type: {name}")
        check(value == converted[name], f"Native conversion changed {name}")
    if case == 4:
        check(original["amount"] is None and converted["amount"] is None, "SQL NULL was not preserved")
        check(converted["optional_text"] is None, "NULL changed to empty text")
    else:
        for value in (original["amount"], transformed["amount"], converted["amount"]):
            check(type(value) is Decimal, "Decimal passed through a different type")
            check(value.as_tuple() == Decimal(AMOUNTS[case]).as_tuple(), "Decimal precision/scale changed")
    for error in result.errors:
        if error.stage == "conversion":
            check(error.field not in converted, "Failed conversion has a substituted value")
    if case == 1:
        check(converted["code"] == "003", "Leading-zero code changed")
        check(converted["greek_text"] == "Αθήνα", "Unicode text changed")
        check(converted["memo"] == "Πρώτη γραμμή\r\nΔεύτερη γραμμή", "CRLF multiline text changed")
        check(converted["native_bigint"] == 9007199254740993, "Big integer precision changed")
    if case == 2:
        check(converted["code"] == " 17 " and transformed["code_as_int"] == "17" and converted["code_as_int"] == 17, "Explicit trim/int stages disagree")
    if case in (2, 5):
        check(converted["optional_text"] == "   ", "Whitespace changed implicitly")
        check(converted["trim_then_null"] is None and converted["null_then_trim"] == "", "Transform order lost")
    if case in (1, 3):
        check(converted["optional_text"] == "", "Empty text changed to null")


def emit(value):
    # Tags are presentation only; checks above inspect real Decimal/date values.
    print(json.dumps(value, ensure_ascii=True, indent=2, default=json_default))


@contextmanager
def measurement(label):
    tracemalloc.start()
    started = perf_counter()
    try:
        yield
    finally:
        elapsed = perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        emit({"measurement": label, "seconds": round(elapsed, 3), "peak_python_mib": round(peak / 1048576, 3)})


def verify(spec, expected_rows, batch_size):
    pipeline = pipeline_from_dict(spec)
    selected, counts = {}, {"processed": 0, "valid": 0, "invalid": 0}
    with measurement("source + process_row"), audit_driver(batch_size) as audit:
        with SqlServerSource(pipeline.source, batch_size=batch_size).open() as stream:
            emit({"schema": [asdict(column) for column in stream.schema]})
            for number, row in enumerate(stream, 1):
                check(row.number == number, "Nonsequential execution-relative row number")
                result = process_row(pipeline, row, {})  # Fixture has no lookups.
                verify_result(result)
                counts["processed"] += 1
                counts["valid" if result.valid else "invalid"] += 1
                if row.values["row_id"] <= 7:
                    selected[row.values["row_id"]] = result
        audit.verify(expected_rows)
    check(set(selected) == set(range(1, 8)), "Missing original seed rows")
    expected_valid = sum((expected_rows + 7 - case) // 7 for case in (2, 4, 7))
    check(counts == {"processed": expected_rows, "valid": expected_valid,
                     "invalid": expected_rows - expected_valid}, "Unexpected fixture case distribution")
    for seed_id in sorted(selected):
        result = selected[seed_id]
        emit({"fixture_id": seed_id, "native_types": {k: type(v).__name__ for k, v in result.original_values.items()}, "result": result})
    emit({"counts": counts, "audit": asdict(audit)})
    return counts


def verify_export(spec, counts, output, kind):
    spec = {**spec, "destination": {"kind": kind, "delimiter": ";"}}
    with measurement(f"full {kind} export"), audit_driver(1000) as audit:
        report = execute(spec, ROOT, output, on_row=verify_result)
        audit.verify(counts["processed"])
    check({key: report[key] for key in counts} == counts, "Export counts differ from direct processing; keep fixture unchanged")
    directory = Path(report["directory"])
    emit({"export_directory": str(directory), "counts": counts, "audit": asdict(audit),
          "files": {p.name: p.stat().st_size for p in directory.iterdir() if p.is_file()}})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-rows", type=int, choices=(7, 10000, 100000), default=7)
    parser.add_argument("--batch-size", type=int, default=2, help="Direct adapter pass only; full export retains production batch size 1000")
    parser.add_argument("--export", choices=("csv", "xlsx"), help="Also execute the existing full export path (a second SELECT)")
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "stage4-verification")
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")
    if not os.environ.get("ETL_SQL_STAGE4"):
        parser.error("Set ETL_SQL_STAGE4 to the dedicated fixture database connection string; no live verification performed")
    spec = json.loads(Path(__file__).with_name("pipeline.v2.json").read_text(encoding="utf-8"))
    counts = verify(spec, args.expected_rows, args.batch_size)
    if args.export:
        verify_export(spec, counts, args.output, args.export)
    print("PASS: live SQL source, typed processing, batching and resource-close checks")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Never echo raw driver messages or connection strings.
        if isinstance(error, AssertionError):
            print(f"FAIL: {error}", file=sys.stderr)
        else:
            print(f"FAIL: {type(error).__name__}; check fixture, connection and driver configuration", file=sys.stderr)
        sys.exit(1)
