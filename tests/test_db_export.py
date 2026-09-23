"""Writing pipeline output into a database table, in place of a file.

The one guarantee this module exists for: a run either replaces the target
table completely, or leaves the previous one exactly as it was. Never a
half-written table, never two pipelines silently sharing one.
"""
from contextlib import contextmanager
from decimal import Decimal
import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from etl.db_export import (SqlServerOutputWriter, authorize_export, export_policies,
                           sanitize_table_name, shadow_name_for, sql_type_for, table_name_for)
from etl.engine import execute
from etl.spec import ConfigError, validate
from etl.store import Store

POLICY = json.dumps({"ETL_SQL_MAIN": {"schema": "dbo", "max_timeout_seconds": 60}})
REFERENCE = "ETL_SQL_MAIN"


class FakeCursor:
    """Records every statement in order; can be told to fail on demand."""
    def __init__(self, fail_on=None, error=None, existing_object_ids=()):
        self.statements = []
        self.executed_batches = []
        self.fail_on = fail_on or (lambda sql: False)
        self.error = error or FAKE_PYODBC.Error
        self.fast_executemany = False
        # A real driver can leave a cursor unable to run anything else once a
        # statement on it has failed mid-flight; this reproduces that so a
        # cleanup step can be shown to need a fresh cursor rather than reusing
        # the one the failure happened on.
        self.broken = False
        # Real T-SQL identifiers a SELECT OBJECT_ID('schema.name') check
        # should report as already existing - everything else reports absent,
        # matching a fresh/empty real database.
        self.existing_object_ids = set(existing_object_ids)
        self._last_sql = None

    def execute(self, sql, *args):
        self.statements.append(sql)
        self._last_sql = sql
        if self.broken or self.fail_on(sql):
            self.broken = True
            raise self.error("simulated failure")
        return self

    def executemany(self, sql, rows):
        self.statements.append(sql)
        if self.broken or self.fail_on(sql):
            self.broken = True
            raise self.error("simulated failure")
        self.executed_batches.append((sql, list(rows)))

    def fetchone(self):
        if self._last_sql and "OBJECT_ID(" in self._last_sql:
            for identifier in self.existing_object_ids:
                if identifier in self._last_sql:
                    return (1,)
            return (None,)
        return None

    def close(self):
        pass


class FakePyodbcError(Exception):
    pass


class FakePyodbc:
    Error = FakePyodbcError


FAKE_PYODBC = FakePyodbc()


class _CursorLog:
    """Every cursor a fake connection hands out, as if it were one cursor.

    finish()'s cleanup path opens a fresh cursor rather than reusing one a
    failure happened on (see fake_connection's `error` parameter for why).
    Tests that only ever cause one cursor to be created see no difference;
    tests that exercise the fresh-cursor cleanup can still read `.statements`
    as the combined, in-order record of everything any cursor ran.
    """
    def __init__(self):
        self.cursors = []

    def _make(self, fail_on, error, existing_object_ids):
        cursor = FakeCursor(fail_on=fail_on, error=error, existing_object_ids=existing_object_ids)
        self.cursors.append(cursor)
        return cursor

    @property
    def statements(self):
        return [statement for cursor in self.cursors for statement in cursor.statements]

    @property
    def executed_batches(self):
        return [batch for cursor in self.cursors for batch in cursor.executed_batches]


@contextmanager
def fake_connection(fail_on=None, error=None, existing_object_ids=()):
    """Patches etl.db_export's own `import pyodbc` and pyodbc.connect.

    `error` is the exception class raised where `fail_on` matches - defaults
    to the fake pyodbc.Error, but a test can pass MemoryError (or anything
    else pyodbc.Error's `except` clauses don't catch) to prove the module
    still cleans up rather than depending on that one exception hierarchy.
    `existing_object_ids` names identifiers a SELECT OBJECT_ID(...) check
    should report as already present in the (fake) real database.
    """
    log = _CursorLog()
    connection = MagicMock()
    connection.cursor.side_effect = lambda: log._make(fail_on, error, existing_object_ids)
    commits, rollbacks = [], []
    connection.commit.side_effect = lambda: commits.append(True)
    connection.rollback.side_effect = lambda: rollbacks.append(True)
    module = MagicMock()
    module.Error = FakePyodbcError
    module.connect.return_value = connection
    with patch.dict("sys.modules", {"pyodbc": module}), \
         patch.dict(os.environ, {REFERENCE: "NEVER_PRINT_CREDENTIALS", "ETL_EXPORT_CONNECTIONS": POLICY}):
        yield log, commits, rollbacks


