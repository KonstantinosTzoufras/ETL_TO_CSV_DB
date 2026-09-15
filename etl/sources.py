"""Context-managed source adapters with v1 dictionary compatibility wrappers.

Adapters expose source values without transforms, conversion or null policy.
Consume rows inside the open() context; exiting it closes both the iterator and
its resources, including when a consumer stops early or raises an exception.
"""
import csv
import os
import sys
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import ContextManager, Protocol, runtime_checkable

from .models import SourceColumn, SourceDefinition, SourceRow
from .spec import ConfigError, require, source_spec


def input_path(root, name):
    path = (Path(root) / name).resolve()
    require(path.is_relative_to(Path(root).resolve()), "Input files must be inside the workspace")
    require(path.suffix.lower() == ".csv", "CSV sources must use a .csv file")
    require(path.is_file(), f"CSV file not found: {name}")
    return path


def identifier(value):
    return "[" + value.replace("]", "]]") + "]"


def checked_headers(headers):
    require(headers and all(isinstance(h, str) and h.strip() for h in headers), "Source requires nonempty column headers")
    require(len(set(headers)) == len(headers), "Source has duplicate column headers")
    return headers


@dataclass(frozen=True, slots=True)
class SourceStream:
    """Schema and single-pass rows from one open source session."""

    schema: tuple[SourceColumn, ...]
    rows: Iterator[SourceRow]

    def __iter__(self) -> Iterator[SourceRow]:
        return self.rows


@runtime_checkable
class Source(Protocol):
    def read_schema(self) -> tuple[SourceColumn, ...]: ...

    def open(self) -> ContextManager[SourceStream]: ...


def _definition(spec, kind):
    if isinstance(spec, SourceDefinition):
        require("kind" not in spec.options, "Source options must not redefine kind")
        spec = {"kind": spec.kind, **spec.options}
    source_spec(spec)
    require(spec["kind"] == kind, f"Expected {kind} source")
    return SourceDefinition(kind, {key: value for key, value in spec.items() if key != "kind"})


class CsvSource:
    def __init__(self, definition, root):
        self.definition = _definition(definition, "csv")
        self.root = Path(root).resolve()

    def read_schema(self) -> tuple[SourceColumn, ...]:
        with self.open() as stream:
            return stream.schema

    @contextmanager
    def open(self) -> Iterator[SourceStream]:
        options = self.definition.options
        with input_path(self.root, options["path"]).open(encoding=options.get("encoding", "utf-8-sig"), newline="") as handle:
            reader = csv.reader(handle, delimiter=options.get("delimiter", ";"), strict=True)
            headers = checked_headers(next(reader, []))
            schema = tuple(SourceColumn(name, native_type="str") for name in headers)
            with closing(self._rows(reader, headers)) as rows:
                yield SourceStream(schema, rows)

    @staticmethod
    def _rows(reader, headers):
        number = 0
        csv_record = 1  # Header included, as in legacy structural-error messages.
        while True:
            line_start = reader.line_num + 1
            try:
                values = next(reader)
            except StopIteration:
                return
            csv_record += 1
            if not values:
                continue  # Preserve legacy blank-record skipping.
            require(len(values) == len(headers), f"CSV record {csv_record}: expected {len(headers)} fields, got {len(values)}")
            number += 1
            yield SourceRow(number, dict(zip(headers, values)), line_start, reader.line_num)


SQL_ERROR = "SQL Server operation failed. Check connection settings, driver, table permissions and query timeout."


def _sql_schema(description):
    require(bool(description), "Source requires nonempty column headers")
    headers = checked_headers([item[0] for item in description])
    columns = []
    for name, item in zip(headers, description):
        def part(index):
            return item[index] if len(item) > index else None

        def size(index):
            value = part(index)
            return value if type(value) is int and value >= 0 else None

        type_code = part(1)
        columns.append(SourceColumn(
            name=name,
            # DB-API description exposes a Python value class, not SQL DDL.
            native_type=type_code.__name__ if isinstance(type_code, type) else None,
            nullable=part(6) if type(part(6)) is bool else None,
            display_size=size(2), internal_size=size(3), precision=size(4), scale=size(5),
        ))
    return tuple(columns)


class SqlServerSource:
    def __init__(self, definition, batch_size=1000):
        self.definition = _definition(definition, "sqlserver")
        require(type(batch_size) is int and batch_size > 0, "SQL batch_size must be a positive integer")
        self.batch_size = batch_size

    def read_schema(self) -> tuple[SourceColumn, ...]:
        # Keep the existing SELECT semantics; inspect description without fetching.
        with self.open() as stream:
            return stream.schema

    @contextmanager
    def _cursor(self):
        try:
            import pyodbc
        except ImportError:
            raise ConfigError("SQL Server needs pyodbc. Install requirements.txt and the Microsoft ODBC driver.") from None
        options = self.definition.options
        connection_string = os.environ.get(options["connection_env"])
        require(bool(connection_string), f"Set environment variable {options['connection_env']} before starting ETL")
        connection = cursor = None
        try:
            connection = pyodbc.connect(connection_string, timeout=10, autocommit=True)
            connection.timeout = 60
            cursor = connection.cursor()
            cursor.execute(f"SELECT * FROM {identifier(options['schema'])}.{identifier(options['table'])}")
            yield cursor
        except pyodbc.Error:
            # Drivers can include connection details in their messages.
            raise ConfigError(SQL_ERROR) from None
        finally:
            unwinding = sys.exc_info()[0] is not None
            close_failed = False
            for resource in (cursor, connection):
                if resource is not None:
                    try:
                        resource.close()
                    except Exception:
                        close_failed = True
            # Attempt both closes, but do not hide an existing processing error.
            if close_failed and not unwinding:
                raise ConfigError(SQL_ERROR) from None

    @contextmanager
    def open(self) -> Iterator[SourceStream]:
        with self._cursor() as cursor:
            schema = _sql_schema(cursor.description)
            headers = [column.name for column in schema]
            with closing(self._rows(cursor, headers)) as rows:
                yield SourceStream(schema, rows)

    def _rows(self, cursor, headers):
        import pyodbc

        number = 0
        while True:
            try:
                batch = cursor.fetchmany(self.batch_size)
            except pyodbc.Error:
                # Also sanitize errors caught by a consumer *inside* open().
                raise ConfigError(SQL_ERROR) from None
            if not batch:
                return
            for row in batch:
                number += 1
                # No stringification, normalization, date inference or null policy.
                yield SourceRow(number, dict(zip(headers, row)))


def create_source(spec, root, batch_size=1000) -> Source:
    """Construct an adapter from a v1 dictionary or immutable definition."""
    if isinstance(spec, SourceDefinition):
        kind = spec.kind
    else:
        source_spec(spec)
        kind = spec["kind"]
    if kind == "csv":
        return CsvSource(spec, root)
    if kind == "sqlserver":
        return SqlServerSource(spec, batch_size=batch_size)
    raise ConfigError("Source kind must be csv or sqlserver")


@contextmanager
def open_source(spec, root, batch_size=1000):
    """V1 API: yield (list[str], iterator[dict]) using one adapter session.

    Source positions are available through the new adapter API. They are not
    added to legacy row dictionaries or engine reports in this stage.
    """
    with create_source(spec, root, batch_size).open() as stream:
        with closing((dict(row.values) for row in stream.rows)) as rows:
            yield [column.name for column in stream.schema], rows


def inspect_source(spec, root):
    return [column.name for column in create_source(spec, root).read_schema()]
