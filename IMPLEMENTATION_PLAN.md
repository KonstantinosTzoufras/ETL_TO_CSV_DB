# Implementation plan — response to the review

Maps the six review items to concrete work. Written after inspecting the code,
so every claim below is checked against the current implementation rather than
assumed. Line references are to the state at the time of writing.

Companion document: [REVIEW_BRIEF.md](REVIEW_BRIEF.md).

**Status key:** ☐ not started · ◐ in progress · ☑ done

---

## Item 1 — Version 2 as the default for new pipelines ☑

**Priority: highest.** The current easy path produces subtly wrong data: every
column is typed `string`, and version 1 forces an Excel-safety apostrophe on any
string starting with `-`, `+`, `=` or `@`. A real 423-row export contained 33
such cells, all negative numbers.

### What changes

| File | Change |
|---|---|
| `etl/static/app.js:6` | `blank()` — `version:1` → `version:2` |

That is the entire product change. There is no hidden default in the backend:
`etl/spec.py:68` requires an explicit `version` on every definition, so nothing
falls back to 1 on its own.

### What is already version 2

Checked, and it narrows the blast radius considerably:

- `etl/templates.py:36,44` — mapping templates already default to `processing_version: 2`
- `etl/static/ordered.js:3` — ordered query steps already create `processing_version: 2`
- `etl/static/index.html` — the template version selector already lists 2 first

The plain single-source pipeline was the **only** path still defaulting to 1.

### What is deliberately left alone

`examples/customers.json` stays at version 1. It is a shipped saved definition,
not a newly created pipeline, and it demonstrates that version 1 remains
supported. Verified that it is not a correctness risk either — the demo produces
**identical counts in both versions**:

```
demo v1 -> processed=5 valid=3 rejected=2
demo v2 -> processed=5 valid=3 rejected=2
```

So it can be migrated later if wanted; it is not blocking.

### Tests to change

`tests/templates_browser.cjs:27-28` clicks *New pipeline* and then expects the
status `"processing versions differ"` when applying a version 2 template. With a
version 2 empty draft the versions now match and the guard no longer fires.

The guard still needs coverage, so the step will be reworked to create the
mismatch deliberately — a version 1 draft or a version 1 template — rather than
relying on the default being 1.

`tests/test_web.py:84,170,183` set `spec["version"]` explicitly and are
unaffected.

### Owner decision — 2026-09-17

Keep NULL distinct from empty string. New drafts do **not** set `null_value`;
the existing v2 exporter default `\N` applies. Empty strings remain empty fields.
This supersedes the original recommendation to collapse both to empty output.
The shipped v1 demo and all existing saved definitions remain unchanged.

### Compatibility risk

Low. Saved pipelines carry their own `version` and are untouched. Version 1
remains fully supported: its code paths, tests and byte-level output are
unchanged.

---

## Item 2 — RowResult ownership path ☑ (evaluated; production adoption declined)

The benchmark-only prototype in `integration/benchmark.py` adopts stage dicts
while validating keys and freezing non-scalar nested values. The public model
and engine remain unchanged. See `REVIEW_IMPLEMENTATION_RESULTS.md` for measured
results and the decision; the proposed API below was not shipped.

**Measured prize: 1.59×**, which equals removing the freeze entirely — the cost
is the copying, not the protection.

```
one 173-column mapping, 20,000 times
   copy + freeze :  0.384s
   view only     :  0.008s     <- 51x cheaper
```

### Proposed API

```python
@dataclass(frozen=True, slots=True)
class RowResult(_Immutable):

    @classmethod
    def owning(cls, source, transformed, converted, errors):
        """Adopt locally built stage maps instead of copying them.

        The caller transfers ownership: it must not retain, reuse or mutate
        the mappings afterwards. The public constructor keeps copying and is
        what any other caller should use.
        """
```

It bypasses `__init__`/`__post_init__`, wraps each mapping in
`MappingProxyType` without copying, and normalises `errors` to a tuple.

**The public constructor is not touched.** `RowResult(...)` keeps its defensive
copy, so `tests/test_models.py:204` — which mutates a caller-owned dict after
construction and asserts the model is unaffected — passes unchanged.

### Call site

Exactly one, confirmed by grepping the application:

```
etl/engine.py:124   inside process_row, from dicts it builds itself and returns once
```

Every other construction in the repository is in tests.

### Tests to add

1. `owning()` and the public constructor produce equal content for the same input.
2. Mappings from `owning()` are read-only — assignment raises `TypeError`.
3. Two consecutive `process_row` calls return results that **do not share**
   mappings, which would catch an accidental dict reuse.
4. The existing defensive-copy test stays exactly as it is, asserting the public
   constructor still copies.

### Compatibility risk

Medium, and worth stating plainly:

- Values inside the adopted mappings are no longer type-checked at construction.
  They come from `convert_value` and `apply_transform`, which produce known
  scalars, and the exporter rejects anything unsupported — but the early failure
  is gone.
