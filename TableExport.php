<?php
// table_view_select_columns.php — PURE PHP (no JS)
// - Per-table Select All / Unselect All
// - LEFT JOINs with selectable left table (base or previously joined) + Cancel per join
// - Preview: Show TOP 100 / Show All
// - Export: CSV / XLSX (SimpleXLSXGen.php in same folder)
// - NEW: User-defined column aliases (headers show only the alias)

// includes
@include_once __DIR__ . '/connection.php';     // DB1 -> $conn (READ/EXPORT ONLY)

// --- AJAX endpoint: fetch columns for a given schema+table ---
if (isset($_POST['ajax']) && $_POST['ajax'] === 'cols') {
    header('Content-Type: application/json; charset=utf-8');

    $schema = $_POST['schema'] ?? 'dbo';
    $table  = $_POST['table']  ?? '';

    $cols = [];
    if ($table !== '') {
        $stmt = sqlsrv_query(
            $conn,
            "SELECT c.name
             FROM sys.columns c
             JOIN sys.tables  t ON t.object_id = c.object_id
             JOIN sys.schemas s ON s.schema_id = t.schema_id
             WHERE s.name = ? AND t.name = ?
             ORDER BY c.column_id",
            [$schema, $table]
        );
        if ($stmt === false) {
            http_response_code(500);
            echo json_encode(['error' => sqlsrv_errors()], JSON_UNESCAPED_UNICODE);
            exit;
        }
        while ($r = sqlsrv_fetch_array($stmt, SQLSRV_FETCH_NUMERIC)) $cols[] = $r[0];
        sqlsrv_free_stmt($stmt);
    }

    echo json_encode(['columns' => $cols], JSON_UNESCAPED_UNICODE);
    exit;
}


@include_once __DIR__ . '/connection2db.php';  // DB2 -> $conn2 (SAVE/LOAD ONLY)
require_once __DIR__ . '/SimpleXLSXGen.php';


$saveError = '';
$saveOk = false;
$savedPresets = [];
$invalidTotal = null;

// ---- Feature flags ----
const UI_DISABLE_JOINS = true;   // hide + ignore LEFT JOIN UI and logic
const UI_DISABLE_WHERE = true;   // hide + ignore WHERE UI and logic


// ---- Logging safety knobs ----
const MAX_INVALIDS_TO_LOG = 100000;   // cap how many invalid rows you INSERT per run
const LOG_INVALIDS_MODE   = 'cap';    // 'cap' | 'count-only' | 'off'

// Pack the current UI state into an array we will JSON-encode
function pack_state(): array {
    return [
        'SCHEMA'         => $GLOBALS['SCHEMA'],
        'baseTable'      => $GLOBALS['baseTable'],
        'joinCount'      => $GLOBALS['joinCount'],
        'joinLeftTable'  => $GLOBALS['joinLeftTable'],
        'joinLeftCol'    => $GLOBALS['joinLeftCol'],
        'joinRightTable' => $GLOBALS['joinRightTable'],
        'joinRightCol'   => $GLOBALS['joinRightCol'],

        'joinOnLeftCol'  => $GLOBALS['joinOnLeftCol'],
        'joinOnOp'       => $GLOBALS['joinOnOp'],
        'joinOnRight'    => $GLOBALS['joinOnRight'],
        'joinOnIsLit'    => $GLOBALS['joinOnIsLit'],
        'joinOnLitVal'   => $GLOBALS['joinOnLitVal'],

        'whereTable'     => $GLOBALS['whereTable'],
        'whereCol'       => $GLOBALS['whereCol'],
        'whereOp'        => $GLOBALS['whereOp'],
        'whereVal'       => $GLOBALS['whereVal'],

        // Output columns builder
        'outNames'       => $GLOBALS['outNames'],
		'outSources'     => $GLOBALS['outSources'],
		'outIsLit'       => $GLOBALS['outIsLit'],   
		'outLitVal'      => $GLOBALS['outLitVal'],  

        // legacy selections & aliases (still useful)
        'selectedCols'   => $GLOBALS['selectedCols'],
        'aliasInput'     => $GLOBALS['aliasInput'],
		'outRule'        => $GLOBALS['outRule'],   // NEW rules
		'outType'        => $GLOBALS['outType'],
		'outMaxLen'      => $GLOBALS['outMaxLen'],
		// pack_state()
		'outExistsSrc'   => $GLOBALS['outExistsSrc'],


    ];
}

// Load a state array into the current POST-backed variables (so the UI repopulates)
function apply_state(array $st): void {
    $GLOBALS['SCHEMA']         = (string)($st['SCHEMA'] ?? $GLOBALS['SCHEMA']);
    $GLOBALS['baseTable']      = (string)($st['baseTable'] ?? '');
    $GLOBALS['joinCount']      = (int)($st['joinCount'] ?? 0);

    $GLOBALS['joinLeftTable']  = (array)($st['joinLeftTable']  ?? []);
    $GLOBALS['joinLeftCol']    = (array)($st['joinLeftCol']    ?? []);
    $GLOBALS['joinRightTable'] = (array)($st['joinRightTable'] ?? []);
    $GLOBALS['joinRightCol']   = (array)($st['joinRightCol']   ?? []);

    $GLOBALS['joinOnLeftCol']  = (array)($st['joinOnLeftCol']  ?? []);
    $GLOBALS['joinOnOp']       = (array)($st['joinOnOp']       ?? []);
    $GLOBALS['joinOnRight']    = (array)($st['joinOnRight']    ?? []);
    $GLOBALS['joinOnIsLit']    = (array)($st['joinOnIsLit']    ?? []);
    $GLOBALS['joinOnLitVal']   = (array)($st['joinOnLitVal']   ?? []);

    $GLOBALS['whereTable']     = (array)($st['whereTable'] ?? []);
    $GLOBALS['whereCol']       = (array)($st['whereCol']   ?? []);
    $GLOBALS['whereOp']        = (array)($st['whereOp']    ?? []);
    $GLOBALS['whereVal']       = (array)($st['whereVal']   ?? []);

    $GLOBALS['outNames']   = (array)($st['outNames']   ?? []);
	$GLOBALS['outSources'] = (array)($st['outSources'] ?? []);
	$GLOBALS['outIsLit']   = (array)($st['outIsLit']   ?? []);  
	$GLOBALS['outLitVal']  = (array)($st['outLitVal']  ?? []);  


    $GLOBALS['selectedCols']   = (array)($st['selectedCols'] ?? []);
    $GLOBALS['aliasInput']     = (array)($st['aliasInput']   ?? []);
	$GLOBALS['outRule'] = (array)($st['outRule'] ?? []);   // NEW
	$GLOBALS['outType']   = (array)($st['outType']   ?? []);
	$GLOBALS['outMaxLen'] = (array)($st['outMaxLen'] ?? []);
	// apply_state($st)
	$GLOBALS['outExistsSrc'] = (array)($st['outExistsSrc'] ?? []);


}


$ASSETS = rtrim(dirname($_SERVER['PHP_SELF'] ?? ''), '/\\') . '/assets';



if (!isset($conn) || $conn === false) {
    die("<h1>Connection error</h1><pre>" . print_r(sqlsrv_errors(), true) . "</pre>");
}
ini_set('display_errors', '1'); error_reporting(E_ALL);

// Optional: allow longer script runtime for exports only (we still set SQL timeouts).
// You can bump this later if needed.
// set_time_limit(60);

// Make SELECTs non-blocking and fail fast on locks for the viewer connection
sqlsrv_query($conn, "SET NOCOUNT ON; SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED; SET LOCK_TIMEOUT 5000;");

// Do the same for the save/load connection if it exists (safe even if it's not set)



// Small helper so every query has a timeout
function q($conn, string $sql, array $params = [], int $timeout = 25) {
    // Adjust default timeout above (25s) if you like
    return sqlsrv_query($conn, $sql, $params, ['QueryTimeout' => $timeout]);
}



@set_time_limit(300);
ini_set('max_execution_time', '300');

$QOPTS = ["Scrollable" => SQLSRV_CURSOR_FORWARD, "QueryTimeout" => 0];

function escIdent(string $s): string { return '[' . str_replace(']', ']]', $s) . ']'; }


function getTableCols(string $table): array {
    static $cache = [];               // per-request only
    global $conn, $SCHEMA;
    if ($table === '') return [];
    if (isset($cache[$SCHEMA][$table])) return $cache[$SCHEMA][$table];

    $cols = [];
    $stmt = sqlsrv_query(
        $conn,
        "SELECT c.name AS COLUMN_NAME
         FROM sys.columns AS c
         INNER JOIN sys.tables  AS t2 ON t2.object_id = c.object_id
         INNER JOIN sys.schemas AS s  ON s.schema_id  = t2.schema_id
         WHERE s.name = ? AND t2.name = ?
         ORDER BY c.column_id",
        [$SCHEMA, $table]
    );
    if ($stmt) {
        while ($r = sqlsrv_fetch_array($stmt, SQLSRV_FETCH_ASSOC)) {
            $cols[] = $r['COLUMN_NAME'];
        }
        sqlsrv_free_stmt($stmt);
    }
    return $cache[$SCHEMA][$table] = $cols;
}



$SCHEMA = 'dbo';
/* ---------- Load tables ---------- */
$tables = [];
$stmt = sqlsrv_query(
    $conn,
    "SELECT t.name AS TABLE_NAME
     FROM sys.tables AS t
     INNER JOIN sys.schemas AS s ON s.schema_id = t.schema_id
     WHERE s.name = ?
     ORDER BY t.name",
    [$SCHEMA]
);
if ($stmt) {
    while ($r = sqlsrv_fetch_array($stmt, SQLSRV_FETCH_ASSOC)) {
        $tables[] = $r['TABLE_NAME'];
    }
    sqlsrv_free_stmt($stmt);
}



/* ---------- POST state ---------- */
$baseTable         = $_POST['table'] ?? '';
$joinCount         = (int)($_POST['join_count'] ?? 0);

/* LEFT JOIN definition: LEFT JOIN [right_table] ON [left_table].[left_col] = [right_table].[right_col] */
$joinLeftTable     = $_POST['join_left_table']  ?? []; // left table from allowed scope
$joinLeftCol       = $_POST['join_left_col']    ?? [];
$joinRightTable    = $_POST['join_right_table'] ?? []; // any table
$joinRightCol      = $_POST['join_right_col']   ?? [];


/* ---------- JOIN extra ON rows (per join) ---------- */
// arrays of arrays: index 0..joinCount-1, each contains rows for that join
$joinOnLeftCol = $_POST['join_on_left_col'] ?? [];   // join_on_left_col[i][]  (column from left table)
$joinOnOp      = $_POST['join_on_op']      ?? [];    // join_on_op[i][]
$joinOnRight   = $_POST['join_on_right']   ?? [];    // join_on_right[i][]     (column from right table OR literal flag)
$joinOnIsLit   = $_POST['join_on_is_lit']  ?? [];    // join_on_is_lit[i][]    ("1" if right side is literal)
$joinOnLitVal  = $_POST['join_on_lit_val'] ?? [];    // join_on_lit_val[i][]   (literal value when is_lit=1)

/* ---------- WHERE builder ---------- */
$whereTable = $_POST['where_table'] ?? []; // table name in scope (or empty for “any”)
$whereCol   = $_POST['where_col']   ?? []; // column name
$whereOp    = $_POST['where_op']    ?? []; // =, <>, >, <, >=, <=, LIKE, NOT LIKE, IN, NOT IN, BETWEEN, IS NULL, IS NOT NULL
$whereVal   = $_POST['where_val']   ?? []; // value text (supports comma for IN, a..b for BETWEEN)


/* Column selections (values = "Table|Column") */
$selectedCols      = $_POST['cols'] ?? [];

/* Column alias inputs: alias["Table|Column"] = "Alias" */
/* Column alias inputs (posted as parallel arrays to avoid weird chars in keys) */
$aliasKeys = $_POST['alias_key'] ?? [];   // each value == "Table|Column"
$aliasVals = $_POST['alias_val'] ?? [];   // each value == alias string
$aliasInput = [];
if (is_array($aliasKeys) && is_array($aliasVals)) {
    $n = min(count($aliasKeys), count($aliasVals));
    for ($i = 0; $i < $n; $i++) {
        $k = (string)$aliasKeys[$i];
        $v = (string)$aliasVals[$i];
        $aliasInput[$k] = $v;
    }
}


/* Actions */
$action_refresh    = isset($_POST['refresh']);
$action_addjoin    = isset($_POST['add_join']);
$action_top        = isset($_POST['show_top']);
$action_all        = isset($_POST['show_all']);
$action_csv        = isset($_POST['download_csv']);
$action_xlsx       = isset($_POST['download_xlsx']);
$action_err_csv  = isset($_POST['download_errors_csv']);
$action_err_xlsx = isset($_POST['download_errors_xlsx']);
$selectAllReq      = $_POST['select_all']     ?? []; // ['TableName' => '1']
$unselectAllReq    = $_POST['unselect_all']   ?? []; // ['TableName' => '1']
$removeJoinIndex   = isset($_POST['remove_join']) ? (int)$_POST['remove_join'] : -1;


$action_savePreset = isset($_POST['save_preset']);
$action_loadPreset = isset($_POST['load_preset']);
$action_deletePreset = isset($_POST['delete_preset']);
$presetName        = trim((string)($_POST['preset_name'] ?? ''));
$presetIdToLoad    = (int)($_POST['preset_id'] ?? 0);
$presetIdToDelete   = (int)($_POST['preset_id'] ?? 0);
$action_clearUI = isset($_POST['clear_ui']);
$action_count = isset($_POST['count_rows']);
// decide the action label for logging (used in z0ErrorsExport)
$runAction = $action_xlsx ? 'xlsx'
           : ($action_csv ? 'csv'
           : ($action_all ? 'all'
           : ($action_top ? 'top' : 'preview')));

// one token per click/run to group its invalid rows
$runToken = bin2hex(random_bytes(16)); // 32 hex chars




$selectionTouched = isset($_POST['cols']) || isset($_POST['select_all']) || isset($_POST['unselect_all']) ||
                    isset($_POST['show_top']) || isset($_POST['show_all']) ||
                    isset($_POST['download_csv']) || isset($_POST['download_xlsx']) || isset($_POST['refresh']);

//reads the posts 
// collect Output Columns builder arrays
$outNames   = $_POST['out_name']   ?? [];
$outSources = $_POST['out_source'] ?? [];
$outIsLit   = $_POST['out_is_lit'] ?? [];   // NEW: checkbox per row (indexed by row)
$outLitVal  = $_POST['out_lit_val'] ?? [];  // NEW: value per row
$outRule = $_POST['out_rule'] ?? [];   // NEW: rule per output row




$outType   = $_POST['out_type']   ?? [];   // NEW: type per output row
$outMaxLen = $_POST['out_maxlen'] ?? [];   // NEW: optional max length (for nvarchar)

$outExistsSrc = $_POST['out_exists_src'] ?? [];   // each value "Table|Column"
if (!is_array($outExistsSrc)) $outExistsSrc = [];