def columns():
    """Column shapes for the writer's own type mapping - no `source` needed."""
    return [{"name": "code", "type": "string", "max_length": 10},
            {"name": "amount", "type": "decimal"},
            {"name": "active", "type": "bool"}]


def pipeline_columns():
    """The same three fields, as a real pipeline spec's `columns` requires them."""
    return [{"name": c["name"], "source": c["name"], **{k: v for k, v in c.items() if k not in ("name",)}}
            for c in columns()]


class NamingTests(unittest.TestCase):
    def test_ascii_transliteration_and_prefix(self):
        self.assertEqual(table_name_for("Customers Export"), "z0_customers_export")

    def test_non_ascii_is_stripped_not_kept(self):
        # Recommendation adopted: ASCII-safe core, never raw Greek in the name.
        self.assertTrue(sanitize_table_name("Πελάτες Εξαγωγή").isascii())

    def test_empty_after_sanitization_falls_back(self):
        self.assertEqual(sanitize_table_name("###"), "export")

    def test_leading_digit_is_not_a_valid_identifier_start(self):
        self.assertFalse(sanitize_table_name("2026_export")[0].isdigit())

    def test_length_is_capped(self):
        self.assertLessEqual(len(sanitize_table_name("x" * 500)), 100)

    def test_shadow_name_is_stable_and_identifier_safe(self):
        pid = "abc123ef" * 4
        self.assertEqual(shadow_name_for(pid), shadow_name_for(pid))
        self.assertRegex(shadow_name_for(pid), r"^z0__building_[a-f0-9]+$")


class TypeMappingTests(unittest.TestCase):
    def test_every_pipeline_type_maps_to_a_sql_server_type(self):
        expectations = {"string": "NVARCHAR(10)", "int": "BIGINT", "decimal": "DECIMAL(38, 10)",
                        "float": "FLOAT", "bool": "BIT", "date": "DATE", "datetime": "DATETIME2"}
        for kind, expected in expectations.items():
            with self.subTest(kind=kind):
                self.assertEqual(sql_type_for({"type": kind, "max_length": 10 if kind == "string" else None}), expected)

    def test_unbounded_string_uses_max(self):
        self.assertEqual(sql_type_for({"type": "string"}), "NVARCHAR(MAX)")