- If future code inside `process_row` ever retains or mutates those dicts after
  constructing the result, diagnostics would be silently wrong. Test 3 above is
  the guard; the docstring states the contract.

### Benchmark

Alternating rounds, best of N, against the current implementation, at 173×8,000
and 20×8,000. Accept only if the measured gain is close to the 1.59× probe;
otherwise revert and keep the simpler code.

---

## Item 3 — Keep the SQL/Python split ☑ (decision, no code)

Confirmed by measurement, not preference:

```
no transforms at all        6.29s
trim on ALL 173 columns     6.56s     <- 4% difference
```

Pushing `trim`/`upper`/`lower` into SQL would buy about **4%**, because the
per-value cost is the pipeline itself — read, check, convert, freeze, write —
which runs whether or not a transform is configured.

**Division of labour, unchanged:**

- **SQL Server:** filtering, joins, aggregation — anything that *reduces* rows or
  columns before they cross into Python.
- **Python:** per-field transforms, validation, type conversion, rejection
  diagnostics, file output.

To be recorded in `REVIEW_BRIEF.md` and the architecture notes so it is not
revisited from intuition later.

---

## Item 4 — No stored procedure support yet ☑ (decision, no code)

Views remain the bridge for existing database-side logic. Arbitrary `EXEC` stays
blocked.

If a real case appears that a view cannot reasonably cover, the shape to consider
is a dedicated `ProcedureSource` with pre-approved procedure names and typed
parameters — mirroring how query sources are approved today — never free-form
`EXEC`.

---

## Item 5 — Approval friction, without weakening SELECT-only ☐

### 5a. `CONVERT` via explicit AST validation ☑

Implemented with exact `Convert` arguments (`this`, `expression`, `style`),
supported scalar target types, and optional nonnegative integer literal style.
Dynamic/parameterized style expressions are deliberately outside this first
subset. SQL text and bound values reach the driver unchanged. The server checks
whether a literal style is meaningful for the requested conversion.

Inspected: `CONVERT(VARCHAR(20), ID)` parses to a `Convert` node with arguments
`this` (the target type), `expression` (the value), and optionally `style` (the
format code).

The target type is an `exp.DataType`, which the validator **already** restricts
to the supported scalar set — the same guard that makes `CAST` safe. So `CONVERT`
can be allowed by the existing mechanism rather than by relaxing anything.

Test to add: the `style` argument (for example `CONVERT(VARCHAR(10), Stamp, 112)`)
changes the rendered date format, so it deserves explicit coverage.

### 5b. Coarse-grained approval — investigation only

Today `ETL_QUERY_CONNECTIONS` lists exact `[schema, table]` pairs per connection.
Onboarding a database means enumerating every table.

To investigate: approving a **schema** rather than each object. This changes a
security contract, so it will be proposed separately with its own reasoning
rather than bundled into this batch.

### Unchanged, explicitly

AST validation, single-statement restriction, no `EXEC`/`INTO`/temp tables,
timeouts, and the fail-closed deployment approval. Database permissions remain
the real security boundary; the parser is defence in depth.

---

## Item 6 — Benchmark harness ☑; live SQL measurements deferred

`python -m integration.benchmark --help` documents the local CSV generator,
per-pass row cap, repeated rounds, explicit SQL opt-in and ownership probe.
No database was contacted for this implementation. Live local/remote comparisons
still require an approved workload/account. Lookup pipelines are excluded from
this first measurement harness so there are no incidental secondary reads.

### Cases to measure

| # | Case | Purpose |
|---|---|---|
| 1 | Local SQL Server source | ODBC cost without network |
| 2 | Remote SQL Server source | whether the network dominates |
| 3 | Narrow/high-row (~20 columns, many more rows) | whether per-row rather than per-value cost appears |
| 4 | Read / process / write split for each | where the time actually goes now |

A CSV baseline already exists for case 4:

```
read CSV        13%
process          65%
write CSV        22%
```

The open question is whether *process* is still dominant once a real ODBC driver
and a network replace a local file read.

### Blocker

This needs a real server. The configured connection points at a **remote**
database, and benchmarking means repeated reads against it.

**No connection will be opened without explicit approval.** Options:

1. Approve benchmarking against the existing remote database (say which table and
   roughly how many rows are acceptable to read repeatedly).
2. Provide a local SQL Server instance or container.
3. Build the harness now and run it later.

The harness itself can be written without any of these.

---

## Sequencing

1. **Item 1** — version 2 default, plus the `templates_browser.cjs` rework. Lowest
   risk, highest correctness value.
2. **Item 5a** — `CONVERT`, small and self-contained.
3. **Item 2** — `RowResult.owning()` with its tests and benchmark; revert if the
   measurement disappoints.
4. **Item 6** — harness now, measurements once access is agreed.
5. **Items 3 and 4** — record the decisions in the documentation.

After this batch: **no further micro-optimisation** unless a measurement shows a
real-world bottleneck. If item 6 shows ODBC or the network dominating, Python
micro-optimisation stops being worth doing at all.