// If joins are disabled, force-empty everything related
if (UI_DISABLE_JOINS) {
    $joinCount = 0;
    $joinLeftTable = $joinLeftCol = $joinRightTable = $joinRightCol = [];
    $joinOnLeftCol = $joinOnOp = $joinOnRight = $joinOnIsLit = $joinOnLitVal = [];
}

// If WHERE is disabled, force-empty filters
if (UI_DISABLE_WHERE) {
    $whereTable = $whereCol = $whereOp = $whereVal = [];
}




function reset_state_to_blank(): void {
    // base state: nothing selected
    $GLOBALS['baseTable']      = '';
    $GLOBALS['joinCount']      = 0;

    $GLOBALS['joinLeftTable']  = [];
    $GLOBALS['joinLeftCol']    = [];
    $GLOBALS['joinRightTable'] = [];
    $GLOBALS['joinRightCol']   = [];

    $GLOBALS['joinOnLeftCol']  = [];
    $GLOBALS['joinOnOp']       = [];
    $GLOBALS['joinOnRight']    = [];
    $GLOBALS['joinOnIsLit']    = [];
    $GLOBALS['joinOnLitVal']   = [];

    $GLOBALS['whereTable']     = [];
    $GLOBALS['whereCol']       = [];
    $GLOBALS['whereOp']        = [];
    $GLOBALS['whereVal']       = [];

    $GLOBALS['selectedCols']   = [];
    $GLOBALS['aliasInput']     = [];

    $GLOBALS['outNames']   = [];
    $GLOBALS['outSources'] = [];
    $GLOBALS['outIsLit']   = [];
    $GLOBALS['outLitVal']  = [];
    $GLOBALS['outRule']    = [];
    $GLOBALS['outType']    = [];
    $GLOBALS['outMaxLen']  = [];
}

if ($action_clearUI) {
    reset_state_to_blank();
    // also clear any built SQL / results so the page shows blank
    $rows = [];
    $builtSql = '';
    $error = '';
}





if (!is_array($outType))   $outType = [];
if (!is_array($outMaxLen)) $outMaxLen = [];

if (!is_array($outRule)) $outRule = [];


if (!is_array($outNames))   $outNames   = [];
if (!is_array($outSources)) $outSources = [];
if (!is_array($outIsLit))   $outIsLit   = [];
if (!is_array($outLitVal))  $outLitVal  = [];



/* Normalize arrays */
if (!is_array($aliasKeys)) $aliasKeys = [];
if (!is_array($aliasVals)) $aliasVals = [];

foreach (['joinLeftTable','joinLeftCol','joinRightTable','joinRightCol','selectedCols','aliasInput'] as $var) {
    if (!is_array($$var)) $$var = [];
}
if ($action_addjoin) $joinCount++;
if ($removeJoinIndex >= 0) {
    // remove one join row across arrays
    if (isset($joinLeftTable[$removeJoinIndex]))  array_splice($joinLeftTable,  $removeJoinIndex, 1);
    if (isset($joinLeftCol[$removeJoinIndex]))    array_splice($joinLeftCol,    $removeJoinIndex, 1);
    if (isset($joinRightTable[$removeJoinIndex])) array_splice($joinRightTable, $removeJoinIndex, 1);
    if (isset($joinRightCol[$removeJoinIndex]))   array_splice($joinRightCol,   $removeJoinIndex, 1);
	
	if (isset($joinOnLeftCol[$removeJoinIndex])) array_splice($joinOnLeftCol, $removeJoinIndex, 1);
    if (isset($joinOnOp[$removeJoinIndex]))      array_splice($joinOnOp,      $removeJoinIndex, 1);
    if (isset($joinOnRight[$removeJoinIndex]))   array_splice($joinOnRight,   $removeJoinIndex, 1);
    if (isset($joinOnIsLit[$removeJoinIndex]))   array_splice($joinOnIsLit,   $removeJoinIndex, 1);
    if (isset($joinOnLitVal[$removeJoinIndex]))  array_splice($joinOnLitVal,  $removeJoinIndex, 1);
	
    $joinCount = max(0, $joinCount - 1);
}
if ($joinCount < 0) $joinCount = 0;



/* ---------- PRESETS: load list + load action + save action (DB2 via $conn2) ---------- */

// 1) Fetch saved presets list (for the dropdown)
if (isset($conn2) && $conn2) {
    $ps = sqlsrv_query(
        $conn2,
        "SELECT id, name, created_at
         FROM dbo.z0SaveTableExport
         ORDER BY created_at DESC"
    );
    if ($ps) {
        while ($row = sqlsrv_fetch_array($ps, SQLSRV_FETCH_ASSOC)) {
            $savedPresets[] = $row;
        }
        sqlsrv_free_stmt($ps);
    }
}


// DELETE preset
if ($action_deletePreset && $presetIdToDelete > 0 && isset($conn2) && $conn2) {
    $ok = sqlsrv_query(
        $conn2,
        "DELETE FROM dbo.z0SaveTableExport WHERE id = ?",
        [$presetIdToDelete]
    );
    if ($ok === false) {
        $saveError = "Delete failed: " . print_r(sqlsrv_errors(), true);
    } else {
        $saveOk = true;

        // Refresh the dropdown list after deletion
        $savedPresets = [];
        $ps = sqlsrv_query(
            $conn2,
            "SELECT id, name, created_at FROM dbo.z0SaveTableExport ORDER BY created_at DESC"
        );
        if ($ps) {
            while ($row = sqlsrv_fetch_array($ps, SQLSRV_FETCH_ASSOC)) $savedPresets[] = $row;
            sqlsrv_free_stmt($ps);
        }

        // Optionally clear current UI state if it was the one you deleted (no-op is fine)
        if ($presetIdToDelete === $presetIdToLoad) {
            // e.g. $presetIdToLoad = 0;
        }
    }
}


// 2) LOAD preset: apply saved payload to current state
if ($action_loadPreset && $presetIdToLoad > 0 && isset($conn2) && $conn2) {
    $ps = sqlsrv_query(
        $conn2,
        "SELECT payload FROM dbo.z0SaveTableExport WHERE id = ?",
        [$presetIdToLoad]
    );
    if ($ps && ($row = sqlsrv_fetch_array($ps, SQLSRV_FETCH_ASSOC))) {
        $st = json_decode((string)$row['payload'], true);
        if (is_array($st)) {
            apply_state($st);
			
			// Re-load tables for the (possibly) new $SCHEMA from the preset
$tables = [];
$stmt = sqlsrv_query(
    $conn,
    "SELECT t.name AS TABLE_NAME
     FROM sys.tables AS t
     INNER JOIN sys.schemas AS s ON s.schema_id = t.schema_id
     WHERE s.name = ?
     ORDER BY t.name",
    [$SCHEMA]
);
if ($stmt) {
    while ($r = sqlsrv_fetch_array($stmt, SQLSRV_FETCH_ASSOC)) $tables[] = $r['TABLE_NAME'];
    sqlsrv_free_stmt($stmt);
}

// Recompute validity with the fresh list
$validBase = $baseTable !== '' && in_array($baseTable, $tables, true);



            // NOTE: variables (baseTable, joins, where, etc.) are now replaced from the preset.
            // The rest of the page (scope/validBase/etc.) will honor these values.
        } else {
            $saveError = "Load failed: invalid JSON payload.";
        }
    } else {
        $saveError = "Load failed: " . print_r(sqlsrv_errors(), true);
    }
    if ($ps) sqlsrv_free_stmt($ps);
}

// 3) SAVE preset: store current UI state json (last_sql optional)
if ($action_savePreset && isset($conn2) && $conn2) {
    if ($presetName === '') {
        $saveError = "Please provide a name for the preset.";
    } else {
        $payload = json_encode(pack_state(), JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES);
        $ok = sqlsrv_query(
            $conn2,
            "INSERT INTO dbo.z0SaveTableExport (name, schema_name, base_table, payload, last_sql)
             VALUES (?, ?, ?, ?, ?)",
            [$presetName, $SCHEMA, $baseTable ?: null, $payload, null] // last_sql stored as NULL (optional)
        );
        if ($ok === false) {
            $saveError = "Save failed: " . print_r(sqlsrv_errors(), true);
        } else {
            $saveOk = true;
            // refresh the dropdown list
            $savedPresets = [];
            $ps = sqlsrv_query(
                $conn2,
                "SELECT id, name, created_at FROM dbo.z0SaveTableExport ORDER BY created_at DESC"
            );
            if ($ps) {
                while ($row = sqlsrv_fetch_array($ps, SQLSRV_FETCH_ASSOC)) $savedPresets[] = $row;
                sqlsrv_free_stmt($ps);
            }
        }
    }
}



$rows = []; $builtSql = ''; $error = '';
$validBase = $baseTable !== '' && in_array($baseTable, $tables, true);

/* ---------- helpers ---------- */
function addTableColsToSelection(array $current, string $table): array {
    foreach (getTableCols($table) as $c) {
        $key = $table . '|' . $c;
        if (!in_array($key, $current, true)) $current[] = $key;
    }
    return $current;
}
function removeTableColsFromSelection(array $current, string $table): array {
    $remove = array_flip(array_map(fn($c)=>$table.'|'.$c, getTableCols($table)));
    return array_values(array_filter($current, fn($k)=>!isset($remove[$k])));
}


/* Make aliases from input; default to column name; force uniqueness; allow safe chars. */
function build_aliases(array $finalCols, array $aliasInput): array {
    $aliases = [];
    $used    = [];
    foreach ($finalCols as $ck) {
        [$t,$c] = explode('|', $ck, 2);
        $raw = trim((string)($aliasInput[$ck] ?? $c)); // default to just the column name
        // sanitize: keep letters/digits/_/space/- (you can relax this if needed)
        $san = preg_replace('/[^A-Za-z0-9 _-]/', '', $raw);
        if ($san === '') $san = $c;
        $base = $san;
        $name = $base;
        $i = 2;
        while (isset($used[strtolower($name)])) { $name = $base . '_' . $i; $i++; }
        $used[strtolower($name)] = true;
        $aliases[$ck] = $name;
    }
    return $aliases; // map: "Table|Column" => "Alias"
}

/* ---------- Scope tables for UI (base + selected join tables) ---------- */

$scopeTables = [];
if ($validBase) {
    $scopeTables[] = $baseTable;
    if (!UI_DISABLE_JOINS) {
        for ($i=0; $i<$joinCount; $i++) {
            $lt = trim((string)($joinLeftTable[$i]  ?? ''));
            $rt = trim((string)($joinRightTable[$i] ?? ''));
            if ($lt !== '' && in_array($lt, $tables, true)) $scopeTables[] = $lt;
            if ($rt !== '' && in_array($rt, $tables, true)) $scopeTables[] = $rt;
        }
    } else {
        // belt & suspenders
        $joinCount = 0;
        $joinLeftTable = $joinLeftCol = $joinRightTable = $joinRightCol = [];
        $joinOnLeftCol = $joinOnOp = $joinOnRight = $joinOnIsLit = $joinOnLitVal = [];
    }   
}       

$scopeTables = array_values(array_unique(array_filter($scopeTables)));




/* Apply per-table select/unselect */
if (!empty($selectAllReq)) {
    $t = array_key_first($selectAllReq);
    if ($t && in_array($t, $scopeTables, true)) $selectedCols = addTableColsToSelection($selectedCols, $t);
}
if (!empty($unselectAllReq)) {
    $t = array_key_first($unselectAllReq);
    if ($t && in_array($t, $scopeTables, true)) $selectedCols = removeTableColsFromSelection($selectedCols, $t);
}



// rules function 

function rule_sql_for_alias(string $alias, string $rule, ?string $rhsTable = null, ?string $rhsCol = null): array {
    $a = '[' . str_replace(']', ']]', $alias) . ']';
    switch ($rule) {
        case 'mandatory':
            $v = "$a IS NOT NULL AND LTRIM(RTRIM(CONVERT(NVARCHAR(MAX),$a))) <> ''";
            return [$v, "'$alias: mandatory'"];

        case 'dateformat':
            $v = "TRY_CONVERT(date,$a,23) IS NOT NULL AND CONVERT(char(10), TRY_CONVERT(date,$a,23), 23) = $a";
            return [$v, "'$alias: dateformat'"];

        case 'exists_in':
            if (!$rhsTable || !$rhsCol) return ['1=1', "''"];
            global $SCHEMA; // <<< add this
            $t = escIdent($SCHEMA) . '.' . escIdent($rhsTable); // <<< schema-qualify
            $c = escIdent($rhsCol);
            $v = "EXISTS (SELECT 1 FROM $t WITH (NOLOCK) WHERE $t.$c = $a)";
            return [$v, "'$alias: value not in lookup'"];

        default:
            return ['1=1', "''"];
    }
}




function type_sql_for_alias(string $alias, string $type, ?string $maxLen): array {
    // returns [validExpr, errorMsgExpr]
    $a = '[' . str_replace(']', ']]', $alias) . ']';
    switch ($type) {
        case 'int':
            // strict: string must round-trip as integer
            $valid = "TRY_CONVERT(BIGINT, $a) IS NOT NULL
                      AND CONVERT(NVARCHAR(100), TRY_CONVERT(BIGINT, $a)) = LTRIM(RTRIM(CONVERT(NVARCHAR(MAX), $a)))";
            $err   = "'$alias: not integer'";
            return [$valid, $err];

        case 'decimal':
            $valid = "TRY_CONVERT(DECIMAL(38,10), $a) IS NOT NULL";
            $err   = "'$alias: not decimal'";
            return [$valid, $err];

        case 'float':
            $valid = "TRY_CONVERT(FLOAT, $a) IS NOT NULL";
            $err   = "'$alias: not float'";
            return [$valid, $err];

        case 'bit':
            // allows 0/1/true/false-like inputs that TRY_CONVERT(bit, ...) accepts
            $valid = "TRY_CONVERT(BIT, $a) IS NOT NULL";
            $err   = "'$alias: not bit (0/1)'";
            return [$valid, $err];

        case 'date':
            // strict yyyy-mm-dd (style 23) same as your dateformat
            $valid = "TRY_CONVERT(date, $a, 23) IS NOT NULL
                      AND CONVERT(char(10), TRY_CONVERT(date, $a, 23), 23) = $a";
            $err   = "'$alias: not yyyy-mm-dd date'";
            return [$valid, $err];

        case 'datetime2':
            $valid = "TRY_CONVERT(datetime2, $a) IS NOT NULL";
            $err   = "'$alias: not datetime'";
            return [$valid, $err];

        case 'nvarchar':
            if ($maxLen !== null && $maxLen !== '' && ctype_digit($maxLen)) {
                // LEN counts characters (works for NVARCHAR)
                $valid = "LEN(CONVERT(NVARCHAR(MAX), $a)) <= ".(int)$maxLen;
                $err   = "'$alias: length > $maxLen'";
                return [$valid, $err];
            }
            return ['1=1', "''"]; // nvarchar with no length limit always passes

        default:
            return ['1=1', "''"]; // no type specified
    }
}









