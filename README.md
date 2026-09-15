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
2. **Preview 100 rows**: οι πρώτες 100 εγγραφές εισόδου, μαζί με απορρίψεις. Το demo δίνει **5 συνολικά, 3 έγκυρες, 2 απορρίψεις**.
3. **Save pipeline**: αποθήκευση και επαναφόρτωση από το sidebar.
4. **Run & export**: πλήρης εκτέλεση στο παρασκήνιο, λήψη του αποτελέσματος και των απορρίψεων από το ιστορικό.

Η εκτέλεση χρησιμοποιεί την τρέχουσα φόρμα, ακόμη και αν δεν έχει αποθηκευτεί. Κάθε run κρατά αντίγραφο της περιγραφής του. Τα αρχεία και η βάση κατάστασης αποθηκεύονται κάτω από `data/`, το οποίο εξαιρείται από το git.

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

- CSV: streaming, UTF-8 BOM, configurable delimiter, διατήρηση αλλαγών γραμμής μέσα σε quoted πεδία. Formula-like **strings** αποκτούν αρχικό `'` για ασφαλές άνοιγμα σε Excel· οι typed αριθμοί παραμένουν αριθμητικό κείμενο.
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
