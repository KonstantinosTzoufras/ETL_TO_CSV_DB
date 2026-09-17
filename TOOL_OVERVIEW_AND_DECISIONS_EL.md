# ETL Studio — τι κάνει, πού γίνεται και τι κρατάμε

**Αποτύπωση: 17 Σεπτεμβρίου 2026.**

Αυτό το αρχείο είναι χάρτης της σημερινής λειτουργίας και βάση για τις επόμενες
αποφάσεις. Οι προτάσεις δεν είναι ήδη υλοποιημένες λειτουργίες ούτε εντολές
διαγραφής. Δεν αλλάζει κώδικα, pipelines ή δεδομένα.

## 1. Τι είναι το εργαλείο

Το ETL Studio διαβάζει δεδομένα, εφαρμόζει τους κανόνες που ορίζουμε και παράγει
αρχεία εξόδου. Δεν αποφασίζει μόνο του τι σημαίνει σωστός πελάτης, προϊόν ή
κωδικός. Δεν είναι αυτόματο εργαλείο διόρθωσης της βάσης.

- **Pipeline:** αποθηκευμένη συνταγή πηγής, mappings, κανόνων και εξόδου.
- **Run:** μία συγκεκριμένη εκτέλεση αυτής της συνταγής.
- **Template:** επαναχρησιμοποιήσιμη περιγραφή πεδίων εξόδου και κανόνων.
- **Source Sample:** μικρό δείγμα αρχικών τιμών, χωρίς processing.
- **Processed Preview:** μικρό δείγμα που περνά από τον κανονικό processor.

Το app είναι τοπική εφαρμογή Python με web οθόνη. Ο browser εμφανίζει και
διαμορφώνει τις ρυθμίσεις· η Python διαβάζει και επεξεργάζεται τα δεδομένα.
Το HTTP backend είναι standard-library server, όχι Django.

```text
Πηγή CSV / SQL Server / εγκεκριμένο SELECT
                  ↓
SourceRow — τι διαβάστηκε
                  ↓
Mappings: source column ή σταθερή τιμή → output field
                  ↓
Transforms → required → max length → type conversion → lookup
                  ↓
RowResult — αρχικές / transformed / converted τιμές και σφάλματα
                  ↓
          ┌───────┴────────┐
          ↓                ↓
       valid            rejected
          ↓                ↓
 valid.csv / XLSX     rejected.csv
```

## 2. Η διαδρομή του χρήστη

### Source — από πού διαβάζουμε

Επιλέγουμε CSV, SQL Server table/view ή εγκεκριμένο parameterized SELECT.
Το Browse datasets βρίσκει αρχεία, schemas, tables/views και metadata.
Το Use this dataset επιλέγει πηγή· δεν αντιστοιχίζει αυτόματα ονόματα πεδίων.

Το Source Sample δείχνει αρχικές τιμές. Δεν κάνει trim, μετατροπές ή validations.
Η επιθεώρηση columns δίνει τα διαθέσιμα πεδία για ρητή επιλογή.

### Map & validate — τι παράγουμε

| Ρύθμιση | Σημασία |
|---|---|
| Source / constant | Από ποιο πεδίο ή σταθερή τιμή παίρνουμε το περιεχόμενο |
| Output name | Όνομα πεδίου στο αρχείο εξόδου |
| Type | Ζητούμενη μετατροπή και έλεγχος συμβατότητας τύπου |
| Transforms | Ρητές αλλαγές τιμής, με συγκεκριμένη σειρά |
| Required | Αν επιτρέπεται NULL, empty ή μόνο whitespace |
| Max length | Μέγιστο μήκος μετά τα transforms |
| Lookup | Έλεγχος συμμετοχής σε σύνολο επιτρεπτών τιμών |

Το Add selected / Add all unused columns δημιουργεί mappings τύπου string χωρίς
αυτόματα transforms ή business rules. Αυτό είναι επιλογή διατήρησης τιμών,
όχι συμπέρασμα για τον πραγματικό τύπο κάθε πεδίου.

Υπάρχουν checkboxes για επιλογή mappings, bulk transforms και bulk removal.
Τα transforms μπορούν να προστεθούν στο τέλος, να αντικατασταθούν ή να καθαριστούν.
Η αφαίρεση mappings επιβεβαιώνεται και αφαιρεί και τους κανόνες τους.
Το Deselect all καθαρίζει την επιλογή, όχι τα mappings.
Το Undo αφορά την τελευταία bulk αλλαγή transforms· δεν είναι γενικό undo για
διαγραφές. Αλλαγές στη δομή των mappings ακυρώνουν αυτό το undo.

