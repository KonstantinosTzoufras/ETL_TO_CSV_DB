-- Optional: run AFTER setup.sql in the same dedicated test database.
-- Choose total rows (including the seven seeds): 10000 or 100000.
-- Append-only and repeatable; does not shrink a previously larger dataset.
SET NOCOUNT ON;
SET XACT_ABORT ON;
DECLARE @TargetRows int = 10000;
IF @TargetRows NOT IN (10000, 100000)
    THROW 50002, 'Choose 10000 or 100000 rows.', 1;
IF (SELECT COUNT(*) FROM dbo.ETLStage4Fixture WHERE row_id BETWEEN 1 AND 7 AND case_id = row_id) <> 7
    THROW 50003, 'Seven original fixture seeds are required.', 1;

BEGIN TRANSACTION;
;WITH digits(n) AS (
    SELECT n FROM (VALUES(0),(1),(2),(3),(4),(5),(6),(7),(8),(9)) AS d(n)
), numbers(n) AS (
    SELECT 1 + a.n + 10*b.n + 100*c.n + 1000*d.n + 10000*e.n
    FROM digits a CROSS JOIN digits b CROSS JOIN digits c CROSS JOIN digits d CROSS JOIN digits e
)
INSERT dbo.ETLStage4Fixture
SELECT n.n, s.case_id, s.code, s.greek_text, s.native_bigint, s.native_int,
       s.amount, s.enabled, s.native_date, s.native_datetime, s.date_text,
       s.optional_text, s.required_probe, s.memo
FROM numbers n
JOIN dbo.ETLStage4Fixture s ON s.row_id = 1 + ((n.n - 1) % 7)
WHERE n.n > 7 AND n.n <= @TargetRows
  AND NOT EXISTS (SELECT 1 FROM dbo.ETLStage4Fixture existing WHERE existing.row_id = n.n);
COMMIT;
SELECT COUNT_BIG(*) AS fixture_rows FROM dbo.ETLStage4Fixture;
