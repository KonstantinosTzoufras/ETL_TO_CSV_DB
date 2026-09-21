"""Query adapter: approved SELECT only, bound parameters, context-owned resources."""
from contextlib import closing, contextmanager
import os
import sys

from .models import SourceDefinition, SourceRow
from .queries import QueryError, authorize, check, parameter_value
from .serialization import _source_from_dict, _source_to_dict
from .sources import SourceStream, _sql_schema
from .spec import ConfigError, source_spec


def driver_error(error):
    state = str(error.args[0]) if error.args else ""
    code = "QUERY_TIMEOUT" if state.startswith(("HYT", "S1T")) else "QUERY_PERMISSION_DENIED" if state == "28000" else "QUERY_EXECUTION_FAILED"
    return QueryError(code, "Query source failed; check connection permissions, parameter types and timeout")


def binding_sizes(parameters, values, driver):
    sizes = []
    for parameter, value in zip(parameters, values):
        kind = parameter.type
        if kind == "string":
            size = max(1, len((value or "").encode("utf-16-le")) // 2)
            sizes.append((driver.SQL_WLONGVARCHAR if size > 4000 else driver.SQL_WVARCHAR, size, 0))
        elif kind == "decimal":
            scale = -value.as_tuple().exponent if value is not None else 0
            precision = max(len(value.as_tuple().digits), scale, 1) if value is not None else 38
            sizes.append((driver.SQL_DECIMAL, precision, scale))
        else:
            sizes.append(({"int": driver.SQL_BIGINT, "bool": driver.SQL_BIT, "date": driver.SQL_TYPE_DATE,
                           "datetime": driver.SQL_TYPE_TIMESTAMP}[kind], {"int": 19, "bool": 1, "date": 10, "datetime": 26}[kind], 6 if kind == "datetime" else 0))
    return sizes


def query_schema(description):
    """Build query metadata, disambiguating JOIN/wildcard result names.

    DB-API exposes result labels but not reliable table lineage. Keep the first
    occurrence unchanged and suffix later case-insensitive duplicates in result
    order. Avoid generated names that collide with an explicit result label.
    """
    check(bool(description), "QUERY_SCHEMA_INVALID", "Query requires nonempty result columns")
    raw = [item[0] for item in description]
    check(all(isinstance(name, str) and name.strip() for name in raw),
          "QUERY_SCHEMA_INVALID", "Give every unnamed query expression an alias")
    reserved = {name.casefold() for name in raw}
    used, names = set(), []
    for name in raw:
        folded = name.casefold()
        if folded not in used:
            candidate = name
        else:
            number = 2
            while True:
                suffix = f"__{number}"
                candidate = name[:128 - len(suffix)] + suffix
                candidate_folded = candidate.casefold()
                if candidate_folded not in used and candidate_folded not in reserved:
                    break
                number += 1
        used.add(candidate.casefold())
        names.append(candidate)
    renamed = [tuple([name, *tuple(item)[1:]]) for name, item in zip(names, description)]
    return _sql_schema(renamed)


class SqlServerQuerySource:
    def __init__(self, definition, batch_size=1000, *, timeout_cap=None):
        raw = _source_to_dict(definition) if isinstance(definition, SourceDefinition) else definition
        source_spec(raw)
        check(raw["kind"] == "sqlserver_query", "QUERY_INVALID", "Expected SQL query source")
        self.definition = _source_from_dict(raw)
        check(type(batch_size) is int and batch_size > 0, "QUERY_INVALID", "Batch size must be positive")
        check(timeout_cap is None or type(timeout_cap) is int and timeout_cap > 0, "QUERY_INVALID", "Timeout cap must be positive")
        self.batch_size, self.timeout_cap = batch_size, timeout_cap

    def read_schema(self):
        with self.open(timeout_cap=15) as stream:
            return stream.schema

    @contextmanager
    def open(self, *, timeout_cap=None):
        options = self.definition.options
        # Always revalidate safety and deployment approval immediately before
        # opening a connection, including execution of an old saved snapshot.
        query = authorize(options["connection_env"], options["query"])
        values = tuple(parameter_value(p) for p in query.parameters)
        connection_string = os.environ.get(options["connection_env"])
        check(bool(connection_string), "QUERY_PERMISSION_DENIED", "Approved query connection is not configured")
        try:
            import pyodbc
        except ImportError:
            raise QueryError("QUERY_EXECUTION_FAILED", "SQL query sources require pyodbc and an ODBC driver") from None
        connection = cursor = None
        try:
            connection = pyodbc.connect(connection_string, timeout=10, autocommit=True, readonly=True)
            connection.timeout = min(query.timeout_seconds, self.timeout_cap or query.timeout_seconds, timeout_cap or query.timeout_seconds)
            cursor = connection.cursor()
            if values:
                cursor.setinputsizes(binding_sizes(query.parameters, values, pyodbc))
                cursor.execute(query.sql, values)
            else:
                cursor.execute(query.sql)
            try:
                schema = query_schema(cursor.description)
            except QueryError:
                raise
            except ConfigError:
                raise QueryError("QUERY_SCHEMA_INVALID", "Give every result column a nonempty unique alias") from None
            with closing(self._rows(cursor, [c.name for c in schema], pyodbc)) as rows:
                yield SourceStream(schema, rows)
        except pyodbc.Error as error:
            raise driver_error(error) from None
        finally:
            unwinding = sys.exc_info()[0] is not None
            failed = False
            for resource in (cursor, connection):
                if resource is not None:
                    try:
                        resource.close()
                    except Exception:
                        failed = True
            if failed and not unwinding:
                raise QueryError("QUERY_EXECUTION_FAILED", "Could not close query resources cleanly") from None

    def _rows(self, cursor, headers, driver):
        number = 0
        while True:
            try:
                batch = cursor.fetchmany(self.batch_size)
            except driver.Error as error:
                raise driver_error(error) from None
            if not batch:
                return
            for values in batch:
                number += 1
                yield SourceRow(number, dict(zip(headers, values)))
