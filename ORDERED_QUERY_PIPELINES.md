# Ordered Query Export Pipelines

A bounded, separate pipeline kind for 1-20 independent SELECT query exports on
one approved SQL Server connection reference. No source, transform, conversion,
validation, or exporter semantics were changed for this capability.

## Models and codec

`etl.models.OrderedQueryPipeline` contains an immutable ordered tuple of
`QueryExportStep` values. Steps reuse immutable QueryDefinition, FieldMapping,
and destination mappings. No nested mutable lists/dicts remain exposed.
`etl.ordered.from_dict` / `to_dict` validate and detach JSON definitions. The
optional failure policy defaults to `stop`; canonical encoding makes it explicit.

```json
{
  "kind": "ordered_query_export",
  "format_version": 1,
  "name": "Daily extracts",
  "connection_env": "ETL_SQL_REPORTING",
  "failure_policy": "stop",
  "steps": [
    {
      "id": "customers",
      "name": "Customers",
      "processing_version": 2,
      "query": {
        "format_version": 1,
        "dialect": "tsql",
        "sql": "SELECT Code, Name FROM dbo.Customers WHERE Active = ?",
        "parameters": [{"name": "active", "type": "bool", "value": true}],
        "timeout_seconds": 60
      },
      "columns": [
        {"name": "CustomerCode", "source": "Code", "type": "string"},
        {"name": "Name", "source": "Name", "type": "string"}
      ],
      "destination": {"kind": "csv"}
    }
  ]
}
```

Add further step entries for orders/balances. Array position is the only order.
IDs are unique lowercase ASCII identifiers starting with a letter, at most 48
characters, allowing digits, underscores and hyphens. Windows reserved filenames
are rejected. Display names are independent of file paths.

Each step has an explicit processing version (1 or 2). Ordered format version 1
is not a new processing version. Templates apply to an empty selected step using
the existing explicit binding workflow, with no runtime template dependency.

## Execution and security

`etl.ordered_runs.coordinate` validates all definitions and preflights all
approvals/static lookup paths before opening any SQL source. The web submit path
also performs this preflight before creating a queued run and takes its own
canonical detached snapshot. A queued definition cannot follow later caller edits.

For each step, the coordinator rechecks the current safety/connection policy,
materializes a regular single `Pipeline`, and calls the existing `execute` once.
The query adapter independently revalidates before connecting. Parameters remain
separately bound. Each step closes its source before the next step starts. The
ODBC driver may pool physical connections, but each step opens/closes its own
logical connection. There is no shared transaction or connection session.

One connection reference applies throughout the ordered definition. SQL lookups
must use the same reference and an approved table/view object. Static workspace
CSV lookups remain supported, but files resolving under the run output directory
are prohibited. Query lookups and step-result references are not supported. There
is no variable substitution or code that sends results into another query.

Query safety and administrator approval use the existing query-source policy.
Neither a SELECT-only test nor an ODBC read-only hint proves login permissions.
No approval or credential was installed by this feature. Live verification remains
explicitly deferred until an administrator-approved read-only account is confirmed.

## Failure policy and counts

- `stop` (default): first step failure stops the run and marks later steps skipped.
- `continue`: independent later steps are attempted; final status is
  `completed_with_errors` if any step failed.
- Rejected rows are ordinary completed processing outcomes, not failed steps.
- Security-policy failures/revocation, storage failures, persistence failures and
  unexpected coordinator errors stop execution regardless of continue policy.
- No retries, resume, result dependencies, Python joins, DAG or step parallelism.

Step statuses: pending, running, completed, failed, skipped, interrupted.
Overall statuses: queued, running, completed, completed_with_errors, failed,
interrupted. UTC start/end timestamps are stored. The existing run `started`
field records queue creation; the ordered report also records execution start.

Each step records processed/valid/invalid counts, status, times, output paths and
an execution error where applicable. Progress is checkpointed every 1000 processed
rows and at step transitions. Failed-step counts describe RowResults observed
before failure; they do not assert how many rows were durably exported. Interrupted
counts are the last persisted checkpoint. Rejection counts retain the existing
`invalid` JSON key. Completed counts come from the existing engine's final report.

Coordinator reports contain step summaries, not complete row datasets. Source
reads and CSV/XLSX exports retain existing streaming behavior. There is no combined
preview; preview executes only the explicitly selected step. Other unfinished
steps do not prevent that selected-step preview, but must validate before a run.

## Output publication

```text
runs/<run-id>/
  001-customers/
    customers.csv
    rejected.csv
    report.json
  002-orders/
    orders.csv
    rejected.csv
    report.json
  .partial/
    003-balances/<engine-generated-id>/...
```

The coordinator passes a private `.partial/<position>-<step-id>` working root to
the unchanged engine. Once the engine returns and resources are closed, it renames
the valid file to `<step-id>.<extension>`, writes a counts/step-snapshot report,
and renames the entire completed directory to its generated final path.

No user-selected output directory/filename is accepted. Paths must remain under
the generated run directory. Existing run directories cannot be overwritten or
resumed. Empty private staging directories may remain after successful publication.
Failed files remain private partial artifacts and never receive success download
links. Completed earlier steps remain downloadable even when the overall run is
failed, interrupted or completed_with_errors.

