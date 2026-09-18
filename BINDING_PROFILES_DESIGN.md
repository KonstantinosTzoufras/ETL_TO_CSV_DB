# Reusable binding profiles — design review

Design only. Nothing is implemented, and the name matcher is left exactly where it
is pending the decision at the end.

Companion: [TEMPLATE_MATCHING_PLAN.md](TEMPLATE_MATCHING_PLAN.md), whose section 3
this document demotes from "the reuse mechanism" to "first-time assistance".

---

## The premise, restated

`customer_code` against `KOD_PEL`, `CUSTNO`, `IDCLIENT` is not a naming variation.
No deterministic rule reaches it, and no honest heuristic should try. The knowledge
that `KOD_PEL` is the customer code is **an act of human judgement about one
dataset** — so the thing worth storing is that judgement, not a rule that might
reconstruct it.

That reframes the whole feature. The matcher saves keystrokes on the easy half.
A binding profile saves the part that actually cost something.

### The three objects

| | Holds | Source-specific? | Reusable across |
|---|---|---|---|
| **Template** | output names, types, transforms, validation | **no**, by contract | every source |
| **Binding profile** | target → source column / literal / lookup | **yes**, that is its purpose | every pipeline on that source |
| **Pipeline** | the executable definition | yes, concrete | nothing — it is the product |

The important structural point: **a binding profile is a saved copy of the
`bindings` array that `bind_template` already accepts**, plus identity and drift
metadata. No new execution path, no change to the engine, no change to what a
pipeline is.

---

## 1. Identity — how a profile finds its source

### The source key

A non-secret descriptor derived from the source definition:

| Source kind | Key | Not in the key |
|---|---|---|
| `csv` | `{kind, path}` | `delimiter`, `encoding` — read options, not identity |
| `sqlserver` | `{kind, connection_env, schema, table}` | — |
| `sqlserver_query` | `{kind, connection_env, query_digest}` | the SQL text itself |

`connection_env` is a **variable name**, not a credential — it is already stored in
every saved pipeline. No connection string, password or host ever enters a profile.

For `sqlserver_query` the dataset *is* the SQL, so the key carries a digest of the
statement canonicalised through the parser already in `queries.py`. Editing the
query produces a different key, which is correct: the columns may have changed.

### Recommendation: the key suggests, it never selects

Auto-loading a profile because a key matched is the wrong default. Keys break for
mundane reasons — the file moved, the env var was renamed, DEV became PROD — and a
silent wrong binding is the failure mode this whole tool exists to avoid.

So:

- profiles whose key matches exactly are **listed first**, marked *for this source*
- profiles for the same `schema.table` under a different `connection_env` are listed
  second, marked *same table, different connection*
- everything else for this template is listed last
- **the operator picks one and presses Load.** Always.

This also answers the DEV/PROD case, which is otherwise a design hole: the same
binding genuinely applies to both, and a weaker secondary key surfaces it without
pretending it is the same dataset.

---

## 2. Does a profile belong to a template revision?

It records one, and is **not restricted to it**.

```
template_id        which template's targets these are — binding
authored_revision  which revision it was written against — informational
```

The binding is stored **by target `output_name`, never by position**. Position would
silently mis-bind the moment a template reorders its fields, which is exactly the
class of error that must not be possible here.

A profile is tied to one `template_id` because its keys *are* that template's target
names. Reusing it across templates is possible and covered in section 6, but it goes
through review rather than being the normal path.

---

## 3. What is stored

```json
{
  "format_version": 1,
  "id": "<32 hex>",
  "name": "ERP_A · dbo.Customers",
  "template_id": "<32 hex>",
  "authored_revision": 2,
  "source_key": {"kind": "sqlserver", "connection_env": "ETL_SQL_ERP_A",
                 "schema": "dbo", "table": "Customers"},
  "schema_snapshot": ["KOD_PEL", "POSO", "XWRA", "HMNIA"],
  "bindings": {
    "customer_code": {"source": "KOD_PEL"},
    "amount":        {"source": "POSO"},
    "country":       {"source": "XWRA"},
    "origin":        {"literal": "ERP-A"}
  },
  "updated": "2026-09-18T12:00:00Z"
}
```

`schema_snapshot` is the source's column names at the time of writing. It exists
solely so that drift can be **named** later rather than discovered at run time.

