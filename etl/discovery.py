"""Read-only discovery before execution; no mapping, processing or exporting."""
import base64
from collections.abc import Mapping
from contextlib import contextmanager
import csv
from dataclasses import dataclass, fields, is_dataclass, replace
import heapq
import hashlib
import json
import os
import sys
from pathlib import Path, PureWindowsPath
from typing import Generic, Protocol, TypeVar, runtime_checkable

from .models import _Immutable, SourceColumn, SourceDefinition, SourceRow
from .serialization import json_default
from .sources import create_source
from .spec import ConfigError, source_spec

MAX_SAMPLE_BYTES = 1024 * 1024
T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Dataset(_Immutable):
    key: str
    connector: str
    kind: str
    namespace: tuple[str, ...]
    name: str


@dataclass(frozen=True, slots=True)
class DiscoveryCapabilities(_Immutable):
    operations: tuple[str, ...]
    dataset_kinds: tuple[str, ...]
    metadata_fields: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DiscoveredColumn(_Immutable):
    column: SourceColumn
    declared_type: str | None = None
    max_length_bytes: int | None = None  # SQL -1 means max/unbounded, not unknown.


@dataclass(frozen=True, slots=True)
class SourceSample(_Immutable):
    source: SourceDefinition
    columns: tuple[SourceColumn, ...]
    rows: tuple[SourceRow, ...]
    requested_limit: int
    stop_reason: str


@dataclass(frozen=True, slots=True)
class Page(_Immutable, Generic[T]):
    items: tuple[T, ...]
    next_cursor: str | None = None


class DiscoveryError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def check(condition, code, message):
    if not condition:
        raise DiscoveryError(code, message)


