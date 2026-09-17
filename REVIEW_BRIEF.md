# ETL Studio — brief for an outside reviewer

> Update 2026-09-17: the text below records the original review baseline.
> New drafts now default to v2 with NULL/empty distinction preserved; bounded
> `CONVERT` support and a benchmark harness have been added. Production RowResult
> defensive copying remains. See [implementation results](REVIEW_IMPLEMENTATION_RESULTS.md).
> Historical performance figures below were not revalidated against SQL Server.

Written for an engineer who has not seen this repository. It summarises what the
tool is, what has been measured, and the four decisions the owner would like a
second opinion on. Nothing here requires reading the 3,800 lines of application
code first.

No credentials, hostnames or customer data appear in this document.

---

## 1. What the tool is

A local, single-user extraction tool. It runs on one Windows workstation, binds
to `127.0.0.1`, and has no login, no multi-user model and no remote deployment.

| | |
|---|---|
| Language | Python 3.12, standard library for the core |
| Dependencies | `openpyxl`, `lxml` (XLSX), `pyodbc` (SQL Server), `sqlglot` (SQL parsing) |
| Application code | ~3,800 lines (Python + vanilla JS, no build step, no CDN) |
| Tests | 3,625 lines Python (268 tests), 715 lines Playwright (11 tests), all green |
| Interfaces | Local web UI, CLI, background run queue |

**What it does:** reads a CSV file, a SQL Server table/view, or an approved
parameterised `SELECT`; applies per-field transforms, validation and type
conversion; writes CSV or XLSX, plus a rejection file that records, per field,
what failed and why.

**What it replaced:** two legacy PHP scripts (`TableExport.php`,
`SimpleXLSXGen.php`), still in the repo for comparison.

---

## 2. Architecture in one screen

Every field of every row goes through a fixed pipeline:

```
source value or literal
   → transforms, in configured order (trim, upper, lower, empty_to_null)
   → required check
   → max_length check
   → target type conversion (string, int, decimal, float, bool, date, datetime)
   → lookup membership check
   → export
```

Three models carry the data, and the separation is deliberate:

| Model | Meaning |
|---|---|
| `SourceRow` | what was read: original native values, record number, CSV line range |
| `RowResult` | what happened: original, transformed and converted values, plus per-field errors |
| Exporter | how the final value is represented as text |

`RowResult` is the heart of the design. It keeps **all three stages per field**,
so a rejected row can be explained precisely: which field, at which stage, with
the value before and after. Example from a real run:

```
FIELD    1 ORIGINAL    2 TRANSFORMED   3 CONVERTED
CODE     '  ab-2 '     'AB-2'          'AB-2'
QTY      'N/A'         'N/A'           -- MISSING --   error: conversion / invalid_type
PRICE    '1,5'         '1,5'           -- MISSING --   error: conversion / invalid_type
```

Two processing versions coexist. Version 1 is the legacy behaviour and is
preserved exactly. Version 2 is the conservative one: NULL and empty string stay
distinct, transforms are strict about text, `Decimal` never passes through
`float`.

**Everything runs per value, in Python. Nothing is pushed down to SQL.** The SQL
adapter issues a plain `SELECT` and fetches in batches of 1,000; all transforms
and checks happen locally. This is intentional — a CSV and a SQL table go through
identical code and produce identical results — and it is the source of the
performance ceiling discussed below.

---

## 3. Measured performance

### Method

CSV source, 8,000 rows × 173 columns (the shape of a real production table),
version 2, `trim` on every column. Each variant runs N times; the best time is
taken. Variants alternate within each round so machine drift and thermal effects
hit both equally.

**Caveat that matters when reading these numbers:** this workstation shows up to
**1.8× run-to-run variance** on an identical workload, with the first run always
slowest. Single measurements here are worthless; everything below is best-of-N
with alternation.

### Throughput model

Cost scales with **values** (rows × columns), not rows, which is why a wide
table is slow even at modest row counts. Before optimisation the tool sustained
roughly **110,000 values/second** on this machine.

Time decomposition of a full export (8,000 × 173):

```
read CSV        13%
process          65%      <- Python, per value
write CSV        22%
```

### Optimisations applied, each measured separately