Filesystem publication and SQLite checkpointing are not one atomic transaction.
A crash between publication and checkpointing can leave a complete-looking file
without a durable completed status; it is deliberately not promoted or offered as
a successful download. There is no automated recovery/resume of such artifacts.

## History and interruption

The existing SQLite schema is unchanged. The run spec contains every step and its
SQL, typed parameters, mappings and destination. Ordered summaries occupy the
existing JSON report column. Editing/deleting a saved definition does not mutate
run snapshots. Connection credentials are never copied into snapshots.

The history UI shows ordered step status, times, counts, errors and individual
links. `/api/diagnostics/rejections` accepts `step_id` for an ordered run and
projects that stored step into the existing diagnostics reader. Composite run/step
identity prevents reuse of a pagination cursor for another step. Query validation,
connection approval, and source adapters are not invoked when reading history.

Completed-step diagnostics retain existing v1/v2 behavior. Failed/interrupted
steps can expose clearly labelled partial rejection diagnostics if a readable
rejection file exists. A failure before an artifact is created has an execution
error but no row diagnostics; the viewer reports those diagnostics unavailable.
No partial valid output is downloadable as a success.

After restart, completed steps remain completed, active steps become interrupted,
and pending steps become skipped. No source is reopened. CLI Ctrl+C records an
interrupted ordered run where storage is available. Persistence failures stop the
coordinator immediately; if storage remains unavailable, the last durable state
can only be marked interrupted when the application next starts successfully.

## UI/API/CLI

Choose **New ordered query exports**. Set the shared approved connection and
failure policy; add/reorder steps with explicit buttons. The existing editor
below applies to the selected step, including source sample, explicit mappings,
template binding, validation, destination and processed preview. The source kind
and connection cannot be overridden per step. Save/Run apply to the whole ordered
pipeline. Advanced JSON represents the entire ordered definition.

Existing `/api/pipelines`, `/api/validate`, `/api/runs`, and `/api/runs` history
accept the new discriminator. Existing single-source requests stay compatible.
New/extended paths:

- POST `/api/ordered/preview`: `{spec, step_id, diagnostics}`; exactly one step.
- POST `/api/diagnostics/rejections`: `{run_id, step_id, cursor, limit}` for ordered runs.
- GET `/download/<run-id>/<step-id>/<generated-filename>`: completed step only,
  independently of overall run status. Allowed files: generated valid export,
  `rejected.csv`, `report.json`.
- GET `/ordered.js`: bounded editor/history UI.

```powershell
.venv\Scripts\python.exe -m etl preview ordered.json --step customers
.venv\Scripts\python.exe -m etl run ordered.json
```

CLI success is exit 0; failed/completed_with_errors ordered runs return 1, and an
interrupted ordered CLI run returns 130. A preview creates no run or export files.

## Compatibility and limits

Single-source definitions retain their previous JSON shape, codec, engine,
exports, download routes and historical diagnostics. Dispatch uses the explicit
`kind: ordered_query_export` discriminator. No migration of saved definitions and
no database schema migration. The existing application can still handle separate
run submissions; steps within an ordered run always execute sequentially.

Limits: 20 steps, one approved reference, static independent parameters, existing
lookup bounds, existing query timeouts. A multi-step run has no overall deadline
or point-in-time database consistency guarantee. Source data may change between
steps; earlier files cannot be rolled back by a later failure. All driver-specific
limitations documented in READ_ONLY_QUERY_SOURCES.md remain applicable.

## Acceptance coverage

`tests/test_ordered.py`: immutable definitions, format/default guards, all-step
preflight, fresh sequential connections, publication/naming, stop/continue,
rejections, policy revocation, state failures, lookup restrictions, selected
preview, history/cursor separation, real interrupted-source cleanup/restart,
no overwrite/resume, mixed destinations, independent parameters, CLI, and two
10001-row streamed steps with bounded reports.

`tests/test_ordered_web.py`: saved snapshots, detached queue inputs, preflight
before queue, partial download blocking, completed downloads after failure,
selected preview, continue status, rejection pagination and no history source reads.

`tests/ordered_browser.cjs`: shared connection, explicit mappings, reordering,
selected preview, stop/continue, downloads, diagnostics paging, snapshot reload,
return to single-source mode and mobile layout. Uses the existing isolated
`tests.query_browser_fixture_server` with a mocked pyodbc connection.

Files changed for this feature: etl/models.py, etl/ordered.py, etl/ordered_runs.py,
etl/store.py, etl/web.py, etl/__main__.py, etl/static/app.js,
etl/static/index.html, etl/static/style.css, etl/static/ordered.js,
tests/test_ordered.py, tests/test_ordered_web.py, tests/ordered_browser.cjs,
README.md and this document.

## Verification result

- 258 Python tests passed: 235 existing plus 23 ordered coordinator/API/CLI tests.
- Six browser suites passed: ordered, query, discovery, templates, diagnostics, legacy.
- Seven existing scalar/rendering tests passed; whitespace checks passed.
- Approved live query connections: zero. Live SQL Server verification was not run.
- No source, processing-stage, exporter, or SQL Server schema changes were made for this feature.
