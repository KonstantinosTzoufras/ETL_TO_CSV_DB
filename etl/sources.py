"""Source adapters expose headers and an iterator, independent of the UI."""
import csv
import os
from contextlib import contextmanager
from pathlib import Path

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


@contextmanager
def open_source(spec, root, batch_size=1000):
    source_spec(spec)
    if spec["kind"] == "csv":
        with input_path(root, spec["path"]).open(encoding=spec.get("encoding", "utf-8-sig"), newline="") as handle:
            reader = csv.reader(handle, delimiter=spec.get("delimiter", ";"), strict=True)
            headers = checked_headers(next(reader, []))

            def rows():
                for number, values in enumerate(reader, 2):
                    if not values:
                        continue
                    require(len(values) == len(headers), f"CSV record {number}: expected {len(headers)} fields, got {len(values)}")
                    yield dict(zip(headers, values))

            yield headers, rows()
        return

    try:
        import pyodbc
    except ImportError:
        raise ConfigError("SQL Server needs pyodbc. Install requirements.txt and the Microsoft ODBC driver.") from None
    connection_string = os.environ.get(spec["connection_env"])
    require(bool(connection_string), f"Set environment variable {spec['connection_env']} before starting ETL")
    connection = None
    cursor = None
    try:
        connection = pyodbc.connect(connection_string, timeout=10, autocommit=True)
        connection.timeout = 60
        cursor = connection.cursor()
        cursor.execute(f"SELECT * FROM {identifier(spec['schema'])}.{identifier(spec['table'])}")
        headers = checked_headers([c[0] for c in cursor.description])

        def rows():
            while batch := cursor.fetchmany(batch_size):
                for row in batch:
                    yield dict(zip(headers, row))

        yield headers, rows()
    except pyodbc.Error:
        # Drivers may include connection details in exception messages.
        raise ConfigError("SQL Server operation failed. Check connection settings, driver, table permissions and query timeout.") from None
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()


def inspect_source(spec, root):
    with open_source(spec, root) as (headers, _):
        return headers
