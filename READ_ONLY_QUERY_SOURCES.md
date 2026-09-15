# Read-only SQL Query Sources

Status: implemented and locally verified with mocked SQL connections. Live ERP
query-source verification is **deferred**: the existing login has not been
independently confirmed SELECT-only. No query connection has been approved by
this implementation, and no SQL Server database writes were performed.

## Flow and compatibility

`SourceDefinition(kind="sqlserver_query")` embeds an immutable `QueryDefinition`
with a tuple of immutable `QueryParameter` values. The source yields the existing
`SourceRow` model, then the existing processor and exporter handle the results.
Query-derived expressions are original source values from the processor's view.
Table/view and CSV adapter behavior is unchanged. The only engine integration is
a query-specific preview batch size so a 100-row preview does not fetch 1000 rows.

Query format version 1 is independent of pipeline processing version 1 or 2.
There is no version upgrade, named-query registry, query orchestration, or new
processing/export policy. Mapping and template binding remain explicit. Query sources cannot be used as
lookup dependencies; existing CSV/table/view lookup rules remain supported.

## Definition

```json
{
  "kind": "sqlserver_query",
  "connection_env": "ETL_SQL_REPORTING",
  "query": {
    "format_version": 1,
    "dialect": "tsql",
    "sql": "SELECT c.Code AS CustomerCode, r.Name AS RegionName, c.CreditLimit-c.Balance AS AvailableCredit FROM dbo.Customers c LEFT JOIN dbo.Regions r ON r.Id=c.RegionId WHERE c.CompanyId=? AND c.CreatedAt>=? ORDER BY c.Id;",
    "parameters": [
      {"name": "company", "type": "int", "value": 7},
      {"name": "since", "type": "datetime", "value": "2026-01-01T00:00:00"}
    ],
    "timeout_seconds": 60
  }
}
```

Every query and parameter key is required; unknown keys fail validation. SQL is
limited to 32768 characters, 4096 AST nodes, and 256 positional parameters.

Parameter names are display labels; positional order binds each `?`. Repeated
markers require repeated entries (labels may repeat). Values are passed separately
to `cursor.execute(sql, values)`. SQL text is never interpolated or transpiled.

- `string`: exact Unicode/whitespace/empty text, maximum 32768 characters.
- `int`: signed SQL bigint; canonical integer text or safe JSON integer. Integers
  outside JavaScript's safe range must be strings. Leading-zero integer text fails.
- `decimal`: plain decimal string, at most 38 digits of precision/scale; decoded
  directly to Decimal and explicitly bound with precision/scale. No float path.
- `bool`: JSON true/false.
- `date`: strict YYYY-MM-DD.
- `datetime`: timezone-naive ISO text with seconds and at most six fractional digits.
- Every type accepts explicit JSON null; `""` is a separate string value.

The model retains the validated JSON scalar spelling for lossless snapshots;
ODBC binding decodes it into a native scalar. Input-size/type hints include typed
NULLs. Strings bind as Unicode, sized in UTF-16 units, with long text binding above
4000 units. Real-driver acceptance of these bindings is still pending.

## Deployment approval (disabled by default)

The application does not discover credentials from `.env`, grant permissions,
create logins, or assume that an existing connection is read-only.

An administrator must first approve a dedicated restricted SQL principal and
review the allowed tables/views (including dependencies behind views or synonyms).
Then supply the connection string through the existing ETL_SQL_* environment
reference and configure **process environment** `ETL_QUERY_CONNECTIONS` as JSON:

```json
{
  "ETL_SQL_REPORTING": {
    "read_only": true,
    "objects": [["dbo", "Customers"], ["dbo", "Regions"]],
    "max_timeout_seconds": 120
  }
}
```

This example is documentation, not an installed approval. `read_only: true` is an
administrator attestation, not automatic permission proof. The application does
not test safety by attempting writes. Object names match policy spelling exactly,
which is conservative for case-sensitive databases. Credentials and approvals
cannot be supplied by the pipeline or HTTP request. Missing/malformed approval
fails closed before opening a connection. Revoking approval blocks later execution
of already-created adapters and saved snapshots.

