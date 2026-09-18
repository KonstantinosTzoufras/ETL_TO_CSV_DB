# Reusable mapping templates — copy on apply

Plain-language companion: [TEMPLATES_EXPLAINED_EL.md](TEMPLATES_EXPLAINED_EL.md).

Templates describe target fields and existing processing rules. Applying a revision
copies them into an unbound draft. Explicit binding generates a normal pipeline;
the engine, exporters and run history never resolve a template reference.

## Models and strict schema

`MappingTemplate` contains a generated opaque ID, name, immutable revision number,
format version (1), processing version (1 or 2), and an ordered tuple of
`TemplateField` values. Fields contain output name, target type, ordered existing
`Transform` values, required, optional max length and a lookup-required flag.

Frozen models recursively freeze nested collections. JSON validation rejects
unknown keys, duplicate target names, unsupported types/transforms, invalid flags,
versions and length limits. Templates hold 1 field up to the pipeline column limit (`MAX_OUTPUT_COLUMNS`);
each revision is at most 1 MB. Processing version defaults to 2 for a new blueprint.

Source definitions, source column bindings, literals, lookup source definitions,
connection settings and credentials are not template properties and are rejected.
Names are user-authored target labels, not inferred from a source. There is no
ERP-specific identifier or connector behavior in the template layer.

## Local immutable revisions

Revisions are JSON files at `data/templates/<id>/<revision>.json` (or the configured
application data directory). Creating a template generates an ID and revision 1.
Saving an edit appends a revision; it does not rewrite previous files.

Publication writes and flushes a temporary file, then atomically links the completed
file into its final name without overwriting. The filesystem must support hard
links, such as local NTFS. Unsupported storage fails explicitly. Revision saves
include the editor's base revision; stale edits and concurrent collisions fail
instead of replacing another revision. Existing files remain usable after restart.

The initial UI lists revisions and supports creation, reading and new revisions.
There is no delete UI, merge, propagation or update-existing-mappings operation.
Removing a local template file cannot affect already copied/generated pipelines.

## Application and binding

1. Choose a source, then select a template revision.
2. Apply it to a draft with **zero existing mappings** and the **same processing
   version**. A mismatch is an error; no automatic version change occurs.
3. Every target starts Unmapped, including exact source-name matches and optional
   fields. Read binding columns only populates a choice list.
4. Choose Source column, Text literal, NULL literal, Empty-string literal, Boolean
   true or Boolean false for each target. Source names can also be entered exactly.
5. Configure required lookups explicitly using the existing lookup JSON shape.
6. Generate mappings, then use ordinary preview, diagnostics, save and export.

**New pipeline** creates the draft; it has produced version 2 since v2 became the
default, so the separate *New empty v2 pipeline* action was removed as a duplicate.
It does not change an existing v1 pipeline.

Required/optional describes row validation, not whether a configuration binding
is needed. Both unresolved required and unresolved optional targets block pipeline
generation. Explicit NULL is a resolved binding even on a required field; the
existing processor may then reject that row under the required rule.

NULL produces `{"literal":null}`; empty string produces `{"literal":""}`.
Text literals preserve whitespace and multiline content. Numeric/date constants
can be supplied as text and converted through the selected existing target type;
no numeric guessing occurs in this UI. The API also accepts the existing supported
literal scalar types. Untouched literal values are preserved when the generated
pipeline is rendered back into the existing editor, including multiline text.

Extra source fields create no output. Bindings are never selected by name, fuzzy
matching, AI or inference. Source existence/type compatibility is still checked by
the existing inspection/processing path, not guessed during template application.

### Lookup requirements

`lookup_required: true` stores only a requirement. It contains no lookup source,
column or connection. The binding panel asks for the existing rule:

```json
{"source":{"kind":"csv","path":"allowed.csv","delimiter":";"},"column":"code"}
```

Generation remains blocked until the rule passes existing pipeline validation.
Lookup existence is evaluated by the existing processor during preview/execution.
The configured lookup belongs exclusively to the generated pipeline. There is no
shared lookup catalog and no new lookup rule or execution semantics.

## Independence and execution truth

The pending browser draft owns a detached copy of the selected revision. A library
edit while binding cannot change its fields. Pending drafts are session-only and
cannot be saved/run as incomplete pipelines; refresh loses unfinished bindings.

Generation converts the copied fields and explicit bindings through the existing
`FieldMapping`/pipeline codec. The resulting JSON contains only the ordinary
version, name, source, columns and destination. No template ID/revision is required
for execution. Saved pipelines and run snapshots retain their own complete rules.

The UI guards preview/save/run and the legacy column-initialization shortcut while
bindings are pending. Pipeline JSON editing is available after generation. Once
generated, pipelines remain ordinary editable pipelines with existing validation.

## API

All operations use POST `/api/templates/<operation>` with existing local-host,
origin and `X-ETL-Token` protections:

| Operation | Body | Result |
|---|---|---|
| `list` | `{}` | Revision metadata |
| `read` | `id`, `revision` | Full target-only template |
| `create` | `definition` blueprint | New template revision 1 |
| `revision` | `id`, `base_revision`, `definition` | Newly appended revision |
| `apply` | `id`, `revision`, `spec` empty draft | Detached template, draft, all-null binding slots |
| `bind` | `template` copied document, `spec`, `bindings` | Existing executable pipeline format |

A create/revision blueprint accepts `format_version`, `processing_version`, `name`
and `fields`; IDs/revisions are managed by storage. `bind` uses the copied document
only and does not consult the library. An unresolved binding slot (`null`) differs
from a resolved NULL literal (`{"literal":null}`).

## Verification

- `tests/test_templates.py`: schema restrictions, immutability, revisions/restart,
  concurrent publication and failures, explicit bindings, lookup requirements,
  version guards, independence after removal, CSV execution/export equivalence,
  and the same template through a mocked SQL Server batched adapter.
- `tests/test_web.py`: full create/list/read/revision/apply/bind/save/run flow,
  snapshot independence after edits/removal, unresolved states and API protection.
- `tests/templates_browser.cjs`: UI authoring/revision flow, no automatic bindings,
  optional unresolved fields, lookup configuration, NULL/empty/multiline literals,
  generated preview, copy independence, stale revisions and mobile layout.
- `integration/sqlserver/verify_mapping_template.py`: opt-in verification using
  the previously authorized connection helper. One generic template processes CSV
  and a bounded `dbo.BRANDS` sample, then exports to temporary CSV. The test deletes
  its own template file before SQL processing, audits SELECT-only access and closes
  resources. No ERP write statements or setup scripts are run. Sanitized results
  are stored in `integration/sqlserver/mapping_template_result.json`.

For browser tests, start `.venv\Scripts\python.exe -m tests.browser_fixture_server`
and use the existing optional `ETL_TEST_URL`, `ETL_PLAYWRIGHT`, `ETL_CHROMIUM` settings.

Source adapters, processing stages, exporters, pipeline formats and database schema
are unchanged. This feature adds no new runtime dependency or execution semantics.