| # | Change | Before | After | Ratio |
|---|---|---|---|---|
| 1 | `_freeze` tested abstract-base `Mapping` before the scalar case; scalars are ~99% of values, so the order was inverted | 9.18s | 6.29s | 1.45× |
| 2 | `process_row` rebuilt each column's rule mapping **per row** (865,000 times for this export) although the rules are fixed per column; also removed a per-call method table and two redundant `str()` round trips | 8.64s | 5.87s | 1.47× |
| 3 | `_freeze` walked every mapping twice — once to validate keys, once to copy | 5.87s | 4.65s | 1.26× |

All three are pure removal of repeated work. No behaviour changed; version 1
output is byte-identical; the 268 tests passed after each step.

### End-to-end result

The three ratios above were measured in separate sessions, so their product is
not trustworthy. A single alternating benchmark of the original commit against
the current one, best of four rounds, gives the real figure:

| Shape | Before | After | Ratio |
|---|---|---|---|
| 173 columns, `trim` on all | 12.69s | 5.38s | **2.36×** |
| 173 columns, no transforms | 9.49s | 4.62s | **2.05×** |
| 20 columns, `trim` | 1.47s | 0.71s | **2.08×** |

Throughput went from roughly **110,000 to 250,000 values/second**. (An earlier
estimate of 2.6× from multiplying the separate ratios was optimistic; this
measurement supersedes it.)

### Practical projection

Derived from the measured rates above, for 2,000,000 rows:

| Columns | Values | Before | After |
|---|---|---|---|
| 20 | 40M | ~6 min | ~3 min |
| 50 | 100M | ~15 min | ~7 min |
| 173 | 346M | ~53 min | ~22 min |

Memory is bounded at any size — the pipeline streams. The only in-memory
structure is a lookup set, explicitly capped at 100,000 rows.

**Not measured:** SQL Server read time over the network. All figures above use a
CSV source. With a remote database the driver and network may well become the
bottleneck instead of Python.

---

## 4. Decision 1 — a further 1.59× that costs a guarantee

`_freeze` makes domain models genuinely immutable. `@dataclass(frozen=True)`
alone only blocks attribute reassignment; it does not stop
`row.values['CODE'] = 'x'`. `_freeze` converts mappings to `MappingProxyType`
and sequences to tuples, **copying them first**, so a caller that keeps a
reference to the dict it passed in cannot mutate the model behind its back.

The copy is the expensive part:

```
one 173-column mapping, 20,000 times
   copy + freeze :  0.384s
   view only     :  0.008s     <- 51x cheaper
```

`MappingProxyType(d)` is an O(1) read-only *view*, not a copy. `RowResult`
builds two such mappings per row, so a 8,000-row export performs **2.77 million
value copies** that could be 16,000 view constructions. Replacing the copy with a
view was measured at **1.59×** — identical to removing the freeze entirely, which
shows the cost is the copying, not the protection.

**Why it was not taken.** `tests/test_models.py` constructs a `RowResult` with a
caller-owned dict, mutates that dict afterwards, and asserts the model is
unaffected. The guarantee is explicit and tested.

**Why it is arguably vacuous today.** Grepping the application, exactly one
production site constructs a `RowResult`:

```
etl/engine.py:124   inside process_row, from dicts it built itself and returns once
```

Every other construction is in tests. No production caller passes a dict it
retains, so nothing is currently protected.

> **Question for the reviewer:** is dropping this guardrail the right trade for
> 1.59×, given it protects only hypothetical future callers? Or is there a
> cleaner way to express "this mapping is owned by its constructor" without
> either copying every row or weakening the public contract?

---

## 5. Decision 2 — how much work belongs in the database

The owner's existing ETL logic lives in **SQL Server stored procedures**. This
tool cannot call them: the query subset is `SELECT`-only by design.

A counter-intuitive measurement shapes this decision:

```
no transforms at all        6.29s
trim on ALL 173 columns     6.56s     <- 4% difference
```

Moving transforms into SQL would therefore save about **4%**. The per-value cost
is the pipeline itself — read, check, convert, freeze, write — which runs whether
or not any transform is configured.

The real lever is **reducing what crosses into Python**: fewer columns, a `WHERE`
that cuts rows, a `GROUP BY` that aggregates. A join over two million rows inside
the database is seconds with indexes and moves no data; the same join in Python
means downloading both tables.

The practical bridge available today is a **view**: wrap the procedure logic in a
view and point the tool at it. The heavy set-based work stays in the database,
the tool provides per-field validation and file output.

