# ETL Studio — Python edition

Πρώτη λειτουργική έκδοση του εργαλείου σε Python, με ανεξάρτητο πυρήνα, τοπικό web UI και CLI. Τα αρχικά `TableExport.php` και `SimpleXLSXGen.php` παραμένουν διαθέσιμα για σύγκριση.

## Εκκίνηση στα Windows

Απαιτείται Python 3.12+.

```powershell
cd C:\etltool
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m etl serve
```

Άνοιξε **http://127.0.0.1:8765**. Αν το περιβάλλον `.venv` υπάρχει ήδη, αρκεί η τελευταία εντολή. Εναλλακτικά τρέξε `start.ps1` από PowerShell. Δεν απαιτούνται Node, PHP ή εξωτερική βάση για το demo. Ο πυρήνας CSV και το web UI χρησιμοποιούν μόνο τη standard library της Python· το XLSX χρειάζεται `openpyxl` και ο SQL Server `pyodbc`.

Η αρχική οθόνη φορτώνει το συνθετικό παράδειγμα `examples/customers.json`:

1. **Read source columns**: επιθεώρηση πεδίων της πηγής. Σε κενή ροή δημιουργεί αρχικές αντιστοιχίσεις.
2. **Processed Preview (100 rows)**: οι πρώτες 100 εγγραφές εισόδου, μαζί με απορρίψεις. Το demo δίνει **5 συνολικά, 3 έγκυρες, 2 απορρίψεις**.
3. **Save pipeline**: αποθήκευση και επαναφόρτωση από το sidebar.
4. **Run & export**: πλήρης εκτέλεση στο παρασκήνιο, λήψη του αποτελέσματος και των απορρίψεων από το ιστορικό.

Η εκτέλεση χρησιμοποιεί την τρέχουσα φόρμα, ακόμη και αν δεν έχει αποθηκευτεί. Κάθε run κρατά αντίγραφο της περιγραφής του. Τα αρχεία και η βάση κατάστασης αποθηκεύονται κάτω από `data/`, το οποίο εξαιρείται από το git.

## Guided source discovery

Open **Discover a source** to browse workspace CSV files or SQL Server schemas and
tables/views. Inspect metadata, read a bounded **Source Sample**, then choose
**Use this dataset**. This changes only the draft source; mappings stay unchanged.
Source Sample shows original values without processing. **Processed Preview**
continues to use the existing pipeline engine. Samples default to 20 records,
maximum 100, with a 1 MiB response limit. No database writes or automatic mappings
are added. See [the discovery contract, policies and acceptance evidence](GUIDED_SOURCE_DISCOVERY.md).

## Processing diagnostics

**Processed Preview** now lets you expand each record and mapped field to compare
original, transformed and converted values, with recorded errors and source
positions. **Run history → View rejection diagnostics** reads the completed run's
existing rejection file without rerunning its source. Historical v1 stages that
were never recorded are explicitly unavailable. See [Processing Diagnostics Viewer](PROCESSING_DIAGNOSTICS.md).

## Reusable mapping templates

**Reusable mapping templates** stores target-only blueprints as immutable local
revisions. Apply a revision to an empty draft of the same processing version, bind
every target explicitly, then generate ordinary pipeline mappings. Optional fields
still need a binding; NULL and empty-string literals are distinct choices. Required
lookups are configured in the pipeline, never in the template. See
[Reusable Mapping Templates](REUSABLE_MAPPING_TEMPLATES.md), or
[Τι είναι και πότε το θέλεις](TEMPLATES_EXPLAINED_EL.md) for the same feature in
plain language.

## Χρήση χωρίς οθόνη

```powershell
.\.venv\Scripts\python.exe -m etl preview examples/customers.json
.\.venv\Scripts\python.exe -m etl run examples/customers.json
.\.venv\Scripts\python.exe -m etl --root C:\etltool --data C:\etltool\data serve --port 8765
```

Το CLI επιστρέφει JSON με counts, μικρό δείγμα και τη διαδρομή των αρχείων. Exit code 0 σημαίνει ότι η επεξεργασία ολοκληρώθηκε, ακόμη και αν υπάρχουν απορρίψεις. Exit code 1 σημαίνει αποτυχία εκτέλεσης. Για προγραμματισμένες εκτελέσεις μπορεί να χρησιμοποιηθεί η εντολή `run` από Windows Task Scheduler· δεν έχει προστεθεί ενσωματωμένος scheduler.

## Σύνδεση SQL Server

Χρειάζονται `pyodbc` και εγκατεστημένος **Microsoft ODBC Driver for SQL Server**. Δημιούργησε environment variable στο ίδιο PowerShell πριν ξεκινήσεις την εφαρμογή, π.χ. με Windows Authentication:

```powershell
$env:ETL_SQL_MAIN = 'DRIVER={ODBC Driver 18 for SQL Server};SERVER=YOUR_SERVER;DATABASE=YOUR_DATABASE;Trusted_Connection=yes;Encrypt=yes;TrustServerCertificate=no;'
.\.venv\Scripts\python.exe -m etl serve
```

