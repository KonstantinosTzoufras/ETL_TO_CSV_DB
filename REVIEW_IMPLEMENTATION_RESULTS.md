# Review implementation — 2026-09-17

## Product changes

- New ordinary pipeline drafts use v2. Existing definitions and the shipped v1
  example keep their own versions. NULL remains `\N` in default v2 CSV output;
  empty strings remain empty. No exporter or conversion semantics changed.
- `CONVERT` supports the existing scalar CAST types and an optional nonnegative
  integer literal style. Parameters remain separately bound. Unsupported types,
  dynamic styles and previously prohibited SQL constructs still fail closed.
- Exact object approval stays unchanged. Schema-wide approval is deferred to a
  separate security proposal; no configuration or database permissions changed.

Schema approval would reduce onboarding work, but would also include newly
created objects automatically. A future proposal must address that expanding
scope, case-sensitive names, view dependencies and audit visibility. It should
use a distinct policy field rather than silently changing existing object lists.

## Execution boundary decisions

SQL does explicitly requested filtering, joins and aggregation. Python retains
per-field processing, diagnostics and exports across connectors. No automatic
transform pushdown. No stored procedures or free-form EXEC. Views can expose
compatible SELECT-based logic, but are not a universal replacement for procedures.

## Ownership experiment

The prototype lives only in `integration/benchmark.py`. It adopts locally owned
stage maps while validating keys and defensively freezing nested values. Tests
cover read-only maps, nested mutation isolation, equivalent content, and the
unchanged public constructor's defensive copy. It is intentionally not used in
production: preserving these guarantees did not reproduce the claimed 1.59x
benefit. Raw local measurements are in `data/ownership-173.json` and
`data/ownership-20.json`; these files also include a final human-readable ratio.

Three alternating rounds per shape, 8,000 generated CSV rows, best wall times:

| Columns | Current | Ownership prototype | Ratio |
|---|---:|---:|---:|
| 173 | 5.363 s | 4.878 s | 1.10x |
| 20 | 0.903 s | 0.791 s | 1.14x |

These rounds ran without test suites concurrently. An earlier overlapping run
was discarded. The safer prototype deliberately includes nested-value checks
missing from the shallow-view probe in the original review, so these are not
equivalent implementations. Under the plan's “close to 1.59x or keep it simple”
criterion, the production optimization is declined.

## Benchmark harness

```
python -m integration.benchmark --columns 173 --rows 8000 --rounds 3 --probe-ownership
python -m integration.benchmark --columns 20 --rows 8000 --rounds 3 --probe-ownership
python -m integration.benchmark --pipeline path/to/pipeline.json --rows 10000 --rounds 3
```

For a reviewed SQL workload, the last command additionally requires `--allow-sql`.
Only then is workspace connection configuration loaded. Query-source approval
is still enforced by the existing adapter. This flag records operator intent;
it does not grant database read-only permissions. Do not run it on an unapproved
workload/account. No live SQL benchmark has been performed in this batch.

The harness reads each source once per pass, streams a capped number of rows
through the existing processor and exporters, and removes temporary files at
exit. SQL may prefetch a batch beyond the input cap; a cap is not a bound on
server query work. Narrow sources/queries before benchmarking large datasets.
Lookups are excluded. Statistics contain counts/times, not row values or SQL.

Read time includes connection/schema setup and row materialization; process time
includes RowResult construction; write time includes formatting and finalization.
Total also includes orchestration and source cleanup. Per-row timer overhead and
OS caching affect results. All rounds are retained; alternating variant order
reduces drift, but best-of-N ratios remain local measurements, not SQL throughput
guarantees. For remote/local comparisons run the same shape on separately approved
connections. No changes to sources, run lifecycle or architecture were needed.

## Verification

- Full Python suite: 273 tests passed.
- Four browser suites passed: templates (including explicit v1/v2 mismatch),
  mouse workflow, column picker and bulk transforms/removal.
- New tests cover CONVERT style/target restrictions, exact SQL and parameter
  forwarding, rejection before connection, benchmark row caps/counts, SQL opt-in,
  ownership prototype nested freezing and defensive-copy compatibility.
- JavaScript syntax and `git diff --check` passed.
- No live SQL connection opened. Restart an already-running app process to load
  the Python query validator change; refresh the page for the new draft default.
