# Processing Diagnostics Viewer

The viewer explains existing processing evidence. It does not perform transforms,
conversions, validation, cleanup or source reads of its own.

## UI flow

**Processed Preview → expand a record → expand a mapped field.** Each row shows
`Valid` or a rejected-field/error count. Each field has separate original,
transformed and converted cells, plus all its recorded errors in their original
order. An error shows its stage, stable code and readable message.

**Run history → View rejection diagnostics** reads the completed run's existing
`rejected.csv`. The viewer uses that run's pipeline snapshot to associate output
fields with source fields or literals. Editor changes and later saved-pipeline
edits cannot reinterpret historical mappings. Pages replace the previous page;
Next rejected rows continues through the file. Reopen the run to start again.

**Source Sample** remains the separate raw `SourceRow` view. It has no processing
diagnostics. No source discovery code was changed for this capability.

## Value representation

`etl/diagnostics.py` produces presentation cells, not new processing models:

```json
{"available":true,"type":"decimal","text":"123.4500","label":"Decimal"}
```

The distinction between a value and an unavailable stage is explicit:

```json
{"available":true,"type":"null","text":"","label":"NULL"}
{"available":false,"label":"Conversion failed — no converted value"}
```

- NULL, empty strings and whitespace-only strings have different labels.
- Text appears in whitespace-preserving cells. An expandable **Escaped text**
  representation exposes spaces, tabs, quotes and exact `\r`/`\n` line endings.
  Browser HTML layout normalizes line endings visually; escaped text preserves
  the distinction. No business string is trimmed or normalized.
- Decimal text is obtained directly from Decimal or its existing stored tag,
  retaining precision and trailing zeros. No float intermediary is used.
- Dates/datetimes use existing ISO representations; no locale conversion or
  inference from string content occurs.
- Every integer is sent to the viewer as explicitly labelled text, avoiding
  JavaScript precision loss. Booleans, floats, bytes/base64 and UUID are labelled.
- All values, names, error messages and codes are escaped before HTML display.

Original field values come from `RowResult.original_values` using the mapping's
source name. A literal comes from the processing/run definition snapshot and is
labelled as such. Transformed and converted values use output field names.

**Unchanged** compares original and transformed representations, including type.
**Transformed value differs** means those stored stages differ. These labels do
not attempt to replay individual transforms or claim that no configured transform
ran when the final value happens to be unchanged.

**Successfully converted** can coexist with **Validation failure**: required or
max-length validation may fail even when conversion succeeds. Conversion failure
never invents a converted value. Transform failure and conversion not completed
are shown separately. Validation categories come from the recorded error stages,
not another validation pass.

## Preview API and compatibility

The UI requests:

```text
POST /api/preview
{"spec": <existing pipeline>, "diagnostics": true}
```

The handler captures the existing engine's `on_row` callback during its single
100-input-row preview execution and adds a `diagnostics` list to the response.
Each entry presents the actual `RowResult`. Both v1 and v2 previews therefore have
full stage evidence, while executing their original version-specific semantics.

Calls without `diagnostics: true` keep the existing response shape. The existing
`sample` field is retained for API compatibility. The new viewer uses the exact
presentation cells, so legacy sample formatting or JSON numeric limits do not
affect its display. A preview is not saved, exported or rerun to populate details.

## Completed-run API

```text
POST /api/diagnostics/rejections
{"run_id":"...", "limit":20, "cursor":null}
```

Response: `run_id`, snapshot `name`, pipeline `version`, `legacy`, `rows`, and
optional `next_cursor`. Existing local-host, origin and request-token protections
apply. The file must belong under the application's runs directory and the run
must be completed. No source adapter, processor or exporter is invoked.

- **V2:** reads the existing four-column rejection CSV, respecting its configured
  delimiter/encoding. JSON cells already hold source values, positions, transformed
  values, converted values and structured errors. Native tags are displayed exactly.
- **V1:** reads the original legacy layout. Original values and mixed mapped values
  are shown, but separated stages, stable codes and error stages are marked
  **Not recorded**. Lost decimal/date type information and NULL/empty distinctions
  cannot be recovered from legacy mixed values. No rules are rerun to guess them.
- Only rejected rows are browsed from completed runs. Full diagnostics for accepted
  historical rows were not persisted; this feature adds no new persistence.

Pages default to 20 and accept 1–100 records, with a 4 MiB row-payload budget.
Signed text-file seek cursors avoid repeatedly scanning earlier pages and are
bound to the run, file size/modification time and application session. A restart
or changed rejection file requires reopening the viewer. Files close on normal
completion, page limits and exceptions. No complete rejection dataset is loaded.

Missing, malformed or oversized diagnostics produce an explicit error. The viewer
uses the existing standard CSV field-size limit; especially large JSON cells may
exceed it. The original rejection download remains available. Cells are never
silently truncated. No global CSV parser setting is changed.

## Tests and scope

- `tests/test_diagnostics.py`: native values, stages, row summaries, multiple errors,
  source/literal associations, positions, no processing during presentation, existing
  v1/v2 files, paging, byte budgets, cursor scope, file errors and precision.
- `tests/test_web.py`: one processing pass per preview row for both versions,
  diagnostics access protection, exact historical values, stored snapshot mapping,
  and historical viewing after deleting the input file with source access blocked.
- `tests/diagnostics_render.cjs`: valid/rejected HTML, stage errors, NULL/empty/
  whitespace/multiline rendering, precise scalar text and HTML escaping.
- `tests/diagnostics_browser.cjs`: expandable preview/history, exact native values,
  v1 limitations, v2 snapshots after editor changes, no source reread and mobile.

Run Python tests with `.venv\Scripts\python.exe -m unittest discover -s tests` and
renderer tests with `node --test tests/diagnostics_render.cjs`. Start the isolated
browser fixture app with `.venv\Scripts\python.exe -m tests.browser_fixture_server`,
then run `node tests/diagnostics_browser.cjs`. The fixture app contains only copied
demo files and synthetic multiline CSV records; it never loads SQL credentials.
Browser tests use the same optional `ETL_TEST_URL`,
`ETL_PLAYWRIGHT`, `ETL_CHROMIUM` settings as discovery acceptance.

Source adapters, engine, transforms, conversions, validations, exporters, run
lifecycle and database schema remain unchanged. No fixes, suggestions, profiling,
mapping templates or further architecture stages are included.
