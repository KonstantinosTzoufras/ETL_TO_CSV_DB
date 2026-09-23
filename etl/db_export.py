"""Writing a pipeline's output into a database table instead of a file.

Approval is a separate, fail-closed gate from ETL_QUERY_CONNECTIONS: that one
attests read_only; reusing it here would either force a false attestation or
quietly weaken what read_only has meant everywhere else in this tool. A row
that already exists at the approved connection/schema is the real boundary -
this is defence in depth, not a substitute for database permissions.

A run either replaces the table completely or leaves the previous one exactly
as it was: rows are written to a shadow table, and only once every row has
landed does the shadow table take the claimed name, atomically, in the same
transaction that drops whatever was there before. A run that fails at any
point - mid-write, or before writing a single row - never leaves a half-built
table behind and never disturbs a previously successful one.
"""
import json
import logging
import os
import re
import sys
import unicodedata
from datetime import date, datetime, time
from decimal import Decimal

from .conversions import text
from .sources import identifier
from .spec import ConfigError, require

_ASCII_UNSAFE = re.compile(r"[^A-Za-z0-9_]")
_CONNECTION_ENV = re.compile(r"ETL_SQL_[A-Z0-9_]+")
MAX_TABLE_NAME = 100  # SQL Server's own limit is 128; leave headroom for the shadow prefix.


def export_policies():
    """Administrator-owned process configuration, never supplied by HTTP/spec."""
    try:
        policies = json.loads(os.environ.get("ETL_EXPORT_CONNECTIONS", "{}"))
        require(isinstance(policies, dict), "Invalid export connection policy")
        for reference, policy in policies.items():
            require(_CONNECTION_ENV.fullmatch(reference), "Invalid export connection policy")
            require(isinstance(policy, dict) and set(policy) == {"schema", "max_timeout_seconds"},
                    "Export connection policy needs schema and max_timeout_seconds")
            require(isinstance(policy["schema"], str) and policy["schema"].strip() and len(policy["schema"]) <= 128,
                    "Export connection policy schema is required")
            require(type(policy["max_timeout_seconds"]) is int and 1 <= policy["max_timeout_seconds"] <= 600,
                    "Export connection policy timeout must be 1-600 seconds")
        return policies
    except (ValueError, TypeError):
        raise ConfigError("Invalid export connection policy") from None


def authorize_export(connection_env):
    policy = export_policies().get(connection_env)
    require(policy is not None, "Connection is not approved for database export")
    return policy


def sanitize_table_name(name):
    """An ASCII-safe identifier core, so the generated name survives any tool.

    Greek stays legal T-SQL either way (SQL Server is Unicode-native), but a
    name that never leaves ASCII never becomes a support ticket in whatever
    reads it next.
    """
    ascii_only = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"_+", "_", _ASCII_UNSAFE.sub("_", ascii_only)).strip("_").lower()
    if not cleaned:
        cleaned = "export"
    if cleaned[0].isdigit():
        cleaned = "t_" + cleaned
    return cleaned[:MAX_TABLE_NAME]


def table_name_for(pipeline_name):
    return "z0_" + sanitize_table_name(pipeline_name)


def shadow_name_for(pipeline_id):
    # Deterministic per pipeline, not per run: a crashed prior run's leftover
    # shadow is simply overwritten by DROP-then-CREATE at the start of the next.
    return "z0__building_" + re.sub(r"[^a-f0-9]", "", pipeline_id.lower())[:32]


_SQL_TYPES = {
    "string": lambda length: f"NVARCHAR({length})" if length else "NVARCHAR(MAX)",
    "int": lambda length: "BIGINT",
    "decimal": lambda length: "DECIMAL(38, 10)",
    "float": lambda length: "FLOAT",
    "bool": lambda length: "BIT",
    "date": lambda length: "DATE",
    "datetime": lambda length: "DATETIME2",
}


def sql_type_for(column):
    return _SQL_TYPES[column.get("type", "string")](column.get("max_length"))


def _sql_string(value):
    """A quoted T-SQL string literal, for the few places an identifier cannot
    be used (OBJECT_ID and sp_rename take a string, not a bracketed name).
    table_name/shadow_name are already restricted to [a-z0-9_] by the
    sanitizers above; schema comes from the administrator's own environment
    configuration and is escaped here rather than assumed safe."""
    return "'" + value.replace("'", "''") + "'"


def sql_value(value, kind):
    """A converted domain value as a parameter pyodbc can bind directly."""
    if value is None:
        return None
    if kind == "decimal":
        return value if isinstance(value, Decimal) else Decimal(text(value))
    if kind in ("date", "datetime") and isinstance(value, (date, datetime)):
        return value
    if kind == "bool":
        return bool(value)
    if kind in ("int", "float"):
        return value
    return text(value) if not isinstance(value, str) else value


