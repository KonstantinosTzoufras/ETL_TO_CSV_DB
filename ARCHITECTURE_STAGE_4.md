# Stage 4: processing stages and RowResult

Stage 4 is complete. Exporter and run-service restructuring remain deferred.
This document supersedes the earlier stages' statements that version 2 is not
accepted and that the desired-v2 tests are expected failures.

## Files changed

- Added `etl/transforms.py`, `etl/conversions.py`, `etl/validations.py`.
- Updated `etl/engine.py`: shared row orchestration and v1 compatibility projection.
- Updated `etl/spec.py`, `etl/serialization.py`: explicit v2 definitions and typed
  result encoding. Updated `etl/models.py` documentation to describe integration.
- Updated `etl/web.py`, `etl/store.py`, `etl/__main__.py`: JSON encoding at existing
  output/storage boundaries only.
- Updated `etl/static/app.js`: preserve loaded version and display v2 result fields.
- Added `tests/test_processing_stages.py`.
- Updated `tests/test_desired_v2_semantics.py`, `tests/semantics_support.py`,
  `tests/test_models.py`, `tests/test_source_contract.py`, `tests/test_web.py`.
- Added this report, `ARCHITECTURE_STAGE_4.md`.

## Processing order

`engine.process_row(pipeline, source_row, lookups)` is the shared processor for
preview and full execution, for both definition versions:

1. Read the configured source field or literal.
2. Apply transforms in their configured order (`transforms.py`).
3. Check required (`validations.py`).
4. Check max length on the transformed value (`validations.py`).
5. Convert the explicit target type, defaulting to string (`conversions.py`).
6. Check lookup membership if conversion succeeded (`validations.py`).
7. Return an immutable `RowResult`; validity means no field errors.

Required, length and conversion errors accumulate independently. A successful
conversion remains recorded even when required, length or lookup validation
rejects the row. A failed conversion has **no entry** in `converted_values`.
Lookup never executes after conversion failure. Transform failure records the
last successful intermediate value and skips later stages for that field;
those stages would otherwise validate an incomplete transformation. Other
fields continue processing.

`execute` opens the Stage-3 adapter in its resource context and calls the same
processor for every `SourceRow`. Preview only limits input consumption with
`islice`; it does not contain separate field logic. Full-run samples remain
bounded to 20 rows, preview samples to the requested limit. Optional
`on_row(result)` delivers every immutable result synchronously without making
the engine retain the full dataset. Exceptions still unwind the source context.

## Explicit version boundary

Definitions must explicitly contain integer `version: 1` or `version: 2`.
There is no automatic migration or inference from the fields used. Both versions
use the same definition shape and lossless definition codec. Unknown versions
are rejected. Existing saved definitions and historical snapshots retain their
recorded version. New UI definitions remain v1; editing a loaded v2 definition
preserves its version. Opt into v2 by explicitly editing the definition version.

| Behavior | v1 | v2 |
| --- | --- | --- |
| Empty string during conversion | Becomes null | Stays empty for string; rejected for other types |
| Optional null | Remains null | Remains null |
| Text transforms on native non-text values | Convert to text first | Field transform error |
| Decimal input from native float | Existing text conversion retained | Conversion error; precision cannot be recovered |
| Empty lookup reference | Skipped | Retained for string; invalid for numeric/date types unless explicitly made null |
| Public execute sample | Existing string-valued dictionaries and message lists | Native immutable RowResult objects |

`engine.map_row`, `engine.transform`, and `engine.convert` remain v1 compatibility
APIs. `map_row` delegates to the shared processor and projects the result into
the historical mixed-value dictionary and error-message lists. V1 execute
reports, rejection JSON layout/content and export behavior retain that same
projection. New code should use `process_row` to obtain separated stages.

## Conservative v2 values

- No implicit empty-to-null, case normalization, date inference or code cleanup.
- `trim` removes only leading/trailing whitespace; internal whitespace and
  multiline content remain intact. `empty_to_null` changes only exactly `""`.
- Ordered `trim, empty_to_null` changes whitespace-only input to null; reversed
  order produces empty text. No transforms are applied unless configured.