class ApprovalTests(unittest.TestCase):
    def test_fail_closed_without_any_policy(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ETL_EXPORT_CONNECTIONS", None)
            with self.assertRaises(ConfigError):
                authorize_export("ETL_SQL_MAIN")

    def test_a_named_connection_and_schema_is_approved(self):
        with patch.dict(os.environ, {"ETL_EXPORT_CONNECTIONS": POLICY}):
            policy = authorize_export("ETL_SQL_MAIN")
            self.assertEqual(policy["schema"], "dbo")

    def test_an_unapproved_connection_is_refused(self):
        with patch.dict(os.environ, {"ETL_EXPORT_CONNECTIONS": POLICY}):
            with self.assertRaises(ConfigError):
                authorize_export("ETL_SQL_OTHER")

    def test_read_only_is_never_a_valid_key_here(self):
        # This policy is deliberately its own shape: reusing the read-only
        # attestation from ETL_QUERY_CONNECTIONS would misdescribe a write.
        policy = json.dumps({"ETL_SQL_MAIN": {"schema": "dbo", "max_timeout_seconds": 60, "read_only": True}})
        with patch.dict(os.environ, {"ETL_EXPORT_CONNECTIONS": policy}):
            with self.assertRaises(ConfigError):
                export_policies()

    def test_timeout_bounds_are_enforced(self):
        for timeout in (0, 601, "60"):
            policy = json.dumps({"ETL_SQL_MAIN": {"schema": "dbo", "max_timeout_seconds": timeout}})
            with self.subTest(timeout=timeout), patch.dict(os.environ, {"ETL_EXPORT_CONNECTIONS": policy}):
                with self.assertRaises(ConfigError):
                    export_policies()


class SpecTests(unittest.TestCase):
    def spec(self, destination):
        return {"version": 2, "name": "DB export", "source": {"kind": "csv", "path": "in.csv"},
                "columns": [{"name": "code", "source": "code", "type": "string"}],
                "destination": destination}

    def test_a_minimal_sqlserver_destination_validates(self):
        validate(self.spec({"kind": "sqlserver", "connection_env": "ETL_SQL_MAIN"}))

    def test_split_by_is_refused_by_omission(self):
        with self.assertRaises(ConfigError):
            validate(self.spec({"kind": "sqlserver", "connection_env": "ETL_SQL_MAIN", "split_by": "code"}))

    def test_csv_only_options_are_refused(self):
        for extra in ({"delimiter": ";"}, {"encoding": "utf-8"}, {"null_value": ""}, {"formula_policy": "preserve"}):
            with self.subTest(extra=extra):
                with self.assertRaises(ConfigError):
                    validate(self.spec({"kind": "sqlserver", "connection_env": "ETL_SQL_MAIN", **extra}))

    def test_connection_env_must_look_like_one(self):
        with self.assertRaises(ConfigError):
            validate(self.spec({"kind": "sqlserver", "connection_env": "not-a-reference"}))

    def test_an_explicit_table_name_validates(self):
        validate(self.spec({"kind": "sqlserver", "connection_env": "ETL_SQL_MAIN", "table": "customers_clean"}))

    def test_an_explicit_table_name_must_be_a_nonempty_bounded_string(self):
        for table in ("", "   ", "x" * 101, 123, None):
            with self.subTest(table=table):
                with self.assertRaises(ConfigError):
                    validate(self.spec({"kind": "sqlserver", "connection_env": "ETL_SQL_MAIN", "table": table}))


class WriterLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "state.sqlite3")
        self.destination = {"kind": "sqlserver", "connection_env": "ETL_SQL_MAIN"}

    def make(self, pipeline_id="p1", name="Customers", destination=None):
        return SqlServerOutputWriter(columns(), destination or self.destination, pipeline_id, name,
                                     self.store.claim_export_table, self.store.export_table_for)

    def test_an_explicit_table_name_overrides_the_pipeline_name_default(self):
        with fake_connection() as (cursor, commits, rollbacks):
            writer = self.make(name="Customers", destination={**self.destination, "table": "customers_clean"})
            self.assertEqual(writer.table_name, "z0_customers_clean")

    def test_without_an_explicit_table_name_the_pipeline_name_is_still_used(self):
        with fake_connection() as (cursor, commits, rollbacks):
            writer = self.make(name="Customers")
            self.assertEqual(writer.table_name, "z0_customers")

    def test_a_first_claim_colliding_with_a_real_untracked_table_is_refused(self):
        # dbo.z0_customers already exists in the real database, but nothing in
        # this store's registry knows about it - a brand-new claim must not
        # silently adopt (and later overwrite) someone else's table.
        with fake_connection(existing_object_ids=("dbo.z0_customers",)) as (log, commits, rollbacks):
            with self.assertRaisesRegex(ConfigError, "already exists"):
                self.make(name="Customers")
            self.assertFalse(any("CREATE TABLE" in s for s in log.statements),
                             "must be refused before any DDL runs, not just before the swap")
            self.assertIsNone(self.store.export_table_for("p1"),
                              "a refused first claim must not be left permanently in the registry")

    def test_a_second_run_of_the_same_pipeline_is_not_blocked_by_its_own_table(self):
        # The pipeline's own previously-created table obviously already
        # exists on every later run; that must never be treated as a
        # collision with someone else's table.
        with fake_connection() as (cursor, commits, rollbacks):
            self.make().finish()
        with fake_connection(existing_object_ids=("dbo.z0_customers",)) as (cursor, commits, rollbacks):
            writer = self.make()
            self.assertEqual(writer.table_name, "z0_customers")

    def test_without_a_lookup_callback_the_collision_check_is_skipped(self):
        # lookup_table is optional (e.g. direct construction in older code
        # paths); without it, behaviour is exactly what it was before this
        # check existed, rather than refusing to run at all.
        with fake_connection(existing_object_ids=("dbo.z0_customers",)) as (cursor, commits, rollbacks):
            writer = SqlServerOutputWriter(columns(), self.destination, "p1", "Customers", self.store.claim_export_table)
            self.assertEqual(writer.table_name, "z0_customers")

    def test_a_successful_run_drops_the_shadow_and_renames_it_into_place(self):
        with fake_connection() as (cursor, commits, rollbacks):
            writer = self.make()
            self.assertEqual(writer.table_name, "z0_customers")
            writer.finish()
            joined = " ".join(cursor.statements)
            self.assertIn("CREATE TABLE", joined)
            self.assertIn("sp_rename", joined)
            self.assertTrue(commits, "the successful path must commit")
            self.assertFalse(rollbacks)

    def test_a_failure_mid_write_never_touches_a_previously_successful_table(self):
        with fake_connection() as (cursor, commits, rollbacks):
            writer = self.make()
        # First run succeeds and claims the name.
        with fake_connection() as (cursor, commits, rollbacks):
            writer = self.make()
            writer.finish()
        # Second run fails while inserting; the rename must never be attempted.
        # finish() is called from a `finally` while the write error is still
        # propagating, matching how engine.py's ExitStack actually invokes it
        # mid-unwind - calling it afterward, with no exception in flight, would
        # not reproduce the failure path finish() has to detect.
        with fake_connection(fail_on=lambda sql: "INSERT INTO" in sql) as (cursor, commits, rollbacks):
            writer = self.make()
            with self.assertRaises(ConfigError):
                try:
                    writer.write_result(_result("A", Decimal("1.00"), True))
                    writer._flush()
                finally:
                    writer.finish()
            self.assertFalse(any("sp_rename" in s for s in cursor.statements),
                             "a failed write must never reach the swap")
            self.assertTrue(rollbacks)

    def test_the_very_first_run_leaves_no_table_at_all_on_failure(self):
        with fake_connection(fail_on=lambda sql: "CREATE TABLE" in sql) as (cursor, commits, rollbacks):
            with self.assertRaises(ConfigError):
                self.make()
            # No swap was ever reachable - construction itself failed.
            self.assertFalse(any("sp_rename" in s for s in cursor.statements))

    def test_finish_is_safe_to_call_once_only(self):
        with fake_connection() as (cursor, commits, rollbacks):
            writer = self.make()
            writer.finish()
            statements_after_first = len(cursor.statements)
            writer.finish()
            self.assertEqual(len(cursor.statements), statements_after_first, "a second finish() must do nothing")

    def test_fast_executemany_is_disabled_for_an_unbounded_string_column(self):
        # NVARCHAR(MAX) with fast_executemany batching real driver memory in
        # a way that scales with batch size, not with the actual data - the
        # incident this guards against. Bounded columns have no such column.
        unbounded = [{"name": "code", "type": "string"}]
        with fake_connection() as (log, commits, rollbacks):
            SqlServerOutputWriter(unbounded, self.destination, "p1", "Notes", self.store.claim_export_table)
            self.assertFalse(log.cursors[0].fast_executemany)

    def test_fast_executemany_stays_on_when_every_string_is_bounded(self):
        with fake_connection() as (log, commits, rollbacks):
            self.make()
            self.assertTrue(log.cursors[0].fast_executemany)

    def test_cleanup_after_an_exception_pyodbc_never_raises_uses_a_fresh_cursor(self):
        # The real incident: a MemoryError mid-executemany is not a
        # pyodbc.Error, so it reaches finish() unconverted - and the cursor it
        # happened on was left unable to run anything else. Cleanup must not
        # depend on that same cursor still working.
        with fake_connection(fail_on=lambda sql: "INSERT INTO" in sql, error=MemoryError) as (log, commits, rollbacks):
            writer = self.make()
            with self.assertRaises(MemoryError):
                try:
                    writer.write_result(_result("A", Decimal("1.00"), True))
                    writer._flush()
                finally:
                    writer.finish()
            self.assertEqual(len(log.cursors), 2, "cleanup must open its own cursor rather than reuse the broken one")
            self.assertTrue(any("DROP TABLE" in s for s in log.cursors[1].statements),
                             "the shadow table must still be dropped on a fresh cursor")
            self.assertTrue(rollbacks, "the failed insert transaction must still be rolled back")

    def test_a_cleanup_failure_is_logged_not_silently_lost(self):
        # If even the fresh cleanup cursor's DROP fails, the original
        # exception must still be the one that surfaces - but the cleanup
        # failure must not vanish without a trace, or an orphaned table
        # becomes invisible. The first DROP (inside __init__, clearing any
        # leftover shadow before CREATE) must still succeed, or construction
        # itself would fail before there is anything to test.
        drops_seen = {"count": 0}

        def fail_on(sql):
            if "INSERT INTO" in sql:
                return True
            if "DROP TABLE" in sql:
                drops_seen["count"] += 1
                return drops_seen["count"] > 1
            return False

        with fake_connection(fail_on=fail_on, error=MemoryError) as (log, commits, rollbacks):
            writer = self.make()
            with self.assertLogs(level="ERROR") as captured:
                with self.assertRaises(MemoryError):
                    try:
                        writer.write_result(_result("A", Decimal("1.00"), True))
                        writer._flush()
                    finally:
                        writer.finish()
            self.assertTrue(any("shadow" in message.lower() or "drop" in message.lower()
                                 for message in captured.output))

    def test_rows_written_is_accurate_before_the_final_partial_batch_flushes(self):
        with fake_connection() as (cursor, commits, rollbacks):
            writer = self.make()
            for i in range(5):
                writer.write_result(_result(f"A{i}", Decimal("1.00"), True))
            self.assertEqual(writer.rows_written, 5, "counted on write, not only after a full batch flushes")
            writer.finish()
            self.assertEqual(writer.rows_written, 5)


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "state.sqlite3")

    def test_the_same_pipeline_reuses_its_claimed_name_even_if_desired_changes(self):
        first = self.store.claim_export_table("p1", "z0_customers")
        second = self.store.claim_export_table("p1", "z0_renamed_pipeline")
        self.assertEqual(first, second, "the claim is permanent once made")

    def test_a_different_pipeline_cannot_claim_the_same_name(self):
        self.store.claim_export_table("p1", "z0_customers")
        with self.assertRaises(ConfigError):
            self.store.claim_export_table("p2", "z0_customers")

    def test_export_table_for_reports_none_before_any_claim(self):
        self.assertIsNone(self.store.export_table_for("never-run"))
        self.store.claim_export_table("p1", "z0_customers")
        self.assertEqual(self.store.export_table_for("p1"), "z0_customers")


