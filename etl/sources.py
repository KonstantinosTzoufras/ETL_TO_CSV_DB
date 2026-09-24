"""Context-managed source adapters with v1 dictionary compatibility wrappers.

Adapters expose source values without transforms, conversion or null policy.
Consume rows inside the open() context; exiting it closes both the iterator and
its resources, including when a consumer stops early or raises an exception.
"""
import csv
import itertools
import os
import sys
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import ContextManager, Protocol, runtime_checkable

from .models import SourceColumn, SourceDefinition, SourceRow
from .spec import ConfigError, require, source_spec


def input_path(root, name, extension=".csv"):
    path = (Path(root) / name).resolve()
    require(path.is_relative_to(Path(root).resolve()), "Input files must be inside the workspace")
    require(path.suffix.lower() == extension, f"{extension[1:].upper()} sources must use a {extension} file")
    require(path.is_file(), f"{extension[1:].upper()} file not found: {name}")
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


def _local_tag(tag):
    return tag.rsplit("}", 1)[-1] if tag.startswith("{") else tag


class XmlSource:
    """Flat XML: every direct child of the document root is one row, and that
    row element's own direct children are its columns (tag name -> text).

    Deliberately simple for a first cut: no attributes, no nested/repeating
    structure below the row (a SAP-style multi-level export needs a real
    design decision - which level is "the row" - before it can be supported).
    """

    def __init__(self, definition, root):
        self.definition = _definition(definition, "xml")
        self.root = Path(root).resolve()

    def read_schema(self) -> tuple[SourceColumn, ...]:
        with self.open() as stream:
            return stream.schema

    @contextmanager
    def open(self) -> Iterator[SourceStream]:
        options = self.definition.options
        path = input_path(self.root, options["path"], extension=".xml")
        handle = path.open("rb")
        try:
            elements = self._row_elements(handle)
            try:
                first = next(elements)
            except StopIteration:
                raise ConfigError("XML source has no row elements") from None
            headers = checked_headers([_local_tag(child.tag) for child in first])
            schema = tuple(SourceColumn(name, native_type="str") for name in headers)
            with closing(self._rows(itertools.chain([first], elements), headers)) as rows:
                yield SourceStream(schema, rows)
        finally:
            handle.close()

    @staticmethod
    def _row_elements(handle):
        # Every direct child of the root, streamed without holding the whole
        # document in memory: a stack tracks depth since iterparse gives no
        # parent links, and each row is cleared once consumed.
        try:
            context = ET.iterparse(handle, events=("start", "end"))
            stack = []
            for event, element in context:
                if event == "start":
                    stack.append(element)
                    continue
                stack.pop()
                if len(stack) == 1:
                    yield element
                    element.clear()
        except ET.ParseError:
            raise ConfigError("XML source is not well-formed") from None

    @staticmethod
    def _rows(elements, headers):
        number = 0
        for element in elements:
            number += 1
            values = {}
            for child in element:
                name = _local_tag(child.tag)
                require(name not in values, f"XML record {number}: duplicate element <{name}>")
                require(name in headers, f"XML record {number}: unexpected element <{name}>")
                values[name] = child.text or ""
            require(len(values) == len(headers), f"XML record {number}: expected {len(headers)} elements, got {len(values)}")
            yield SourceRow(number, values)


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
    if kind == "sqlserver_query":
        from .query_source import SqlServerQuerySource
        return SqlServerQuerySource(spec, batch_size=batch_size)
    if kind == "csv":
        return CsvSource(spec, root)
    if kind == "xml":
        return XmlSource(spec, root)
    if kind == "sqlserver":
        return SqlServerSource(spec, batch_size=batch_size)
    raise ConfigError("Source kind must be csv, xml or sqlserver")


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
