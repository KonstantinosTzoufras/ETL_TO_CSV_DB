# Architecture refactor — Stages 1 and 2

## Scope

This change adds characterization tests, an explicit desired-v2 test contract,
immutable domain models, and version-1 JSON conversion. It does not enable v2
processing or modify the current engine, sources, exporters, UI, CLI, or store.
This document records the Stage 1–2 checkpoint. The subsequent source-adapter
work is documented in [Stage 3](ARCHITECTURE_STAGE_3.md).

## Stage 1: behavioral baseline

`tests/test_legacy_semantics.py` characterizes the current engine. In particular,
it intentionally records implicit empty-string-to-null conversion, the mixed
stages in `mapped_json`, and the loss of null/empty distinctions in preview.

`tests/test_desired_v2_semantics.py` documents the conservative ERP requirements:

- `DesiredV2AlreadySatisfiedTests`: requirements the current processor satisfies.
- `DesiredV2PendingTests`: eight `unittest.expectedFailure` cases for deferred
  behavior changes. These are executable assertions against the current engine,
  not a placeholder v2 implementation. An unexpected success fails the suite
  and requires reviewing the corresponding expectation.

The pending cases cover preserving empty strings, trim-only output, reverse
transform order, rejecting empty integer/date/decimal inputs, preserving null
in preview, and retaining original/transformed rejection diagnostics.

The legacy preview/full-run test compares actual per-row processing results for
limits 1, 7, and 100 against a 137-row full execution containing rejected rows.
It does not assume the full run's bounded 20-row sample contains all 100 rows.

## Stage 2: domain models

All models live in `etl/models.py`:

| Model | Purpose |
|---|---|
| `Pipeline` | Version, name, source, ordered columns, destination settings |
| `SourceDefinition` | Source kind and immutable source-specific options |
| `FieldMapping` | Source or literal, target type, ordered transforms, validation rules |
| `Transform` | Configured transform name; no transformation implementation |
| `ValidationRule` | Configured rule name and immutable parameters; no validation implementation |
| `SourceColumn` | Name, optional native type and nullability |
| `SourceRow` | Record number, original values, optional CSV line range |
| `FieldError` | Field, stable code, message, processing stage |
| `RowResult` | Source row, separate transformed and converted values, errors |
| `PipelineRun` | Run identity, captured pipeline definition, status and timestamps |
| `RunResult` | Counts, bounded sample, preview flag and output directory |

`SourceDefinition` is configuration, not a source adapter. Required/max-length
and lookup configuration are represented as `ValidationRule` instances. Target
type remains an explicit `FieldMapping.target_type`; its validity is still
checked by the existing converter. No second rule engine has been introduced.

Frozen, slotted dataclasses reject ordinary attribute assignment. Their shared
constructor hook copies mappings recursively into read-only mappings and lists
into tuples. This also applies to direct Python construction, not just parsed
JSON. Caller-owned containers and serializer output cannot mutate the models.
Unsupported objects are rejected rather than retained as mutable values.

Native row values such as `None`, strings, `Decimal`, dates and datetimes remain
native values. `RowResult.valid` is derived from the error collection. Failed
conversions can be represented by an error and absence from `converted_values`,
while the original and transformed values remain available separately.

The new result models are not wired into `execute()` yet. Existing run samples
cannot be upgraded losslessly because their missing processing stages were never
stored; this change does not invent those stages or rewrite historical reports.

## JSON conversion and public API compatibility

`etl/serialization.py` adds:

```python
pipeline_from_dict(definition) -> Pipeline
pipeline_to_dict(pipeline) -> dict
pipeline_from_json(payload) -> Pipeline
pipeline_to_json(pipeline, *, indent=None) -> str
```

Example using the unchanged public engine:

```python
from pathlib import Path
from etl.engine import execute
from etl.serialization import pipeline_from_json, pipeline_to_dict

pipeline = pipeline_from_json(Path("examples/customers.json").read_text(encoding="utf-8"))
report = execute(pipeline_to_dict(pipeline), Path.cwd(), limit=100)
```

Both conversion directions reuse the existing `spec.validate()` contract.
Version 2 is still rejected. `execute()`, `validate()`, `Store`, HTTP responses,
and the saved-pipeline/run schemas keep their existing signatures and formats.
Callers opt into the model layer explicitly and convert back at the boundary.

### Round-trip guarantees

For valid version-1 definitions, conversion preserves the decoded JSON data:

- Source options, destination options, field names and values.
- Omitted options versus explicitly supplied defaults.
- `required: false`, omitted `required`, omitted transforms and `transforms: []`.
- `literal: null`, empty string, booleans, integer and floating-point literals.
- Field order and transform order, including repeated transforms.
- CSV and SQL Server configurations, including lookup sources.
- Unicode, multiline text, and exact decimal strings.

`UNSET` distinguishes an omitted literal from a literal null. Optional target
type and transforms use `None` to represent an omitted key; v1 does not accept
explicit JSON null for either option. Explicit `required: false` is retained as
a rule configuration with a false value. No defaults are injected on writing.

Each serialization creates fresh dictionaries/lists. Round-tripping is
structural, not byte-for-byte: JSON indentation, escaping style and object-key
ordering are not preserved. Unknown configuration and rules that cannot be
represented in v1 raise errors instead of disappearing during serialization.

## Compatibility verification

`tests/test_model_compatibility.py` creates preexisting-format SQLite rows in a
temporary database, reopens them, and verifies unchanged schemas and historical
JSON/report values. It also verifies that later saved-pipeline edits cannot
change a run's snapshot and that round-tripped definitions execute identically.
The application's existing local database is not migrated or rewritten.

## Behavior ambiguities retained for later review

1. **Null versus empty:** v1 still turns `""` into `None` for every target type.
   Consequently `trim` alone and both transform orders can collapse to null.
2. **Required does not normalize:** whitespace-only values fail required checks,
   but remain whitespace unless a transform explicitly changes them.
3. **Validation continues after failure:** max-length/required failures do not
   prevent conversion. A rejected field can contain a converted value; multiple
   errors accumulate in the current order.
4. **Diagnostics:** rejection CSV includes original values, but `mapped_json`
   mixes converted values and failed conversions' transformed values. Preview
   stringifies values and omits originals. New result models do not fix this yet.
5. **Row positions:** current numbers identify emitted input records, not CSV
   physical lines. The model has optional line positions for a later adapter
   change; the current engine does not populate them.
6. **Decimal input:** decimal text and native `Decimal` retain precision and
   scale during conversion. V1 JSON numeric literals still use Python's normal
   float decoding. Precision already lost before conversion cannot be recovered.
   Use a JSON string for an exact decimal literal. No decoding policy is changed.
7. **Export representation:** legacy CSV formula escaping and XLSX text cells
   remain unchanged. Null/empty export distinctions require a later explicit
   output policy.

## Running the tests

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_legacy_semantics -v
.\.venv\Scripts\python.exe -m unittest tests.test_desired_v2_semantics -v
.\.venv\Scripts\python.exe -m unittest tests.test_models tests.test_model_compatibility -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The expected failures identify deferred work; they must not be reported as
passing v2 functionality. Source, exporter, and service extraction remain future
stages requiring review before implementation.
