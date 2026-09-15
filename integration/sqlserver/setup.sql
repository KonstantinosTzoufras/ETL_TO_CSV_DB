-- Run manually in a dedicated test database using SSMS or sqlcmd -f 65001.
-- Intentionally refuses to overwrite an existing table. No DROP/TRUNCATE.
SET NOCOUNT ON;
SET XACT_ABORT ON;
IF OBJECT_ID(N'dbo.ETLStage4Fixture', N'U') IS NOT NULL
    THROW 50001, 'ETLStage4Fixture already exists. Use a fresh test database.', 1;

BEGIN TRANSACTION;
CREATE TABLE dbo.ETLStage4Fixture (
    row_id bigint NOT NULL PRIMARY KEY,
    case_id int NOT NULL,
    code varchar(32) NULL,
    greek_text nvarchar(64) NULL,
    native_bigint bigint NULL,
    native_int int NULL,
    amount decimal(38,18) NULL,
    enabled bit NULL,
    native_date date NULL,
    native_datetime datetime NULL,
    date_text varchar(32) NULL,
    optional_text nvarchar(40) NULL,
    required_probe nvarchar(16) NULL,
    memo nvarchar(max) NULL
);

INSERT dbo.ETLStage4Fixture VALUES
(1, 1, '003', N'Αθήνα', 9007199254740993, 2147483647,
 CAST('12345678901234567890.123456789012345678' AS decimal(38,18)), 1,
 CONVERT(date,'20240229',112), CONVERT(datetime,'2024-02-29T12:34:56.123',126),
 '2024-02-29', N'', N'OK', N'Πρώτη γραμμή' + NCHAR(13) + NCHAR(10) + N'Δεύτερη γραμμή'),
(2, 2, ' 17 ', N'  Θεσσαλονίκη  ', -9007199254740993, -2147483648,
 CAST('-0.000000000000000001' AS decimal(38,18)), 0,
 CONVERT(date,'20240101',112), CONVERT(datetime,'2024-01-01T00:00:00.000',126),
 '2024-01-01', N'   ', N'OK', N'Κείμενο; με "εισαγωγικά"'),
(3, 3, '', N'', 0, 0, CAST('0' AS decimal(38,18)), 0,
 NULL, NULL, '', N'', N'', N''),
(4, 4, NULL, NULL, NULL, NULL, NULL, NULL,
 NULL, NULL, NULL, NULL, N'OK', NULL),
(5, 5, '   ', N'   ', 5, 5, CAST('1.230000000000000000' AS decimal(38,18)), 1,
 CONVERT(date,'20240229',112), CONVERT(datetime,'2024-02-29T01:02:03.000',126),
 '29/02/2024', N'   ', N'   ', N'γραμμή 1' + NCHAR(10) + N'γραμμή 2'),
(6, 6, '42', N'Πάτρα', 42, 42, CAST('99999999999999999999.999999999999999999' AS decimal(38,18)), 1,
 CONVERT(date,'20230228',112), CONVERT(datetime,'2023-02-28T23:59:59.997',126),
 '2023-02-29', N'  μέσα  ', NULL, N'Μη έγκυρη ημερομηνία ως κείμενο'),
(7, 7, '7', N'Ηράκλειο', 7, 7, CAST('3.140000000000000000' AS decimal(38,18)), 1,
 CONVERT(date,'20240229',112), CONVERT(datetime,'2024-02-29T12:00:00.000',126),
 '2024-02-29', N'κείμενο', N'OK', N'Κανονική εγγραφή');
COMMIT;

SELECT COUNT_BIG(*) AS fixture_rows FROM dbo.ETLStage4Fixture;