**Bulk editing μειώνει τη χειρωνακτική δουλειά του χρήστη, όχι τις πράξεις
processing ανά τιμή.**

### Save / Preview / Run

- **Save pipeline:** αποθηκεύει ρυθμίσεις, όχι αντίγραφο της πηγής.
- **Processed Preview:** επεξεργάζεται τις πρώτες 100 input rows από το UI.
  Χρησιμοποιεί τον ίδιο processor με το full run και δεν δημιουργεί μόνιμα
  export/rejection αρχεία.
- **Run & export:** χρησιμοποιεί τις τρέχουσες ρυθμίσεις της φόρμας και γράφει
  αρχεία στο παρασκήνιο. Δεν χρειάζεται πρώτα Save.
- **History:** δείχνει εκτελέσεις, μετρητές, αρχεία και αποθηκευμένα diagnostics.

Το preview είναι δείγμα, όχι εγγύηση ότι οι υπόλοιπες γραμμές θα περάσουν.
Χωρίς ORDER BY, οι SQL γραμμές δεν έχουν εγγυημένη σειρά. Ο αριθμός source row
είναι θέση μέσα στη συγκεκριμένη ανάγνωση, όχι primary key της βάσης.

## 3. Τι συμβαίνει σε κάθε πεδίο

Παράδειγμα με αρχική τιμή `"  Αθήνα  "`:

| Στάδιο | Αποτέλεσμα |
|---|---|
| Ανάγνωση | `"  Αθήνα  "` |
| Ρητό trim | `"Αθήνα"` |
| Required | Περνάει |
| Max length 50 | Περνάει |
| Type string | `"Αθήνα"` |
| RowResult | Κρατά ξεχωριστά τις τιμές και τα σφάλματα |
| Export | Γράφει την τελική τιμή σύμφωνα με το output policy |

Οι independent έλεγχοι μπορούν να συγκεντρώσουν περισσότερα από ένα σφάλματα.
Ένας εξαρτώμενος έλεγχος δεν εκτελείται αν απέτυχε η προϋπόθεσή του: π.χ. lookup
δεν γίνεται μετά από αποτυχημένη type conversion.

Η απουσία converted value στα diagnostics σημαίνει ότι δεν ολοκληρώθηκε η
μετατροπή. Δεν είναι το ίδιο με επιτυχημένη μετατροπή σε NULL.

## 4. Εκδόσεις και cleansing

Τα νέα συνηθισμένα drafts είναι **v2**. Τα υπάρχοντα v1 pipelines και το shipped
v1 demo διατηρούν τη δική τους συμπεριφορά. Δεν γίνεται σιωπηρή αναβάθμιση.

Στο v2:

- NULL και `""` παραμένουν διαφορετικά.
- Το string `"003"` διατηρείται. Ως strict int απορρίπτεται.
- Δεν υπάρχει implicit trim, αλλαγή πεζών/κεφαλαίων ή date inference.
- Τα Decimal δεν περνούν ενδιάμεσα από float.
- Invalid conversion δίνει error, όχι αυθαίρετη εναλλακτική τιμή.

| Transform σε production | Τι κάνει |
|---|---|
| `trim` | Αφαιρεί whitespace μόνο από αρχή/τέλος |
| `upper` | Μετατρέπει το κείμενο σε κεφαλαία |
| `lower` | Μετατρέπει το κείμενο σε πεζά |
| `empty_to_null` | Μετατρέπει ακριβώς το `""` σε NULL |

Η σειρά έχει σημασία: `trim → empty_to_null` μπορεί να μετατρέψει whitespace-only
κείμενο σε NULL. Η αντίστροφη σειρά μπορεί να αφήσει empty string.

**Enter → space: πειραματικό, εκτός production.** Το prototype χειρίζεται CRLF
ως μία αλλαγή γραμμής και CR/LF ξεχωριστά. Δεν αφαιρεί tabs ή άλλα spaces.
Δεν έχει ακόμη προστεθεί στα production pipelines, templates ή UI.

Το cleansing δεν είναι απαραίτητο για να είναι έγκυρο ένα CSV. Χρειάζεται μόνο
όπου το απαιτούν οι business rules ή ο importer του προορισμού. Πρώτα ορίζουμε
τι πρέπει να παραμείνει ίδιο και τι επιτρέπεται να αλλάξει.