// build_query (supports Output Columns builder)
function build_query(
		array $tables, string $baseTable,
		array $joinLeftTable, array $joinLeftCol, array $joinRightTable, array $joinRightCol, int $joinCount,
		array $selectedCols, array $aliasInput, bool $allRows,
		string &$builtSqlOut, array &$aliasesOut,
		array $outNames = [], array $outSources = [], array $outIsLit = [], array $outLitVal = [],
		array $outRule = [], array $outType = [], array $outMaxLen = [],
		array $joinOnLeftCol = [], array $joinOnOp = [], array $joinOnRight = [], array $joinOnIsLit = [], array $joinOnLitVal = [],
		array $whereTable = [], array $whereCol = [], array $whereOp = [], array $whereVal = [],
		int $invalidLimit = 0,
		array $outExistsSrc = []          // <— NEW
	) : array {


    if ($baseTable === '' || !in_array($baseTable, $tables, true)) {
        throw new RuntimeException("Invalid base table.");
    }

    $params = [];

    // Validate joins (left must be base or previously-joined right)
    $joins = []; // each: [lt, lc, rt, rc, extraOnClauses[]]
    $allowedLeft = [$baseTable];
    for ($i=0; $i<$joinCount; $i++) {
        $lt = trim((string)($joinLeftTable[$i]  ?? ''));
        $lc = trim((string)($joinLeftCol[$i]    ?? ''));
        $rt = trim((string)($joinRightTable[$i] ?? ''));
        $rc = trim((string)($joinRightCol[$i]   ?? ''));

        if ($lt==='' && $lc==='' && $rt==='' && $rc==='') continue;

        if (!in_array($lt, $allowedLeft, true)) throw new RuntimeException("Invalid left table at join ".($i+1));
        if (!in_array($rt, $tables, true))     throw new RuntimeException("Invalid right table at join ".($i+1));
        if (!in_array($lc, getTableCols($lt), true)) throw new RuntimeException("Invalid left column at join ".($i+1));
        if (!in_array($rc, getTableCols($rt), true)) throw new RuntimeException("Invalid right column at join ".($i+1));

        // extra ON rows for this join
        $extra = [];
        $rows  = max(
            count($joinOnLeftCol[$i] ?? []),
            count($joinOnOp[$i] ?? []),
            count($joinOnRight[$i] ?? []),
            count($joinOnIsLit[$i] ?? []),
            count($joinOnLitVal[$i] ?? [])
        );
        for ($k=0; $k<$rows; $k++) {
            $lcol = trim((string)(($joinOnLeftCol[$i] ?? [])[$k] ?? ''));
            $op   = trim((string)(($joinOnOp[$i]      ?? [])[$k] ?? ''));
            $rsel = trim((string)(($joinOnRight[$i]   ?? [])[$k] ?? ''));
            $isLt = (string)(($joinOnIsLit[$i] ?? [])[$k] ?? '') === '1';
            $lit  = (string)(($joinOnLitVal[$i] ?? [])[$k] ?? '');

            if ($lcol==='' || $op==='') continue;
            if (!in_array($lcol, getTableCols($lt), true)) throw new RuntimeException("Join ".($i+1).": invalid left column in ON");

            if ($isLt) {
                // left_col OP ?
                $extra[] = escIdent($lt).'.'.escIdent($lcol) . " $op ?";
                $params[] = $lit;
            } else {
                if ($rsel==='') continue;
                if (!in_array($rsel, getTableCols($rt), true)) throw new RuntimeException("Join ".($i+1).": invalid right column in ON");
                // left_col OP right_col
                $extra[] = escIdent($lt).'.'.escIdent($lcol) . " $op " . escIdent($rt).'.'.escIdent($rsel);
            }
        }

        $joins[] = [$lt,$lc,$rt,$rc,$extra];
        if (!in_array($rt, $allowedLeft, true)) $allowedLeft[] = $rt;
    }

    // Allowed scope
    $allScope = [$baseTable];
    foreach ($joins as [$lt,$lc,$rt]) { $allScope[]=$lt; $allScope[]=$rt; }
    $allScope = array_values(array_unique($allScope));
    $allow = [];
    foreach ($allScope as $t) foreach (getTableCols($t) as $c) $allow["$t|$c"] = true;

    // SELECT list (keeps your Output Columns builder first, fallback to checkbox path)
    $selectParts = [];
    $aliasesOut  = [];

$useOutBuilder = false;

	// iterate over the longest of the four arrays (name, source, isLit, litVal)
	$rowMax = max(count($outNames), count($outSources), count($outIsLit), count($outLitVal));
	for ($i = 0; $i < $rowMax; $i++) {
		$name = trim((string)($outNames[$i]   ?? ''));
		$src  = trim((string)($outSources[$i] ?? ''));
		$isLt = ((string)($outIsLit[$i] ?? '') === '1');
		$lit  = (string)($outLitVal[$i] ?? '');
	
		if ($name === '' && $src === '' && !$isLt && $lit === '') continue;
	
		$useOutBuilder = true;
		$alias = $name !== '' ? $name : ('col_' . ($i+1));
		$aliasesOut[] = $alias;
	
		if ($isLt) {
			$selectParts[] = '? AS ' . escIdent($alias);
			$params[] = $lit;
			continue;
		}
	
		if ($src === '') {
			$selectParts[] = 'NULL AS ' . escIdent($alias);
			continue;
		}
	
		if (!isset($allow[$src])) throw new RuntimeException("Invalid source column: $src");
		[$t,$c] = explode('|', $src, 2);
		$selectParts[] = escIdent($t) . '.' . escIdent($c) . ' AS ' . escIdent($alias);
	}
	


    if (!$useOutBuilder) {
        $final = [];
		if (empty($selectedCols)) {
			// Default to ALL columns from ALL tables in scope (base + joined)
			foreach ($allScope as $t) {
				foreach (getTableCols($t) as $c) $final[] = "$t|$c";
			}
		} else {
			foreach ($selectedCols as $ck) if (isset($allow[$ck])) $final[] = $ck;
			if (empty($final)) {
				foreach ($allScope as $t) foreach (getTableCols($t) as $c) $final[] = "$t|$c";
			}
		}
		
        $aliasMap = []; $used = [];
        foreach ($final as $ck) {
            [$t,$c] = explode('|', $ck, 2);
            $raw = trim((string)($aliasInput[$ck] ?? $c));
            $san = preg_replace('/[^A-Za-z0-9 _-]/', '', $raw);
            if ($san === '') $san = $c;
            $base = $san; $name = $base; $j = 2;
            while (isset($used[strtolower($name)])) { $name = $base . '_' . $j; $j++; }
            $used[strtolower($name)] = true;
            $aliasMap[$ck] = $name;
        }
        foreach ($final as $ck) {
            [$t,$c] = explode('|', $ck, 2);
            $alias = $aliasMap[$ck];
            $selectParts[] = escIdent($t) . '.' . escIdent($c) . ' AS ' . escIdent($alias);
            $aliasesOut[]  = $alias;
        }
    }

    // FROM + JOINs (+ extra ON clauses)
    $from = 'FROM ' . escIdent($baseTable);
    foreach ($joins as [$lt,$lc,$rt,$rc,$extra]) {
        $onParts = [
            escIdent($lt).'.'.escIdent($lc) . ' = ' . escIdent($rt).'.'.escIdent($rc)
        ];
        foreach ($extra as $piece) $onParts[] = $piece;

        $from .= ' LEFT JOIN ' . escIdent($rt)
               . ' ON ' . implode(' AND ', $onParts);
    }

    // WHERE (parameterized)
    $whereParts = [];
    $rows = max(count($whereTable), count($whereCol), count($whereOp), count($whereVal));
    for ($i=0; $i<$rows; $i++) {
        $wt = trim((string)($whereTable[$i] ?? '')); // may be empty
        $wc = trim((string)($whereCol[$i]   ?? ''));
        $op = strtoupper(trim((string)($whereOp[$i]    ?? '')));
        $wv = (string)($whereVal[$i] ?? '');

        if ($wc==='' || $op==='') continue;

        // pick a table for the column: either user-selected, or find the first table in scope that has it
        $tableForCol = '';
        // fixed
		if ($wt !== '' && in_array($wt, $allScope, true) && in_array($wc, getTableCols($wt), true)) {
			$tableForCol = $wt;
		} else {
			foreach ($allScope as $cand) {
				if (in_array($wc, getTableCols($cand), true)) { 
					$tableForCol = $cand; 
					break; 
				}
			}
		}
		
        if ($tableForCol === '') throw new RuntimeException("WHERE: column $wc not found in scope");

        $fq = escIdent($tableForCol).'.'.escIdent($wc);

        switch ($op) {
            case 'IS NULL':
            case 'IS NOT NULL':
                $whereParts[] = "$fq $op";
                break;
            case 'IN':
            case 'NOT IN':
                $vals = array_values(array_filter(array_map('trim', explode(',', $wv)), fn($x)=>$x!==''));
                if (empty($vals)) throw new RuntimeException("WHERE $wc $op needs values");
                $place = implode(',', array_fill(0, count($vals), '?'));
                $whereParts[] = "$fq $op ($place)";
                foreach ($vals as $v) $params[] = $v;
                break;
            case 'BETWEEN':
                // supports "a..b" or "a,b"
                $parts = preg_split('/\.\.|,/', $wv);
                if (count($parts) !== 2) throw new RuntimeException("WHERE $wc BETWEEN needs two values");
                $a = trim((string)$parts[0]); $b = trim((string)$parts[1]);
                $whereParts[] = "$fq BETWEEN ? AND ?";
                $params[] = $a; $params[] = $b;
                break;
            case 'LIKE':
            case 'NOT LIKE':
            case '=': case '<>': case '>': case '<': case '>=': case '<=':
                $whereParts[] = "$fq $op ?";
                $params[] = $wv;
                break;
            default:
                throw new RuntimeException("WHERE: unsupported operator $op");
        }
    }

// Build SELECT list once
$selectList = implode(', ', $selectParts);

// Build the *no-TOP* version (used for COUNTs)
$sqlNoTop = 'SELECT ' . $selectList . "\n" . $from;
if (!empty($whereParts)) $sqlNoTop .= "\nWHERE " . implode(' AND ', $whereParts);

// Build the version used for preview/export (TOP 100 only when !allRows)
$top = $allRows ? '' : 'TOP (100) ';
$sql = 'SELECT ' . $top . $selectList . "\n" . $from;
if (!empty($whereParts)) $sql .= "\nWHERE " . implode(' AND ', $whereParts);

// CTEs: one for preview/export, one for counts (no TOP)
$cte      = "WITH cte AS (\n$sql\n)";
$cteCount = "WITH cte AS (\n$sqlNoTop\n)";



// AFTER you've built $sql and set:
    $cte = "WITH cte AS (\n$sql\n)";

    // Build validity conditions + error messages (RULE + TYPE together)
    $validConds = [];
    $errorPieces = [];
	  $fqCol = function(string $table, string $col): string {
        return escIdent($table) . '.' . escIdent($col);
    };
	
    foreach ($aliasesOut as $idx => $alias) {
        if ($alias === '') continue;

        // Rule
        $rules = $outRule[$idx] ?? [];
        if (!is_array($rules)) $rules = ($rules === '' ? [] : [$rules]);

        foreach ($rules as $rname) {
    $rname = (string)$rname;
    if ($rname === '') continue;

    $rhsTable = null; $rhsCol = null;
    if ($rname === 'exists_in') {
        $rhsSel = (string)($outExistsSrc[$idx] ?? '');
        if ($rhsSel !== '' && strpos($rhsSel, '|') !== false) {
            [$rhsTable, $rhsCol] = explode('|', $rhsSel, 2);
        }
    }

    [$vExpr, $eExpr] = rule_sql_for_alias($alias, $rname, $rhsTable, $rhsCol);
    $validConds[]  = "($vExpr)";
    $errorPieces[] = "CASE WHEN ($vExpr) THEN '' ELSE ($eExpr) END";
}

		

        // Type
        $type   = (string)($outType[$idx]   ?? '');
        $maxLen = (string)($outMaxLen[$idx] ?? '');
        if ($type !== '') {
            [$tvExpr, $teExpr] = type_sql_for_alias($alias, $type, $maxLen);
            $validConds[]  = "($tvExpr)";
            $errorPieces[] = "CASE WHEN ($tvExpr) THEN '' ELSE ($teExpr) END";
        }
    }

    $validExpr = empty($validConds) ? '1=1' : implode(' AND ', $validConds);

    // Concatenate failing parts into one readable summary
    $errCol = empty($errorPieces)
        ? "''"
        : "LTRIM(STUFF(("
          . "SELECT CASE WHEN LEN(x)>0 THEN '; '+x ELSE '' END "
          . "FROM (VALUES " . implode(',', array_map(fn($e)=>"($e)", $errorPieces)) . ") AS E(x) "
          . "FOR XML PATH(''), TYPE).value('.','NVARCHAR(MAX)'),1,2,''))";


// --- Build per-field failures: __BadFields and __FieldReasons ---

// --- Build per-field failures: __BadFields and __FieldReasons (supports MULTIPLE rules) ---
$fieldBadNameExprs = [];
$fieldReasonExprs  = [];

foreach ($aliasesOut as $idx => $alias) {
    if ($alias === '') continue;
    $a = '[' . str_replace(']', ']]', $alias) . ']';

    // Collect validity and reason pieces for this field
    $vParts = [];
    $reasonPiecesForField = [];

    // RULES — may be multiple
    // RULES — may be multiple
$rulesHere = $outRule[$idx] ?? [];
if (!is_array($rulesHere)) $rulesHere = ($rulesHere === '' ? [] : [$rulesHere]);

foreach ($rulesHere as $rname) {
    $rname = (string)$rname;
    if ($rname === '') continue;

    $rhsTable = null; $rhsCol = null;
    if ($rname === 'exists_in') {
        $rhsSel = (string)($outExistsSrc[$idx] ?? '');
        if ($rhsSel !== '' && strpos($rhsSel, '|') !== false) {
            [$rhsTable, $rhsCol] = explode('|', $rhsSel, 2);
        }
    }

    [$vExpr, $eExpr] = rule_sql_for_alias($alias, $rname, $rhsTable, $rhsCol);
    $vParts[] = "($vExpr)";
    $reasonPiecesForField[] = "CASE WHEN ($vExpr) THEN '' ELSE ($eExpr) END";
}


    // TYPE — single
    $type   = (string)($outType[$idx]   ?? '');
    $maxLen = (string)($outMaxLen[$idx] ?? '');
    if ($type !== '') {
        [$tvExpr, $teExpr] = type_sql_for_alias($alias, $type, $maxLen);
        $vParts[] = "($tvExpr)";
        $reasonPiecesForField[] = "CASE WHEN ($tvExpr) THEN '' ELSE ($teExpr) END";
    }

    // Field fails if ANY check fails
    $allValid  = empty($vParts) ? '1=1' : implode(' AND ', $vParts);
    $failsExpr = "NOT ($allValid)";

    // Emit bad field name if fails
    $fieldBadNameExprs[] =
        "CASE WHEN $failsExpr THEN '".str_replace("'", "''", $alias)."' ELSE '' END";

    // Join reasons for this field
    $reasonsConcat = empty($reasonPiecesForField)
        ? "''"
        : "LTRIM(STUFF(("
          ."SELECT CASE WHEN LEN(x)>0 THEN '; '+x ELSE '' END "
          ."FROM (VALUES ".implode(',', array_map(fn($e)=>"($e)", $reasonPiecesForField)).") AS E(x) "
          ."FOR XML PATH(''), TYPE).value('.','NVARCHAR(MAX)'),1,2,''))";

    // Emit "Field[reason; reason]" when it fails
    $fieldReasonExprs[] =
        "CASE WHEN $failsExpr THEN '".str_replace("'", "''", $alias)."' + '[' + $reasonsConcat + ']' ELSE '' END";
}

// Aggregate columns
$badFieldsCol = empty($fieldBadNameExprs) ? "''" :
  "LTRIM(STUFF((SELECT CASE WHEN LEN(x)>0 THEN ', '+x ELSE '' END FROM (VALUES "
  . implode(',', array_map(fn($e)=>"($e)", $fieldBadNameExprs))
  . ") AS E(x) FOR XML PATH(''), TYPE).value('.','NVARCHAR(MAX)'),1,2,''))";

$fieldReasonsCol = empty($fieldReasonExprs) ? "''" :
  "LTRIM(STUFF((SELECT CASE WHEN LEN(x)>0 THEN '; '+x ELSE '' END FROM (VALUES "
  . implode(',', array_map(fn($e)=>"($e)", $fieldReasonExprs))
  . ") AS E(x) FOR XML PATH(''), TYPE).value('.','NVARCHAR(MAX)'),1,2,''))";




$sqlValid   = $cte . "\nSELECT * FROM cte WHERE $validExpr";
$sqlInvalid = $cte . "\nSELECT *, $errCol AS __RuleSummary, $badFieldsCol AS __BadFields, $fieldReasonsCol AS __FieldReasons FROM cte WHERE NOT ($validExpr)";
if ($invalidLimit > 0) {
    $sqlInvalid = $cte . "\nSELECT TOP ($invalidLimit) *, $errCol AS __RuleSummary, $badFieldsCol AS __BadFields, $fieldReasonsCol AS __FieldReasons FROM cte WHERE NOT ($validExpr)";
}

// Counts – MUST ignore TOP -> use $cteCount
$sqlInvalidCount = $cteCount . "\nSELECT COUNT_BIG(*) AS __InvalidCount FROM cte WHERE NOT ($validExpr)";
$sqlValidCount   = $cteCount . "\nSELECT COUNT_BIG(*) AS __ValidCount   FROM cte WHERE $validExpr";

// expose one of the data SQLs for the on-page <pre>
$builtSqlOut = $sqlValid;
return [$sqlValid, $sqlInvalid, $params, $sqlInvalidCount, $sqlValidCount];

}