### Explicitly not stored

Connection strings, passwords, hosts, the pipeline's destination, the template's
rules (types, transforms, max_length — those live in the template and would go stale
the moment it gains a revision), and any column data.

---

## 4. Do literals belong here?

**Yes — and this is their natural home.**

The argument is the one already made for templates in the other direction. A literal
such as `origin = "ERP-A"` is *the label of the source system*. That is why a
template must refuse it. It is also why a binding profile is exactly where it
belongs: it is knowledge about one dataset, reusable for every export from that
dataset.

So the three objects partition cleanly:

| | literals |
|---|---|
| Template | **rejected** — would carry one source's identity into all the others |
| Binding profile | **stored** — it is a property of this source |
| Pipeline | baked in, as today |

NULL, empty-string and boolean literals store identically to text ones.

### Lookups

Stored, with one constraint. A lookup rule names a lookup source and column; for a
CSV lookup that is a path, for a SQL lookup a `connection_env`. Both are the same
class of non-secret reference already permitted in a source key.

The rule must be re-validated on load rather than trusted, because a profile is a
file that may be old. An invalid lookup marks that target unresolved and says so.

---

## 5. When the source schema changes

Verified on every Load, by reading the source's columns — one cheap metadata read,
the same one `Load source columns` already performs.

| Case | Behaviour |
|---|---|
| bound column still present | carried over, marked `from profile` |
| bound column **missing** | binding dropped, target marked **unresolved**, message names the column |
| new column in the source | informational only; never bound automatically |
| literal binding | unaffected — it has no source column |

The failure is never silent and never fatal: a profile with three missing columns
still restores the rest, and you fix three rows instead of thirty.

An operator may proceed without loading columns, but then Apply is blocked with
*"verify the profile against the source first"* — the alternative is discovering the
problem at run time, after a partial export.

---

## 6. When the template gains a revision

Matching by `output_name`, on Load:

| Target | Behaviour |
|---|---|
| in both revisions | binding carried over |
| **new** in the current revision | unresolved; this is where the name matcher earns its place |
| **removed** from the current revision | binding dropped, reported, not silently retained |
| present but its **rules changed** | binding carried over, and the change is **reported** |

That last row matters and is easy to miss. A binding says *where the value comes
from*; a rule says *what must be true of it*. They are independent, so the binding
stays valid — but if `amount` went from `string` to `decimal`, the same source column
may now start failing conversion. The operator is told, and decides.

### Copying and migrating

Two explicit operations, both ending in review, neither ever silent:

- **Copy to another revision** of the same template — the table above, applied.
- **Copy to another template** — targets are matched by name across the two
  templates, every carried binding is marked `copied`, and everything unmatched is
  left open. This is where `propose_bindings` is genuinely useful, because the two
  templates were written by the same people and *do* share naming.

Neither operation writes to the source profile. Both produce a draft you save under a
new name.

---

## 7. How the UI exposes it

The `Review mappings` panel gains one row at the top, above the targets:

```
Binding profile   [ ERP_A · dbo.Customers  (for this source)   ▾ ]   [ Load ]

   3 saved for this template · 1 matches this source
```

After Load:

```
Loaded "ERP_A · dbo.Customers", written for revision 2 · you are on revision 3

   customer_code  <-  KOD_PEL        from profile
   amount         <-  POSO           from profile · type changed to decimal since
   country        <-  XWRA           from profile
   vat_number     <-  ?              new target in revision 3
   origin         =   "ERP-A"        from profile (constant)

   [ Match remaining by name ]        [ Apply mappings ]
```

Then, next to the existing buttons:

```
[ Save as new profile ]   [ Update "ERP_A · dbo.Customers" ]
```

Default name when saving is derived from the source key — `ERP_A · dbo.Customers`,
`erp_a.csv` — and is editable. Names are not unique; the source key and template
disambiguate, and the list shows both.

### Where the panel state comes from

Unchanged from today: the proposals and the loaded profile live in the browser, and
`definition.columns` is untouched until **Apply mappings**. Loading a profile writes
nothing anywhere.

---

## 8. Editing, replacing and deleting

Profiles are **mutable**, unlike templates. The reason is specific, not casual:

> Templates are immutable-revisioned because pipelines were *generated* from them
> and a past run must stay explicable. **Nothing is ever generated from a profile
> that does not also carry the result.** A pipeline holds its own complete bindings,
> so an edited profile cannot retroactively change anything.

