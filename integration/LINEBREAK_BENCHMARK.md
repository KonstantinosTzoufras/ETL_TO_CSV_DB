# One-column newline-cleaning experiment

Run `python -m integration.benchmark_linebreaks` from the project root.
Defaults: 1,000,000 rows, three alternating baseline/cleaning rounds.
No SQL connection or production transform registration is involved.

The prototype replaces CRLF with one space, then remaining CR and LF with one
space each. Existing whitespace, tabs, NULL and empty string keep their meaning.
It uses three native string replacements, not a Python character loop. These
can scan the string more than once; native execution does not remove that cost.

Two measurements are recorded:

1. Transform-only: 100- and 1,000-character strings, with 0%, 10% or 100% of
   values containing one CRLF. Includes configured-transform dispatch overhead.
2. CSV read → existing v2 processor/RowResult → CSV write/close, one column total,
   about 100 characters per value, 10% containing CRLF. Greek text, quotes and
   delimiters are included. The original input is identical for both variants.

Input generation and output verification are outside the timed section. Every
export is read back and its header, values and row count checked. Files stream
through temporary directories and are removed after use. Repeated passes can
benefit from OS caches. A fixed pool of synthetic strings is used; this is not a
measurement of SQL latency or every production description distribution.

Use median times and report the spread, rather than promising a fixed overhead.
Full-export differences also include changed quoting/byte volume after cleaning,
so consult the transform-only measurements to isolate the operation's cost.
This does not measure 1,000,000 rows across 173 columns; only one column is
present and cleaned. Production registration/UI work is intentionally deferred.

Raw measurements: `data/linebreak-benchmark.json`.

## Measured results — 2026-09-17

1,000,000 rows per pass, three rounds, no other agent test suites running.
Full export wall times (seconds):

| Round | Baseline | Clean one column |
|---|---:|---:|
| 1 | 32.068 | 49.166 |
| 2 (clean first) | 39.091 | 39.042 |
| 3 | 38.886 | 40.030 |
| Median | 38.886 | 40.030 |

Difference of medians: +1.144 seconds, approximately +2.9%. The wide spread
means this is an observation, not a guaranteed overhead; the first pair alone
would give a misleading estimate. All six outputs were independently read back,
with exactly 1,000,000 expected values and no rejected rows in each.

Transform-only added time for one million values, difference of medians:

| Characters | 0% CRLF | 10% CRLF | 100% CRLF |
|---|---:|---:|---:|
| 100 | 1.061 s | 1.042 s | 1.231 s |
| 1,000 | 1.902 s | 1.641 s | 2.478 s |

Small non-monotonic differences reflect measurement noise. Do not extrapolate
these synthetic local results to SQL/network time or arbitrary large text.
Two focused correctness tests passed, including NULL, empty, CR/LF/CRLF, tabs,
non-text rejection, scoped transform registration and CSV round-trip checks.