/* ---------- Run for preview/export ---------- */
$errorsThisRun = [];

$needsRun = (
  $action_top || $action_all || $action_csv || $action_xlsx
  || $action_err_csv || $action_err_xlsx
) && $validBase;


$wantAllRows = ($action_csv || $action_xlsx); 

$doCountOnly = $action_count && $validBase;


$execMs = 0.0;               // NEW: elapsed time in ms
$fetchedCount = 0;           // NEW: number of rows actually rendered
$totalValidCount = null;     // NEW: total count w/o TOP (optional)


if ($needsRun) {
  try {
    $aliasesOrdered = [];
    $invalidLimit = (LOG_INVALIDS_MODE === 'cap') ? MAX_INVALIDS_TO_LOG : 0;

[$sqlValid, $sqlInvalid, $sqlParams, $sqlInvalidCount, $sqlValidCount] = build_query(
    $tables, $baseTable,
    $joinLeftTable, $joinLeftCol, $joinRightTable, $joinRightCol, $joinCount,
    $selectedCols, $aliasInput, /* allRows */ $wantAllRows,   // <-- was false
    $builtSql, $aliasesOrdered,
    $outNames, $outSources, $outIsLit, $outLitVal,
    $outRule, $outType, $outMaxLen,
    $joinOnLeftCol, $joinOnOp, $joinOnRight, $joinOnIsLit, $joinOnLitVal,
    $whereTable, $whereCol, $whereOp, $whereVal,
    /* invalidLimit */ 0,
    $outExistsSrc
);

	




// --- one execution; branch to export or preview ---
$t0   = microtime(true);
$stmt = sqlsrv_query($conn, $sqlValid, $sqlParams, ['QueryTimeout' => 60]);
if ($stmt === false) {
    throw new RuntimeException(print_r(sqlsrv_errors(), true));
}

/* ===== EXPORTS: stream directly from $stmt and exit ===== */
if ($action_csv) {
    $filenameCsv = 'report_' . date('Ymd_His') . '.csv';
    if (ob_get_level()) ob_end_clean();
    header('Content-Type: text/csv; charset=UTF-8');
    header('Content-Disposition: attachment; filename="'.$filenameCsv.'"; filename*=UTF-8\'\'' . rawurlencode($filenameCsv));

    echo "\xEF\xBB\xBF"; // UTF-8 BOM
    $out = fopen('php://output', 'w');
    fputcsv($out, $aliasesOrdered, ';', '"');

    while ($r = sqlsrv_fetch_array($stmt, SQLSRV_FETCH_ASSOC)) {
        $row = [];
        foreach ($aliasesOrdered as $alias) {
            $v = $r[$alias] ?? '';
            if ($v instanceof DateTimeInterface) $v = $v->format('Y-m-d H:i:s');
            if ($v === null) $v = '';
            $v = str_replace(["\r", "\n"], ' ', (string)$v);
            $row[] = $v;
        }
        fputcsv($out, $row, ';', '"');
    }
    fclose($out);
    sqlsrv_free_stmt($stmt);
    exit;
}

if ($action_xlsx) {
    $data = [];
    $data[] = $aliasesOrdered;
    while ($r = sqlsrv_fetch_array($stmt, SQLSRV_FETCH_ASSOC)) {
        $row = [];
        foreach ($aliasesOrdered as $alias) {
            $v = $r[$alias] ?? '';
            if ($v instanceof DateTimeInterface) $v = $v->format('Y-m-d H:i:s');
            $row[] = ($v === null) ? '' : (string)$v;
        }
        $data[] = $row;
    }
    sqlsrv_free_stmt($stmt);
    $filenameXlsx = 'report_' . date('Ymd_His') . '.xlsx';
    if (ob_get_level()) ob_end_clean();
    SimpleXLSXGen::fromArray($data)->downloadAs($filenameXlsx);
    exit;
}

/* ===== PREVIEW: build $rows once for on-page table ===== */
$rows = [];
while ($r = sqlsrv_fetch_array($stmt, SQLSRV_FETCH_ASSOC)) {
    foreach ($r as $k => $v) {
        if ($v instanceof DateTimeInterface) $r[$k] = $v->format('Y-m-d H:i:s');
    }
    $rows[] = $r;
}
$fetchedCount = count($rows);
$execMs = round((microtime(true) - $t0) * 1000, 1);
sqlsrv_free_stmt($stmt);

/* ===== OPTIONAL TOTAL COUNT (ignores TOP) ===== */
$stmtCnt = sqlsrv_query($conn, $sqlValidCount, $sqlParams, ['QueryTimeout' => 30]);
if ($stmtCnt && ($cr = sqlsrv_fetch_array($stmtCnt, SQLSRV_FETCH_ASSOC))) {
    $totalValidCount = (int)$cr['__ValidCount'];
}
if ($stmtCnt) sqlsrv_free_stmt($stmtCnt);




if ($doCountOnly) {
    try {
        $aliasesOrdered = [];
[$sqlValid, $sqlInvalid, $sqlParams, $sqlInvalidCount, $sqlValidCount] = build_query(
    $tables, $baseTable,
    $joinLeftTable, $joinLeftCol, $joinRightTable, $joinRightCol, $joinCount,
    $selectedCols, $aliasInput, /* allRows */ $wantAllRows,   // <-- was false
    $builtSql, $aliasesOrdered,
    $outNames, $outSources, $outIsLit, $outLitVal,
    $outRule, $outType, $outMaxLen,
    $joinOnLeftCol, $joinOnOp, $joinOnRight, $joinOnIsLit, $joinOnLitVal,
    $whereTable, $whereCol, $whereOp, $whereVal,
    /* invalidLimit */ 0,
    $outExistsSrc
);


        $stmtCnt = sqlsrv_query($conn, $sqlValidCount, $sqlParams, ['QueryTimeout'=>60]);
        if ($stmtCnt && ($cr = sqlsrv_fetch_array($stmtCnt, SQLSRV_FETCH_ASSOC))) {
            $totalValidCount = (int)$cr['__ValidCount'];
        }
        if ($stmtCnt) sqlsrv_free_stmt($stmtCnt);
    } catch (Throwable $e) {
        $error = $e->getMessage();
    }
}




$invalidTotal = null;

if (LOG_INVALIDS_MODE !== 'off' && isset($conn2) && $conn2) {
    // Always get the total count (fast & cheap)
    $stmtBadCount = sqlsrv_query($conn, $sqlInvalidCount, $sqlParams, ['QueryTimeout'=>60]);
    if ($stmtBadCount && ($rc = sqlsrv_fetch_array($stmtBadCount, SQLSRV_FETCH_ASSOC))) {
        $invalidTotal = (int)$rc['__InvalidCount'];
    }
    if ($stmtBadCount) sqlsrv_free_stmt($stmtBadCount);

    if (LOG_INVALIDS_MODE === 'cap' && MAX_INVALIDS_TO_LOG > 0) {
        $stmtBad = sqlsrv_query($conn, $sqlInvalid, $sqlParams, ['QueryTimeout'=>0]);
        if ($stmtBad) {
            $logged = 0;
            while ($bad = sqlsrv_fetch_array($stmtBad, SQLSRV_FETCH_ASSOC)) {
				$summary = (string)($bad['__RuleSummary'] ?? '');
				$badFields = (string)($bad['__BadFields'] ?? '');
				$fieldReasons = (string)($bad['__FieldReasons'] ?? '');
				unset($bad['__RuleSummary'], $bad['__BadFields'], $bad['__FieldReasons']);
			
				$json = json_encode($bad, JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES);
			
				sqlsrv_query(
					$conn2,
					"INSERT INTO dbo.z0ErrorsExport (base_table, rule_summary, row_json, run_token, action, bad_fields, field_reasons)
					VALUES (?, ?, ?, ?, ?, ?, ?)",
					[$baseTable ?: null, $summary, $json, $runToken, $runAction, $badFields, $fieldReasons]
				);
                $logged++;
                if ($logged >= MAX_INVALIDS_TO_LOG) break; // safety
            }
            sqlsrv_free_stmt($stmtBad);
        }
    }
    // If 'count-only', we don’t log individual rows—just use $invalidTotal for UI.
}




if (isset($conn2) && $conn2 && isset($runToken) && $runToken) {
    $errStmt = sqlsrv_query(
		$conn2,
		"SELECT TOP (500)
			created_at, base_table, rule_summary, bad_fields, field_reasons, row_json
		FROM dbo.z0ErrorsExport
		WHERE run_token = ?
		ORDER BY created_at DESC",
		[$runToken]
		);
    $errorsThisRun = [];
    if ($errStmt) {
        while ($er = sqlsrv_fetch_array($errStmt, SQLSRV_FETCH_ASSOC)) {
            $errorsThisRun[] = $er;
        }
        sqlsrv_free_stmt($errStmt);
    }
}


/* --- Export CSV --- */
/*if ($action_csv) {
    $filenameCsv = 'report_' . date('Ymd_His') . '.csv';

    if (ob_get_level()) ob_end_clean();
    header('Content-Type: text/csv; charset=UTF-8');
    header('Content-Disposition: attachment; filename="'.$filenameCsv.'"; filename*=UTF-8\'\'' . rawurlencode($filenameCsv));

    echo "\xEF\xBB\xBF"; // UTF-8 BOM
    $out = fopen('php://output', 'w');

    fputcsv($out, $aliasesOrdered, ';', '"');

    while ($r = sqlsrv_fetch_array($stmt, SQLSRV_FETCH_ASSOC)) {
        $row = [];
        foreach ($aliasesOrdered as $alias) {
            $v = $r[$alias] ?? '';
            if ($v instanceof DateTimeInterface) $v = $v->format('Y-m-d H:i:s');
            if ($v === null) $v = '';
            $v = str_replace(["\r", "\n"], ' ', (string)$v);
            $row[] = $v;
        }
        fputcsv($out, $row, ';', '"');
    }
    fclose($out);
    sqlsrv_free_stmt($stmt);
    exit;
}*/

        /* --- Export XLSX (SimpleXLSXGen) --- */
/*if ($action_xlsx) {
    $data = [];
    $data[] = $aliasesOrdered;
    while ($r = sqlsrv_fetch_array($stmt, SQLSRV_FETCH_ASSOC)) {
        $row = [];
        foreach ($aliasesOrdered as $alias) {
            $v = $r[$alias] ?? '';
            if ($v instanceof DateTimeInterface) $v = $v->format('Y-m-d H:i:s');
            $row[] = ($v === null) ? '' : (string)$v;
        }
        $data[] = $row;
    }
    sqlsrv_free_stmt($stmt);

    $filenameXlsx = 'report_' . date('Ymd_His') . '.xlsx';
    if (ob_get_level()) ob_end_clean();
    SimpleXLSXGen::fromArray($data)->downloadAs($filenameXlsx);
    exit;
}*/

/* ---------- Errors export (CSV/XLSX) ---------- */
if (($action_err_csv || $action_err_xlsx) && $validBase) {
    try {
        // Rebuild queries ensuring NO cap for invalids
        $aliasesErr = [];
		$builtSql2  = '';   // <— initialize as string
		[$sqlValid2, $sqlInvalidAll, $sqlParams2, $sqlInvalidCount2] = build_query(
			$tables, $baseTable,
			$joinLeftTable, $joinLeftCol, $joinRightTable, $joinRightCol, $joinCount,
			$selectedCols, $aliasInput, /*allRows*/ true,
			$builtSql2, $aliasesErr,
			$outNames, $outSources, $outIsLit, $outLitVal,
			$outRule, $outType, $outMaxLen,
			$joinOnLeftCol, $joinOnOp, $joinOnRight, $joinOnIsLit, $joinOnLitVal,
			$whereTable, $whereCol, $whereOp, $whereVal,
			/*invalidLimit*/ 0,
			$outExistsSrc          // <-- add this
		);
		
		

        // Force __RuleSummary to be present in header
        $aliasesErrWithSummary = $aliasesErr;
		$aliasesErrWithSummary[] = '__RuleSummary';
		$aliasesErrWithSummary[] = '__BadFields';
		$aliasesErrWithSummary[] = '__FieldReasons';
		
        $stmtBadAll = sqlsrv_query($conn, $sqlInvalidAll, $sqlParams2, ['QueryTimeout'=>0]);
        if ($stmtBadAll === false) throw new RuntimeException(print_r(sqlsrv_errors(), true));

        /* --- CSV --- */
/* --- CSV --- */
if ($action_err_csv) {
    $filenameErrCsv = 'badrows_' . date('Ymd_His') . '.csv';
    if (ob_get_level()) ob_end_clean();
    header('Content-Type: text/csv; charset=UTF-8');
    header('Content-Disposition: attachment; filename="'.$filenameErrCsv.'"; filename*=UTF-8\'\'' . rawurlencode($filenameErrCsv));

    echo "\xEF\xBB\xBF"; // UTF-8 BOM
    $out = fopen('php://output', 'w');

    // Parse __FieldReasons (internal) into a grouped human line
    $groupReasons = function (string $fieldReasons) {
        $groups = ['mandatory'=>[], 'dateformat'=>[], 'exists_in'=>[], 'type'=>[]];
        if ($fieldReasons === '') return $groups;

        foreach (preg_split('/;\s*/', $fieldReasons, -1, PREG_SPLIT_NO_EMPTY) as $chunk) {
            if (!preg_match('/^(.*?)\[(.*?)\]$/u', $chunk, $m)) continue;
            $field   = trim($m[1]);
            $reasons = array_filter(array_map('trim', preg_split('/;\s*/', $m[2])));
            foreach ($reasons as $r) {
                if (stripos($r, 'mandatory') !== false)        { $groups['mandatory'][]  = $field; }
                elseif (stripos($r, 'dateformat') !== false)   { $groups['dateformat'][] = $field; }
                elseif (stripos($r, 'lookup') !== false)       { $groups['exists_in'][]  = $field; }
                else { // type/length/convert errors -> keep short detail with field
                    $groups['type'][] = $field . '[' . $r . ']';
                }
            }
        }
        // de-dup
        foreach ($groups as $k=>$arr) $groups[$k] = array_values(array_unique($arr, SORT_STRING));
        return $groups;
    };

    // Header: your aliases + concise diagnostics
    $headers = array_merge($aliasesErr, ['__RuleSummary', '__BadFields']);
    fputcsv($out, $headers, ';', '"');

    while ($r = sqlsrv_fetch_array($stmtBadAll, SQLSRV_FETCH_ASSOC)) {
        // 1) your data columns
        $row = [];
        foreach ($aliasesErr as $alias) {
            $v = $r[$alias] ?? '';
            if ($v instanceof DateTimeInterface) $v = $v->format('Y-m-d H:i:s');
            $row[] = ($v === null) ? '' : str_replace(["\r","\n"], ' ', (string)$v);
        }

        // 2) concise summary + bad fields
        $g = $groupReasons((string)($r['__FieldReasons'] ?? ''));
        $parts = [];
        if (!empty($g['mandatory']))  $parts[] = 'mandatory: '  . implode(', ', $g['mandatory']);
        if (!empty($g['dateformat'])) $parts[] = 'dateformat: ' . implode(', ', $g['dateformat']);
        if (!empty($g['exists_in']))  $parts[] = 'exists_in: '  . implode(', ', $g['exists_in']);
        if (!empty($g['type']))       $parts[] = 'type: '       . implode(', ', $g['type']);

        $ruleSummary = implode(' | ', $parts);
        $badFields   = (string)($r['__BadFields'] ?? '');

        $row[] = $ruleSummary;
        $row[] = $badFields;

        fputcsv($out, $row, ';', '"');
    }

    fclose($out);
    sqlsrv_free_stmt($stmtBadAll);
    exit;
}



        /* --- XLSX --- */
        if ($action_err_xlsx) {
    $data = [];
    $data[] = $aliasesErrWithSummary;
    while ($r = sqlsrv_fetch_array($stmtBadAll, SQLSRV_FETCH_ASSOC)) {
        $row = [];
        foreach ($aliasesErrWithSummary as $alias) {
            $v = $r[$alias] ?? '';
            if ($v instanceof DateTimeInterface) $v = $v->format('Y-m-d H:i:s');
            $row[] = ($v === null) ? '' : (string)$v;
        }
        $data[] = $row;
    }
    sqlsrv_free_stmt($stmtBadAll);

    $filenameErrXlsx = 'badrows_' . date('Ymd_His') . '.xlsx';
    if (ob_get_level()) ob_end_clean();
    SimpleXLSXGen::fromArray($data)->downloadAs($filenameErrXlsx);
    exit;
}


    } catch (Throwable $e) {
        $error = $e->getMessage();
    }
}

        /* --- Normal on-page preview --- */
        /*while ($r = sqlsrv_fetch_array($stmt, SQLSRV_FETCH_ASSOC)) {
            foreach ($r as $k=>$v) if ($v instanceof DateTimeInterface) $r[$k] = $v->format('Y-m-d H:i:s');
            $rows[] = $r;
        }
        sqlsrv_free_stmt($stmt);*/
        // $aliasesOrdered is used in the HTML header row below

    } catch (Throwable $e) { $error = $e->getMessage(); }
	
	
	
}



