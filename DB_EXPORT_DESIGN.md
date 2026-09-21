# Writing to a database instead of a file — design review

Design only. Nothing is implemented. This is the DB→DB idea raised earlier and
never closed: *"θα κάναμε create table και μετά ό,τι κάνουμε ήδη."*

Companion precedent: [BINDING_PROFILES_DESIGN.md](BINDING_PROFILES_DESIGN.md) —
same posture, propose in writing, decide, then build.

---

## The premise

Today `destination.kind` is `csv` or `xlsx` only ([spec.py:62](etl/spec.py#L62)).
Everything downstream — the exporter, the download endpoint, the run report —
assumes a *file*. Adding `sqlserver` as a destination is not a small addition:
it is the first place in the whole tool that **writes** to a database rather
than only reading one. Every existing safety mechanism (`ETL_QUERY_CONNECTIONS`,
the SELECT-only parser) was built for reading. None of it applies to writing,
and none of it should be stretched to cover it.

So the read side and the write side get **separate, independent approval**,
exactly as reading a table and running a query already have separate approval
today.

---

## 1. Approval — a new, separate gate

```
ETL_EXPORT_CONNECTIONS={"ETL_SQL_MAIN":{"schema":"dbo","max_timeout_seconds":120}}
```

- A **new** environment variable, never reusing `ETL_QUERY_CONNECTIONS`. That
  one attests `"read_only": true`; conflating the two would either force a
  false attestation or quietly weaken what `read_only` has meant everywhere
  else in this tool.
- Approves a **connection + schema**, not a table — because the table doesn't
  exist yet at approval time. This mirrors the schema-wide `["dbo", "*"]`
  read-approval, whose entire justification is: don't make the administrator
  enumerate every object.
- Fail-closed, absent by default. No key present, no environment variable set
  → DB export is not offered at all, not even reachable in the UI.
- The connection's own database permissions remain the real boundary, same as
  every other layer in this tool. This is defence in depth, not a substitute.

---

## 2. Table identity — the two questions you left open

### a. ASCII or Greek in the generated name

**Recommendation: ASCII only, transliterated/stripped, same instinct as the
split-export folder sanitizer already ships with.**

A generated SQL Server identifier survives everywhere — copy-pasted into other
tools, other scripts, other people's SSMS sessions — precisely when it holds no
surprises. `z0_πελατες` is legal T-SQL (SQL Server is Unicode-native), but the
moment it crosses into a tool, a script, or a terminal that isn't, it becomes a
support ticket. Recommended transform: transliterate where a direct mapping is
obvious, otherwise drop the character, collapse repeats, cap length.

If you'd rather keep the Greek name intact because these tables are only ever
opened from Greek-language tools, say so — it is a one-line change to the same
sanitizer, not an architectural fork.

### b. Two pipelines, the same name

Not hypothetical: right now `data/etl.sqlite3` holds duplicate rows literally
named "Customers · clean export" (a real bug we found and fixed for the *save*
path — pipeline **names** were never unique, only pipeline **ids** are).

**Recommendation: key the table to the pipeline's id, not its name — and
refuse the run if a *different* pipeline would claim the same generated name.**

Concretely:

```sql
CREATE TABLE pipeline_exports (
    pipeline_id   TEXT PRIMARY KEY,
    table_name    TEXT UNIQUE NOT NULL,
    connection_env TEXT NOT NULL,
    schema        TEXT NOT NULL,
    updated       TEXT NOT NULL
)
```

A small local table, same database as `pipelines`/`runs`/`binding_profiles`.

- First run of a pipeline with a DB destination: sanitize its name, check
  `table_name` isn't already claimed by a **different** `pipeline_id`. If free,
  claim it.
- Same pipeline, run again: reuses its own claimed name. Renaming the pipeline
  afterward does **not** rename the table — the claim is permanent for that
  id, so a report or downstream job pointed at the table never breaks quietly.
- A different pipeline whose sanitized name collides: **the run is refused**
  before touching the database, with the exact conflicting table name in the
  message. You rename one of the two pipelines and run again. This was your
  own second option ("stop the run") — the alternative, forcing every pipeline
  name to be globally unique, would be a much bigger constraint for a problem
  that only exists for pipelines that also happen to export to a database.

---

## 3. The write itself — never a half-built table

This is where your question about errors lands exactly.

CSV/XLSX today has no transactional concept: rows stream out as they're
produced, and a failed run simply never gets its output exposed for download
([engine.py](etl/engine.py) — the comment *"Failed runs never expose their
partial files for download"*). The bytes exist on disk, unlisted, harmless.

A database table has no such quiet failure mode — if 49,999 of 100,000 rows
land in `dbo.z0_customers`, whoever queries that table next sees a silently
truncated, wrong dataset, indistinguishable from a genuinely small day. That
is a materially worse failure than an orphaned file.

**Recommendation: write to a shadow name, then swap.**

```
1. DROP TABLE IF EXISTS dbo.z0__building_<pipeline_id>
2. CREATE TABLE dbo.z0__building_<pipeline_id> (...)
3. INSERT rows, batched (see §5)
4. On full success only:
     DROP TABLE IF EXISTS dbo.<claimed_name>
     EXEC sp_rename 'dbo.z0__building_<pipeline_id>', '<claimed_name>'
5. On any failure at any point:
     DROP TABLE IF EXISTS dbo.z0__building_<pipeline_id>
     <claimed_name> is untouched - still holds the last successful run
```

The previously-successful table is only ever removed in the same instant its
replacement is proven complete. A run that fails at row 50,000 leaves the
existing table exactly as it was after the last run that succeeded — never
older data silently mixed with newer, never a partial table masquerading as
whole.

This is a rename-swap, not a multi-hour open transaction: batched inserts can
complete for a long time without holding one lock across the whole run, which
a single big transaction on some setups would.

Rejected rows are unaffected by any of this — they keep going to the existing
local `rejected.csv`, exactly as today. Nothing about rejects needs to change;
your question was specifically about the *valid* side.

---

## 4. Type mapping

| Pipeline type | SQL Server column |
|---|---|
| `string` | `NVARCHAR(max_length)` if set, else `NVARCHAR(MAX)` |
| `int` | `BIGINT` |
| `decimal` | `DECIMAL(38, 10)` — generous fixed scale; exact figures are still exact, just padded with trailing zeros when the source has fewer decimal places |
| `float` | `FLOAT` |
| `bool` | `BIT` |
| `date` | `DATE` |
| `datetime` | `DATETIME2` |

Every column and the table name itself go through the existing
`identifier()` bracket-escaper ([sources.py:28](etl/sources.py#L28)) — the same
one already used for every table/schema name in this codebase. Nothing new to
trust there.

---

## 5. Writing rows

Batched `executemany` (batches of ~1000), not one round-trip per row — this is
the one place row-by-row would visibly cost you, unlike the transform cost we
measured earlier at ~4%. `fast_executemany` where the driver supports it.

---

## 6. What doesn't carry over

- **`split_by` is refused for a DB destination**, exactly as it already is for
  ordered pipelines, and for the same shape of reason: splitting into several
  *tables* multiplies the shadow-swap machinery per group for a case nobody
  has asked for yet. One pipeline, one table.
- **No local `valid.csv`/`valid.xlsx` file is produced.** The run report says
  where the data went instead: table name, schema, connection, row count.
  `rejected.csv` still exists locally, unchanged.
- **Preview is unaffected.** Preview never writes anywhere today (`output_root`
  is `None`); a DB destination changes nothing about that — the 100-row sample
  it already shows is enough to judge before committing to a real run.

---

## 7. UI surface

- **Export & download** gains `Database table` beside `CSV`/`Excel · XLSX` in
  the format select, shown only when at least one `ETL_EXPORT_CONNECTIONS`
  entry exists (mirrors how the query-connection dropdown only appears when
  something is approved).
- Choosing it shows: which approved connection, and the table name that will
  be claimed or reused (read from the small local registry once you save).
- Run history shows *"Written to dbo.z0_customers_export (14,302 rows)"* in
  place of a download link; `rejected.csv` keeps its own link exactly as now.

---

## 8. Compatibility

| Area | Impact |
|---|---|
| CSV/XLSX pipelines | **none** — a third destination kind, nothing existing moves |
| `ETL_QUERY_CONNECTIONS` | **untouched** — a separate variable governs writing |
| Engine, transforms, validation, type conversion | **untouched** — a row is a row until the exporter; only the exporter is new |
| Split export | mutually exclusive with a DB destination, same as ordered pipelines |
| `etl.sqlite3` | one additive table (`pipeline_exports`) |

---

## 9. Tests to add

1. approval is fail-closed: no `ETL_EXPORT_CONNECTIONS` → DB destination
   refused at validation, never reaches a connection attempt
2. a schema-approved connection accepts any table name under that schema; a
   different schema is refused
3. table naming: ASCII sanitization, length cap, collision suffix behaviour
   (mirrors the split-export sanitizer tests almost exactly)
4. same pipeline id run twice reuses its claimed name
5. a second, different pipeline whose sanitized name collides is refused,
   with the conflicting name in the message, before any DDL runs
6. a run that fails mid-write leaves a previously-successful table completely
   unchanged (the actual question this document exists to answer)
7. the very first run of a pipeline that fails mid-write leaves **no** table
   at all - not a half-built shadow one
8. type mapping round-trips: string/int/decimal/float/bool/date/datetime each
   land as the right SQL Server type with the right value
9. `split_by` + DB destination is refused at validation
10. rejected rows are unaffected: identical `rejected.csv` whether the
    destination is CSV or a table

---

## Open for you to confirm

1. **ASCII-only table names** — recommended above; say so if you want Greek
   kept intact instead.
2. **Refuse on name collision between two different pipelines** — recommended
   above, as opposed to forcing globally unique pipeline names.
3. **Shadow-table-then-rename-swap** for atomic replacement — recommended
   above, as opposed to a single long transaction.

Everything else in this document is a direct consequence of those three once
decided. Say yes to proceed as written, or tell me which of the three you'd
change.