## 5. Valid, rejected και αποτυχία run

**Valid = πέρασε τους κανόνες που ρυθμίσαμε.** Αν ένα πεδίο είναι προαιρετικό
string χωρίς άλλο έλεγχο, το εργαλείο δεν γνωρίζει αν το περιεχόμενο είναι
business-valid. Ένας πλήρης έλεγχος VAT, για παράδειγμα, δεν προκύπτει μόνο από
την επιλογή string.

Αν ένα πεδίο αποτύχει, απορρίπτεται ολόκληρη η γραμμή και συνεχίζουν οι επόμενες.
Rejected rows δεν σημαίνουν από μόνες τους αποτυχία του run.

**Failed run** σημαίνει πρόβλημα εκτέλεσης: σύνδεση, query, πρόσβαση αρχείου,
δίσκος, ασυμβατότητα export policy κ.λπ. Τα partial outputs δεν παρουσιάζονται
ως ολοκληρωμένο επιτυχές αποτέλεσμα.

Τα v2 rejections αποθηκεύουν:

- source number/position και αρχικές τιμές,
- transformed και επιτυχημένες converted τιμές,
- field, stable error code, stage και μήνυμα.

Ο κύκλος βελτίωσης είναι:

**Απόρριψη → διάγνωση → ρητή διόρθωση δεδομένων ή κανόνων → νέο run.**

Αυτόματη διόρθωση ή επανεκτέλεση μόνο των rejected δεν υπάρχει σήμερα.

## 6. Πού αποθηκεύονται τα πράγματα

Προεπιλεγμένη εγκατάσταση στο `C:\etltool`:

| Θέση | Περιεχόμενο |
|---|---|
| `.env` | Τοπικές ρυθμίσεις σύνδεσης· δεν αντιγράφουμε credentials στα pipelines |
| `data/etl.sqlite3` | Saved pipelines, run history, snapshots, statuses/reports |
| `data/templates/` | JSON templates με αμετάβλητες revisions |
| `data/runs/<μοναδικός φάκελος>/` | Αρχεία συγκεκριμένης single-source εκτέλεσης |
| `data/runs/<run>/<θέση-step>/` | Δημοσιευμένα αποτελέσματα ordered steps |

Single-source output:

```text
valid.csv ή valid.xlsx
rejected.csv
```

Κάθε run έχει νέο φάκελο. Δεν αντικαθιστά το αρχείο προηγούμενου run.
Οι rejected γράφονται σταδιακά, με buffered I/O, όχι όλες μαζί στο τέλος.
Αυτό δεν σημαίνει disk flush ανά γραμμή ή εγγύηση διάσωσης κάθε buffered byte
σε αιφνίδια διακοπή.

Το `rejected.csv` περιέχει `record_number`, `source_json`, `mapped_json`,
`errors_json`. Στο v2 τα JSON δεδομένα διατηρούν ξεχωριστά τα στάδια και τους
τύπους για τα diagnostics. Το ιστορικό τα εμφανίζει χωρίς source reread.
Τα ιστορικά v1 αρχεία δεν έχουν όλα τα v2 στάδια, και ο viewer δεν τα επινοεί.

Τα run snapshots διατηρούν τη συνταγή της εκτέλεσης. Αλλαγή ενός saved pipeline
δεν αλλάζει το ιστορικό. Δεν αποθηκεύεται πλήρες snapshot όλης της πηγής ή όλων
των σταδίων για κάθε valid row.

Το `--data` μπορεί να αλλάξει τη θέση αποθήκευσης. Retention, backup και ανάκτηση
partial single-source diagnostics χρειάζονται ξεχωριστή απόφαση· οι μοναδικοί
φάκελοι δεν αποτελούν από μόνοι τους σύστημα backup.

## 7. Export policies

| Θέμα | Σημερινή v2 συμπεριφορά |
|---|---|
| NULL | Default token `\N`· δεν είναι literal τιμή της πηγής |
| Empty string | Empty field, διακριτό από το NULL token |
| Decimal | Ακριβής δεκαδική αναπαράσταση, χωρίς float ενδιάμεσα |
| Ημερομηνίες/ώρες | Καθορισμένη ISO-style αναπαράσταση |
| CSV | `csv.writer`, configurable delimiter/encoding, σωστό quoting |
| Multiline | Διατηρείται μέσα σε quoted πεδίο |
| XLSX | Write-only εγγραφή, κελιά text για fidelity/identifiers |
| Formula-like CSV strings | Default preserve· apostrophe policy μόνο ρητά |
| Σύγκρουση με NULL token | Σφάλμα αντί για αμφίσημη έξοδο |