?>
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>SQL Server Viewer — No JS (Joins + Aliases + Export)</title>

<!-- <-- Local Select2 & jQuery -->
<link rel="stylesheet" href="<?= htmlspecialchars($ASSETS) ?>/select2.css">        <!-- <-- -->
<script src="<?= htmlspecialchars($ASSETS) ?>/jQuery.js"></script>                 <!-- <-- load jQuery first -->
<script src="<?= htmlspecialchars($ASSETS) ?>/select2.js"></script>                <!-- <-- then Select2 -->

<style>
  /*  optional: nicer spacing */
  .select2-container { min-width: 260px; }
  .out-cols-table th, .out-cols-table td { padding: 6px 8px; }
  .out-cols-table input[type=text] { width: 220px; }
  
  
  /* Dark tweaks for Select2 */
.select2-container--default .select2-selection--single {
  background-color: #121820;
  border: 1px solid #333;
  color: #e6edf3;
}
.select2-container--default .select2-selection--single .select2-selection__rendered {
  color: #e6edf3;
}
.select2-container--default .select2-selection--single .select2-selection__placeholder {
  color: #9aa5b1;
}
.select2-container--default .select2-selection--single .select2-selection__arrow b {
  border-color: #e6edf3 transparent transparent transparent;
}
.select2-dropdown {
  background: #121820;
  border: 1px solid #333;
  color: #e6edf3;
}
.select2-results__option--highlighted[aria-selected] {
  background: #1c2330;
  color: #e6edf3;
}
.select2-search--dropdown .select2-search__field {
  background: #0b0f14;
  border: 1px solid #333;
  color: #e6edf3;
}
.select2-container { min-width: 260px; }

/* Default theme */
.select2-container--default .select2-results__option--selected {
  background-color: #2b3a52;   /* selected row bg */
  color: #e6edf3;              /* selected row text */
}

/* Hover/keyboard focus (highlight) */
.select2-container--default .select2-results__option--highlighted[aria-selected] {
  background-color: #3a4a65;   /* hovered row bg */
  color: #fff;                  /* hovered row text */
}

/* If an option is both selected AND highlighted, this wins */
.select2-container--default
  .select2-results__option--selected.select2-results__option--highlighted[aria-selected] {
  background-color: #475d82;
  color: #fff;
}

/* (Optional) make non-disabled options share a base look */
.select2-container--default .select2-results__option--selectable {
  background-color: #121820;
  color: #e6edf3;
}

#outColsTable button,
.join table button { padding: 4px 8px; font-size: 12px; }


.select2-container--default.select2-container--focus .select2-selection--multiple
{
	background-color:#1c2330;
}

.select2-container--default .select2-selection--multiple .select2-selection__choice__display
{
	background-color:#1c2330;
}

.select2-container--default .select2-selection--multiple{
	background-color:#1c2330;
}

