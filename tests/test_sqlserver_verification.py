"""Offline checks of the live harness; these are NOT live SQL Server results."""
from contextlib import redirect_stdout
from datetime import date, datetime
from decimal import Decimal
from io import StringIO
from itertools import islice
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from integration.sqlserver import verify as harness
from etl.sources import SqlServerSource


class SqlServerVerificationHarnessTests(unittest.TestCase):
    def setUp(self):
        self.spec = json.loads(Path(harness.__file__).with_name("pipeline.v2.json").read_text(encoding="utf-8"))
        self.driver = MagicMock()
        self.driver.Error = type("DriverError", (Exception,), {})
        self.connections = []
        self.driver.connect.side_effect = self.connect
        modules = patch.dict("sys.modules", {"pyodbc": self.driver})
        environment = patch.dict("os.environ", {"ETL_SQL_STAGE4": "offline synthetic connection"})
        modules.start()
        environment.start()
        self.addCleanup(modules.stop)
        self.addCleanup(environment.stop)

    def connect(self, *args, **kwargs):
        connection = MagicMock()
        cursor = connection.cursor.return_value
        names = ["row_id", "case_id", "code", "greek_text", "native_bigint", "native_int", "amount", "enabled",
                 "native_date", "native_datetime", "date_text", "optional_text", "required_probe", "memo"]
        kinds = [int, int, str, str, int, int, Decimal, bool, date, datetime, str, str, str, str]
        cursor.description = [(n, t, None, None, 38 if n == "amount" else None,
                               18 if n == "amount" else None, True) for n, t in zip(names, kinds)]
        codes = ["003", " 17 ", "", None, "   ", "42", "7"]
        optional = ["", "   ", "", None, "   ", "  μέσα  ", "κείμενο"]
        required = ["OK", "OK", "", "OK", "   ", None, "OK"]
        dates = ["2024-02-29", "2024-01-01", "", None, "29/02/2024", "2023-02-29", "2024-02-29"]
        rows = []
        for case in (4, 7, 1, 5, 3, 6, 2):  # Deliberately not key order.
            row = [case, case, codes[case-1], "Αθήνα", 9007199254740993, 2147483647,
                   Decimal(harness.AMOUNTS[case]) if case != 4 else None, True,
                   date(2024, 2, 29), datetime(2024, 2, 29, 12, 34, 56, 123000), dates[case-1],
                   optional[case-1], required[case-1], "Πρώτη γραμμή\r\nΔεύτερη γραμμή"]
            if case == 4:
                for index in (3, 4, 5, 7, 8, 9, 13):
                    row[index] = None
            rows.append(tuple(row))
        iterator = iter(rows)
        cursor.fetchmany.side_effect = lambda size: list(islice(iterator, size))
        cursor.fetchall.side_effect = AssertionError("Raw fetchall must not run")
        self.connections.append(connection)
        return connection

    def test_offline_full_harness_and_existing_csv_xlsx_exports(self):
        with redirect_stdout(StringIO()), tempfile.TemporaryDirectory() as directory:
            counts = harness.verify(self.spec, expected_rows=7, batch_size=2)
            self.assertEqual(counts, {"processed": 7, "valid": 3, "invalid": 4})
            for kind in ("csv", "xlsx"):
                harness.verify_export(self.spec, counts, Path(directory), kind)
                self.assertEqual(len(list(Path(directory).glob(f"*/valid.{kind}"))), 1)
        self.assertEqual(len(self.connections), 3)
        self.assertEqual([call.args for call in self.connections[0].cursor.return_value.fetchmany.call_args_list], [(2,)] * 5)
        for connection in self.connections[1:]:
            self.assertEqual([call.args for call in connection.cursor.return_value.fetchmany.call_args_list], [(1000,)] * 2)
        for connection in self.connections:
            connection.close.assert_called_once()
            connection.cursor.return_value.close.assert_called_once()
            connection.cursor.return_value.fetchall.assert_not_called()

    def test_fetchall_guard_fails_before_reading_driver(self):
        raw = MagicMock()
        audit = harness.Audit()
        with self.assertRaisesRegex(AssertionError, "fetchall is forbidden"):
            harness.AuditedCursor(raw, audit).fetchall()
        self.assertEqual(audit.fetchall_calls, 1)
        raw.fetchall.assert_not_called()

    def test_audited_real_adapter_closes_on_early_stop_and_consumer_error(self):
        for fail in (False, True):
            with self.subTest(fail=fail), harness.audit_driver(2) as audit:
                try:
                    with SqlServerSource(self.spec["source"], batch_size=2).open() as stream:
                        next(stream.rows)
                        if fail:
                            raise RuntimeError("consumer error")
                except RuntimeError as error:
                    self.assertEqual(str(error), "consumer error")
                self.assertEqual(audit.fetchmany_calls, 1)
                self.assertEqual(audit.returned_rows, 2)
                self.assertEqual(audit.cursors_closed, 1)
                self.assertEqual(audit.connections_closed, 1)


if __name__ == "__main__":
    unittest.main()