Στην οθόνη επίλεξε SQL Server, connection variable `ETL_SQL_MAIN`, schema και table. Χρησιμοποίησε λογαριασμό με δικαίωμα ανάγνωσης. Δεν απαιτείται δεύτερη βάση για presets/errors. Τα στοιχεία σύνδεσης δεν αποθηκεύονται σε pipeline JSON ή SQLite. Δεν έχει γίνει σύνδεση σε πραγματική βάση στο πλαίσιο αυτής της υλοποίησης: λείπουν οι ρυθμίσεις σύνδεσης του αρχικού εργαλείου.

```json
{"kind":"sqlserver","connection_env":"ETL_SQL_MAIN","schema":"dbo","table":"Customers"}
```

Ο adapter εκτελεί `SELECT *` σε έναν πίνακα/view και διαβάζει σε παρτίδες 1.000 εγγραφών. Τα identifiers γίνονται escaped, δεν γίνονται δεκτά ελεύθερα SQL queries, και υπάρχει timeout 60 δευτερολέπτων για SQL statements. Δεν χρησιμοποιείται `NOLOCK`. Τα transformations/validations εκτελούνται στην Python. Η προεπισκόπηση σταματά μετά από 100 input rows, χωρίς εγγυημένη σειρά για SQL πηγές.

## Κανόνες και σημασιολογία

- Μία output στήλη έχει **είτε** `source` **είτε** `literal`. Τα ονόματα εξόδου είναι μοναδικά χωρίς διάκριση πεζών/κεφαλαίων.
- Transforms: `trim`, `lower`, `upper`, `empty_to_null`, με τη σειρά που δηλώνονται.
- Types: `string`, `int`, `decimal`, `float`, `bool`, `date`, `datetime`. Πρόκειται για μετατροπές τιμών και ελέγχους, όχι μόνο SQL predicates.
- `required: true`: απορρίπτει null/κενές/μόνο κενά τιμές. Προαιρετικές κενές τιμές γίνονται null, ακόμη και σε typed πεδία.
- `max_length`: έλεγχος μετά τα transforms, πριν από τη μετατροπή τύπου.
- `int`: signed 64-bit χωρίς αρχικά μηδενικά. Για κωδικούς τύπου `000123` χρησιμοποίησε `string`.
- `decimal`: ακριβής `Decimal`, με τελεία ως υποδιαστολή. Δεν επιβάλλεται το παλιό SQL `DECIMAL(38,10)`.
- `bool`: δέχεται μόνο `0`, `1`, `true`, `false`, χωρίς διάκριση πεζών/κεφαλαίων.
- `date`: αυστηρό `YYYY-MM-DD` και πραγματική ημερομηνία. `datetime`: ISO ημερομηνία και ώρα.
- Lookup: διαμορφώνεται στο **Advanced → pipeline definition**, εφαρμόζει τον ίδιο τύπο/transforms στις τιμές αναφοράς και συγκρίνει στην Python. Αυτό διαφέρει από SQL collation semantics. Η πηγή αναφοράς έχει όριο **100.000 εγγραφών ανά lookup** και φορτώνεται στη μνήμη.

Παράδειγμα στήλης με lookup:

```json
{
  "name": "country",
  "source": "country",
  "type": "string",
  "transforms": ["trim", "upper"],
  "required": true,
  "lookup": {
    "source": {"kind": "csv", "path": "inputs/countries.csv", "delimiter": ";"},
    "column": "code"
  }
}
```

Αν λείπει source column, υπάρχουν διπλές CSV επικεφαλίδες ή λάθος αριθμός πεδίων, αποτυγχάνει όλη η εκτέλεση. Οι αποτυχίες των κανόνων απορρίπτουν μόνο την αντίστοιχη εγγραφή. Άγνωστες επιλογές στη ροή απορρίπτονται, ώστε ένα typo να μην αγνοεί κάποιον κανόνα.

## Εξαγωγές

- CSV v1: streaming, UTF-8 BOM, configurable delimiter, διατήρηση αλλαγών γραμμής μέσα σε quoted πεδία. Formula-like **strings** αποκτούν αρχικό `'` για ασφαλές άνοιγμα σε Excel· οι typed αριθμοί παραμένουν αριθμητικό κείμενο.
- XLSX: write-only export. Όλα τα κελιά γράφονται ως **κείμενο** για διατήρηση IDs/δεκαδικών και αποφυγή formulas. Νέο φύλλο μετά το όριο γραμμών του Excel. Κελιά άνω των 32.767 χαρακτήρων προκαλούν σφάλμα αντί για σιωπηρή αποκοπή.
- `rejected.csv`: αριθμός εγγραφής, αρχική εγγραφή JSON, mapped τιμές JSON και λόγοι ανά πεδίο JSON.
- Κάθε run χρησιμοποιεί νέο directory. Downloads εμφανίζονται μόνο για ολοκληρωμένες εκτελέσεις. Σε αποτυχία μπορεί να παραμείνουν μερικά αρχεία στο directory του run· δεν θεωρούνται ολοκληρωμένο αποτέλεσμα.

