# Bounded guided source discovery

Discovery prepares an existing `SourceDefinition`. It does not build mappings,
process records, export, save a pipeline, or create a run.

```
Discovery → Dataset → SourceDefinition
                        ├─ Source Sample: SourceRow, original values only
                        └─ existing pipeline → Processed Preview: RowResult
```

The source adapters, processor, exporters, pipeline versions, database schema and
run lifecycle are unchanged. This feature adds no automatic mappings, inference,
cleanup, joins, arbitrary SQL, row counts or profiling.

## Python contract

Implemented in `etl/discovery.py`:

```python
class SourceDiscovery(Protocol):
    def capabilities(self) -> DiscoveryCapabilities: ...
    def list_namespaces(self, namespace=(), *, cursor=None, limit=100) -> Page: ...
    def list_datasets(self, namespace=(), *, cursor=None, limit=100) -> Page[Dataset]: ...
    def resolve_dataset(self, locator: str) -> Dataset: ...
    def configure(self, dataset: Dataset, options: Mapping[str, str]) -> SourceDefinition: ...
    def inspect(self, source: SourceDefinition) -> tuple[DiscoveredColumn, ...]: ...
    def sample(self, source: SourceDefinition, *, limit=20) -> SourceSample: ...
```

`create_discovery(connector, root, connection_env=None)` returns `CsvDiscovery` or
`SqlServerDiscovery`. This connector contract has no ERP names or rules.

All new models are frozen and recursively freeze nested containers using the
existing model mechanism:

| Model | Contents |
|---|---|
| `Dataset` | Opaque `key`, `connector`, `kind`, namespace tuple, display `name` |
| `DiscoveryCapabilities` | Supported operation names, dataset kinds, available metadata fields |
| `DiscoveredColumn` | Existing `SourceColumn`, optional `declared_type`, optional `max_length_bytes` |
| `Page[T]` | Immutable `items`, optional `next_cursor` |
| `SourceSample` | Existing source definition, columns, original `SourceRow` records, requested limit, stop reason |

Dataset keys and page cursors are scoped to a workspace or connection variable and
operation/namespace. Treat them as opaque handles, not authorization tokens. Each
request checks paths or SQL object visibility again. Keys do not pin file contents,
SQL object identity or a transaction snapshot across requests.

Metadata field availability describes what a connector can report, not a promise
that every column has a known value. `None` means unknown/unavailable. Column tuple
order defines the 1-based ordinal; it is not a guessed target mapping.

## SQL Server

- One configured connection variable, one current database. Connection strings and
  credentials never appear in definitions, discovery responses or error messages.
- Enumerates visible schemas, then user tables/views in a selected schema. Uses
  fixed parameterized catalog SELECTs; no joins or user-supplied SQL.
- Listings use ordered keyset pages, default/max 100 entries. A catalog page may
  fetch 101 metadata entries to determine whether another page exists.
- Resolves selected objects against the appropriate table/view catalog; a missing
  or invisible object yields `dataset_unavailable`.
- Uses the existing source adapter for column description and raw rows. Metadata
  includes Python read type, driver nullability/precision/scale and sizes where
  known. Catalog enrichment adds declared SQL type and maximum storage bytes.
  For `nvarchar`, bytes are not character length. `-1` means SQL `max`, not unknown.
  Catalog type names may be alias/user-defined types. They are not conversion rules.
- Metadata connections request ODBC read-only mode, with login timeout 10s and
  query timeout 15s. Source reads keep the existing adapter behavior (10s login,
  60s query timeout), with an unchanged, quoted `SELECT * FROM [schema].[table]`.
  All statements issued by discovery are SELECTs. A read-only database login is
  still the appropriate deployment permission boundary.
- Row numbers start at 1 for each execution. No ORDER BY is added; repeated samples
  can contain different rows. No database objects or rows are created or modified.

Example:

```python
discovery = SqlServerDiscovery("ETL_SQL_MAIN", workspace)
schemas = discovery.list_namespaces()
datasets = discovery.list_datasets(("dbo",))  # follow next_cursor as needed
dataset = next(d for d in datasets.items if d.name == "BRANDS")
source = discovery.configure(dataset, {})
columns = discovery.inspect(source)
sample = discovery.sample(source, limit=20)
```

The resulting source uses the existing format:

```json
{"kind":"sqlserver","connection_env":"ETL_SQL_MAIN","schema":"dbo","table":"BRANDS"}
```

## CSV

- Browses folders and `.csv` files inside the workspace. Direct relative paths
  are also accepted. Absolute paths, parent traversal and resolved links escaping
  the workspace are rejected. Browsing hides dot names and `__pycache__`.
- Default/max page size is 100. Directory entries are scanned with `os.scandir`;
  only the smallest next 101 names are retained. Listing is not recursive.
- Delimiter and encoding are explicit configuration, never detected from values.
  Defaults remain `;` and `utf-8-sig`. Existing supported encodings are accepted.
  Explicit `utf-8` preserves a BOM in the first header; `utf-8-sig` handles it.
- Existing CSV header checks and structural errors apply. Empty fields are empty
  strings; missing/extra fields fail. Blank records are skipped as before.
- Read type is `str`; CSV does not declare numeric/date types or nullability.
- Quoting, delimiters, multiline text and original whitespace are preserved by
  the existing CSV adapter. Records expose logical data position and physical
  start/end lines where available.