Το CSV δεν έχει native NULL type: ο importer πρέπει να συμφωνεί ότι `\N`
σημαίνει NULL. Το Excel δεν είναι επαρκές acceptance test για άλλον importer.
Το v1 έχει διαφορετικά legacy defaults που διατηρούνται για συμβατότητα.

## 8. Templates και ordered pipelines

**Mapping templates:** περιέχουν target structure και κανόνες, όχι source
columns/tables/credentials. Εφαρμόζονται με αντιγραφή σε κενό συμβατό draft.
Οι source/literal bindings είναι ρητές. Μεταγενέστερη αλλαγή ή διαγραφή template
δεν αλλάζει έτοιμα pipelines και runs. Δεν υπάρχει αυτόματο name matching.

**Ordered query pipeline:** πολλά ανεξάρτητα query steps, με μία εγκεκριμένη
connection reference, σε καθορισμένη σειρά. Κάθε step έχει δικά του mappings,
outputs, counts και diagnostics και ανοίγει φρέσκια σύνδεση. Default failure
policy: stop, με επόμενα steps skipped. Υποστηρίζεται και continue.
Ολοκληρωμένα προηγούμενα steps παραμένουν διαθέσιμα αν επόμενο step αποτύχει.

Δεν υπάρχουν cross-step data dependencies, Python joins μεταξύ αποτελεσμάτων,
parallel steps, retries ή resume-from-step.

## 9. Πού ξοδεύεται ο χρόνος

1. Query/ανάγνωση και μεταφορά δεδομένων.
2. Δημιουργία SourceRows και διατήρηση αρχικών τιμών.
3. Processing ανά mapped πεδίο και δημιουργία RowResult.
4. Μορφοποίηση και εγγραφή valid/rejected αρχείων.

Οι SQL γραμμές διαβάζονται σε batches, συνήθως 1.000. Το processing παραμένει
ανά γραμμή/πεδίο: batching ανάγνωσης δεν σημαίνει vectorized transforms.
Δεν κρατάμε όλο το dataset στη RAM. Εξαίρεση είναι τα lookup sets, με όριο
100.000 reference rows ανά lookup, καθώς και μικρά bounded samples/buffers.

### Σημαντικό όριο: απλή table/view πηγή κάνει SELECT *

Αν ο πίνακας έχει 173 columns και κρατήσουμε 10 mappings, διαβάζουμε ακόμη και
τις 173 στήλες, αλλά επεξεργαζόμαστε/εξάγουμε τις 10. Η μείωση mappings δεν
μειώνει αυτόματα όσα μεταφέρονται από τον SQL Server.

Εγκεκριμένο SELECT ή κατάλληλο view μπορεί να περιορίζει rows/columns πριν
περάσουν στην Python. Αυτόματη SQL projection βάσει mappings δεν υπάρχει ακόμη.
Αν εξεταστεί, πρέπει να λάβει υπόψη τα αρχικά diagnostics και όλες τις απαιτούμενες
στήλες — δεν είναι απλή διαγραφή του `*` χωρίς σχεδιασμό.

### Τι γνωρίζουμε από benchmarks

Οι χρόνοι εξαρτώνται από rows × columns, μήκος κειμένου, κανόνες, rejected rate,
format εξόδου, δίσκο, driver και δίκτυο. Ένα εκατομμύριο rows μόνο του δεν
καθορίζει χρόνο εκτέλεσης.

Τοπικό newline benchmark: 1.000.000 rows, ένα column περίπου 100 χαρακτήρων,
10% με CRLF, τρία passes ανά περίπτωση:

- Χωρίς καθαρισμό: median 38,886 sec.
- Με καθαρισμό: median 40,030 sec.
- Διαφορά medians: περίπου +1,144 sec / +2,9%.
- Υπήρχε μεγάλη διακύμανση· αυτό δεν είναι ανώτατο όριο ή SLA.

Βλ. [πρωτόκολλο και μετρήσεις](integration/LINEBREAK_BENCHMARK.md).
Δεν πρόκειται για live SQL benchmark ή για ένα εκατομμύριο rows με 173 columns.

