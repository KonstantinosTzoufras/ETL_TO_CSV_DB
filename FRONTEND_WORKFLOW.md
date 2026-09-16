# Source selection and output fields

The table/CSV workflow follows the useful interactions in `TableExport.php`:

1. Choose CSV or SQL Server. SQL uses a configured connection reference; credentials are never sent to the browser. Query-source approval remains a separate policy.
2. Open **Discover a source**, then **Browse datasets**. Choose a workspace folder or SQL schema, then a file/table/view. Search filters loaded datasets; **More datasets** appends additional results.
3. Inspect metadata or read a raw **Source Sample**, then **Use this dataset**. This loads available column names without creating mappings.
4. Search and select columns, or use **Add all unused columns** for a single action. Select all includes columns hidden by the search. Clear selection removes only the current selection, not existing mappings.
5. Added mappings use source order, the source name as the output name, and string type with no transforms or validation rules. Already-bound source columns are skipped. Output-name collisions get a visible `_2`, `_3`, etc. suffix. Existing mappings are preserved. Pipelines and templates share a 4,096-output limit; the UI gets this limit from the backend. The existing HTTP request-size limit still applies to large definitions.
6. Adjust output names, types and rules, then use **Processed Preview**. It runs the existing processor; Source Sample remains raw.
7. **Run & export** writes into a generated per-run directory. The UI displays the server's actual output root. Download completed outputs from Run history; the browser controls where the downloaded copy is saved.

**Read source columns** now loads the same explicit picker. It no longer adds every field as a side effect. This changes editor interaction only, not saved pipeline processing.

**Choose table / view** beside the table input opens the searchable catalog for the current schema. If no table is selected, **Read source columns** opens that catalog rather than submitting an incomplete source. Loaded table names are also offered as browser autocomplete.

Every source-bound mapping has an inline searchable Select2 dropdown. It loads column names on demand when reopening a saved pipeline. Only the selected binding changes. Constants use a literal-value input instead. Column search is paginated in batches of 100; source changes invalidate the available choices. No field names or types are inferred.

Source-setting changes clear discovered choices and suggestions; existing mappings stay intact for review. Late discovery/column responses are discarded when source settings change. Errors and loading feedback appear next to discovery.

Multiple-query exports are under **Advanced exports**. Template bindings remain explicit. New drafts still use the existing processing-version default, and the UI describes the active version; saved definitions are not upgraded.

## Connection setup and current limits

The configured connection selector lists nonempty `ETL_SQL_*` variables from the running server's environment. It exposes names only and does not attest to SQL permissions. Existing saved references can still be opened; availability is checked on use. Startup loads the workspace `.env` without overriding existing environment variables. `DB_HOST`, `DB_NAME`, `DB_USER`, and `DB_PASS` create `ETL_SQL_MAIN` when no explicit reference is set. Restart the server after changing configuration.

CSV browsing remains within the workspace. Dataset search covers loaded pages rather than a server-wide search. Existing mappings are not automatically rebound or cleaned when changing a source. There is no new folder picker, type inference, matching, query builder or processing behavior.

## Verification

`tests/column_picker_browser.cjs` covers CSV discovery and preview, subset/all selection, duplicate prevention, source changes, local errors, mocked SQL discovery with 300 columns, and mobile layout. Run it against `python -m tests.browser_fixture_server`. No live SQL access is required.

## Mouse-friendly layout and Select2

Select2 (with locally bundled jQuery) now supplies searchable connection, schema,
table, mapped-source and template-source choices. Opening a saved mapping's
source dropdown loads available columns on demand. Selecting a table loads its
columns; no mappings are created until the user selects Add selected or Add all.
The older source-chooser button is replaced visually by the inline dropdown.
Catalog choices are cached per connection/schema for the current page, with a
10,000-entry loading bound; the existing paged catalog browser remains available.

Transforms are chosen from a menu, then appended to an ordered list. Up/down and
remove buttons edit that list. Repeated transforms and configured order are
preserved. This changes editing only; the processing engine is untouched.
CSV encoding has a dropdown and preserves custom encodings in loaded definitions.

The pipeline name stays at the top. Save, Processed Preview and Run & export share
a fixed bottom action bar. Section navigation jumps to Source, Mapping, Export
or Preview/history. Source and Mapping have bounded heights and internal scroll;
wide mappings also have horizontal scroll and sticky headings. The column picker
collapses after all source columns have been added so mappings remain visible.
Preview, run history and diagnostics are grouped at the bottom with bounded result
areas. Mobile layout retains all actions without horizontal page overflow.

Typing is still appropriate for output names, literal values, custom numeric
limits and advanced SQL/JSON configuration. This is not a new query/lookup builder.

`tests/mouse_workflow_browser.cjs` verifies dropdown selection without typing,
transform ordering, preview, a 300-column mapping, desktop/mobile dimensions,
local-only assets, and the existing Content Security Policy.
# Bulk transforms

The mapping selection toolbar also provides **Remove selected fields**. Tick
individual mappings or select all, then remove them with one confirmation.
Removal includes those fields' rules; unselected mappings and typed literals are
preserved. Deselect all clears checkboxes without removing mappings. Structural
changes clear transform undo.

The mapping scroll container is positioned to contain Select2's hidden native
controls. This prevents off-screen mapping rows from adding blank document
overflow below the footer.

Mapping rows show collapsed transform summaries; click a summary to edit its
ordered chain. Added source columns remain checked in the column picker.
Unticking an added column asks before removing all mappings using that source
column, including their configured rules. Cancel preserves them. Constants and
other source bindings remain unchanged. Clear selection clears pending additions
only; re-adding a removed column creates a fresh string mapping.

In Map & validate, open **Bulk transforms**. Select individual field checkboxes,
all fields, or fields whose configured target type is string. Compose an ordered
chain using the same transform controls as individual mappings, then choose
Append, Replace, or Clear. A confirmation shows the chain/action and field count.
Append retains existing transforms first, including duplicates. Nothing is
selected or applied automatically, and target types are not inferred.

Undo restores only the previous transform chains from the last bulk action.
Individual transform edits or reloading the mapping rows (including changing
drafts/steps or adding/removing fields) invalidate undo. Selection and undo are
editor state only; saved pipelines contain ordinary per-field transforms.