Actual database permissions remain the security boundary. ODBC `readonly=True`
is only a hint; it does not guarantee that writes are prevented. The driver is
explicitly not required to enforce this attribute:
[Microsoft ODBC reference](https://learn.microsoft.com/en-us/sql/odbc/reference/syntax/sqlsetconnectattr-function?view=sql-server-ver17).

## Supported SQL and parser policy

SQLGlot is pinned to **30.18.0**, using its T-SQL tokenizer/parser. Validation
requires one SELECT AST and an allowlist of both expression classes and populated
arguments. Parser errors, fallback commands, and unsupported syntax fail closed.
Parser warnings containing SQL are suppressed by rejecting their fallback paths.
SQLGlot is not a SQL Server permission checker or a complete semantic verifier:
[SQLGlot parser documentation](https://sqlglot.com/sqlglot/parser.html).

Supported: schema-qualified local table/view references; column expressions and
aliases; INNER/LEFT/RIGHT/FULL/CROSS joins; derived tables and SELECT subqueries;
WHERE/GROUP BY/HAVING/ORDER BY; DISTINCT/TOP; arithmetic, comparisons, boolean
operators, CASE, IN/BETWEEN/LIKE/EXISTS; COALESCE/NULLIF, UPPER/LOWER/LEN,
ABS/ROUND, SUM/AVG/MIN/MAX/COUNT; scalar CAST to the supported built-in types.
Scalar CAST types: int, bigint, decimal, float, bit, date, datetime/datetime2,
text/varchar/nvarchar (as represented by the pinned parser).

Rejected: all writes/DDL/EXEC; SELECT INTO; multiple statements/batches; variables;
sequence advancement; temp objects; linked/cross-database/ad hoc/external access;
user-defined functions; table/query/join hints; CTEs; set operations; window
functions; APPLY; XML/JSON result modes; any expression/options outside the
allowlist. A token-level guard supplements, rather than replaces, AST validation.
One trailing semicolon and ordinary comments are supported. Punctuation and
keywords inside strings or quoted identifiers do not split statements.

Syntax accepted by this subset may still be rejected by SQL Server for object,
type, or dialect reasons. No automatic SQL correction occurs. Administrator review
must account for hidden view/synonym dependencies. This is an advanced local-user
feature, not a sandbox for hostile SQL. Expensive reads can still block or load
SQL Server even though they do not modify business data.

## Metadata, samples and resource lifetime

`SqlServerQuerySource.read_schema()` and `.open()` implement the existing Source
contract. Column metadata comes only from cursor.description: names, Python read
type, nullable flag, sizes, precision and scale where available. Unknown remains
None. No exact SQL declared-type or lineage inference. Empty/duplicate output
names (including case-only duplicates) require explicit unique aliases.

Inspection executes the unchanged bound SELECT, reads description, and fetches
no application rows. It is not a zero-work database operation; the driver/server
may evaluate or prefetch data. No metadata stored procedures or TOP-0 wrappers.

Source Sample defaults to 20, maximum 100, and 1 MiB response size. Its single
fetchmany batch is limited to the requested number; no N+1 probe or row count.
Source Sample remains raw. Processed Preview uses the real processor with a
query-specific bounded batch; full execution fetches 1000 rows at a time. Never
fetchall. Query results are not retained in full by the adapter or exporter.

Rows are numbered from 1 for each execution. SQL positions do not imply stable
ordering. Separate inspections/samples/previews/runs are separate executions;
snapshots do not freeze source data. Use a deterministic ORDER BY when needed.

Connection timeout is 10 seconds. Inspection/sample statement timeout is capped
at 15 seconds. Processed Preview/full execution use the saved timeout (default UI
60, valid range 1–300), also limited by deployment approval. Statement timeouts
are driver-dependent and not a total run deadline. No automatic retry. Context
exit closes iterator, cursor and connection after early stop, exhaustion, source
failure, or a consumer/processor exception; both resource closes are attempted.
The existing run failure/export-artifact lifecycle is unchanged.

## UI and API

Choose **SQL Query (advanced)**, refresh approved references, explicitly select a
reference, enter SELECT text and an ordered typed-parameter JSON list. Validate
locally, inspect columns, read Source Sample, then Use this dataset. Bind fields
manually or apply a target template. Processed Preview/diagnostics/export remain
existing actions. Changing query inputs invalidates inspection/sample. Untouched imported SQL keeps
its exact CRLF/LF spelling even though browser textareas normalize line endings. The legacy
Read source columns shortcut does not auto-create mappings for query sources.

POST endpoints (existing local origin/token protections apply):

- `/api/query/connections`: approved reference names only; no credentials/policy payload.
- `/api/query/validate`: `{query: ...}`; local syntax/parameter checks, no connection.
- Existing `/api/discovery/{capabilities,resolve,configure,columns,sample}` with
  `connector: "sqlserver_query"` and `connection_env`. Dataset key is
  `sqlserver_query:<connection reference>`; configure options contain only query.
- Existing `/api/columns`, `/api/validate`, `/api/pipelines`, `/api/preview`,
  `/api/runs` support the new embedded source.

Query discovery has no namespace/dataset listing or query library. Its authored
Dataset exposes configure/inspect/sample capabilities through SourceDiscovery.
Errors carry stable query/parameter codes; messages never include driver text,
connection strings or parameter values. Routine logs do not log query text or
parameters. Parameters and SQL are intentionally visible in authorized pipeline
and snapshot views and may themselves contain sensitive business data.

## History

Existing JSON pipeline/run storage already captures the entire source. No schema
migration or run-service changes. Snapshots include exact SQL, ordered typed
parameter values, dialect/version, timeout and connection reference. No resolved
credentials. Later pipeline edits cannot alter historical snapshots. Execution
revalidates both syntax policy and current deployment approval. Historical
inspection/diagnostics never rerun the query. Query snapshots reproduce requests,
not historical database contents or the definitions of referenced views.

## Verification

- Unit/source/API tests: `python -m unittest tests.test_queries tests.test_query_web`.
- Full regression: `python -m unittest discover -s tests`.
- Query browser fixture: `python -m tests.query_browser_fixture_server`, then
  `node tests/query_browser.cjs` with the project's Playwright/Chromium environment.
  This server replaces pyodbc.connect for its entire lifetime; no SQL database is used.
- Existing CSV/table/view, template, discovery and diagnostics tests remain required.
- Local tests cover forbidden statements/nested constructs, binding injection text,
  exact values, Unicode, NULL, empty, metadata, stream cleanup, 10001 rows, preview
  consistency, approval revocation, snapshots and explicit template binding.
- Live query-source verification is deferred by the user's explicit instruction.
  It must use a subsequently confirmed administrator-approved read-only account.
  Never run destructive safety tests against the ERP. Real driver precision/typed
  NULL behavior and timeout enforcement remain acceptance limitations until then.

### Verification result for this implementation

- 235 Python tests passed (209 existing + 26 new query/source/API tests).
- Five browser suites passed: query source, discovery, templates, diagnostics, legacy smoke.
- Seven scalar/rendering tests passed: six existing diagnostics tests and one query
  editor snapshot-fidelity test (`node tests/query_render.cjs`).
- Dependency consistency (`pip check`) and tracked whitespace checks passed.
- Deployment approval count: zero. Live SQL query-source verification: not run,
  explicitly deferred until the user confirms an administrator-approved account.

Files touched for this feature: `etl/models.py`, `etl/queries.py`,
`etl/query_source.py`, `etl/query_discovery.py`, `etl/spec.py`,
`etl/serialization.py`, `etl/sources.py`, `etl/engine.py`, `etl/discovery.py`,
`etl/web.py`, `etl/static/app.js`, `etl/static/index.html`, `etl/static/query.js`,
`tests/test_queries.py`, `tests/test_query_web.py`, `tests/query_browser.cjs`,
`tests/query_browser_fixture_server.py`, `tests/query_render.cjs`,
`requirements.txt`, `README.md`, and this note.