Η δοκιμή ownership για RowResult έμεινε εκτός production: το ασφαλές prototype
δεν πλησίασε τον στόχο 1,59×. Ο δημόσιος defensive-copy constructor παραμένει.
Βλ. [αποτελέσματα review](REVIEW_IMPLEMENTATION_RESULTS.md).

## 10. Χάρτης κώδικα — πού γίνεται τι

| Αρχείο | Ευθύνη |
|---|---|
| [etl/__main__.py](etl/__main__.py) | CLI, serve/preview/run |
| [etl/config.py](etl/config.py) | Φόρτωση workspace environment/σύνδεσης |
| [etl/web.py](etl/web.py) | HTTP API, εκκίνηση runs, downloads |
| [etl/models.py](etl/models.py) | Αμετάβλητα domain models |
| [etl/spec.py](etl/spec.py) | Έλεγχος pipeline configuration |
| [etl/serialization.py](etl/serialization.py) | Μετατροπή JSON ↔ models, typed diagnostics |
| [etl/sources.py](etl/sources.py) | CSV/table sources, schema, batches, cleanup |
| [etl/discovery.py](etl/discovery.py) | Dataset discovery και raw samples |
| [etl/queries.py](etl/queries.py) | Query codec, parser safety και approval |
| [etl/query_source.py](etl/query_source.py) | Bound parameters, query execution/streaming |
| [etl/query_discovery.py](etl/query_discovery.py) | Query metadata και sample |
| [etl/engine.py](etl/engine.py) | Processing orchestration, κοινή preview/full ροή |
| [etl/transforms.py](etl/transforms.py) | Ρητές αλλαγές τιμών |
| [etl/validations.py](etl/validations.py) | Required, max length, lookup |
| [etl/conversions.py](etl/conversions.py) | Μετατροπή/έλεγχος τύπων και v1/v2 διαφορά |
| [etl/exporters.py](etl/exporters.py) | CSV/XLSX representation και rejected writer |
| [etl/diagnostics.py](etl/diagnostics.py) | Παρουσίαση diagnostics και stored pagination |
| [etl/store.py](etl/store.py) | Τοπική SQLite αποθήκευση pipelines/runs |
| [etl/templates.py](etl/templates.py) | Templates, revisions, binding/application |
| [etl/ordered.py](etl/ordered.py) | Ordered definitions, codec και preflight |
| [etl/ordered_runs.py](etl/ordered_runs.py) | Sequential coordinator, status/output publication |
| [etl/static/index.html](etl/static/index.html) | Δομή οθόνης |
| [etl/static/style.css](etl/static/style.css) | Layout, bounded scroll περιοχές |
| [etl/static/app.js](etl/static/app.js) | Βασικός editor, discovery, mappings, actions |
| [etl/static/controls.js](etl/static/controls.js) | Select2, transform controls, bulk actions |
| [etl/static/diagnostics.js](etl/static/diagnostics.js) | Diagnostics viewer |
| [etl/static/templates.js](etl/static/templates.js) | Template UI |
| [etl/static/query.js](etl/static/query.js) | Query UI |
| [etl/static/ordered.js](etl/static/ordered.js) | Ordered pipeline UI |
| [integration/benchmark.py](integration/benchmark.py) | Opt-in benchmark harness |
| [integration/benchmark_linebreaks.py](integration/benchmark_linebreaks.py) | Απομονωμένο Enter-cleaning experiment |
| `tests/` | Python και browser regression/acceptance tests |

Τα `TableExport.php` και `SimpleXLSXGen.php` είναι legacy υλικό αναφοράς. Δεν
αποτελούν μέρος της Python execution διαδρομής.

## 11. Τι κρατάμε, τι βελτιώνουμε, τι αφήνουμε εκτός