def to_dict(value):
    """Discovery response boundary; reuse existing exact scalar JSON encoding."""
    if type(value) is int and abs(value) > 9007199254740991:
        return {"$type": "integer", "value": str(value)}
    if isinstance(value, SourceDefinition):
        return {"kind": value.kind, **to_dict(value.options)}
    if is_dataclass(value):
        return {field.name: to_dict(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {key: to_dict(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [to_dict(item) for item in value]
    return value


def _size(value):
    return len(json.dumps(to_dict(value), ensure_ascii=False, default=json_default).encode("utf-8"))


def _limit(limit):
    check(type(limit) is int and 1 <= limit <= 100, "invalid_read_options", "Limit must be an integer from 1 to 100")


def _namespace(value):
    check(isinstance(value, (tuple, list)) and all(isinstance(item, str) and item for item in value),
          "invalid_read_options", "Namespace must contain nonempty names")
    return tuple(value)


@contextmanager
def _source_errors():
    try:
        yield
    except DiscoveryError:
        raise
    except PermissionError:
        raise DiscoveryError("access_denied", "Access to this source was denied") from None
    except FileNotFoundError:
        raise DiscoveryError("dataset_unavailable", "Dataset is no longer available") from None
    except UnicodeError:
        raise DiscoveryError("invalid_read_options", "Cannot decode this file with the selected encoding") from None
    except csv.Error:
        raise DiscoveryError("malformed_source", "Malformed CSV; check delimiter and quoting") from None
    except ConfigError as error:
        message = str(error)
        code = "invalid_headers" if message.startswith("Source ") else "malformed_source" if message.startswith("CSV record ") else "source_read_failed"
        raise DiscoveryError(code, message if code != "source_read_failed" else "Source read failed; check connection, permissions and settings") from None
    except (OSError, TypeError, ValueError):
        raise DiscoveryError("source_read_failed", "Source read failed; check the dataset and settings") from None


@runtime_checkable
class SourceDiscovery(Protocol):
    def capabilities(self) -> DiscoveryCapabilities: ...
    def list_namespaces(self, namespace=(), *, cursor=None, limit=100) -> Page: ...
    def list_datasets(self, namespace=(), *, cursor=None, limit=100) -> Page[Dataset]: ...
    def resolve_dataset(self, locator: str) -> Dataset: ...
    def configure(self, dataset: Dataset, options: Mapping[str, str]) -> SourceDefinition: ...
    def inspect(self, source: SourceDefinition) -> tuple[DiscoveredColumn, ...]: ...
    def sample(self, source: SourceDefinition, *, limit=20) -> SourceSample: ...


class _Discovery:
    OPERATIONS = ("list_namespaces", "list_datasets", "resolve_dataset", "configure", "inspect", "sample")

    def _token(self, scope, value):
        payload = json.dumps([self.context, scope, value], ensure_ascii=True).encode()
        return base64.urlsafe_b64encode(payload).decode()

    def _untoken(self, token, scope):
        try:
            check(isinstance(token, str) and len(token) <= 8192, "invalid_read_options", "Invalid discovery token")
            context, actual_scope, value = json.loads(base64.b64decode(token, altchars=b"-_", validate=True))
            check(context == self.context and actual_scope == scope, "invalid_read_options", "Token belongs to another discovery context")
            return value
        except DiscoveryError:
            raise
        except (ValueError, TypeError, UnicodeError):
            raise DiscoveryError("invalid_read_options", "Invalid discovery token") from None

    def _after(self, cursor, scope):
        after = self._untoken(cursor, scope) if cursor else ""
        check(isinstance(after, str), "invalid_read_options", "Invalid page cursor")
        return after

    def inspect(self, source):
        source = self._validate_source(source)
        with _source_errors():
            return tuple(DiscoveredColumn(column) for column in create_source(source, self.root).read_schema())

    def sample(self, source, *, limit=20):
        _limit(limit)
        source = self._validate_source(source)
        with _source_errors():
            with create_source(source, self.root, batch_size=limit).open() as stream:
                rows = []
                sample = SourceSample(source, stream.schema, (), limit, "end_of_source")
                size = _size(sample)
                check(size <= MAX_SAMPLE_BYTES, "sample_too_large", "Sample schema exceeds the 1 MiB response limit")
                iterator = iter(stream)
                for _ in range(limit):
                    try:
                        row = next(iterator)
                    except StopIteration:
                        break
                    size += _size(row) + 2
                    check(size <= MAX_SAMPLE_BYTES, "sample_too_large", "Source sample exceeds 1 MiB; reduce the limit or use another dataset")
                    rows.append(row)
                else:
                    sample = replace(sample, stop_reason="row_limit")
                return replace(sample, rows=tuple(rows))


class CsvDiscovery(_Discovery):
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.context = "csv-workspace:" + hashlib.sha256(str(self.root).encode("utf-8")).hexdigest()

    def capabilities(self):
        return DiscoveryCapabilities(self.OPERATIONS, ("file",), ("name", "ordinal", "native_type", "record_number", "physical_lines"))

    def _path(self, relative, *, directory=False):
        check(isinstance(relative, str) and "\x00" not in relative, "invalid_read_options", "Invalid workspace-relative path")
        candidate = Path(relative)
        check(not candidate.is_absolute() and not PureWindowsPath(relative).drive and ".." not in candidate.parts,
              "access_denied", "Choose a path inside the workspace")
        with _source_errors():
            path = (self.root / candidate).resolve()
            check(path.is_relative_to(self.root), "access_denied", "Choose a path inside the workspace")
            check(path.is_dir() if directory else path.is_file(), "dataset_unavailable", "Folder or file is unavailable")
        if not directory:
            check(path.suffix.lower() == ".csv", "invalid_read_options", "Choose a CSV file")
        return path

    def _folder(self, namespace):
        namespace = _namespace(namespace)
        check(all(item not in (".", "..") and "/" not in item and "\\" not in item for item in namespace),
              "access_denied", "Invalid workspace folder")
        return self._path("/".join(namespace), directory=True), namespace

    def _list(self, namespace, cursor, limit, directories):
        _limit(limit)
        folder, namespace = self._folder(namespace)
        scope = ["folders" if directories else "files", list(namespace)]
        after = self._after(cursor, scope)
        def candidates():
            with os.scandir(folder) as entries:
                for entry in entries:
                    path = Path(entry.path)
                    if path.name <= after or path.name.startswith(".") or path.name == "__pycache__":
                        continue
                    if not path.resolve().is_relative_to(self.root):
                        continue
                    if entry.is_dir() if directories else entry.is_file() and path.suffix.lower() == ".csv":
                        yield path.name
        with _source_errors():
            names = heapq.nsmallest(limit + 1, candidates())
        items = tuple((*namespace, name) for name in names[:limit]) if directories else tuple(self.resolve_dataset("/".join((*namespace, name))) for name in names[:limit])
        return Page(items, self._token(scope, names[limit - 1]) if len(names) > limit else None)

    def list_namespaces(self, namespace=(), *, cursor=None, limit=100):
        return self._list(namespace, cursor, limit, True)

    def list_datasets(self, namespace=(), *, cursor=None, limit=100):
        return self._list(namespace, cursor, limit, False)

    def resolve_dataset(self, locator):
        check(isinstance(locator, str), "invalid_read_options", "Dataset locator must be text")
        relative = self._untoken(locator[4:], "file") if locator.startswith("csv:") else locator
        path = self._path(relative)
        relative = path.relative_to(self.root)
        return Dataset("csv:" + self._token("file", relative.as_posix()), "csv", "file", relative.parts[:-1], path.name)

    def configure(self, dataset, options):
        check(isinstance(dataset, Dataset) and dataset.connector == "csv", "invalid_read_options", "Expected a CSV dataset")
        current = self.resolve_dataset(dataset.key)
        check(current == dataset, "dataset_unavailable", "Dataset changed; select it again")
        check(isinstance(options, Mapping) and not set(options) - {"delimiter", "encoding"}, "invalid_read_options", "CSV options are delimiter and encoding only")
        source = SourceDefinition("csv", {"path": "/".join((*current.namespace, current.name)),
                                           "delimiter": options.get("delimiter", ";"), "encoding": options.get("encoding", "utf-8-sig")})
        return self._validate_source(source)

    def _validate_source(self, source):
        check(isinstance(source, SourceDefinition) and source.kind == "csv", "invalid_read_options", "Expected a CSV source")
        try:
            source_spec(to_dict(source))
        except (ConfigError, TypeError, ValueError):
            raise DiscoveryError("invalid_read_options", "Invalid CSV delimiter, encoding or source options") from None
        self._path(source.options["path"])
        return source


class SqlServerDiscovery(_Discovery):
    def __init__(self, connection_env, root):
        self.root = Path(root).resolve()
        try:
            source_spec({"kind": "sqlserver", "connection_env": connection_env, "schema": "dbo", "table": "placeholder"})
        except (ConfigError, TypeError):
            raise DiscoveryError("invalid_read_options", "Use a configured ETL_SQL_ connection variable") from None
        self.connection_env = connection_env
        self.context = "sqlserver:" + connection_env

    def capabilities(self):
        return DiscoveryCapabilities(self.OPERATIONS, ("table", "view"),
                                     ("name", "ordinal", "native_type", "declared_type", "nullable", "precision", "scale", "max_length_bytes", "record_number"))

    @contextmanager
    def _cursor(self):
        try:
            import pyodbc
        except ImportError:
            raise DiscoveryError("connection_failed", "SQL discovery requires pyodbc and an ODBC driver") from None
        connection_string = os.environ.get(self.connection_env)
        check(bool(connection_string), "connection_failed", "The selected connection variable is not configured")
        connection = cursor = None
        try:
            connection = pyodbc.connect(connection_string, timeout=10, autocommit=True, readonly=True)
            connection.timeout = 15
            cursor = connection.cursor()
            yield cursor
        except pyodbc.Error as error:
            state = str(error.args[0]) if error.args else ""
            code = "timeout" if state.startswith(("HYT", "S1T")) else "access_denied" if state == "28000" else "connection_failed" if connection is None else "source_read_failed"
            raise DiscoveryError(code, "SQL discovery failed; check connection settings, permissions and timeout") from None
        finally:
            unwinding = sys.exc_info()[0] is not None
            close_failed = False
            for resource in (cursor, connection):
                if resource is not None:
                    try:
                        resource.close()
                    except Exception:
                        close_failed = True
            if close_failed and not unwinding:
                raise DiscoveryError("source_read_failed", "Could not close SQL discovery resources cleanly")

    def list_namespaces(self, namespace=(), *, cursor=None, limit=100):
        _limit(limit)
        check(not _namespace(namespace), "unsupported_operation", "SQL discovery lists schemas only within its configured database")
        after = self._after(cursor, "schemas")
        with self._cursor() as handle:
            handle.execute("SELECT TOP (?) name FROM sys.schemas WHERE name > ? AND name NOT IN ('sys','INFORMATION_SCHEMA') ORDER BY name", limit + 1, after)
            rows = handle.fetchmany(limit + 1)
        return Page(tuple((r[0],) for r in rows[:limit]), self._token("schemas", rows[limit - 1][0]) if len(rows) > limit else None)

    def list_datasets(self, namespace=(), *, cursor=None, limit=100):
        _limit(limit)
        namespace = _namespace(namespace)
        check(len(namespace) == 1, "invalid_read_options", "Select one SQL schema")
        scope = ["objects", list(namespace)]
        after = self._after(cursor, scope)
        with self._cursor() as handle:
            handle.execute("SELECT TOP (?) name, kind FROM (SELECT name, 'table' AS kind FROM sys.tables WHERE schema_id=SCHEMA_ID(?) AND is_ms_shipped=0 UNION ALL SELECT name, 'view' AS kind FROM sys.views WHERE schema_id=SCHEMA_ID(?) AND is_ms_shipped=0) AS datasets WHERE name > ? ORDER BY name", limit + 1, namespace[0], namespace[0], after)
            rows = handle.fetchmany(limit + 1)
        return Page(tuple(self._dataset(namespace[0], r[0], r[1]) for r in rows[:limit]), self._token(scope, rows[limit - 1][0]) if len(rows) > limit else None)

    def _dataset(self, schema, name, kind):
        return Dataset("sqlserver:" + self._token("object", [schema, name, kind]), "sqlserver", kind, (schema,), name)

    def resolve_dataset(self, locator):
        check(isinstance(locator, str) and locator.startswith("sqlserver:"), "invalid_read_options", "Select a SQL dataset key")
        value = self._untoken(locator[10:], "object")
        check(isinstance(value, list) and len(value) == 3 and all(isinstance(v, str) for v in value), "invalid_read_options", "Invalid dataset key")
        schema, name, kind = value
        check(kind in ("table", "view"), "unsupported_operation", "Only tables and views are supported")
        with self._cursor() as handle:
            catalog = "sys.tables" if kind == "table" else "sys.views"
            handle.execute(f"SELECT name FROM {catalog} WHERE schema_id=SCHEMA_ID(?) AND name=? AND is_ms_shipped=0", schema, name)
            row = handle.fetchone()
        check(row is not None, "dataset_unavailable", "Dataset is unavailable or not visible to this connection")
        return self._dataset(schema, row[0], kind)

    def configure(self, dataset, options):
        check(isinstance(dataset, Dataset) and dataset.connector == "sqlserver", "invalid_read_options", "Expected a SQL dataset")
        check(isinstance(options, Mapping) and not options, "invalid_read_options", "SQL discovery accepts no query or read options")
        current = self.resolve_dataset(dataset.key)
        check(current == dataset, "dataset_unavailable", "Dataset changed; select it again")
        return self._validate_source(SourceDefinition("sqlserver", {"connection_env": self.connection_env, "schema": current.namespace[0], "table": current.name}))

    def _validate_source(self, source):
        check(isinstance(source, SourceDefinition) and source.kind == "sqlserver", "invalid_read_options", "Expected a SQL source")
        try:
            source_spec(to_dict(source))
        except (ConfigError, TypeError, ValueError):
            raise DiscoveryError("invalid_read_options", "Invalid SQL source options") from None
        check(source.options["connection_env"] == self.connection_env, "access_denied", "Source belongs to another connection")
        with self._cursor() as handle:
            handle.execute("SELECT name FROM sys.objects WHERE schema_id=SCHEMA_ID(?) AND name=? AND type IN ('U','V') AND is_ms_shipped=0", source.options["schema"], source.options["table"])
            found = handle.fetchone()
        check(found is not None, "dataset_unavailable", "Table/view is unavailable or not visible to this connection")
        return source

    def inspect(self, source):
        columns = super().inspect(source)
        # Optional catalog enrichment must not replace the working driver schema.
        from .sources import identifier
        qualified = identifier(source.options["schema"]) + "." + identifier(source.options["table"])
        with self._cursor() as handle:
            handle.execute("SELECT name, TYPE_NAME(user_type_id), max_length FROM sys.columns WHERE object_id=OBJECT_ID(?) ORDER BY column_id", qualified)
            metadata = {}
            while True:
                batch = handle.fetchmany(100)
                if not batch:
                    break
                metadata.update({row[0]: (row[1], row[2]) for row in batch})
        return tuple(DiscoveredColumn(item.column, *metadata.get(item.column.name, (None, None))) for item in columns)


def create_discovery(connector, root, connection_env=None) -> SourceDiscovery:
    if connector == "sqlserver_query":
        from .query_discovery import SqlServerQueryDiscovery
        return SqlServerQueryDiscovery(connection_env, root)
    if connector == "csv":
        return CsvDiscovery(root)
    if connector == "sqlserver":
        return SqlServerDiscovery(connection_env, root)
    raise DiscoveryError("unsupported_operation", "Supported discovery connectors are csv and sqlserver")


def dispatch(root, operation, body):
    allowed = {"connector", "connection_env", "namespace", "cursor", "limit", "locator", "dataset_key", "options", "source"}
    check(not set(body) - allowed, "invalid_read_options", "Unknown discovery options")
    discovery = create_discovery(body.get("connector"), root, body.get("connection_env"))
    if operation == "capabilities":
        result = discovery.capabilities()
    elif operation in ("namespaces", "datasets"):
        method = discovery.list_namespaces if operation == "namespaces" else discovery.list_datasets
        result = method(body.get("namespace", ()), cursor=body.get("cursor"), limit=body.get("limit", 100))
    elif operation == "resolve":
        result = discovery.resolve_dataset(body.get("locator"))
    elif operation == "configure":
        result = discovery.configure(discovery.resolve_dataset(body.get("dataset_key")), body.get("options", {}))
    elif operation in ("columns", "sample"):
        definition = body.get("source")
        check(isinstance(definition, dict) and "kind" in definition, "invalid_read_options", "Source definition required")
        with _source_errors():
            source = SourceDefinition(definition["kind"], {k: v for k, v in definition.items() if k != "kind"})
        result = discovery.inspect(source) if operation == "columns" else discovery.sample(source, limit=body.get("limit", 20))
    else:
        raise DiscoveryError("unsupported_operation", "Unsupported discovery operation")
    check(_size(result) <= MAX_SAMPLE_BYTES, "sample_too_large", "Discovery response exceeds 1 MiB")
    return to_dict(result)
