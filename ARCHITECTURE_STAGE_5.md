# Stage 5 — exporter behavior and output policy

Stage 5 is complete. Source adapters, row-processing semantics, run lifecycle and
database schema are unchanged. No service refactoring has started.

## Files changed

- `etl/exporters.py` — extracted streaming valid/rejected writers, scalar formatting,
  explicit output policies and the legacy presentation helpers.
- `etl/engine.py` — delegates output to the writers; retains public imports of
  `OutputWriter`, `excel_safe` and `legacy_projection` for existing callers.
- `etl/spec.py` — validates the v2 destination options below; v1 shape is unchanged.
- `etl/static/app.js` — preserves advanced destination options when reading the
  existing editor. No new controls, screens or workflow.
- `requirements.txt` — adds `lxml>=5.3,<7` for correct streaming XLSX line endings.
- `tests/test_exporters.py` — 19 focused tests including both version policies.
- `integration/sqlserver/verify_brands_export.py` — repeatable, read-only real-data
  verification with a temporary CSV and an independent formatting oracle.
- `integration/sqlserver/brands_stage5_result.json` — sanitized live verification
  result, without business rows or connection credentials.
- `README.md` and this report — current output-policy documentation.

## Interface and separation

`OutputWriter(directory, names, destination, stack, version=...)` exposes
`write_result(valid_row_result)` and `finish()`. It only consumes successfully
converted values. Passing a rejected RowResult fails explicitly.

`RejectedWriter(..., version=...)` exposes `write_result(rejected_row_result)`.
Passing a valid result fails explicitly. The caller's existing ExitStack owns
file closure, and the existing engine callback finalizes XLSX workbooks.

`OutputWriter.write(mapping)` and default `version=1` remain available for legacy
callers. Exporters never call transforms or conversions. They do not mutate
RowResults, infer types from strings, trim, change case or normalize identifiers.
No result set is accumulated by either exporter.

## Versioned destination policy

Output defaults are selected by the pipeline's explicit version:

| Option | Version 1 | Version 2 |
| --- | --- | --- |
| NULL representation | Empty field (legacy behavior) | `\N` |
| Empty string | Empty field | Empty field, distinct from NULL token |
| CSV encoding | UTF-8-SIG | UTF-8-SIG; configurable to UTF-8 |
| CSV formula policy | Historical apostrophe prefix | Preserve; apostrophe protection is opt-in |
| XLSX typing | Text cells | Text cells |

V1 definitions still only accept destination `kind` and `delimiter`, and their
CSV bytes, rejection shape and XLSX text/null/formula policies remain compatible.
V2 accepts the following additional scalar options:

```json
{
  "kind": "csv",
  "delimiter": ";",
  "encoding": "utf-8-sig",
  "null_value": "\\N",
  "formula_policy": "preserve"
}
```

`encoding` accepts exactly `utf-8` or `utf-8-sig`. `formula_policy` accepts
`preserve` or `apostrophe`. `null_value` accepts any string, including `NULL`,
`\N`, a custom token or explicitly `""`.

**Stage-4 v2 exports previously inherited v1 output defaults. Stage 5 changes
v2 output defaults as shown above.** To reproduce their previous CSV formatting,
explicitly set `null_value: ""`, `formula_policy: "apostrophe"` and
`encoding: "utf-8-sig"`. There is no definition or history migration, and no
change to v1 defaults or either version's processing semantics. The definition
codec preserves explicit policies without inserting omitted defaults.

## NULL, empty text and token collisions

Default v2 output writes None as literal `\N` and empty text as an empty field.
Both CSV and XLSX use the same NULL token policy. A non-null value whose final
formatted representation equals the nonempty NULL marker causes an export
error. This includes numeric collisions (e.g. `null_value: "0"` and integer 0).
Choose a different token; the exporter never silently changes the real value.

Explicit `null_value: ""` opts into losing the distinction between NULL and
empty text. CSV readers must be configured to interpret the chosen NULL token;
quoting alone does not give CSV a native null type. A token containing delimiters,
quotes or newlines is handled by the CSV library like any other field.

In XLSX, an empty string is an empty inline-string cell. Some readers, including
openpyxl, report that as None; SQL NULL is still the distinct nonempty token.
Using the explicit empty NULL policy makes both blank by choice.

## Exact scalar formatting

| Processed value | V2 output text |
| --- | --- |
| String | Unchanged, including leading zeros, whitespace and multiline text |
| Decimal | `str(Decimal)` directly; `123.4500` retains zeros, exponent form is retained when present |
| Integer | Base-10 `str(int)`, including sign; no float step |
| Float | Finite Python float's deterministic `str` representation; no extra rounding |
| Boolean | `true` / `false` |
| Date | `YYYY-MM-DD` via `isoformat()` |
| Datetime | ISO `T` separator; seconds, nonzero microseconds and supplied offset preserved |
| Time | `isoformat()` |
| Bytes | `base64:` followed by standard ASCII base64 |
| UUID | Standard lowercase hyphenated UUID string |

No locale-dependent formatting is used. Non-finite numbers and unsupported
native objects fail explicitly. Bytes/UUID formatting applies when those values
are already present in converted results supplied to the exporter; this stage
adds no processing target types. Diagnostic JSON keeps its existing scalar tags.

## CSV and formula policy