| Μέρος | Πρόταση | Λόγος |
|---|---|---|
| SourceRow → RowResult → Exporter | Κρατάμε | Ξεχωρίζει ανάγνωση, processing, representation |
| Κοινός preview/full processor | Κρατάμε | Δεν συντηρούμε δύο διαφορετικές λογικές |
| Streaming/batches | Κρατάμε | Δεν μεγαλώνει η RAM ανάλογα με όλο το dataset |
| v1 compatibility και v2 defaults | Κρατάμε | Νέα σωστή συμπεριφορά χωρίς αλλαγή ιστορικών jobs |
| Explicit transforms, NULL/empty διάκριση | Κρατάμε | Αποφεύγουμε κρυφές αλλοιώσεις |
| Rejected files και snapshots | Κρατάμε | Εξηγήσιμα και ελέγξιμα αποτελέσματα |
| Discovery, templates και bulk actions | Κρατάμε | Μειώνουν χειρωνακτική ρύθμιση |
| Query safety/approved references | Κρατάμε | Δεν υποκαθιστούμε database permissions |
| Simple table SELECT * | Εξετάζουμε με μέτρηση | Μπορεί να μεταφέρει περιττές στήλες |
| Visibility απορρίψεων/αρχείων | Βελτιώνουμε στοχευμένα | Ο χρήστης να βλέπει πού πήγαν οι γραμμές |
| Διακοπές, retention, backup | Σχεδιάζουμε ξεχωριστά | Αποθήκευση δεν σημαίνει ανάκτηση/backup |
| Enter → space | Μόνο αν το χρειάζεται ο προορισμός | Το CSV μπορεί ήδη να κρατήσει multiline |
| RowResult ownership optimization | Δεν υιοθετούμε τώρα | Το measured gain δεν κάλυψε τον συμφωνημένο στόχο |
| Αυτόματο cleansing παντού | Αφήνουμε εκτός | Δεν ξέρουμε ποια whitespace/τιμή έχει σημασία |
| Stored procedures/free EXEC | Αφήνουμε εκτός | Απαιτούν άλλο security/execution contract |
| DAG, parallel steps, dependencies | Αφήνουμε εκτός | Δεν δικαιολογούνται από την τωρινή ανάγκη |
| Legacy PHP | Κρατάμε ως reference προς το παρόν | Διαγραφή μόνο αφού βεβαιωθούμε ότι δεν χρειάζεται |
| Παλιά αντικρουόμενη τεκμηρίωση | Διορθώνουμε | Όχι άλλη σύγχυση από ξεπερασμένες οδηγίες |

**Δεν προτείνεται διαγραφή core λειτουργιών τώρα.** Πρώτα επιβεβαιώνουμε τι
χρειάζεται η πραγματική δουλειά. «Εκτός» σημαίνει ότι δεν το επεκτείνουμε σε αυτό
το βήμα, όχι ότι σβήνουμε αρχεία χωρίς απόφαση.

## 12. Επόμενο πρακτικό βήμα

1. Διαλέγουμε έναν πραγματικό προορισμό εισαγωγής και τις απαιτήσεις του.
2. Ορίζουμε τα απαραίτητα πεδία, types, NULL policy και business validations.
3. Φτιάχνουμε μικρό acceptance dataset: NULL, empty, whitespace, Enter, ελληνικά,
   quotes/delimiters, leading zeros, decimals και σκόπιμα invalid values.
4. Κάνουμε export και πραγματικό import στον προορισμό.
5. Μετράμε read/process/write σε εγκεκριμένο αντιπροσωπευτικό workload.
6. Διορθώνουμε μόνο το αποδεδειγμένο πρόβλημα, με tests και επανάληψη της μέτρησης.

Μέχρι τότε δεν χρειάζεται νέο γενικό architecture refactor. Προτεραιότητα:
**σωστή μεταφορά → εξήγηση απορρίψεων → εύκολη επανάληψη → μετρημένη ταχύτητα.**

## 13. Σημείωση για την τεκμηρίωση

Το README περιέχει παλιές παραγράφους για auto-mappings, παλαιά empty/null
semantics και αρχικό SQL scope, παρά τις μεταγενέστερες προσθήκες. Δεν πρέπει
να εκλαμβάνονται ως πλήρης περιγραφή του σημερινού v2 προϊόντος.
Χρειάζεται μελλοντικός συγχρονισμός· αυτό το αρχείο δεν ισχυρίζεται ότι έχει γίνει.

Σχετικά αρχεία:

- [Data-flow note με πραγματικό BRANDS παράδειγμα](DEVELOPER_NOTE_DATA_FLOW.md)
- [Frontend workflow](FRONTEND_WORKFLOW.md)
- [Processing diagnostics](PROCESSING_DIAGNOSTICS.md)
- [Query sources](READ_ONLY_QUERY_SOURCES.md)
- [Implementation plan](IMPLEMENTATION_PLAN.md)
- [Review results](REVIEW_IMPLEMENTATION_RESULTS.md)
