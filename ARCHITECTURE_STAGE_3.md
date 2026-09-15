# Stage 3 — Source adapters

This stage extracts the existing readers behind a common interface. It does not
change transforms, conversions, validation, exporters, UI, or run lifecycle.
Stage 4 has not started. The eight expected failures for deferred v2 behavior
remain expected failures.

## Interface and usage

`etl.sources.Source` is a structural Python protocol:

```python
class Source(Protocol):
    def read_schema(self) -> tuple[SourceColumn, ...]: ...
    def open(self) -> ContextManager[SourceStream]: ...
```

`SourceStream` holds `schema` and a single-pass `rows` iterator yielding immutable
`SourceRow` values. It is also iterable. Schema and rows belong to the **same
session**: obtaining them together does not execute the SQL query twice.

```python
from pathlib import Path
from etl.sources import create_source

source = create_source(
    {"kind": "csv", "path": "examples/customers.csv", "delimiter": ";"},
    root=Path.cwd(),
)

with source.open() as stream:
    print(stream.schema)
    for row in stream.rows:
        print(row.number, row.line_start, row.line_end, row.values)
```

`create_source()` accepts either a current source dictionary or a Stage-2
`SourceDefinition`. Direct constructors are:

```python
CsvSource(definition, root)
SqlServerSource(definition, batch_size=1000)
```

Each adapter can be reopened. Each `open()` owns a new session and starts row
numbering again. `read_schema()` opens and closes a standalone session; it does
not fetch SQL data rows or parse CSV data records.

## Compatibility wrappers

These existing signatures remain available:

```python
open_source(spec, root, batch_size=1000)
inspect_source(spec, root)
```

`open_source()` still yields `(list_of_header_names, iterator_of_dicts)` inside
a context manager. `inspect_source()` still returns a list of header names.
The wrappers use the adapters and retain the current engine's dictionary shape.
New positions and metadata are exposed through the adapter API, not inserted
into legacy row dictionaries or run reports.

`input_path()`, `identifier()`, and `checked_headers()` remain available.

## CSV behavior and metadata

- Configurable delimiter and encoding retain their existing meanings.
- The default `utf-8-sig` reads UTF-8 files with or without a BOM. Explicit
  `utf-8` retains a BOM as part of the first header, matching existing behavior.
- Header names and their order are preserved, including surrounding whitespace.
- All fields remain strings. Codes such as `003`, empty strings, case, and
  multiline content are preserved exactly as parsed by `csv.reader`.
- No type inference, normalization, trimming, or null conversion is performed.
- Headers report `native_type="str"`. Nullability, sizes, precision, and scale
  are unknown (`None`). Empty field content does not imply schema nullability.
- Duplicate/empty headers and wrong field counts still raise `ConfigError`.
  Bad quoting raises `csv.Error`; encoding failures remain decoding errors.
- Blank CSV records are skipped as before. A quoted empty single field (`""`)
  is a real record, and a whitespace-only record is retained.

### Record and line positions

`SourceRow.number` is a **1-based emitted data-record number**, excluding the
header and skipped blank records. `line_start` and `line_end` are inclusive,
1-based physical line numbers from the CSV parser. They include multiline
headers, quoted multiline fields, embedded empty lines, and skipped blank lines.

For example, a record spanning lines 2–4 can have `number=1`; after a blank line,
the next record can have `number=2, line_start=6, line_end=6`.

Existing structural-error messages keep their previous parser-record numbering
(including the header and skipped blank records). They are not silently changed
to use physical line numbers or emitted record positions.

## SQL Server behavior and metadata

The query remains exactly a quoted `SELECT * FROM [schema].[table]`. There is no
new ORDER BY, TOP clause, join, filter, arbitrary SQL input, or dirty-read hint.
Schema inspection executes that same query and reads `cursor.description`
without calling fetch methods; this does not promise zero work on the server.

Connection behavior stays at a 10-second connection timeout, autocommit enabled,
and a 60-second statement timeout. Rows are fetched lazily using
`fetchmany(batch_size)` (default 1,000). The adapter holds at most the current
batch; it never calls `fetchall()`. Batch size must be a positive integer.

Metadata comes only from `cursor.description`:

| `SourceColumn` field | Meaning |
|---|---|
| `name` | Returned column name |
| `native_type` | Driver-reported **Python value type name**, e.g. `str` or `Decimal`; not SQL DDL |
| `nullable` | Driver-reported boolean, otherwise unknown |
| `display_size` | Nonnegative integer if reported, otherwise unknown |
| `internal_size` | Nonnegative integer if reported, otherwise unknown |
| `precision` | Nonnegative integer if reported, otherwise unknown |
| `scale` | Nonnegative integer if reported, otherwise unknown |

No metadata is guessed from row values. Sparse descriptors remain usable.
See the [pyodbc cursor description documentation](https://github.com/mkleehammer/pyodbc/wiki/Cursor#description)
for the driver descriptor contract.

Values retain their native types, including null, text, bytes, `Decimal`, dates,
datetimes, and time values. The immutable model now also permits `datetime.time`
and `UUID`, so native source values need not be stringified to create a row.
These are representation additions, not conversion rules.

`SourceRow.number` is 1-based and **relative to that execution only**. SQL rows
have no CSV line positions. No stable ordering or database key is implied.

## Resource lifecycle

Consume a stream inside `with source.open()`:

- Normal exhaustion, early break/preview, and unused streams all close on
  context exit. Resources remain owned by the context until it exits.
- The row generator is explicitly closed before closing the file/cursor. A
  retained iterator cannot yield buffered rows after the context has exited.
- Consumer exceptions close resources and propagate to the caller.
- SQL cleanup attempts cursor close and connection close independently. A
  cursor-close failure does not prevent the connection-close attempt.
- Cleanup errors do not replace an already-active processing exception. A
  cleanup failure without an active exception raises a sanitized `ConfigError`.
- SQL connection/query/fetch failures are sanitized to avoid exposing driver
  connection details. Fetch errors are sanitized even if caught inside the
  consumer's open context.

Leaving a generator or context manager unclosed is not a supported lifecycle.
The legacy wrappers also own and close their adapters, so existing engine
preview and exception paths get the same cleanup guarantees.

## Intended v2 semantics remain deferred

The intended contract is unchanged: null and empty text are distinct; transforms
are explicit and ordered; `trim` affects only edges; `empty_to_null` matches
exactly empty text; strict conversion rejects invalid representations; decimal
processing avoids intermediate floats; and validation collects useful errors
unless a dependent step cannot safely execute. Original, transformed and
converted diagnostic values are separate, and null formatting is an exporter
policy. There is no automatic case, date, or identifier normalization.

The source adapters preserve these distinctions at the input boundary. The
current v1 engine still has its characterized empty/null and diagnostic behavior;
this stage does not implement the deferred changes or upgrade pipeline versions.

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_source_contract -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
```

The source tests cover schema, encoding, delimiters, multiline fields, missing
structure versus empty values, record positions, batching, raw native values,
legacy wrappers, and cleanup on success, early stop, source errors, and consumer
errors. Actual engine preview/processing-error paths are exercised as well.
SQL tests use a mock driver; a live SQL Server integration test still requires
connection settings and an accessible database.