## Δομή

| Αρχείο | Ευθύνη |
|---|---|
| `etl/spec.py` | Έλεγχος του versioned συμβολαίου της ροής |
| `etl/sources.py` | CSV και SQL Server adapters |
| `etl/engine.py` | Μετασχηματισμοί, κανόνες, preview και exports |
| `etl/store.py` | SQLite για presets και ιστορικό |
| `etl/web.py` | Τοπικό HTTP API και background runs |
| `etl/static/` | Web περιβάλλον, χωρίς build step ή CDN |
| `etl/__main__.py` | CLI |

## Έλεγχοι

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Οι δοκιμές καλύπτουν CSV/XLSX, Unicode, απορρίψεις, lookups, precision, περιορισμό preview, batching, αποθήκευση, HTTP API και downloads. Ο SQL Server ελέγχεται με mock driver· απαιτείται επιπλέον integration test με πραγματικό server. Το προαιρετικό `tests/browser.cjs` χρησιμοποιεί Playwright και το demo για έλεγχο UI, desktop/mobile και download.

## Όρια πρώτης έκδοσης και επόμενα βήματα

Αυτή είναι τοπική εφαρμογή για έναν έμπιστο χρήστη, με bind στο `127.0.0.1`, host/origin checks και token για POST requests. Δεν είναι deployment πολλών χρηστών: δεν έχει login, δικαιώματα ανά χρήστη, μόνιμη ουρά εργασιών ή αυτόματο resume μετά από restart. Τρέχουν έως δύο jobs ταυτόχρονα και γίνονται δεκτά έως οκτώ συνολικά. Τρέξε ένα instance ανά data directory. Τα input CSV πρέπει να βρίσκονται μέσα στο workspace.

Δεν έχει ολοκληρωθεί πλήρης ισοδυναμία με το PHP: δεν περιλαμβάνονται joins/WHERE builder, import των παλιών presets, error XLSX, SQL table browser ή εγγραφή σε βάσεις. Επόμενο ουσιαστικό βήμα είναι να συγκρίνουμε μια πραγματική PHP ροή με τη νέα υλοποίηση και να ορίσουμε τις ακριβείς απαιτήσεις για joins, προορισμούς και αυξημένους όγκους.

Τεκμηρίωση εξαρτήσεων: [pyodbc](https://github.com/mkleehammer/pyodbc), [openpyxl write-only mode](https://openpyxl.readthedocs.io/en/latest/optimized.html).


## Stage 5 — Export policies

See [ARCHITECTURE_STAGE_5.md](ARCHITECTURE_STAGE_5.md) for the current versioned
output contract. V1 output policies remain compatible. V2 defaults to `\N` for
NULL, preserves empty text and formula-like strings, and supports explicit
`encoding`, `null_value`, and `formula_policy` destination options. XLSX uses
text cells and the lxml backend to preserve identifiers, decimals and line endings.
Run `pip install -r requirements.txt` and restart an existing app process.

Live read-only BRANDS verification passed: 423 rows, 173 columns, exact CSV cell
representations and no database writes. The temporary data was removed; see the
[sanitized verification result](integration/sqlserver/brands_stage5_result.json).

Read-only parameterized SQL query sources: see [READ_ONLY_QUERY_SOURCES.md](READ_ONLY_QUERY_SOURCES.md). Query execution is disabled until an administrator approves a restricted connection; existing table/view sources are unchanged.

Ordered independent query exports: see [ORDERED_QUERY_PIPELINES.md](ORDERED_QUERY_PIPELINES.md). One approved connection, sequential steps, explicit stop/continue policy, and separate outputs/diagnostics per step.
# Local `.env` startup

Both `python -m etl serve` and `start.ps1` load `.env` from the workspace root.
Existing process environment variables take precedence. Values are literal: no
shell evaluation or variable interpolation. Do not commit this file.

An explicit `ETL_SQL_MAIN` connection string is supported. The original
`DB_HOST`, `DB_NAME`, `DB_USER`, `DB_PASS` fields also work: startup creates
`ETL_SQL_MAIN` from them. `DB_PORT` is optional. `DB_DRIVER` can select an installed
ODBC driver; otherwise Driver 18 is preferred, with Driver 17 as fallback.
`DB_ENCRYPT` defaults to `yes`; `DB_TRUST_SERVER_CERTIFICATE` defaults to `no`.
Set certificate trust explicitly only when appropriate for the intended server.
Credentials stay on the server; the UI lists reference names only.
This configuration does not grant query-source approval or assert database
permissions. Restart after changing `.env`.
# New pipeline defaults

New single-source drafts use processing version 2. Saved pipelines and the shipped
version-1 demo keep their explicit version. Default v2 CSV output represents NULL
as `\N` and empty strings as empty fields; these remain distinct in processing.
See [review implementation results](REVIEW_IMPLEMENTATION_RESULTS.md) for the
bounded CONVERT addition, performance experiment and opt-in benchmark harness.