class SqlServerOutputWriter:
    """write_result(valid RowResult), finish() - the same shape as OutputWriter.

    finish() is registered via the caller's ExitStack, exactly like the file
    writers, so it always runs - success or failure - and decides which of
    those it was from whether an exception is unwinding through it.
    """
    # Measured against the real destination server: 1000 gave ~2,200 rows/sec,
    # 5000 ~4,500, 20000 ~5,900 - each executemany pays a largely fixed
    # per-round-trip cost, so fewer, larger round trips win. The whole write is
    # already one transaction regardless of batch size (committed only in
    # finish(), for the shadow-swap), so a bigger batch adds no lock-duration
    # risk beyond what already exists - only client-side memory for one batch.
    BATCH = 20000

    def __init__(self, columns, destination, pipeline_id, pipeline_name, claim_table):
        require(isinstance(pipeline_id, str) and pipeline_id, "Database export needs a saved pipeline; save it first")
        try:
            import pyodbc
        except ImportError:
            raise ConfigError("SQL Server needs pyodbc. Install requirements.txt and the Microsoft ODBC driver.") from None
        self._pyodbc = pyodbc
        connection_env = destination["connection_env"]
        policy = authorize_export(connection_env)
        self.schema = policy["schema"]
        connection_string = os.environ.get(connection_env)
        require(bool(connection_string), f"Set environment variable {connection_env} before starting ETL")
        self.names = [column["name"] for column in columns]
        self.types = {column["name"]: column.get("type", "string") for column in columns}
        # An explicit table name is still routed through the same sanitizer as
        # the pipeline-name default, and through the same permanent claim: the
        # first successful run decides it, later edits here have no effect.
        self.table_name = claim_table(pipeline_id, table_name_for(destination.get("table") or pipeline_name))
        self.shadow_name = shadow_name_for(pipeline_id)
        self._batch = []
        self._rows = 0
        self._closed = False

        self.connection = pyodbc.connect(connection_string, timeout=10, autocommit=False)
        self.connection.timeout = policy["max_timeout_seconds"]
        self.cursor = self.connection.cursor()
        # fast_executemany asks the driver to bind a whole batch's parameters
        # at once. For an unbounded NVARCHAR(MAX)/VARCHAR(MAX) column this is a
        # known pyodbc failure mode: the driver sizes its per-row buffer for
        # the worst case rather than the actual value, and a large batch times
        # an unbounded column can exhaust memory outright (observed: a
        # MemoryError mid-batch on a column with no max_length). Bounded
        # columns have no such worst case, so they keep the fast path.
        if all(c.get("type", "string") != "string" or c.get("max_length") for c in columns):
            try:
                self.cursor.fast_executemany = True
            except Exception:
                pass
        try:
            schema_id, shadow_id = identifier(self.schema), identifier(self.shadow_name)
            self.cursor.execute(f"IF OBJECT_ID({_sql_string(self.schema + '.' + self.shadow_name)}) IS NOT NULL DROP TABLE {schema_id}.{shadow_id}")
            columns_sql = ", ".join(f"{identifier(c['name'])} {sql_type_for(c)}" for c in columns)
            self.cursor.execute(f"CREATE TABLE {schema_id}.{shadow_id} ({columns_sql})")
            self.connection.commit()
        except pyodbc.Error:
            self._close(commit=False)
            raise ConfigError("Database export failed while preparing the table") from None

    def write_result(self, result):
        row = [sql_value(result.converted_values.get(name), self.types[name]) for name in self.names]
        self._batch.append(row)
        # Counted the moment a row is accepted, not when its batch is flushed:
        # the caller may read rows_written before finish()'s final flush runs,
        # and an under-count there would be wrong on every row count that
        # doesn't happen to be a multiple of BATCH.
        self._rows += 1
        if len(self._batch) >= self.BATCH:
            self._flush()

    def _flush(self):
        if not self._batch:
            return
        schema_id, shadow_id = identifier(self.schema), identifier(self.shadow_name)
        columns_sql = ", ".join(identifier(name) for name in self.names)
        placeholders = ", ".join("?" for _ in self.names)
        sql = f"INSERT INTO {schema_id}.{shadow_id} ({columns_sql}) VALUES ({placeholders})"
        try:
            self.cursor.executemany(sql, self._batch)
        except self._pyodbc.Error:
            raise ConfigError("Database export failed while writing rows") from None
        self._batch = []

    def _close(self, *, commit):
        try:
            if commit:
                self.connection.commit()
            else:
                self.connection.rollback()
        finally:
            try:
                self.cursor.close()
            finally:
                self.connection.close()

    def finish(self):
        if self._closed:
            return
        self._closed = True
        unwinding = sys.exc_info()[0] is not None
        schema_id, shadow_id, table_id = identifier(self.schema), identifier(self.shadow_name), identifier(self.table_name)
        if unwinding:
            # The failure already happened; do not let cleanup mask it with a
            # second exception, and never touch the previously successful table.
            # A fresh cursor, not the one the failure happened on: a batch that
            # died mid-executemany can leave its cursor unable to run anything
            # else, and reusing it here would just repeat that failure.
            try:
                self.connection.rollback()
                cleanup_cursor = self.connection.cursor()
                cleanup_cursor.execute(f"IF OBJECT_ID({_sql_string(self.schema + '.' + self.shadow_name)}) IS NOT NULL DROP TABLE {schema_id}.{shadow_id}")
                self.connection.commit()
                cleanup_cursor.close()
            except Exception:
                # Still don't let cleanup mask the original failure - but a
                # failed cleanup leaves a table behind, so it must be visible
                # somewhere rather than vanishing along with the exception.
                logging.getLogger(__name__).exception(
                    "Database export cleanup failed to drop shadow table %s.%s", self.schema, self.shadow_name)
            self._close(commit=False)
            return
        try:
            self._flush()
            self.cursor.execute(f"IF OBJECT_ID({_sql_string(self.schema + '.' + self.table_name)}) IS NOT NULL DROP TABLE {schema_id}.{table_id}")
            self.cursor.execute(f"EXEC sp_rename {_sql_string(self.schema + '.' + self.shadow_name)}, {_sql_string(self.table_name)}")
            self._close(commit=True)
        except self._pyodbc.Error:
            self._close(commit=False)
            raise ConfigError("Database export failed while finishing the table swap") from None

    @property
    def rows_written(self):
        return self._rows