- String mapping preserves `"003"`; integer mapping rejects it. Integers retain
  the existing canonical signed 64-bit grammar. Numbers reject surrounding
  whitespace unless an explicit trim removes it.
- Decimal conversion uses `Decimal` directly for text, integers and Decimal
  inputs, retaining precision and scale. Float inputs are rejected. Use JSON
  **strings** for exact decimal literals; the definition decoder intentionally
  retains the existing JSON number decoding behavior.
- Dates require an explicit date target, exact `YYYY-MM-DD` format and a valid
  calendar date. Datetimes retain the existing ISO date/time grammar. Numeric
  and boolean grammars otherwise remain the existing explicit grammars,
  including scientific notation for decimal/float and case-insensitive boolean
  tokens. A boolean conversion does not normalize source/transformed text.
- Required rejects null, empty and whitespace-only text without changing it.
  Length measures transformed text, without truncation. Optional null bypasses
  lookup membership, preserving the existing null policy.
- Lookup references use the field's configured transforms and target type.
  Invalid reference values remain configuration errors; the existing 100,000
  reference-row cap and SQL query behavior are unchanged.

## Diagnostics and JSON boundaries

RowResult holds the original `SourceRow` (including unmapped source columns),
`transformed_values`, `converted_values`, and ordered `FieldError` objects.
Source record numbers and CSV physical line ranges survive processing. SQL row
numbers remain execution-relative and imply no stable ordering. Literal values
are available in the immutable pipeline snapshot and the transformed mapping;
they are not invented as source columns.

Stable error codes/stages:

| Code | Stage |
| --- | --- |
| invalid_transform_input | transform |
| required | required |
| max_length_exceeded | max_length |
| invalid_type | conversion |
| lookup_missing | lookup |

Each error also carries its target field and readable message. Processing never
serializes diagnostics. `row_result_to_dict` creates a boundary dictionary while
retaining native scalars. `json_default` then encodes RowResults and uses
`{"$type":"decimal","value":"123.4500"}` tags for exact decimals. Dates,
datetimes, times, UUIDs and bytes have analogous tags (bytes use base64).
Null and empty text remain JSON null and `""`. Stored/HTTP reports are JSON
trees; they are not automatically rehydrated into domain objects.

Only JSON encoding calls in HTTP, CLI and history storage changed; no service
lifecycle or database schema changed. The existing preview table can read the
v2 boundary fields and messages; there is no new UI workflow.

The rejection CSV retains its four existing columns. For v2, `source_json`
contains original values; `mapped_json` contains `source_position`,
`transformed_values`, and `converted_values`; `errors_json` groups structured
errors by field. V1 rejection content is unchanged. OutputWriter and its valid
CSV/XLSX formatting are unchanged. Null representation in exports remains an
export policy, not a processor decision.

## Tests and review decisions

Verification: `.\.venv\Scripts\python.exe -m unittest discover -q` passed
all **128 tests**, with zero failures and zero expected failures.
`node --check etl/static/app.js` also passed. SQL behavior remains verified with
the existing mocked source-contract tests, not a live SQL Server.

All eight former expected failures now exercise explicit v2 processing and
pass: empty-string preservation, trim alone, reverse transform order, empty
int/date/decimal rejection, null-vs-empty preview and separated diagnostics.
No desired-v2 expected failures remain. Legacy characterization still exercises
v1, including its empty-to-null and mixed-diagnostic behavior.

`test_processing_stages.py` adds native stage separation, immutable diagnostics,
multiple errors, lookup dependencies, literal mappings, transform order,
precision, invalid conversions, multiline positions, real rejection output,
lookup semantics, bounded preview/full equivalence and versioned history tests.
HTTP tests cover v2 preview and background run JSON encoding. Existing source
cleanup tests now inject exceptions at the shared processor boundary.

Decisions to review before another stage: rejecting native float-to-decimal and
non-text text-transform inputs, optional null bypassing lookups, and the tagged
JSON scalar representation. None requires a v1 migration. Exporter and service
refactoring have not started.