> **Question for the reviewer:** is "database does set-based work, tool does
> per-field validation and file output" the right division? Or should the tool
> grow the ability to execute a stored procedure and consume its result set —
> and if so, how would that be made safe?

---

## 6. Decision 3 — is the SQL safety model proportionate?

Query sources are gated twice.

**Parse-time allowlist.** SQL is parsed with `sqlglot` and checked against an
allowlist of node classes *and* populated arguments, so a new parser feature
cannot become executable merely because its parent is a `Select`. There is also a
token-level blocklist and a 4,096-node complexity limit. SQL text is never
rewritten — the original string is passed to the driver.

```
Allowed  joins, subqueries, GROUP BY/HAVING/ORDER BY, DISTINCT/TOP, CASE,
         IN/BETWEEN/LIKE/EXISTS, CAST, COALESCE/NULLIF,
         UPPER/LOWER/LEN/TRIM/LTRIM/RTRIM, ABS/ROUND, aggregates

Blocked  EXEC, WITH/CTE, UNION/INTERSECT/EXCEPT, CONVERT, window functions,
         table-valued functions, temp tables, multiple statements, INTO
```

**Deployment approval.** An environment variable lists, per connection, the exact
`[schema, table]` pairs a query may touch, a maximum timeout, and a read-only
attestation. Missing or malformed approval fails closed. Approval is re-checked
immediately before every connection, including for saved pipelines.

The documentation is explicit that this is defence in depth, not a substitute for
database permissions, and that `readonly=True` on the ODBC connection is only a
hint.

> **Question for the reviewer:** for a single-user tool on a trusted workstation,
> is this proportionate engineering or over-built? The practical cost is real:
> every new database needs an approval entry listing every table, and common
> functions like `CONVERT` are rejected.

---

## 7. Decision 4 — friction that blocks "configure, don't code"

The goal is a tool that is parameterised rather than modified per job. The common
path is already close: choose connection → browse → pick table → *Use this
dataset* → *Add all unused columns* → *Run*. That last button creates a faithful
`string` mapping for every column; no per-column work is needed.

Three things get in the way.

**a. New pipelines default to processing version 1.** Version 1 forces an
Excel-safety apostrophe on any string starting with `-`, `+`, `=` or `@`. Because
the easy path types every column as `string`, negative numbers export as
`'-13.1900` instead of `-13.1900`. A real export of 423 rows contained 33 such
cells. Version 2 defaults to `preserve` and has no such problem. **The easy path
currently produces subtly wrong data.** Changing the default is a one-line change
and does not touch saved pipelines. *Still open.*

**b. Query approval is per connection and per object.** Necessary under the
current security model, but it means onboarding a new database is not purely a UI
action.

**c. `.env` is read once at startup**, so configuration changes need a restart.

### Established as *not* a problem

Choosing `string` for every column with version 2 is **lossless for every ODBC
type tested** — verified against `Decimal` (scale preserved: `0.50` stays
`0.50`), `int`, `bigint`, `VARCHAR` with leading zeros, text, `CHAR(n)` padding,
`NULL`, `datetime`, `date`, `bit` and `VARBINARY` (exported as `base64:`).
Forcing a numeric type is actively worse for numbers stored as text: `'007'`
becomes `'7'`, `'+5'` becomes `'5'`, and non-numeric values are rejected.

The useful rule, arrived at by measurement: **the target type is validation, not
formatting.** Set a type when you want bad data caught, not to make a number
export correctly.

---

## 8. Verification status

- **268 Python tests** — engine, exporters, sources, query safety, templates,
  ordered runs, HTTP API, diagnostics. SQL Server is covered with a mock driver.
- **11 Playwright browser tests** — the UI, including a 300-column mapping,
  mobile layout, Content Security Policy, and that the page requests nothing from
  the internet.
- Browser tests need **two fixture servers** (ports 8768 and 8769) and a **clean
  start**: they share SQLite state, so a rerun without restarting can fail a test
  that passes on its own. This is a weakness of the tests, not the application.

---

## 9. What would be most useful to hear

1. **The 1.59×** (section 4): take it, or keep the guarantee?
2. **The division of labour** (section 5): is view-plus-tool the right shape, or
   should stored procedures be supported?
3. **The security model** (section 6): proportionate, or over-built for one
   trusted user?
4. **Anything in the measurements** that looks wrong, or that a different method
   would contradict. The machine has high variance and every number here should
   be treated as a ratio measured under alternation, not an absolute.