Valid CSV uses Python `csv.writer`, `QUOTE_MINIMAL`, double-quote escaping and
CRLF record terminators. Files open with `newline=""`, so Windows does not add
extra carriage returns. Delimiters, quotes, CR, LF and CRLF within field content
round-trip without normalization under the preserve policy. Header order exactly
matches mapping order. Encoding is explicit, with BOM only for UTF-8-SIG.

`preserve` writes formula-like text exactly as processed. It is appropriate when
exact exchange values are required; it does not claim spreadsheet formula
protection. CSV itself also cannot stop a spreadsheet from guessing numeric types.

`apostrophe` applies the existing protection algorithm **at the CSV boundary**:
prefix a single `'` to string values or headers whose left-stripped form starts
with `=`, `+`, `-`, or `@`, or whose actual first character is TAB, CR or LF.
For example, string `-003` becomes `'-003`, while native integer -3 stays `-3`.
An internal newline is untouched; a leading newline receives the prefix. The
NULL token is also subject to this policy; collision checks compare final output.
These differences are deliberate export formatting, not changes to RowResult.

This is the historical limited mitigation, not a universal spreadsheet safety
guarantee. Spreadsheet behavior and save/reopen cycles differ; proper CSV quoting
alone is not formula protection. See the [OWASP CSV Injection review](https://owasp.org/www-community/attacks/CSV_Injection).
For spreadsheet delivery with preserved literal values, XLSX text cells avoid
creating formulas without adding an apostrophe to business strings.

## XLSX policy and discovered backend issue

All cells are explicitly text, including headers, integers, booleans, dates,
decimals and identifiers. `003` remains text. A string starting with `=` is a
literal text cell, not a formula; CSV's apostrophe option does not modify XLSX.
Decimal values are never stored as Excel floating-point numbers. Dates and
datetimes are the documented ISO text, including offsets if supplied.

Workbooks use openpyxl write-only mode. Sheets repeat the header after 1,048,575
data rows. Strings longer than 32,767 characters fail instead of truncating;
characters not representable in worksheet XML fail rather than being stripped.

The new test exposed a Windows problem in the installed `et_xmlfile` backend:
CRLF text round-tripped as two line feeds. The `lxml` backend preserves CR, LF
and CRLF, as verified by reading the exported workbook. V2 XLSX now requires that
backend, installed through requirements.txt. If unavailable or disabled, export
fails with a clear message before writing the workbook. Restart an already-running
app after installing dependencies so openpyxl selects the backend.

Installing lxml selects that XML backend for openpyxl generally, including v1;
v1 output policy is unchanged, while affected legacy multiline XLSX content also
benefits from corrected XML serialization. Byte-identical ZIP/XML packaging is
not a compatibility guarantee.

## Rejected-row layout

Both versions retain four columns:

`record_number`, `source_json`, `mapped_json`, `errors_json`.

For v2, `source_json` contains original values; `mapped_json` contains separate
`source_position`, `transformed_values` and `converted_values`; `errors_json`
contains field-grouped objects with field, stable code, stage and message.
Successful conversions remain available even when another field fails. No
transformed fallback is inserted into the converted map. Positions preserve CSV
physical lines where available; SQL numbers remain execution-relative.

JSON null/empty remain distinct independently of valid-output `null_value`.
Decimal/date/bytes/UUID diagnostic tags are unchanged. V2 rejection CSV follows
the configured delimiter and encoding. V1 retains its historical semicolon,
UTF-8-SIG sidecar, mixed mapped values and message lists.

## Verification

- Full Python suite: **150 tests passed**, no expected failures.
- 19 new exporter tests cover all requested value types, exact quoting/encoding,
  NULL tokens/collisions, explicit formula behavior, v1 compatibility, structured
  rejections, XLSX backend/errors/rollover and real engine policy delegation.
- Streaming tests write 20,000 CSV rows and 5,000 XLSX rows from generated
  RowResults, count all output records and check peak Python allocations stay
  below 8 MiB in each pass. XLSX write-only mode is asserted.
- JavaScript syntax and preservation of advanced destination settings were
  checked without adding a UI workflow.

### Real read-only BRANDS export

The [sanitized result](integration/sqlserver/brands_stage5_result.json) records:

- **423 input rows → 423 CSV rows**, zero rejected rows.
- **173 mapped columns**, identical ordered CSV headers and field counts.
- **11,175 Decimal cells**, **429 Unicode-containing cells**, **2 multiline cells**,
  **35,642 NULL cells** and **2,515 empty-string cells**.
- Every parsed cell matched its independently calculated representation, checked
  using ordered incremental hashes; no complete result set was retained.
- UTF-8-SIG, semicolon delimiter, explicit `\N` NULL and preserve formula policy.
- 444,338-byte CSV; approximately 5.9 seconds for the verification pass.
- One schema SELECT without fetching rows, followed by the unchanged full source
  SELECT with `fetchmany(1000)` returning `[423, 0]`. `fetchall` was forbidden.
- Both cursors/connections closed. **Zero SQL writes.** Temporary CSV/rejection
  files were removed; no business rows or credentials were retained in the report.

The script uses the existing local `.env` DB_* values without editing it and the
previously approved certificate-trust override. It is an opt-in integration script,
not part of automatic unit-test discovery. The old synthetic fixture/generator
SQL was not run against the ERP database.

## Decisions for review before another stage

Review the chosen v2 defaults (`\N`, preserve formula policy), NULL-token collision
failure behavior, all-text XLSX policy, and its lxml dependency. No additional
decision blocks this implementation. Native typed Excel cells, alternate formula
mitigations and service/run-lifecycle refactoring remain outside Stage 5.