</style>
<style>
body { font-family: Arial, sans-serif; margin: 20px; background:#0b0f14; color:#e6edf3; }
select, button, input[type=submit], input[type=text] { padding: 8px; font-size: 14px; border-radius:6px; border:1px solid #333; background:#121820; color:#e6edf3; }
/* scroll area */
.results-wrap { overflow: auto; max-height: 70vh; background: #0b0f14; }

/* IMPORTANT: don't collapse when using sticky headers */
table { border-collapse: separate; border-spacing: 0; width: 100%; }

/* Cells */
th, td { padding: 6px 10px; text-align: left; background-clip: padding-box; }

/* Header: sticky, opaque, above rows */
thead th {
  position: sticky;
  top: 0;
  z-index: 5;                    /* keep above tbody cells */
  background: #1c2330;           /* solid background */
  color: #e6edf3;
  border-top: 1px solid #333;
  border-left: 1px solid #333;
  border-right: 1px solid #333;
}

/* subtle separator so rows don’t show through at the bottom edge */
thead th::after {
  content: "";
  position: absolute;
  left: 0; right: 0; bottom: -1px;
  height: 1px;
  background: #0b0f14;           /* same as page bg to “mask” body */
}

/* Body cells: set explicit background so nothing is transparent */
tbody td {
  background: #0f1622;
  border-right: 1px solid #333;
  border-bottom: 1px solid #333;
}

/* Optional zebra to improve contrast */
tbody tr:nth-child(even) td { background: #0c121a; }

.error { color: #ff6b6b; white-space: pre-wrap; }
.columns { display:flex; gap:8px; flex-wrap:wrap; }
.columns h4 { width:100%; margin:10px 0 6px; color:#cbd5e1; }
.columns label { background:#1c2330; padding:4px 8px; border-radius:6px; cursor:pointer; }
.actions { margin-top:10px; display:flex; gap:8px; flex-wrap:wrap; }
.join { border:1px dashed #2a3544; padding:10px; border-radius:8px; margin-top:8px; }
.join .grid { display:grid; grid-template-columns:1fr 1fr 1fr 1fr; gap:8px; }
.small { font-size:12px; color:#a0aec0; }
hr { border:0; border-top:1px solid #2a3544; margin:14px 0; }
.aliases { margin-top: 16px; }
.aliases table { width: auto; }
.aliases th, .aliases td { white-space: nowrap; }

  .topbar {
    display:flex;
    justify-content: space-between;
    align-items: center;
    gap: 16px;
    margin-bottom: 10px;
  }
  .topbar .load-preset {
    margin-left: auto;           /* push to right */
  }
  .topbar .load-preset select {
    min-width: 280px;
  }
  
  
  
  /* hide the 'exists' compare column by default */
.exists-col-head,
.exists-cell { display: none; }

</style>

</head>
<body>

<h1>SQL Server Table Viewer </h1>

<form method="post">
<div class="topbar">
  <div>
    <input type="hidden" name="join_count" value="<?= htmlspecialchars((string)$joinCount) ?>">

  <!-- Base table -->
  <label>Base table (schema: <?= htmlspecialchars($SCHEMA) ?>)</label>
  <select name="table">
    <option value="">-- select --</option>
    <?php foreach ($tables as $t): ?>
      <option value="<?= htmlspecialchars($t) ?>" <?= $t===$baseTable ? 'selected':'' ?>><?= htmlspecialchars($t) ?></option>
    <?php endforeach; ?>
  </select>

  <div class="actions">
    <button type="submit" name="refresh" value="1">Επιλογή του table</button>
  </div>



  </div> <!-- left side empty, keep for spacing -->
<div class="load-preset">
  <label style="display:block; margin-bottom:4px;">Load preset</label>
  <select name="preset_id" class="out-source">
    <option value="0">-- choose saved preset --</option>
    <?php foreach ($savedPresets as $p): ?>
      <option value="<?= (int)$p['id'] ?>">
        <?= htmlspecialchars($p['name']) ?>
        <?php if (!empty($p['created_at'])): ?>
          — <?= htmlspecialchars(($p['created_at'] instanceof DateTimeInterface) ? $p['created_at']->format('Y-m-d H:i') : (string)$p['created_at']) ?>
        <?php endif; ?>
      </option>
    <?php endforeach; ?>
  </select>
  <button type="submit" name="load_preset" value="1">Load</button>
  <button type="submit" name="clear_ui" value="1" title="Unload any loaded preset and start blank">Clear UI</button>
  <button style="margin-left:5px;"type="submit" name="delete_preset" value="1"
          onclick="return confirm('Delete this preset permanently?');">
    Delete
  </button>
  
</div>


</div>
<?php if ($validBase): ?>
<?php if (!UI_DISABLE_JOINS):?>

    <h3>LEFT JOINs</h3>
    <?php
      $prevRightTables = [];
      for ($i=0; $i<$joinCount; $i++):
        $allowedLeft = array_values(array_unique(array_filter(array_merge([$baseTable], $prevRightTables))));
        $selLT = $joinLeftTable[$i]  ?? '';
        $selLC = $joinLeftCol[$i]    ?? '';
        $selRT = $joinRightTable[$i] ?? '';
        $selRC = $joinRightCol[$i]   ?? '';
    ?>
      <div class="join">
        <div class="grid">
          <div>
            <label>Left side table</label><br>
            <select name="join_left_table[]">
              <option value="">-- select --</option>
              <?php foreach ($allowedLeft as $t): ?>
                <option value="<?= htmlspecialchars($t) ?>" <?= $t===$selLT ? 'selected':'' ?>><?= htmlspecialchars($t) ?></option>
              <?php endforeach; ?>
            </select>
          </div>
          <div>
            <label>Left column</label><br>
            <select name="join_left_col[]">
              <option value="">-- select --</option>
              <?php foreach (getTableCols($selLT) as $c): ?>
                <option value="<?= htmlspecialchars($c) ?>" <?= $c===$selLC ? 'selected':'' ?>><?= htmlspecialchars($c) ?></option>
              <?php endforeach; ?>
            </select>
          </div>
          <div>
            <label>Right (joined) table</label><br>
            <select name="join_right_table[]">
              <option value="">-- select --</option>
              <?php foreach ($tables as $t): ?>
                <option value="<?= htmlspecialchars($t) ?>" <?= $t===$selRT ? 'selected':'' ?>><?= htmlspecialchars($t) ?></option>
              <?php endforeach; ?>
            </select>
          </div>
          <div>
            <label>Right column</label><br>
            <?php $rtCols = $selRT ? getTableCols($selRT) : []; ?>
            <select name="join_right_col[]">
              <option value="">-- select --</option>
              <?php foreach ($rtCols as $c): ?>
                <option value="<?= htmlspecialchars($c) ?>" <?= $c===$selRC ? 'selected':'' ?>><?= htmlspecialchars($c) ?></option>
              <?php endforeach; ?>
            </select>
          </div>
        </div>
		
		<div style="margin-top:8px;">
  <strong>Additional ON conditions</strong>
  <table>
    <thead>
      <tr>
        <th>Left col (<?= htmlspecialchars($selLT ?: 'left') ?>)</th>
        <th>Op</th>
        <th>Right side</th>
        <th>Literal?</th>
        <th>Value (if literal)</th>
		<th></th>
      </tr>
    </thead>
    <tbody id="joinOnBody<?= $i ?>">
      <?php
        $joinOnRows = max(
			count($joinOnLeftCol[$i] ?? []),
			count($joinOnOp[$i] ?? []),
			count($joinOnRight[$i] ?? []),
			count($joinOnIsLit[$i] ?? []),
			count($joinOnLitVal[$i] ?? []),
			1
		);
		for ($k=0; $k < $joinOnRows; $k++):
			
          $lcol = (string)(($joinOnLeftCol[$i] ?? [])[$k] ?? '');
          $op   = (string)(($joinOnOp[$i]      ?? [])[$k] ?? '=');
          $rcol = (string)(($joinOnRight[$i]   ?? [])[$k] ?? '');
          $isLt = (string)(($joinOnIsLit[$i]   ?? [])[$k] ?? '');
          $litv = (string)(($joinOnLitVal[$i]  ?? [])[$k] ?? '');
      ?>
      <tr>
        <td>
          <select name="join_on_left_col[<?= $i ?>][]" class="out-source">
            <option value="">-- select --</option>
            <?php foreach (getTableCols($selLT) as $c): ?>
              <option value="<?= htmlspecialchars($c) ?>" <?= $c===$lcol ? 'selected':'' ?>><?= htmlspecialchars($c) ?></option>
            <?php endforeach; ?>
          </select>
        </td>
        <td>
          <select name="join_on_op[<?= $i ?>][]">
            <?php foreach (['=','<>','>','<','>=','<=','LIKE','NOT LIKE'] as $oop): ?>
              <option value="<?= $oop ?>" <?= $oop===$op ? 'selected':'' ?>><?= $oop ?></option>
            <?php endforeach; ?>
          </select>
        </td>
        <td>
          <select name="join_on_right[<?= $i ?>][]" class="out-source">
            <option value="">-- select right col --</option>
            <?php foreach (getTableCols($selRT) as $c): ?>
              <option value="<?= htmlspecialchars($c) ?>" <?= $c===$rcol ? 'selected':'' ?>><?= htmlspecialchars($c) ?></option>
            <?php endforeach; ?>
          </select>
        </td>
        <td>
          <label><input type="checkbox" name="join_on_is_lit[<?= $i ?>][]" value="1" <?= $isLt==='1'?'checked':'' ?>> literal</label>
        </td>
        <td><input type="text" name="join_on_lit_val[<?= $i ?>][]" value="<?= htmlspecialchars($litv) ?>" placeholder="only used when literal"></td>
		<td>
			<button type="button" onclick="delJoinOnRow(<?= $i ?>, this)">Delete</button>
		</td>
	  </tr>
      <?php endfor; ?>
    </tbody>
  </table>
  <div class="actions" style="margin-top:6px;">
    <button type="button" onclick="addJoinOnRow(<?= $i ?>)">+ Add ON row</button>
  </div>
</div>

		
		
        <div class="actions">
          <button type="submit" name="remove_join" value="<?= $i ?>">Cancel this join</button>
          <!--<button type="submit" name="refresh" value="1">Refresh</button>-->
        </div>
        <div class="small">ON [<?= htmlspecialchars($selLT ?: 'left_table') ?>].[<?= htmlspecialchars($selLC ?: 'left_col') ?>]
            = [<?= htmlspecialchars($selRT ?: 'right_table') ?>].[<?= htmlspecialchars($selRC ?: 'right_col') ?>]</div>
      </div>
    <?php
        if ($selRT && in_array($selRT, $tables, true)) $prevRightTables[] = $selRT;
      endfor;
    ?>

    <div class="actions">
      <button type="submit" name="add_join" value="1">+ Add LEFT JOIN</button>
      <!--<button type="submit" name="refresh" value="1">Refresh</button>-->
    </div>

    <hr>
<?php endif;?>
    
	
	
	<div class="actions" style="gap:12px;align-items:flex-start;">
  <div>
    <label><strong>Quick add columns</strong></label><br>
    <select id="quickAddCols" multiple class="out-source" style="min-width:420px;"></select>
    <button type="button" id="btnQuickAdd">Add selected</button>
 <!--   <label style="margin-left:10px;">
      <input type="checkbox" id="qaUseTablePrefix" checked> prefix aliases with table
    </label>-->
  </div>
</div>

	
	
	<!-- Columns (per-table Select/Unselect) -->
 <h3>Output columns</h3>
<p class="small">Give each output column a <em>Name</em>. Choose a <em>Source</em> (Table.Column) or leave it as “New (empty)”.</p>
<div class="results-wrap">
<table class="out-cols-table" id="outColsTable">
<thead>
  <tr>
    <th>#</th>
    <th>Name (alias / new column)</th>
    <th>Source (Table.Column or New)</th>
	<th>Rule</th>
	<th class="exists-col-head">Compare to (for exists)</th>
	<th>Type</th>
	<th>Max len</th>
    <th>Use literal</th>
    <th>Literal value</th>
    <th></th>
  </tr>
</thead>
  <tbody id="outColsTbody">
    <?php
      // Build the options from the current scope (base + joined tables)
     // Build the options from the current scope (base + joined tables)
	$sourceOptions = [];
	
	// 1) Count column-name collisions across scope tables
	$colCounts = [];
	foreach ($scopeTables as $t) {
		foreach (getTableCols($t) as $c) {
			$colCounts[$c] = ($colCounts[$c] ?? 0) + 1;
		}
	}
	
	// 2) Build labels: just "Column" if unique; else "Table.Column"
	foreach ($scopeTables as $t) {
		foreach (getTableCols($t) as $c) {
			$val   = "$t|$c";
			$label = ($colCounts[$c] > 1) ? "$t.$c" : $c;
			$sourceOptions[$val] = $label;
		}
	}
	

      // Rehydrate posted rows, else show 3 empty rows
$rowCount = max(
  count($outNames),
  count($outSources),
  count($outRule),
  count($outType),
  count($outMaxLen),
  count($outIsLit),     
  count($outLitVal),    
  1
);


      for ($i=0; $i<$rowCount; $i++):
        $name = htmlspecialchars((string)($outNames[$i]   ?? ''), ENT_QUOTES);
        $src  = (string)($outSources[$i] ?? '');
    ?>
 <tr>
  <td><?= $i+1 ?></td>
  <td><input type="text" name="out_name[]" value="<?= $name ?>" placeholder="e.g. CustomerName"></td>

  <td>
    <select name="out_source[]" class="out-source">
      <option value="">— New (empty) —</option>
      <?php foreach ($sourceOptions as $val => $label): ?>
        <option value="<?= htmlspecialchars($val) ?>" <?= $val===$src ? 'selected':'' ?>>
          <?= htmlspecialchars($label) ?>
        </option>
      <?php endforeach; ?>
    </select>
  </td>

	<?php
	$rulesHere = $outRule[$i] ?? [];
	if (!is_array($rulesHere)) $rulesHere = ($rulesHere === '' ? [] : [$rulesHere]);
	?>
	<td>
	<select name="out_rule[<?= $i ?>][]" class="out-rule out-source" multiple>
		<option value="mandatory"  <?= in_array('mandatory', $rulesHere, true)  ? 'selected' : '' ?>>mandatory</option>
		<option value="dateformat" <?= in_array('dateformat', $rulesHere, true) ? 'selected' : '' ?>>dateformat (yyyy-mm-dd)</option>
		<option value="exists_in"  <?= in_array('exists_in', $rulesHere, true)  ? 'selected' : '' ?>>exists_in (lookup)</option>
	</select>

	</td>
	
  
  <?php $typeHere = (string)($outType[$i] ?? ''); ?>
    <?php
    $existsSrc = (string)($outExistsSrc[$i] ?? '');
  ?>
	<td class="exists-cell">
	<!-- 1) pick table -->
	<select name="out_exists_table[]" class="out-exists-table out-source">
		<option value="">— choose table —</option>
		<?php foreach ($tables as $t): ?>
		<option value="<?= htmlspecialchars($t) ?>"><?= htmlspecialchars($t) ?></option>
		<?php endforeach; ?>
	</select>
	
	<!-- 2) pick column (filled lazily) -->
	<select name="out_exists_col[]" class="out-exists-col out-source" disabled>
		<option value="">— choose column —</option>
	</select>
	
	<!-- 3) hidden combined "Table|Column" that your PHP already reads -->
	<input type="hidden" name="out_exists_src[]" value="<?= htmlspecialchars($existsSrc) ?>">
	</td>
	
	
<td>
  <select name="out_type[]">
    <option value="">—</option>
    <option value="int"       <?= $typeHere==='int'?'selected':'' ?>>integer</option>
    <option value="decimal"   <?= $typeHere==='decimal'?'selected':'' ?>>decimal(38,10)</option>
    <option value="float"     <?= $typeHere==='float'?'selected':'' ?>>float</option>
    <option value="bit"       <?= $typeHere==='bit'?'selected':'' ?>>bit (0/1)</option>
    <option value="date"      <?= $typeHere==='date'?'selected':'' ?>>date (yyyy-mm-dd)</option>
    <option value="datetime2" <?= $typeHere==='datetime2'?'selected':'' ?>>datetime2</option>
    <option value="nvarchar"  <?= $typeHere==='nvarchar'?'selected':'' ?>>nvarchar</option>
  </select>
</td>
<td>
  <?php $lenHere = (string)($outMaxLen[$i] ?? ''); ?>
  <input type="text" name="out_maxlen[]" value="<?= htmlspecialchars($lenHere) ?>" placeholder="e.g. 50">
</td>


  <?php
    $isLitChecked = ((string)($outIsLit[$i] ?? '') === '1');
    $litValDisp   = (string)($outLitVal[$i] ?? '');
  ?>
  <td style="text-align:center;">
    <input type="checkbox" name="out_is_lit[<?= $i ?>]" value="1" <?= $isLitChecked ? 'checked' : '' ?> class="out-is-lit">
  </td>
  <td>
    <input type="text" name="out_lit_val[]" value="<?= htmlspecialchars($litValDisp) ?>" placeholder="e.g. f or 2000" class="out-lit-val">
  </td>

  <td><button type="button" class="btn-del-row" onclick="delOutRow(this)">Delete</button></td>
</tr>


    <?php endfor; ?>
  </tbody>
</table>
</div>
<div class="actions">
  <button type="button" onclick="addOutRow()">+ Add column</button>
  <!--<button type="submit" name="refresh" value="1">Refresh</button>-->
</div>

<hr>

    
<?php if (!UI_DISABLE_WHERE): ?>
   <h3>WHERE</h3>
<p class="small">
  Rows combine with <strong>AND</strong>. Operators: =, &lt;&gt;, &gt;, &lt;, &gt;=, &lt;=, LIKE, NOT LIKE, IN, NOT IN, BETWEEN, IS NULL, IS NOT NULL.
  For <em>IN</em> use comma values (e.g. <code>a,b,c</code>). For <em>BETWEEN</em> use <code>a..b</code> or <code>a,b</code>.
</p>

<table class="out-cols-table" id="whereTable">
  <thead>
    <tr><th>#</th><th>Table</th><th>Column</th><th>Op</th><th>Value</th><th></th></tr>
  </thead>
  <tbody id="whereBody">
    <?php
      $wrows = max(count($whereTable), count($whereCol), count($whereOp), count($whereVal), 1);
      for ($i=0;$i<$wrows;$i++):
        $wt = (string)($whereTable[$i] ?? '');
        $wc = (string)($whereCol[$i]   ?? '');
        $wo = (string)($whereOp[$i]    ?? '=');
        $wv = (string)($whereVal[$i]   ?? '');
    ?>
    <tr>
      <td><?= $i+1 ?></td>
      <td>
        <select name="where_table[]" class="out-source">
          <option value="">(auto)</option>
          <?php foreach ($scopeTables as $t): ?>
            <option value="<?= htmlspecialchars($t) ?>" <?= $t===$wt?'selected':'' ?>><?= htmlspecialchars($t) ?></option>
          <?php endforeach; ?>
        </select>
      </td>
      <td>
        <select name="where_col[]" class="out-source">
          <option value="">-- column --</option>
          <?php
            $uniqueCols = [];
            foreach ($scopeTables as $t) foreach (getTableCols($t) as $c) $uniqueCols[$c] = true;
            foreach (array_keys($uniqueCols) as $c):
          ?>
            <option value="<?= htmlspecialchars($c) ?>" <?= $c===$wc ? 'selected':'' ?>><?= htmlspecialchars($c) ?></option>
          <?php endforeach; ?>
        </select>
      </td>
      <td>
        <select name="where_op[]">
          <?php foreach (['=','<>','>','<','>=','<=','LIKE','NOT LIKE','IN','NOT IN','BETWEEN','IS NULL','IS NOT NULL'] as $op): ?>
            <option value="<?= $op ?>" <?= $op===$wo?'selected':'' ?>><?= $op ?></option>
          <?php endforeach; ?>
        </select>
      </td>
	  
      <td><input type="text" name="where_val[]" value="<?= htmlspecialchars($wv) ?>" placeholder="value(s)"></td>
      <td><button type="button" onclick="delWhereRow(this)">Delete</button></td>
    </tr>
    <?php endfor; ?>
  </tbody>
</table>
<div class="actions">
  <button type="button" onclick="addWhereRow()">+ Add filter</button>
  <!--<button type="submit" name="refresh" value="1">Refresh</button>-->
</div>

<hr>
<?php endif; ?>
    

   <div class="actions" style="margin-top:16px;">
  <button type="submit" name="show_top" value="1">Show TOP 100</button>
  <!--<button type="submit" name="show_all" value="1">Show All</button>-->
  <button type="submit" name="download_csv" value="1">Download CSV</button>
  <!--<button type="submit" name="download_xlsx" value="1">Download XLSX</button>-->

  <!-- NEW: errors export -->
  <button type="submit" name="download_errors_csv" value="1">Download Errors CSV</button>
  <!--<button type="submit" name="download_errors_xlsx" value="1">Download Errors XLSX</button>-->
  <!--<button type="submit" name="count_rows" value="1">Count rows</button>-->
  <?php if ($totalValidCount !== null): ?>
  <div class="small" style="margin-top:8px;">
    Total rows matching filters/joins: <strong><?= number_format($totalValidCount) ?></strong>
  </div>
<?php endif; ?>


</div>

  <?php endif; ?>
  


<?php if ($error): ?>
  <div class="error"><?= htmlspecialchars($error) ?></div>
<?php endif; ?>

<?php if (!empty($builtSql)): ?>
  <script>
    // Collapsed console group so it doesn't look scary
    console.groupCollapsed('SQL (preview)');
    console.log(<?= json_encode($builtSql, JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES|JSON_HEX_TAG|JSON_HEX_AMP|JSON_HEX_APOS|JSON_HEX_QUOT) ?>);
    console.groupEnd();
  </script>
<?php endif; ?>


<?php if (!empty($rows)): ?>
  <h3>Results (<?= $action_all ? 'All' : 'Top 100' ?>)</h3>
  <div style="overflow:auto; max-height:70vh;">
  <div class="results-wrap">
    <table>
      <thead>
        <tr>
          <?php
            // Show only aliases in the header if we have a recent run
            if (isset($aliasesOrdered) && is_array($aliasesOrdered) && !empty($aliasesOrdered)) {
                foreach ($aliasesOrdered as $alias) echo '<th>'.htmlspecialchars($alias).'</th>';
            } else {
                // Fallback (shouldn’t happen after a run): derive from first row keys
                $firstRow = is_array($rows) ? reset($rows) : [];
			if (is_array($firstRow)) {
				foreach (array_keys($firstRow) as $col) {
					echo '<th>'.htmlspecialchars($col).'</th>';
				}
			}
            }
          ?>
        </tr>
      </thead>
       <tbody>
        <?php foreach ($rows as $r): ?>
          <tr>
            <?php
              if (isset($aliasesOrdered) && is_array($aliasesOrdered) && !empty($aliasesOrdered)) {
                  foreach ($aliasesOrdered as $alias) {
                      $v = $r[$alias] ?? '';
                      echo '<td>'.htmlspecialchars((string)($v ?? 'NULL')).'</td>';
                  }
              } else {
                  foreach ($r as $v) echo '<td>'.htmlspecialchars((string)($v ?? 'NULL')).'</td>';
              }
            ?>
          </tr>
        <?php endforeach; ?>
      </tbody>
    </table>
  </div>
  </div>
<?php elseif (($action_top || $action_all) && !$error): ?>
  <p>No rows found.</p>
<?php endif; ?>


<?php if ($invalidTotal !== null): ?>
  <div class="small" style="margin-top:8px;">
    <?php if (LOG_INVALIDS_MODE === 'off'): ?>
      Found <?= number_format($invalidTotal) ?> invalid rows (logging disabled).
    <?php elseif (LOG_INVALIDS_MODE === 'count-only'): ?>
      Found <?= number_format($invalidTotal) ?> invalid rows (not logged — count only).
    <?php else: // cap ?>
      Found <?= number_format($invalidTotal) ?> invalid rows.
      Logged first <?= number_format(min($invalidTotal, MAX_INVALIDS_TO_LOG)) ?> rows for this run token.
    <?php endif; ?>
  </div>
<?php endif; ?>



<?php if (!empty($errorsThisRun)): ?>
  <h3>Invalid rows (this run)</h3>
  <div style="overflow:auto; max-height:40vh;">
    <table>
      <thead>
		<tr>
			<th>Time (UTC)</th>
			<th>Base table</th>   
			<th>Bad fields</th>
			<th>Per-field reasons</th>
			<th>Rule summary</th>
			<th>Row (JSON)</th>
		</tr>
		</thead>
      <tbody>
        <?php foreach ($errorsThisRun as $er): ?>
          <?php
            // Backward-safe extraction (works even if columns don't exist yet)
            $createdAt = $er['created_at'] ?? '';
            $badFields = $er['bad_fields']    // from z0ErrorsExport new column
                         ?? $er['__BadFields'] // in case you display directly from SQL (not typical here)
                         ?? '';
            $fieldReasons = $er['field_reasons']
                            ?? $er['__FieldReasons']
                            ?? '';
            $ruleSummary = $er['rule_summary'] ?? $er['__RuleSummary'] ?? '';
            $rowJson     = $er['row_json'] ?? '';
          ?>
          <tr>
            <td>
              <?= htmlspecialchars(
                $createdAt instanceof DateTimeInterface
                  ? $createdAt->format('Y-m-d H:i:s')
                  : (string)$createdAt
              ) ?>
            </td>
            <td><?= htmlspecialchars((string)($er['base_table'] ?? '')) ?></td>
			<td><?= htmlspecialchars((string)($er['bad_fields'] ?? $er['__BadFields'] ?? '')) ?></td>
			<td><?= htmlspecialchars((string)($er['field_reasons'] ?? $er['__FieldReasons'] ?? '')) ?></td>
			<td><?= htmlspecialchars((string)($er['rule_summary'] ?? $er['__RuleSummary'] ?? '')) ?></td>
			<td><pre style="white-space:pre-wrap; margin:0;"><?= htmlspecialchars((string)($er['row_json'] ?? '')) ?></pre></td>
		  </tr>
        <?php endforeach; ?>
      </tbody>
    </table>
  </div>
<?php endif; ?>




<hr>
<h3>Save preset</h3>
<div class="actions" style="gap:12px; align-items:center; flex-wrap:wrap;">
  <div>
    <label>Preset name</label><br>
    <input type="text" name="preset_name" placeholder="e.g. Customers 2023 report">
    <button type="submit" name="save_preset" value="1">Save current options</button>
  </div>
</div>

<?php if ($saveOk): ?>
  <div class="small" style="color:#22c55e;">Preset saved.</div>
<?php endif; ?>
<?php if ($saveError): ?>
  <div class="small" style="color:#ff6b6b; white-space:pre-wrap;"><?= htmlspecialchars($saveError) ?></div>
<?php endif; ?>




</form>

<script>
// ---------- helpers & config ----------

let cachedExistsOptionsHTML = ''; // for out_exists_src[]
const schemaPhp = '<?= htmlspecialchars($SCHEMA) ?>';

// safely escape HTML and CSS attribute selectors
function escapeHtml(s){ return $('<div>').text(s).html(); }
function cssEscape(v){ return (v||'').replace(/([ #;?%&,.+*~\':"!^$[\]()=>|\/@])/g,'\\$1'); }

// Select2 binding for any dynamically-added selects with class .out-source
function bindSelect2(scope) {
  (scope ? $(scope) : $(document))
    .find('select.out-source')
    .not('.select2-hidden-accessible')
    .select2({ width: 'resolve' });
}
const allTables = <?= json_encode(array_values($tables), JSON_UNESCAPED_UNICODE) ?>;


const existsTableOptionsHTML =
  '<option value="">— choose table —</option>' +
  (<?= json_encode(array_values($tables)) ?>).map(t => `<option value="${escapeHtml(t)}">${escapeHtml(t)}</option>`).join('');




// keep source disabled if "literal" is ticked
function bindLiteralToggles(scope){
  (scope ? $(scope) : $(document)).find('input.out-is-lit').each(function(){
    const $row = $(this).closest('tr');
    const $src = $row.find('select[name="out_source[]"]');
    const checked = $(this).is(':checked');
    $src.prop('disabled', checked).trigger('change.select2');
  });
}

// rebuild a (columns) <select> with a new columns list, preserving selection if possible
function rebuildColSelect($select, cols, placeholder){
  const old = $select.val();
  let html = '<option value="">' + (placeholder || '-- select --') + '</option>';
  cols.forEach(c => { html += '<option value="'+escapeHtml(c)+'">'+escapeHtml(c)+'</option>'; });
  $select.html(html);
  if (old && cols.indexOf(old) !== -1) $select.val(old); else $select.val('');
  $select.trigger('change.select2');
}

// ---------- ajax: fetch columns with an in-memory cache ----------
const colsCache = new Map(); // key: table, value: Promise<string[]>
function fetchCols(schema, table){
  if (!table) return Promise.resolve([]);
  if (!colsCache.has(table)) {
    const p = $.post(window.location.href, { ajax:'cols', schema, table }, null, 'json')
      .then(res => Array.isArray(res?.columns) ? res.columns : [])
      .catch(() => []);
    colsCache.set(table, p);
  }
  return colsCache.get(table);
}

// ---------- figure out the "scope" tables from current UI ----------
/*async function getScopeTablesFromDOM() {
  const base = $('select[name="table"]').val() || '';
  const tables = new Set();
  if (base) tables.add(base);
  $('select[name="join_left_table[]"]').each(function(){ const v=$(this).val(); if (v) tables.add(v); });
  $('select[name="join_right_table[]"]').each(function(){ const v=$(this).val(); if (v) tables.add(v); });
  return Array.from(tables);
}*/




async function getScopeTablesFromDOM() {
  const base = $('select[name="table"]').val() || '';
  const tables = new Set();
  if (base) tables.add(base);

  if (!<?= UI_DISABLE_JOINS ? 'true' : 'false' ?>) {
    $('select[name="join_left_table[]"]').each(function(){ const v=$(this).val(); if (v) tables.add(v); });
    $('select[name="join_right_table[]"]').each(function(){ const v=$(this).val(); if (v) tables.add(v); });
  }
  return Array.from(tables);
}


// cached option HTML (for adding new rows instantly)
let cachedSourceOptionsHTML = ''; // for out_source[]
let cachedWhereTableHTML   = '';  // for where_table[]
let cachedWhereColsHTML    = '';  // for where_col[]






// table picker for "exists_in"
// --- exists_in handlers ---
$(document).on('change', '.out-exists-table', async function () {
  const $cell = $(this).closest('.exists-cell');
  const table = $(this).val();
  const $col  = $cell.find('.out-exists-col');
  const $hid  = $cell.find('input[name="out_exists_src[]"]');

  if (!table) {
    $col.prop('disabled', true)
        .html('<option value="">— choose column —</option>')
        .trigger('change.select2');
    $hid.val('');
    return;
  }

  const cols = await fetchCols(schemaPhp, table);
  let html = '<option value="">— choose column —</option>';
  cols.forEach(c => { html += `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`; });
  $col.html(html).prop('disabled', false).trigger('change.select2');
  $col.val('').trigger('change.select2');
  $hid.val('');
});

$(document).on('change', '.out-exists-col', function () {
  const $cell = $(this).closest('.exists-cell');
  const t = $cell.find('.out-exists-table').val();
  const c = $(this).val();
  $cell.find('input[name="out_exists_src[]"]').val(t && c ? `${t}|${c}` : '');
});

// --- rule→picker enable/disable + header toggle ---
function rowHasExistsRule($row) {
  const vals = ($row.find('select.out-rule').val() || []);
  return vals.includes('exists_in');
}
function updateExistsHeaderVisibility() {
  const anyShown = $('#outColsTbody .exists-cell:visible').length > 0;
  $('.exists-col-head').toggle(anyShown);
}
function enableExistsPickersForRow(scope) {
  const $row = $(scope);
  const on = rowHasExistsRule($row);
  const $tbl = $row.find('.out-exists-table');
  const $col = $row.find('.out-exists-col');

  $row.find('.exists-cell').toggle(on);
  $tbl.prop('disabled', !on).trigger('change.select2');
  $col.prop('disabled', !on).trigger('change.select2');

  if (!on) {
    $tbl.val('').trigger('change.select2');
    $col.html('<option value="">— choose column —</option>').val('').trigger('change.select2');
    $row.find('input[name="out_exists_src[]"]').val('');
  }
  updateExistsHeaderVisibility();
}
$(document).on('change', 'select.out-rule', function(){
  enableExistsPickersForRow($(this).closest('tr'));
});

// --- hydrate saved "Table|Column" pairs on load (top-level function) ---
async function hydrateExistsPickers(scope) {
  (scope ? $(scope) : $(document)).find('.exists-cell').each(async function () {
    const $cell = $(this);
    const saved = $cell.find('input[name="out_exists_src[]"]').val() || '';
    if (!saved.includes('|')) return;

    const [t, c] = saved.split('|', 2);
    $cell.find('.out-exists-table').val(t).trigger('change.select2');

    const cols = await fetchCols(schemaPhp, t);
    let html = '<option value="">— choose column —</option>';
    cols.forEach(col => { html += `<option value="${escapeHtml(col)}">${escapeHtml(col)}</option>`; });

    const $col = $cell.find('.out-exists-col');
    $col.html(html).prop('disabled', false).trigger('change.select2');
    $col.val(c).trigger('change.select2');
  });
}





// ---------- recompute options for Output + WHERE based on scope (no refresh) ----------
async function recomputeScopeDrivenOptions() {
  const scopeTables = await getScopeTablesFromDOM();

  // Pull columns for all scope tables in parallel
  const tableColsArr = await Promise.all(scopeTables.map(t => fetchCols(schemaPhp, t)));
  const tableCols = Object.fromEntries(scopeTables.map((t,i) => [t, tableColsArr[i]]));

	// Build "Table.Column" options for Output Sources, but shorten when possible
	let sourceOptions = '<option value="">— New (empty) —</option>';
	
	// Count column-name collisions across scope tables
	const colCounts = {};
	scopeTables.forEach(t => (tableCols[t] || []).forEach(c => {
	colCounts[c] = (colCounts[c] || 0) + 1;
	}));
	
	// Build labels: "Column" if unique; else "Table.Column"
	scopeTables.forEach(t => (tableCols[t] || []).forEach(c => {
	const val = `${t}|${c}`;
	const label = (colCounts[c] > 1) ? `${t}.${c}` : c;
	sourceOptions += `<option value="${escapeHtml(val)}">${escapeHtml(label)}</option>`;
	}));
	cachedSourceOptionsHTML = sourceOptions;
	







function updateExistsHeaderVisibility() {
  const anyShown = $('#outColsTbody .exists-cell').filter(function () {
    return $(this).is(':visible');
  }).length > 0;
  $('.exists-col-head').toggle(anyShown);
}



  // WHERE table options
  let whereTableHTML = '<option value="">(auto)</option>';
  scopeTables.forEach(t => { whereTableHTML += `<option value="${escapeHtml(t)}">${escapeHtml(t)}</option>`; });
  cachedWhereTableHTML = whereTableHTML;

  // WHERE column options (unique across scope)
  const uniqueCols = new Set();
  scopeTables.forEach(t => (tableCols[t]||[]).forEach(c => uniqueCols.add(c)));
  let whereColsHTML = '<option value="">-- column --</option>';
  Array.from(uniqueCols).sort().forEach(c => { whereColsHTML += `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`; });
  cachedWhereColsHTML = whereColsHTML;

  // Apply to existing Output Source selects (keep value if still valid) "Compare to" selects used by exists_in
	/*
$('select[name="out_exists_src[]"]').each(function(){
  const old = $(this).val();
  $(this).html(cachedExistsOptionsHTML || '<option value="">— choose lookup column —</option>');
  if (old && $(this).find(`option[value="${cssEscape(old)}"]`).length) {
    $(this).val(old);
  } else {
    $(this).val('');
  }
  $(this).trigger('change.select2');
});*/



  // Apply to WHERE selects
  $('select[name="where_table[]"]').each(function(){
    const old = $(this).val();
    $(this).html(cachedWhereTableHTML);
    if (old && $(this).find(`option[value="${cssEscape(old)}"]`).length) $(this).val(old);
    $(this).trigger('change.select2');
  });

  $('select[name="where_col[]"]').each(function(){
    const old = $(this).val();
    $(this).html(cachedWhereColsHTML);
    if (old && $(this).find(`option[value="${cssEscape(old)}"]`).length) $(this).val(old);
    $(this).trigger('change.select2');
  });

  bindLiteralToggles();
}



// --- ON-conditions: keep left/right column lists in sync for a join row ---
async function refreshJoinOnSelects(joinIdx){
  const lt = $('select[name="join_left_table[]"]').eq(joinIdx).val() || '';
  const rt = $('select[name="join_right_table[]"]').eq(joinIdx).val() || '';

  const [leftCols, rightCols] = await Promise.all([
    fetchCols(schemaPhp, lt),
    fetchCols(schemaPhp, rt)
  ]);

  // Update ALL "left col" selects in ON table for this join
  $(`select[name="join_on_left_col[${joinIdx}][]"]`).each(function(){
    rebuildColSelect($(this), leftCols, '-- select --');
  });

  // Update ALL "right col" selects in ON table for this join
  $(`select[name="join_on_right[${joinIdx}][]"]`).each(function(){
    rebuildColSelect($(this), rightCols, '-- select right col --');
  });
}

// Hook ON-conditions refresh into existing events
$(document).on('change', 'select[name="join_left_table[]"]', async function () {
  const i = $('select[name="join_left_table[]"]').index(this);
  const table = $(this).val();
  if (table) {
    const cols = await fetchCols(schemaPhp, table);
    rebuildColSelect($('select[name="join_left_col[]"]').eq(i), cols);
  }
  await refreshJoinOnSelects(i);           // <-- update ON rows (left side)
  await recomputeScopeDrivenOptions();
});

$(document).on('change', 'select[name="join_right_table[]"]', async function () {
  const i = $('select[name="join_right_table[]"]').index(this);
  const table = $(this).val();
  if (table) {
    const cols = await fetchCols(schemaPhp, table);
    rebuildColSelect($('select[name="join_right_col[]"]').eq(i), cols);
  }
  await refreshJoinOnSelects(i);           // <-- update ON rows (right side)
  await recomputeScopeDrivenOptions();
});

// When adding a new ON row, populate it with the current LT/RT columns
function addJoinOnRow(i){
  const $tb = $('#joinOnBody'+i);
  const $last = $tb.children('tr:last');
  const $clone = $last.clone(true);

  // clear inputs
  $clone.find('select').val('').trigger('change.select2');
  $clone.find('input[type="checkbox"]').prop('checked', false);
  $clone.find('input[type="text"]').val('');

  $tb.append($clone);
  bindSelect2($clone);

  // fill options according to current join tables
  refreshJoinOnSelects(i);
}

// Also refresh all ON blocks once on page load (for existing joins)
$(async function(){
  $('select[name="join_left_table[]"]').each(function(idx){
    refreshJoinOnSelects(idx);
  });
});

// If base table changes, some joins might still point to it; refresh ON lists for all joins
$('select[name="table"]').on('change', async function(){
  $('select[name="join_left_table[]"]').each(function(idx){
    refreshJoinOnSelects(idx);
  });
});



// ---------- dynamic row add/remove (JOIN ON, OUTPUT, WHERE) ----------
function addJoinOnRow(i){
  const $tb = $('#joinOnBody'+i);
  const $last = $tb.children('tr:last');
  const $clone = $last.clone(true);

  // clear inputs
  $clone.find('select').val('').trigger('change.select2');
  $clone.find('input[type="checkbox"]').prop('checked', false);
  $clone.find('input[type="text"]').val('');

  $tb.append($clone);
  bindSelect2($clone);
}
function delJoinOnRow(i, btn){
  const $tb = $('#joinOnBody' + i);
  const $rows = $tb.children('tr');
  if ($rows.length <= 1) {
    const $r = $(btn).closest('tr');
    $r.find('select').val('').trigger('change.select2');
    $r.find('input[type="checkbox"]').prop('checked', false);
    $r.find('input[type="text"]').val('');
    return;
  }
  $(btn).closest('tr').remove();
}

// Output rows
function existsCellHtml() {
  return [
    '<td class="exists-cell">',
      '<select name="out_exists_table[]" class="out-exists-table out-source">',
        existsTableOptionsHTML,            // <-- use the JS const here
      '</select>',
      '<select name="out_exists_col[]" class="out-exists-col out-source" disabled>',
        '<option value="">— choose column —</option>',
      '</select>',
      '<input type="hidden" name="out_exists_src[]" value="">',
    '</td>'
  ].join('');
}



function addOutRow() {
  const $tbody = $('#outColsTbody');
  const i = $tbody.children('tr').length;
  const sourceHtml = cachedSourceOptionsHTML || '<option value="">— New (empty) —</option>';

  const rowHtml = [
    '<tr>',
      '<td></td>',
      '<td><input type="text" name="out_name[]" value="" placeholder="e.g. CustomerName"></td>',
      '<td><select name="out_source[]" class="out-source">', sourceHtml, '</select></td>',
      '<td>',
        '<select name="out_rule[' + i + '][]" class="out-rule out-source" multiple>',
          '<option value="mandatory">mandatory</option>',
          '<option value="dateformat">dateformat (yyyy-mm-dd)</option>',
          '<option value="exists_in">exists_in (lookup)</option>',
        '</select>',
      '</td>',
      existsCellHtml(),                       // <— new exists cell (table+col+hidden)
      '<td>',
        '<select name="out_type[]">',
          '<option value="">—</option>',
          '<option value="int">integer</option>',
          '<option value="decimal">decimal(38,10)</option>',
          '<option value="float">float</option>',
          '<option value="bit">bit (0/1)</option>',
          '<option value="date">date (yyyy-mm-dd)</option>',
          '<option value="datetime2">datetime2</option>',
          '<option value="nvarchar">nvarchar</option>',
        '</select>',
      '</td>',
      '<td><input type="text" name="out_maxlen[]" placeholder="e.g. 50"></td>',
      '<td style="text-align:center;"><input type="checkbox" name="out_is_lit[' + i + ']" value="1" class="out-is-lit"></td>',
      '<td><input type="text" name="out_lit_val[]" class="out-lit-val" placeholder="e.g. f or 2000"></td>',
      '<td><button type="button" class="btn-del-row" onclick="delOutRow(this)">Delete</button></td>',
    '</tr>'
  ].join('');

  const $row = $(rowHtml).appendTo($tbody);
  bindSelect2($row);
  bindLiteralToggles($row);
  enableExistsPickersForRow($row);           // enable/disable based on rule
  

  
  $('#outColsTbody tr').each(function(idx){ $(this).find('td:first').text(idx+1); });
}


function delOutRow(btn) {
  $(btn).closest('tr').remove();
  $('#outColsTbody tr').each(function(i, el){ $(el).find('td:first').text(i+1); });
}



// Build the quick-add options (Table.Column with value "Table|Column")
async function refreshQuickAddPicker() {
  const scopeTables = await getScopeTablesFromDOM();
  const tableColsArr = await Promise.all(scopeTables.map(t => fetchCols(schemaPhp, t)));
  const tableCols = Object.fromEntries(scopeTables.map((t,i) => [t, tableColsArr[i]]));

  // Count collisions to decide labels
  const colCounts = {};
  scopeTables.forEach(t => (tableCols[t]||[]).forEach(c => { colCounts[c] = (colCounts[c]||0) + 1; }));

  let html = '';
  scopeTables.forEach(t => (tableCols[t]||[]).forEach(c => {
    const val = `${t}|${c}`;
    const label = (colCounts[c] > 1) ? `${t}.${c}` : c;
    html += `<option value="${$('<div>').text(val).html()}">${$('<div>').text(label).html()}</option>`;
  }));

  const $qa = $('#quickAddCols');
  $qa.html(html);
  bindSelect2($qa);
}

function makeUniqueAlias(base, takenLower) {
  let name = base;
  let i = 2;
  while (takenLower.has(name.toLowerCase())) {
    name = `${base}_${i++}`;
  }
  takenLower.add(name.toLowerCase());
  return name;
}

// Add selected quick columns into Output builder rows
function addQuickSelected() {
  const vals = $('#quickAddCols').val() || [];
  if (!vals.length) return;

  const $tbody = $('#outColsTbody');

  // Collect used aliases to avoid duplicates
  const taken = new Set();
  $tbody.find('input[name="out_name[]"]').each(function(){
    const v = ($(this).val() || '').trim();
    if (v) taken.add(v.toLowerCase());
  });

  function makeUniqueAlias(base) {
    let name = base || 'col';
    let k = 2;
    while (taken.has(name.toLowerCase())) name = base + '_' + (k++);
    taken.add(name.toLowerCase());
    return name;
  }

  vals.forEach((val) => {
  const [table, col] = (val || '').split('|');
  const alias = makeUniqueAlias(col);

  const i = $tbody.children('tr').length; // index for new row
  const rowHtml = [
    '<tr>',
      '<td></td>',
      `<td><input type="text" name="out_name[]" value="${$('<div>').text(alias).html()}"></td>`,
      '<td><select name="out_source[]" class="out-source">', (cachedSourceOptionsHTML || '<option value="">— New (empty) —</option>'), '</select></td>',
      '<td>',
        '<select name="out_rule[' + i + '][]" class="out-rule out-source" multiple>',
          '<option value="mandatory">mandatory</option>',
          '<option value="dateformat">dateformat (yyyy-mm-dd)</option>',
          '<option value="exists_in">exists_in (lookup)</option>',
        '</select>',
      '</td>',
      existsCellHtml(),
      '<td>',
        '<select name="out_type[]">',
          '<option value="">—</option>',
          '<option value="int">integer</option>',
          '<option value="decimal">decimal(38,10)</option>',
          '<option value="float">float</option>',
          '<option value="bit">bit (0/1)</option>',
          '<option value="date">date (yyyy-mm-dd)</option>',
          '<option value="datetime2">datetime2</option>',
          '<option value="nvarchar">nvarchar</option>',
        '</select>',
      '</td>',
      '<td><input type="text" name="out_maxlen[]" placeholder="e.g. 50"></td>',
      '<td style="text-align:center;"><input type="checkbox" name="out_is_lit[' + i + ']" value="1" class="out-is-lit"></td>',
      '<td><input type="text" name="out_lit_val[]" class="out-lit-val" placeholder="e.g. f or 2000"></td>',
      '<td><button type="button" class="btn-del-row" onclick="delOutRow(this)">Delete</button></td>',
    '</tr>'
  ].join('');

  const $row = $(rowHtml).appendTo($tbody);
  bindSelect2($row);
  bindLiteralToggles($row);
  enableExistsPickersForRow($row);

  // ✅ set the Source to the picked column
  $row.find('select[name="out_source[]"]')
    .val(val)
    .trigger('change.select2');

  // (optional) hydrate the exists lookup dropdown shell you add later
  $row.find('select[name="out_exists_src[]"]')
    .html(cachedExistsOptionsHTML || '<option value="">— choose lookup column —</option>')
    .trigger('change.select2');
});

// renumber rows after the loop
$('#outColsTbody tr').each(function(idx){ $(this).find('td:first').text(idx+1); });

}

$(async function(){
	await refreshQuickAddPicker();
	await hydrateExistsPickers();
  // refresh picker whenever scope changes
  $(document).on('change', 'select[name="table"], select[name="join_left_table[]"], select[name="join_right_table[]"]', refreshQuickAddPicker);

  $('#btnQuickAdd').on('click', addQuickSelected);
});






// WHERE rows
function addWhereRow(){
  const $tb = $('#whereBody');
  const idx = $tb.children('tr').length + 1;

  const rowHtml = [
    '<tr>',
      '<td>', idx, '</td>',
      '<td><select name="where_table[]" class="out-source">', (cachedWhereTableHTML || '<option value="">(auto)</option>'), '</select></td>',
      '<td><select name="where_col[]" class="out-source">', (cachedWhereColsHTML || '<option value="">-- column --</option>'), '</select></td>',
      '<td>',
        '<select name="where_op[]">',
          '<option value="=">=</option>',
          '<option value="<>">&lt;&gt;</option>',
          '<option value=">">&gt;</option>',
          '<option value="<">&lt;</option>',
          '<option value=">=">&gt;=</option>',
          '<option value="<=">&lt;=</option>',
          '<option value="LIKE">LIKE</option>',
          '<option value="NOT LIKE">NOT LIKE</option>',
          '<option value="IN">IN</option>',
          '<option value="NOT IN">NOT IN</option>',
          '<option value="BETWEEN">BETWEEN</option>',
          '<option value="IS NULL">IS NULL</option>',
          '<option value="IS NOT NULL">IS NOT NULL</option>',
        '</select>',
      '</td>',
      '<td><input type="text" name="where_val[]" placeholder="value(s)"></td>',
      '<td><button type="button" onclick="delWhereRow(this)">Delete</button></td>',
    '</tr>'
  ].join('');

  const $row = $(rowHtml).appendTo($tb);
  bindSelect2($row);
}
function delWhereRow(btn){
  const $tb = $('#whereBody');
  $(btn).closest('tr').remove();
  $tb.children('tr').each(function(i, el){ $(el).find('td:first').text(i+1); });
}



// ---------- INIT & event wiring ----------
$(async function () {
  // Enhance base & join selects with Select2
  $('select[name="table"]').select2({ width: 'resolve' });
  $('select[name="join_left_table[]"], select[name="join_left_col[]"], select[name="join_right_table[]"], select[name="join_right_col[]"]').select2({ width: 'resolve' });
  bindSelect2();           // enhance any out-source selects already in DOM
  bindLiteralToggles();    // keep source disabled if literal was pre-checked

  // initial options cache + paint
  await recomputeScopeDrivenOptions();

  // Base table change: update left-col lists that use it, then recompute scope-driven options
  $('select[name="table"]').on('change', async function () {
    const table = $(this).val();
    if (table) {
      const cols = await fetchCols(schemaPhp, table);
      $('select[name="join_left_table[]"]').each(function(i, el){
        if ($(el).val() === table) {
          rebuildColSelect($('select[name="join_left_col[]"]').eq(i), cols);
        }
      });
    }
    await recomputeScopeDrivenOptions();
  });

  // Per-join Left table change
  $(document).on('change', 'select[name="join_left_table[]"]', async function () {
    const rowIdx = $('select[name="join_left_table[]"]').index(this);
    const table  = $(this).val();
    if (table) {
      const cols = await fetchCols(schemaPhp, table);
      rebuildColSelect($('select[name="join_left_col[]"]').eq(rowIdx), cols);
    }
    await recomputeScopeDrivenOptions();
  });

  // Per-join Right table change
  $(document).on('change', 'select[name="join_right_table[]"]', async function () {
    const rowIdx = $('select[name="join_right_table[]"]').index(this);
    const table  = $(this).val();
    if (table) {
      const cols = await fetchCols(schemaPhp, table);
      rebuildColSelect($('select[name="join_right_col[]"]').eq(rowIdx), cols);
      $(`select[name="join_on_right[${rowIdx}][]"]`).each(function(){
        rebuildColSelect($(this), cols, '-- select right col --');
      });
    }
    await recomputeScopeDrivenOptions();
  });

  // When a source is chosen for an Output row, uncheck literal on that row
  $(document).on('change', 'select[name="out_source[]"]', function(){
    const $row = $(this).closest('tr');
    const $lit = $row.find('input.out-is-lit');
    if ($(this).val()) {
      $lit.prop('checked', false);
      bindLiteralToggles($row);
    }
  });

  // Literal checkbox toggles
  $(document).on('change', 'input.out-is-lit', function(){
    bindLiteralToggles($(this).closest('tr'));
  });

  // If you ever add JOIN rows client-side, call recompute afterward.
  // (Currently joins are added by server postback; this is future-proof.)
  $(document).on('click', 'button[name="add_join"]', async function () {
    await recomputeScopeDrivenOptions();
  });
});
</script>



</body>
</html>