So there is nothing to reproduce, and an append-only history would be storage
without a reader.

| Action | Behaviour |
|---|---|
| **Update** | overwrites, guarded by the `updated` value you loaded. A concurrent edit is refused with *"reload before saving"*, exactly as template revisions do |
| **Save as new** | new id, new name, nothing else touched |
| **Delete** | explicit and confirmed. No pipeline is affected — they carry their own bindings |

---

## 9. Storage

**A new table in the existing `data/etl.sqlite3`**, alongside `pipelines` and `runs`:

```sql
CREATE TABLE IF NOT EXISTS binding_profiles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    template_id TEXT NOT NULL,
    source_key TEXT NOT NULL,      -- canonical JSON, for lookup
    profile TEXT NOT NULL,         -- the full document above
    updated TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS binding_profiles_template ON binding_profiles(template_id);
```

### Why not JSON files, like templates

Templates are files because they are immutable, append-only and want to be readable
and portable. Profiles are the opposite on every count: they are mutable, they are
**queried** (by template, by source key, ordered by match strength), and their
history has no consumer.

`CREATE TABLE IF NOT EXISTS` makes this a no-op migration on an existing database.

**The trade-off, stated:** profiles become less portable than templates. Moving work
between machines would mean an export. Given that the source key is machine-specific
anyway — paths and env var names — a profile is arguably not portable in principle,
so the loss is smaller than it looks. An `export` / `import` pair can be added later
if a real case appears.

---

## 10. Compatibility

| Area | Impact |
|---|---|
| Templates on disk | **none** — no new field, `format_version` stays 1 |
| Saved pipelines | **none** — no profile reference is stored, exactly as no template reference is |
| `bind_template` | **unchanged**. A profile produces the `bindings` list it already takes |
| `apply_template` | **unchanged**, still binds nothing |
| Engine, exporters, diagnostics | **not touched**. This is an authoring feature and reaches nothing that runs |
| `etl.sqlite3` | one additive table |
| Existing browser tests | the `Review mappings` panel gains a row; locator updates only |

The clean part of this design is that everything new sits **before** `bind_template`.
Once mappings are applied, the tool is in a state it already knows how to be in.

---

## 11. What becomes of the name matcher

It stays, and becomes subordinate. It is genuinely useful in three places, none of
which is "the reuse mechanism":

1. **First contact with a source that has no profile** — some columns always do
   agree (`country`/`COUNTRY`), and binding those by hand is pure waste.
2. **A template gained targets** — a profile cannot know them; matching can often
   propose them.
3. **Copying a profile between templates** — targets matched by name across two
   templates written by the same people.

Two rules govern it in every case:

- it only fills targets that are **still open** — it never touches a profile's
  binding or a choice the operator made
- it is a **button**, never automatic, and its proposals are marked as proposals

### Consequence for the half-finished work

The matcher's `propose_bindings`, `match_template` and `match` endpoint stand, and
their unit tests (plan items 1–15) are worth writing regardless — they test a pure
function whose contract does not change under this design.

The browser and CSS work should **not** be finished against the current panel, since
the panel is about to gain a profile row above the targets and a fourth row state
(`from profile`). Doing it now means doing it twice.

---

## 12. Open questions for the owner

**a. Profile per template, or free-floating?**
Recommended: tied to one `template_id`, because the keys are that template's target
names, with an explicit *copy to another template* that goes through review. The
alternative — a profile as a bare `source column → meaning` dictionary reusable
everywhere — is more powerful and considerably less predictable. I would not start
there.

**b. Should a pipeline record which profile produced it?**
Recommended: **no**, for the same reason it records no template. But it is the one
place where a stamp would pay off later — *"which pipelines used the ERP_A binding
before we fixed it?"* is a real question. Worth deciding deliberately rather than by
default.

**c. One profile per source, or several?**
Several, allowed and unnamed-by-default, because two exports from the same table can
legitimately bind different columns. The list makes this visible rather than
surprising.

**d. Sequencing.**
Profiles are the larger feature but the one that solves your actual problem. The
matcher's unit tests are an hour and are independent. My recommendation is to land
those, leave the matcher UI unfinished, and build profiles next — the matcher then
gets its UI once, inside the panel it will actually live in.
