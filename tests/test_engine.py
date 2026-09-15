import copy
import csv
import json
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

from etl.engine import convert, execute, excel_safe
from etl.sources import identifier, inspect_source, open_source
from etl.spec import ConfigError, validate
from etl.store import Store

ROOT = Path(__file__).resolve().parents[1]


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.spec = json.loads((ROOT / "examples/customers.json").read_text(encoding="utf-8"))
        self.spec["source"]["path"] = "input.csv"
        (self.root / "input.csv").write_bytes((ROOT / "examples/customers.csv").read_bytes())

    def test_preview_and_exports_agree_and_rejections_keep_reasons(self):
        preview = execute(self.spec, self.root, limit=100)
        report = execute(self.spec, self.root, self.root / "out")
        self.assertEqual((report["processed"], report["valid"], report["invalid"]), (5, 3, 2))
        self.assertEqual(preview["sample"], report["sample"])
        directory = Path(report["directory"])
        with (directory / "valid.csv").open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle, delimiter=";"))
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["full_name"], "Maria Papadopoulou")
        self.assertEqual(rows[0]["email"], "maria@example.test")
        self.assertEqual(rows[0]["origin"], "CRM")
        with (directory / "rejected.csv").open(encoding="utf-8-sig", newline="") as handle:
            rejected = list(csv.DictReader(handle, delimiter=";"))
        self.assertEqual(len(rejected), 2)
        self.assertEqual(set(json.loads(rejected[0]["errors_json"])), {"customer_id", "joined_on"})
        self.assertEqual(json.loads(rejected[0]["source_json"])["id"], "003")

    def test_preview_stops_before_bad_record_and_does_not_write(self):
        (self.root / "input.csv").write_text("id\n1\n2;extra\n", encoding="utf-8")
        self.spec["columns"] = [{"name": "id", "source": "id", "type": "int"}]
        report = execute(self.spec, self.root, limit=1)
        self.assertEqual(report["processed"], 1)
        self.assertNotIn("directory", report)
        with self.assertRaisesRegex(ConfigError, "expected 1 fields"):
            execute(self.spec, self.root, self.root / "out")

    def test_unknown_source_column_fails_even_on_empty_source(self):
        (self.root / "input.csv").write_text("other\n", encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "Unknown source column"):
            execute(self.spec, self.root, limit=100)

    def test_duplicate_headers_fail(self):
        (self.root / "input.csv").write_text("a;a\n1;2\n", encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "duplicate"):
            inspect_source(self.spec["source"], self.root)

    def test_workspace_path_escape_is_blocked(self):
        self.spec["source"]["path"] = "../outside.csv"
        with self.assertRaisesRegex(ConfigError, "inside the workspace"):
            execute(self.spec, self.root, limit=100)

    def test_unknown_options_and_duplicate_names_are_rejected(self):
        broken = copy.deepcopy(self.spec)
        broken["columns"][0]["rulez"] = ["required"]
        with self.assertRaises(ConfigError):
            validate(broken)
        broken = copy.deepcopy(self.spec)
        broken["columns"][1]["name"] = "CUSTOMER_ID"
        with self.assertRaisesRegex(ConfigError, "Duplicate"):
            validate(broken)

    def test_strict_types_and_exact_decimal_precision(self):
        for value in ("01", "1.0", "9223372036854775808", "true"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                convert(value, "int")
        self.assertEqual(convert("2024-02-29", "date"), date(2024, 2, 29))
        for value in ("2023-02-29", "20240101", "2024-1-1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                convert(value, "date")
        self.assertEqual(convert("123456789012345678.123456789", "decimal"), Decimal("123456789012345678.123456789"))
        for kind in ("float", "decimal"):
            for value in ("NaN", "Infinity", "1_000"):
                with self.subTest(kind=kind,value=value), self.assertRaises(ValueError):
                    convert(value, kind)
        self.assertFalse(convert("FALSE", "bool"))
        self.assertIsNone(convert("", "int"))

    def test_lookup_uses_same_transforms_and_type(self):
        (self.root / "countries.csv").write_text("code\n gr \n", encoding="utf-8")
        self.spec["columns"] = [{"name":"country", "source":"country", "transforms":["trim","upper"], "lookup":{"source":{"kind":"csv","path":"countries.csv"},"column":"code"}}]
        report = execute(self.spec, self.root, limit=100)
        self.assertEqual((report["valid"], report["invalid"]), (3, 2))
        self.assertIn("absent from lookup", report["sample"][2]["errors"]["country"][0])

    def test_formula_text_escaped_but_typed_negative_numbers_preserved(self):
        self.assertEqual(excel_safe("=1+1"), "'=1+1")
        self.assertEqual(excel_safe(" @SUM(A1)"), "' @SUM(A1)")
        self.assertEqual(excel_safe(-12), "-12")
        self.assertEqual(excel_safe(Decimal("-1.25")), "-1.25")

    def test_multiline_unicode_roundtrip(self):
        with (self.root / "input.csv").open("w", encoding="utf-8-sig", newline="") as handle:
            writer=csv.writer(handle,delimiter=";")
            writer.writerow(["name"])
            writer.writerow(['Μαρία; "δοκιμή"\nδεύτερη γραμμή'])
        self.spec["columns"]=[{"name":"name","source":"name"}]
        report=execute(self.spec,self.root,self.root / "out")
        with (Path(report["directory"]) / "valid.csv").open(encoding="utf-8-sig",newline="") as handle:
            self.assertEqual(list(csv.reader(handle,delimiter=";"))[1][0], 'Μαρία; "δοκιμή"\nδεύτερη γραμμή')

    def test_xlsx_export_preserves_text_and_never_creates_formulas(self):
        from openpyxl import load_workbook
        self.spec["destination"]["kind"]="xlsx"
        self.spec["columns"]=[{"name":"formula","literal":"=1+1"},{"name":"id","literal":"000123"}]
        report=execute(self.spec,self.root,self.root / "out")
        workbook=load_workbook(Path(report["directory"]) / "valid.xlsx",read_only=True)
        try:
            rows=list(workbook.active.rows)
            self.assertEqual(len(rows),6)
            self.assertEqual(rows[1][0].value,"=1+1")
            self.assertEqual(rows[1][0].data_type,"s")
            self.assertEqual(rows[1][1].value,"000123")
        finally:
            workbook.close()

    def test_many_rows_keep_bounded_sample_and_progress(self):
        (self.root / "input.csv").write_text("id\n"+"\n".join(str(n) for n in range(2501)),encoding="utf-8")
        self.spec["columns"]=[{"name":"id","source":"id","type":"int"}]
        progress=[]
        report=execute(self.spec,self.root,self.root / "out",progress=progress.append)
        self.assertEqual(report["processed"],2501)
        self.assertEqual(len(report["sample"]),20)
        self.assertEqual([p["processed"] for p in progress],[1000,2000])

    def test_sql_quoting_batching_and_close_on_preview(self):
        driver=MagicMock()
        driver.Error=type("DriverError",(Exception,),{})
        connection=driver.connect.return_value
        cursor=connection.cursor.return_value
        cursor.description=[("id",)]
        cursor.fetchmany.side_effect=[[(1,),(2,)],[]]
        spec={"kind":"sqlserver","connection_env":"ETL_SQL_TEST","schema":"dbo","table":"A]B"}
        with patch.dict("sys.modules",{"pyodbc":driver}), patch.dict("os.environ",{"ETL_SQL_TEST":"test connection"}):
            with open_source(spec,self.root,batch_size=2) as (headers,rows):
                self.assertEqual(headers,["id"])
                self.assertEqual(list(rows),[{"id":1},{"id":2}])
        cursor.execute.assert_called_once_with("SELECT * FROM [dbo].[A]]B]")
        cursor.fetchmany.assert_called_with(2)
        cursor.close.assert_called_once()
        connection.close.assert_called_once()
        self.assertEqual(identifier("x]; DELETE FROM t;--"),"[x]]; DELETE FROM t;--]")

    def test_sql_failure_redacts_driver_details(self):
        driver=MagicMock()
        driver.Error=type("DriverError",(Exception,),{})
        driver.connect.side_effect=driver.Error("password=secret")
        source={"kind":"sqlserver","connection_env":"ETL_SQL_TEST","schema":"dbo","table":"customers"}
        with patch.dict("sys.modules",{"pyodbc":driver}), patch.dict("os.environ",{"ETL_SQL_TEST":"secret"}):
            with self.assertRaises(ConfigError) as error:
                inspect_source(source,self.root)
        self.assertNotIn("secret",str(error.exception))

    def test_persistence_update_and_immutable_execution_snapshot(self):
        store=Store(self.root / "state.sqlite3")
        saved=store.save(self.spec)
        run=store.create_run(self.spec)
        self.spec["name"]="Changed"
        store.save(self.spec,saved)
        store.update_run(run,"completed",{"valid":3})
        self.assertEqual(len(store.pipelines()),1)
        self.assertEqual(store.pipelines()[0]["name"],"Changed")
        self.assertNotEqual(store.run(run)["spec"]["name"],"Changed")
        self.assertEqual(store.run(run)["report"],{"valid":3})


if __name__ == "__main__":
    unittest.main()
