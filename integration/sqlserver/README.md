# Live SQL Server verification for Stage 4

This is an opt-in verification scenario, not an architecture change. The runner
only reads SQL Server. Setup and growth SQL must be run separately in a dedicated
test database. The fixture contains synthetic data; no production data is needed.

## Files

- `setup.sql`: creates and populates `dbo.ETLStage4Fixture` with seven cases.
  Refuses to overwrite an existing table; no automatic cleanup or deletion.
- `pipeline.v2.json`: explicit version-2 pipeline covering native SQL types and
  deliberate conversion/validation failures.
- `verify.py`: real driver audit, SqlServerSource reads, SourceRows, process_row,
  native RowResult assertions, selected diagnostic output and optional export.
- `grow.sql`: optional append-only expansion to exactly 10,000 or 100,000 rows
  when starting with the seven seeds. Re-running does not duplicate IDs or shrink
  a larger table. Keep the seed rows unchanged; run one generator at a time.

## Run the seven-row scenario

1. In SSMS, select a dedicated test database and run `setup.sql`. If using sqlcmd,
   read the script as UTF-8 (`-f 65001`) to preserve Greek literals. All Greek SQL
   literals have the `N` prefix.
2. Set the connection variable in the same PowerShell session as the runner.
   Replace the server/database placeholders with your test environment. Example
   for Windows authentication and a server with a trusted certificate:

   ```powershell
   $env:ETL_SQL_STAGE4 = 'DRIVER={ODBC Driver 18 for SQL Server};SERVER=YOUR_TEST_SERVER;DATABASE=YOUR_TEST_DATABASE;Trusted_Connection=yes;Encrypt=yes;TrustServerCertificate=no'
   ```

3. From `C:\etltool`, run:

   ```powershell
   .\.venv\Scripts\python.exe integration\sqlserver\verify.py
   ```

   The default direct-read batch size is **2**, so seven rows require four
   nonempty batches and one EOF fetch with the usual driver behavior. The audit
   allows smaller returned batches but requires every request to use the chosen
   size and forbids `fetchall`, `fetchone` and cursor iteration. It verifies the
   exact existing SELECT, fetched count, maximum returned batch, EOF, and closure
   of the cursor and connection. No credentials are printed.

4. Optionally include the existing export path:

   ```powershell
   .\.venv\Scripts\python.exe integration\sqlserver\verify.py --export csv
   .\.venv\Scripts\python.exe integration\sqlserver\verify.py --export xlsx
   ```

   Each command first verifies direct processing, then performs a separate full
   `execute` with the same pipeline. Keep the fixture unchanged between passes.
   Full execution retains its existing **1,000-row batches**. The runner audits
   that pass too, checks every native RowResult via `on_row`, compares counts,
   and reports the generated directory and file sizes. It does not redesign
   exporters or insert runs into history.

## Expected cases

| case_id | Representative input | Expected outcome |
| --- | --- | --- |
| 1 | `003`, Αθήνα, bigint above float's exact integer range, decimal(38,18), CRLF text | Rejected only by code_as_int; code and exact decimal preserved |
| 2 | ` 17 `, Greek text with spaces, negative precise decimal, whitespace optional text | Valid; integer becomes 17 only after explicit trim; original code stays spaced |
| 3 | Empty strings, zero, false, nullable date/time | Rejected: empty integer/date and required text |
| 4 | SQL NULL for optional columns; required_probe is OK | Valid; NULL retained, including typed numeric/date fields |
| 5 | Whitespace-only strings, LF Greek text, text date `29/02/2024` | Rejected: integer/date, required and max-length errors |
| 6 | Maximum positive decimal(38,18), impossible text date `2023-02-29`, required NULL | Rejected: date conversion and required |
| 7 | Normal code, Greek text, valid leap-day date text | Valid |

Expected seven-row totals: **7 processed, 3 valid, 4 rejected**. The deliberate
rejections are part of a passing verification; any unexpected code/stage or
silent fallback fails the script. Optional NULL bypasses conversion rejection;
empty text remains distinct. Both trim/empty_to_null orders are mapped from the
same source column to expose their different results.

The script prints the seven seed results selected by fixture `row_id`, with
original, transformed and converted maps, structured errors, validity, native
Python type names and execution-relative source positions. SQL query ordering
is unchanged: no ORDER BY, so fixture ID and SourceRow.number are distinct.
Sorting the seven printed samples does not reorder processing.

Decimal/date JSON tags and Unicode escapes are output encoding only. Assertions
inspect native Decimal/date/datetime/bool/int values before encoding. Decimal
comparisons include scale via `as_tuple()` and avoid float conversion.

## Optional 10k / 100k measurements

Edit `@TargetRows` in `grow.sql` to 10000 or 100000 and run it in the fixture
database. It repeats the seven cases without changing their native field values.
Then run, for example:

```powershell
.\.venv\Scripts\python.exe integration\sqlserver\verify.py --expected-rows 10000 --batch-size 1000 --export csv
.\.venv\Scripts\python.exe integration\sqlserver\verify.py --expected-rows 100000 --batch-size 1000 --export xlsx
```

The second command requires growing the table to 100000 first. Expected totals:

| Input rows | Valid | Rejected |
| --- | --- | --- |
| 10000 | 4286 | 5714 |
| 100000 | 42857 | 57143 |

Only the seven seed results are retained by the direct verifier. The existing
full runner retains its usual bounded sample. Audit counters have constant
size, with no per-batch or per-row log accumulation. Timings include assertions
and tracemalloc overhead: these are comparative verification measurements, not
clean production benchmarks. Peak Python allocation excludes ODBC/native buffers
and SQL Server memory; observe process working set in Task Manager/Performance
Monitor if total memory is relevant. Bounded fetchmany proves the application
read pattern, not the driver's internal buffering strategy.

Existing valid.csv/valid.xlsx and rejected.csv can be inspected in the reported
directory. Exporters still render NULL/empty according to their existing policy;
the RowResult maps and rejection JSON retain the processing distinction.

## Findings and verification status

- No Stage-4 production files were changed for this scenario.
- Schema metadata currently exposes Python type names. Both varchar and nvarchar
  therefore appear as str; this scenario prints safe driver precision/scale and
  nullability metadata but does not invent SQL type names.
- Full execution does not expose a configurable batch size; this harness leaves
  its default of 1000 intact. `--batch-size` affects only the direct adapter pass.
- SQL date/datetime columns cannot hold invalid date representations. Those
  rejection cases deliberately live in varchar, alongside actual typed dates.
- ODBC drivers are installed locally, but no ETL_SQL_* connection variable was
  configured when these assets were prepared. **Live SQL, setup/growth scripts
  and SQL-backed performance measurements have not been executed.**
- `tests/test_sqlserver_verification.py` is an offline harness check using a
  synthetic DB-API driver. It exercises the real adapter/processor/export code,
  scrambled source order, batch auditing, forbidden fetchall and resource cleanup.
  Its results must not be presented as live database verification.
- Local verification completed: **131 tests passed**, including three new harness
  tests and both CSV/XLSX export paths with the synthetic driver. Command:
  `.\.venv\Scripts\python.exe -m unittest discover -q`. The runner's `--help`
  entry point also passed.

Stage 5 and exporter/service refactoring remain paused.