```python
discovery = CsvDiscovery(workspace)
folders = discovery.list_namespaces()
files = discovery.list_datasets(("examples",))
dataset = discovery.resolve_dataset("examples/customers.csv")
source = discovery.configure(dataset, {"delimiter": ";", "encoding": "utf-8-sig"})
columns = discovery.inspect(source)
sample = discovery.sample(source, limit=20)
```

No upload, file modification or encoding/delimiter detection is introduced.

## Source Sample versus Processed Preview

Source Sample needs only a source definition. It uses the adapter directly and
retains native values, including SQL `None`, `Decimal`, dates and integers. It
never calls `process_row`, validation, conversion or exporter formatting.

Processed Preview still needs a valid pipeline with explicit mappings and uses
the existing engine and versioned semantics. Discovery does not choose or upgrade
the pipeline version.

Sampling defaults to 20 records and accepts 1–100. SQL `fetchmany` uses that limit
as its batch size. The reader never peeks at record N+1. `row_limit` means the limit
was reached, not that more records definitely exist; `end_of_source` means EOF
was observed. No total row counts or profiling are computed.

Sample resources close before returning, including early termination and errors.
Samples have a 1 MiB serialized response limit. Oversized results fail with no
partial response or silently truncated cells. This is not a hard driver-buffer
memory limit: an individual value must first be fetched to measure it. Likewise,
the record limit does not cap SQL Server query work; complex existing views can
still be expensive. No query semantics are changed to address that here.

The HTTP boundary reuses exact tagged encodings for Decimal/date/datetime/time,
bytes and UUID. In addition, integers outside JavaScript's safe range
`[-9007199254740991, 9007199254740991]` use
`{"$type":"integer","value":"9223372036854775807"}`. Native Python values remain
unchanged. The UI displays tagged values as text, distinguishes NULL from empty
string, preserves whitespace in `<pre>` cells and escapes HTML.

## HTTP and UI

All routes are POST `/api/discovery/{operation}` with the existing local-host,
origin and `X-ETL-Token` protections. Common request fields are `connector` and,
for SQL Server, `connection_env`. No credential endpoint is added.

| Operation | Additional request fields | Response |
|---|---|---|
| `capabilities` | none | `DiscoveryCapabilities` |
| `namespaces`, `datasets` | `namespace`, optional `cursor`, `limit` | `Page` |
| `resolve` | `locator` (CSV path/key or SQL dataset key) | `Dataset` |
| `configure` | `dataset_key`, `options` | existing flat source JSON |
| `columns` | `source` | ordered `DiscoveredColumn` list |
| `sample` | `source`, optional `limit` | `SourceSample` |

Discovery errors return HTTP 400 with `{code, error}`. Codes include
`unsupported_operation`, `invalid_read_options`, `connection_failed`,
`access_denied`, `timeout`, `dataset_unavailable`, `invalid_headers`,
`malformed_source`, `source_read_failed` and `sample_too_large`. Existing adapter
SQL errors are already sanitized; if the adapter does not expose a specific cause,
discovery reports `source_read_failed` rather than inventing one. Invisible objects
cannot always be distinguished from missing objects. Unknown metadata is shown as
Unknown; actual query failures are surfaced, not disguised as empty results.

UI flow: **Discover a source → browse folder/schema → select dataset → inspect
columns → Read Source Sample → Use this dataset → manually review mappings →
Processed Preview.** CSV also supports **Inspect CSV path** with explicit options.

“Use this dataset” updates only the draft source. It neither creates mappings nor
saves/runs the pipeline. Existing mappings remain visible for manual review. A new
source-only draft cannot run until mappings are added. Changes to source settings
invalidate discovery results; late responses cannot restore stale selections.

The old `/api/columns` and “Read source columns” shortcut are retained for existing
workflow compatibility, including its pre-existing empty-mapping initialization.
The new discovery flow never invokes that shortcut or creates mappings.

## Acceptance evidence

- `tests/test_discovery.py`: immutable models, scoped pages/keys, paths, exact CSV
  options/values/positions, metadata unknowns, structural errors, bounds, cleanup,
  SQL batching, escaped identifiers, sanitized failures, native Decimal/bigint.
- `tests/test_web.py`: complete discovery HTTP flow, protection/error contracts,
  no pipeline/run persistence, existing processed preview behavior.
- `tests/discovery_browser.cjs`: browsing, inspection, sampling, explicit source
  selection, unchanged/empty mappings, processed preview, stale response discard,
  path errors and mobile layout. Run against an isolated temporary app; optionally
  set `ETL_TEST_URL`, `ETL_PLAYWRIGHT` and `ETL_CHROMIUM` for existing installations.
- `integration/sqlserver/verify_discovery_brands.py`: opt-in live acceptance using
  existing authorized `.env` settings. SELECT-only audit rejects `fetchall`, joins,
  count queries and extra data batches. No setup/generator scripts are run.
- `integration/sqlserver/brands_discovery_result.json`: sanitized live result.
  BRANDS exposed 173 columns with declared types; one batch of 20 rows preserved
  500 Decimal values and 20 Unicode values. Every resource closed; zero write
  statements. This is a bounded sample check, not a new full-dataset export check.

No diagnostics viewer, mapping templates or further refactoring is included.