class EndToEndTests(unittest.TestCase):
    """execute() wired to a DB destination, with a fake pyodbc underneath."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "in.csv").write_text("code;amount;active\nA;1.50;true\nB;2.50;false\n", encoding="utf-8")
        self.store = Store(self.root / "state.sqlite3")
        self.spec = {"version": 2, "name": "Customers", "source": {"kind": "csv", "path": "in.csv", "delimiter": ";"},
                     "columns": pipeline_columns(), "destination": {"kind": "sqlserver", "connection_env": "ETL_SQL_MAIN"}}

    def test_a_full_run_reports_the_table_and_writes_no_local_valid_file(self):
        with fake_connection() as (cursor, commits, rollbacks):
            report = execute(self.spec, self.root, self.root / "out", pipeline_id="p1", claim_table=self.store.claim_export_table)
        self.assertEqual(report["files"], ["rejected.csv"])
        self.assertEqual(report["table"], {"schema": "dbo", "name": "z0_customers", "rows": 2})
        directory = Path(report["directory"])
        self.assertTrue((directory / "rejected.csv").is_file())
        self.assertFalse((directory / "valid.csv").exists())

    def test_running_without_a_pipeline_id_is_refused(self):
        with fake_connection():
            with self.assertRaises(ConfigError):
                execute(self.spec, self.root, self.root / "out", claim_table=self.store.claim_export_table)

    def test_preview_never_touches_the_database_at_all(self):
        # No fake_connection patch here on purpose: a real pyodbc.connect
        # attempt would raise ImportError/connect-error and fail the test.
        report = execute(self.spec, self.root, limit=100)
        self.assertNotIn("directory", report)
        self.assertEqual(report["processed"], 2)

    def test_rejected_rows_are_unaffected_by_a_database_destination(self):
        (self.root / "in.csv").write_text("code;amount;active\nA;1.50;true\nB;not_a_number;false\n", encoding="utf-8")
        with fake_connection():
            report = execute(self.spec, self.root, self.root / "out", pipeline_id="p1", claim_table=self.store.claim_export_table)
        self.assertEqual(report["invalid"], 1)
        directory = Path(report["directory"])
        with (directory / "rejected.csv").open(encoding="utf-8-sig") as handle:
            self.assertIn("not_a_number", handle.read())


def _result(code, amount, active):
    from etl.models import RowResult, SourceRow
    return RowResult(SourceRow(1, {"code": code}), {}, {"code": code, "amount": amount, "active": active})


if __name__ == "__main__":
    unittest.main()
