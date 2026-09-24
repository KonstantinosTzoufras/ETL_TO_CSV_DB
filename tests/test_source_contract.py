"""Source-only contracts: schema, unmodified values, positions and lifecycle."""
import csv
import tempfile
import unittest
from contextlib import contextmanager
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import UUID

from etl.engine import execute
from etl.models import SourceColumn, SourceDefinition, SourceRow
from etl.sources import CsvSource, Source, SqlServerSource, XmlSource, create_source, inspect_source, open_source
from etl.spec import ConfigError


class CsvSourceContractTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.path = self.root / "input.csv"
        self.spec = {"kind": "csv", "path": "input.csv"}

    def write(self, text, encoding="utf-8"):
        self.path.write_bytes(text.encode(encoding))

    def adapter(self):
        return CsvSource(self.spec, self.root)

    @contextmanager
    def tracked_files(self):
        handles = []
        original_open = Path.open

        def track(path, *args, **kwargs):
            handle = original_open(path, *args, **kwargs)
            # These scopes only open the fixture input. Avoid comparing Windows
            # temporary paths before/after resolve() (short-name aliases differ).
            handles.append(handle)
            return handle

        with patch.object(Path, "open", track):
            yield handles

    def test_common_interface_and_immutable_definition(self):
        self.write("code\n003\n")
        source = create_source(self.spec, self.root)
        self.assertIsInstance(source, Source)
        self.assertIsInstance(source, CsvSource)
        self.spec["path"] = "changed.csv"
        self.assertEqual(source.read_schema(), (SourceColumn("code", native_type="str"),))
        with source.open() as stream:
            self.assertEqual(next(stream.rows).values["code"], "003")
        from_model = create_source(SourceDefinition("csv", {"path": "input.csv"}), self.root)
        self.assertEqual(from_model.read_schema(), source.read_schema())

    def test_schema_preserves_header_order_case_and_whitespace_without_inference(self):
        self.write(" Code ;date;amount\n003;2024-02-29;12.00\n")
        schema = self.adapter().read_schema()
        self.assertEqual([column.name for column in schema], [" Code ", "date", "amount"])
        for column in schema:
            self.assertEqual(column.native_type, "str")
            self.assertIsNone(column.nullable)
            self.assertIsNone(column.precision)
            self.assertIsNone(column.scale)

    def test_utf8_and_utf8_sig_default_and_explicit_encodings(self):
        for file_encoding, configured in (("utf-8", None), ("utf-8-sig", None), ("utf-8", "utf-8"), ("utf-8-sig", "utf-8-sig")):
            with self.subTest(file_encoding=file_encoding, configured=configured):
                self.write("κωδικός;όνομα\n003;Μαρία\n", file_encoding)
                self.spec = {"kind": "csv", "path": "input.csv"}
                if configured:
                    self.spec["encoding"] = configured
                with self.adapter().open() as stream:
                    row = next(stream.rows)
                    self.assertEqual([c.name for c in stream.schema], ["κωδικός", "όνομα"])
                    self.assertEqual(dict(row.values), {"κωδικός": "003", "όνομα": "Μαρία"})

    def test_explicit_utf8_does_not_silently_strip_a_bom(self):
        self.write("code\n003\n", "utf-8-sig")
        self.spec["encoding"] = "utf-8"
        self.assertEqual(self.adapter().read_schema()[0].name, "\ufeffcode")

    def test_configurable_delimiters_and_quoted_delimiter(self):
        for delimiter in (";", ",", "\t", "|"):
            with self.subTest(delimiter=delimiter):
                self.spec["delimiter"] = delimiter
                self.write(f'code{delimiter}note\n003{delimiter}"a{delimiter}b"\n')
                with self.adapter().open() as stream:
                    self.assertEqual(dict(next(stream.rows).values), {"code": "003", "note": f"a{delimiter}b"})

    def test_multiline_fields_and_blank_lines_have_distinct_record_and_line_positions(self):
        self.write('code;note\r\n003;"First\r\n\r\nLast"\r\n\r\n004;""\r\n')
        with self.adapter().open() as stream:
            rows = list(stream)
        self.assertEqual([(r.number, r.line_start, r.line_end) for r in rows], [(1, 2, 4), (2, 6, 6)])
        self.assertEqual(rows[0].values["note"], "First\r\n\r\nLast")
        self.assertEqual(rows[1].values["note"], "")

    def test_multiline_header_is_accounted_for_in_physical_positions(self):
        self.write('"header\ncontinued";code\nhello;003\n')
        with self.adapter().open() as stream:
            row = next(stream.rows)
            self.assertEqual(stream.schema[0].name, "header\ncontinued")
        self.assertEqual((row.number, row.line_start, row.line_end), (1, 3, 3))

    def test_empty_fields_are_values_and_are_not_null(self):
        self.write('a;b;c\n;;\n003;"";   \n')
        with self.adapter().open() as stream:
            rows = list(stream)
        self.assertEqual(dict(rows[0].values), {"a": "", "b": "", "c": ""})
        self.assertEqual(dict(rows[1].values), {"a": "003", "b": "", "c": "   "})

    def test_blank_records_are_skipped_but_quoted_empty_records_are_kept(self):
        self.write('code\n\n""\n   \n003\n')
        with self.adapter().open() as stream:
            rows = list(stream)
        self.assertEqual([row.values["code"] for row in rows], ["", "   ", "003"])
        self.assertEqual([row.number for row in rows], [1, 2, 3])
        self.assertEqual([row.line_start for row in rows], [3, 4, 5])

    def test_missing_or_extra_structure_raises_instead_of_padding_or_truncating(self):
        for record, count in (("only-one", 1), ("one;two;three", 3)):
            with self.subTest(record=record):
                self.write(f"a;b\n{record}\n")
                with self.tracked_files() as handles:
                    with self.assertRaisesRegex(ConfigError, f"CSV record 2: expected 2 fields, got {count}"):
                        with self.adapter().open() as stream:
                            list(stream)
                self.assertTrue(handles[0].closed)

    def test_invalid_or_duplicate_headers_close_file(self):
        for text in ("", "a;a\n", ";a\n", "   \n"):
            with self.subTest(text=text):
                self.write(text)
                with self.tracked_files() as handles, self.assertRaises(ConfigError):
                    self.adapter().read_schema()
                self.assertTrue(handles[0].closed)

    def test_header_only_source_has_schema_and_no_rows(self):
        self.write("code;name\n")
        with self.adapter().open() as stream:
            self.assertEqual([c.name for c in stream.schema], ["code", "name"])
            self.assertEqual(list(stream), [])

    def test_schema_inspection_does_not_parse_malformed_data_rows(self):
        self.write('code\n"unterminated\n')
        self.assertEqual(self.adapter().read_schema()[0].name, "code")
        with self.tracked_files() as handles, self.assertRaises(csv.Error):
            with self.adapter().open() as stream:
                list(stream)
        self.assertTrue(handles[0].closed)

    def test_decode_errors_close_file(self):
        self.path.write_bytes(b"code\n\xff\n")
        with self.tracked_files() as handles, self.assertRaises(UnicodeDecodeError):
            with self.adapter().open() as stream:
                list(stream)
        self.assertTrue(handles[0].closed)

    def test_path_and_configuration_errors(self):
        for spec in ({"kind": "csv", "path": "missing.csv"},
                     {"kind": "csv", "path": "../outside.csv"},
                     {"kind": "csv", "path": "input.txt"},
                     {"kind": "csv", "path": "input.csv", "delimiter": "::"},
                     {"kind": "csv", "path": "input.csv", "encoding": "unknown-encoding"}):
            with self.subTest(spec=spec), self.assertRaises(ConfigError):
                CsvSource(spec, self.root).read_schema()

    def test_context_exit_closes_iterator_and_file_for_unused_partial_and_full_reads(self):
        self.write("code\n003\n004\n005\n")
        for consumed in (0, 1, 3):
            with self.subTest(consumed=consumed), self.tracked_files() as handles:
                with self.adapter().open() as stream:
                    for _ in range(consumed):
                        next(stream.rows)
                    if consumed == 3:
                        self.assertEqual(list(stream.rows), [])
                self.assertTrue(handles[0].closed)
                self.assertEqual(list(stream.rows), [])

    def test_processing_exception_is_preserved_and_file_is_closed(self):
        self.write("code\n003\n004\n")
        failure = RuntimeError("processing failed")
        with self.tracked_files() as handles, self.assertRaises(RuntimeError) as caught:
            with self.adapter().open() as stream:
                next(stream.rows)
                raise failure
        self.assertIs(caught.exception, failure)
        self.assertTrue(handles[0].closed)
        self.assertEqual(list(stream.rows), [])

    def test_engine_preview_stops_before_malformed_later_row_and_closes_source(self):
        self.write("code\n003\nextra;field\n")
        pipeline = {"version": 1, "name": "Preview", "source": self.spec,
                    "columns": [{"name": "code", "source": "code"}], "destination": {"kind": "csv"}}
        with self.tracked_files() as handles:
            report = execute(pipeline, self.root, limit=1)
        self.assertEqual(report["sample"][0]["values"]["code"], "003")
        self.assertEqual(report["processed"], 1)
        self.assertTrue(handles[0].closed)
        with self.tracked_files() as handles, patch("etl.engine.process_row", side_effect=RuntimeError("processing")):
            with self.assertRaisesRegex(RuntimeError, "processing"):
                execute(pipeline, self.root, limit=1)
        self.assertTrue(handles[0].closed)

    def test_legacy_api_retains_headers_and_mutable_dicts_without_position_keys(self):
        self.write("code\n003\n004\n")
        self.assertEqual(inspect_source(self.spec, self.root), ["code"])
        with self.tracked_files() as handles:
            with open_source(self.spec, self.root) as (headers, rows):
                first = next(rows)
                self.assertEqual(headers, ["code"])
                self.assertIsInstance(first, dict)
                self.assertEqual(first, {"code": "003"})
                first["code"] = "changed"
        self.assertTrue(handles[0].closed)
        self.assertEqual(list(rows), [])


class XmlSourceContractTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.path = self.root / "input.xml"
        self.spec = {"kind": "xml", "path": "input.xml"}

    def write(self, text, encoding="utf-8"):
        self.path.write_bytes(text.encode(encoding))

    def adapter(self):
        return XmlSource(self.spec, self.root)

    @contextmanager
    def tracked_files(self):
        handles = []
        original_open = Path.open

        def track(path, *args, **kwargs):
            handle = original_open(path, *args, **kwargs)
            handles.append(handle)
            return handle

        with patch.object(Path, "open", track):
            yield handles

    def test_common_interface_and_direct_children_become_rows_and_columns(self):
        self.write("<Records><Row><code>003</code><name>Foo</name></Row>"
                    "<Row><code>004</code><name>Bar</name></Row></Records>")
        source = create_source(self.spec, self.root)
        self.assertIsInstance(source, Source)
        self.assertIsInstance(source, XmlSource)
        self.assertEqual(source.read_schema(), (SourceColumn("code", native_type="str"), SourceColumn("name", native_type="str")))
        with source.open() as stream:
            rows = list(stream)
        self.assertEqual([row.values for row in rows], [{"code": "003", "name": "Foo"}, {"code": "004", "name": "Bar"}])
        self.assertEqual([row.number for row in rows], [1, 2])

    def test_schema_preserves_first_row_element_order(self):
        self.write("<Root><Row><b>1</b><a>2</a></Row></Root>")
        schema = self.adapter().read_schema()
        self.assertEqual([column.name for column in schema], ["b", "a"])

    def test_namespaces_are_stripped_to_local_names(self):
        self.write('<r:Root xmlns:r="urn:x"><r:Row><r:code>1</r:code></r:Row></r:Root>')
        schema = self.adapter().read_schema()
        self.assertEqual([column.name for column in schema], ["code"])
        with self.adapter().open() as stream:
            self.assertEqual(next(stream.rows).values, {"code": "1"})

    def test_empty_element_is_an_empty_string_not_null(self):
        self.write("<Root><Row><code>003</code><name/></Row></Root>")
        with self.adapter().open() as stream:
            self.assertEqual(next(stream.rows).values, {"code": "003", "name": ""})

    def test_missing_or_extra_structure_raises_instead_of_padding(self):
        for body, message in (
            ("<Row><a>1</a></Row>", "XML record 2: expected 2 elements, got 1"),
            ("<Row><a>1</a><b>2</b><c>3</c></Row>", "XML record 2: unexpected element <c>"),
        ):
            with self.subTest(body=body):
                self.write(f"<Root><Row><a>x</a><b>y</b></Row>{body}</Root>")
                with self.tracked_files() as handles:
                    with self.assertRaisesRegex(ConfigError, message):
                        with self.adapter().open() as stream:
                            list(stream)
                self.assertTrue(handles[0].closed)

    def test_no_row_elements_or_malformed_xml_close_file(self):
        for text in ("<Root></Root>", "<Root", ""):
            with self.subTest(text=text):
                self.write(text)
                with self.tracked_files() as handles, self.assertRaises(ConfigError):
                    self.adapter().read_schema()
                self.assertTrue(handles[0].closed)

    def test_path_and_configuration_errors(self):
        for spec in ({"kind": "xml", "path": "missing.xml"},
                     {"kind": "xml", "path": "../outside.xml"},
                     {"kind": "xml", "path": "input.txt"},
                     {"kind": "xml", "path": "input.xml", "delimiter": ";"}):
            with self.subTest(spec=spec), self.assertRaises(ConfigError):
                XmlSource(spec, self.root).read_schema()

    def test_context_exit_closes_iterator_and_file_for_unused_partial_and_full_reads(self):
        self.write("<Root><Row><code>1</code></Row><Row><code>2</code></Row><Row><code>3</code></Row></Root>")
        for consumed in (0, 1, 3):
            with self.subTest(consumed=consumed), self.tracked_files() as handles:
                with self.adapter().open() as stream:
                    for _ in range(consumed):
                        next(stream.rows)
                    if consumed == 3:
                        self.assertEqual(list(stream.rows), [])
                self.assertTrue(handles[0].closed)
                self.assertEqual(list(stream.rows), [])

    def test_legacy_api_retains_headers_and_mutable_dicts_without_position_keys(self):
        self.write("<Root><Row><code>003</code></Row></Root>")
        self.assertEqual(inspect_source(self.spec, self.root), ["code"])
        with self.tracked_files() as handles:
            with open_source(self.spec, self.root) as (headers, rows):
                first = next(rows)
                self.assertEqual(headers, ["code"])
                self.assertIsInstance(first, dict)
                self.assertEqual(first, {"code": "003"})
        self.assertTrue(handles[0].closed)


class SqlServerSourceContractTests(unittest.TestCase):
    def setUp(self):
        self.driver = MagicMock()
        self.driver.Error = type("DriverError", (Exception,), {})
        self.connection = self.driver.connect.return_value
        self.cursor = self.connection.cursor.return_value
        self.cursor.description = [("code", str, None, 30, 30, 0, True)]
        self.cursor.fetchmany.side_effect = [[("003",), ("004",)], [("005",)], []]
        modules = patch.dict("sys.modules", {"pyodbc": self.driver})
        environment = patch.dict("os.environ", {"ETL_SQL_TEST": "synthetic connection"})
        modules.start()
        environment.start()
        self.addCleanup(modules.stop)
        self.addCleanup(environment.stop)
        self.spec = {"kind": "sqlserver", "connection_env": "ETL_SQL_TEST", "schema": "dbo", "table": "A]B"}

    def adapter(self, batch_size=2):
        return SqlServerSource(self.spec, batch_size=batch_size)

    def assert_closed(self):
        self.cursor.close.assert_called_once_with()
        self.connection.close.assert_called_once_with()

    def test_common_interface_factory_and_existing_query_semantics(self):
        source = create_source(self.spec, Path.cwd(), batch_size=2)
        self.assertIsInstance(source, Source)
        self.assertIsInstance(source, SqlServerSource)
        with source.open() as stream:
            self.assertEqual(next(stream.rows).values["code"], "003")
        self.driver.connect.assert_called_once_with("synthetic connection", timeout=10, autocommit=True)
        self.assertEqual(self.connection.timeout, 60)
        self.cursor.execute.assert_called_once_with("SELECT * FROM [dbo].[A]]B]")
        self.assert_closed()

    def test_batching_is_lazy_bounded_and_numbers_span_batches(self):
        with self.adapter().open() as stream:
            self.cursor.fetchmany.assert_not_called()
            first = next(stream.rows)
            self.assertEqual(self.cursor.fetchmany.call_count, 1)
            second = next(stream.rows)
            self.assertEqual(self.cursor.fetchmany.call_count, 1)
            third = next(stream.rows)
            self.assertEqual(self.cursor.fetchmany.call_count, 2)
            self.assertEqual(list(stream.rows), [])
        self.assertEqual([r.number for r in (first, second, third)], [1, 2, 3])
        self.assertEqual([r.values["code"] for r in (first, second, third)], ["003", "004", "005"])
        self.assertTrue(all(r.line_start is None and r.line_end is None for r in (first, second, third)))
        self.assertEqual([call.args for call in self.cursor.fetchmany.call_args_list], [(2,), (2,), (2,)])
        self.cursor.fetchall.assert_not_called()
        self.assert_closed()

    def test_default_batch_size_and_invalid_batch_sizes(self):
        with SqlServerSource(self.spec).open() as stream:
            next(stream.rows)
        self.cursor.fetchmany.assert_called_once_with(1000)
        for size in (0, -1, True, 1.5, "1000"):
            with self.subTest(size=size), self.assertRaisesRegex(ConfigError, "positive integer"):
                self.adapter(size)

    def test_schema_uses_driver_metadata_without_fetching_rows(self):
        self.cursor.description = [("amount", Decimal, None, 17, 38, 10, False),
                                   ("code", str, None, 50, 50, 0, True)]
        schema = self.adapter().read_schema()
        self.assertEqual(schema, (
            SourceColumn("amount", "Decimal", False, internal_size=17, precision=38, scale=10),
            SourceColumn("code", "str", True, internal_size=50, precision=50, scale=0),
        ))
        self.cursor.fetchmany.assert_not_called()
        self.cursor.fetchall.assert_not_called()
        self.cursor.execute.assert_called_once_with("SELECT * FROM [dbo].[A]]B]")
        self.assert_closed()

    def test_sparse_or_unknown_metadata_remains_unknown(self):
        self.cursor.description = [("minimal",), ("unknown", "driver-specific", None, -1, None, None, None)]
        schema = self.adapter().read_schema()
        self.assertEqual(schema, (SourceColumn("minimal"), SourceColumn("unknown")))
        self.assert_closed()

    def test_native_values_are_not_normalized_or_converted(self):
        values = {
            "null": None, "empty": "", "spaces": "   ", "code": "003", "case": "aBc",
            "amount": Decimal("12345678901234567890.1234567890123456789000"),
            "date": date(2024, 2, 29), "datetime": datetime(2024, 2, 29, 12, 30),
            "time": time(12, 30, 1, 123456), "bytes": b"\x00\xff",
            "uuid": UUID("a6e70d24-4d01-4b35-9aab-c645b382f88a"), "multiline": "a\r\nb\n",
        }
        self.cursor.description = [(key, type(value), None, None, None, None, None) for key, value in values.items()]
        self.cursor.fetchmany.side_effect = [[tuple(values.values())], []]
        with self.adapter().open() as stream:
            row = next(stream.rows)
        self.assertIsInstance(row, SourceRow)
        self.assertEqual(dict(row.values), values)
        for key, value in values.items():
            self.assertIs(type(row.values[key]), type(value))
        self.assertEqual(row.values["amount"].as_tuple(), values["amount"].as_tuple())
        with self.assertRaises(TypeError):
            row.values["code"] = "3"

    def test_unused_and_early_stopped_streams_close_and_cannot_yield_buffered_rows(self):
        for consume in (False, True):
            with self.subTest(consume=consume):
                self.cursor.reset_mock()
                self.connection.close.reset_mock()
                self.cursor.fetchmany.side_effect = [[("003",), ("004",)], [("005",)]]
                with self.adapter().open() as stream:
                    if consume:
                        next(stream.rows)
                self.assert_closed()
                self.assertEqual(list(stream.rows), [])
                self.assertEqual(self.cursor.fetchmany.call_count, int(consume))

    def test_empty_result_still_exposes_schema_and_closes(self):
        self.cursor.fetchmany.side_effect = [[]]
        with self.adapter().open() as stream:
            self.assertEqual([c.name for c in stream.schema], ["code"])
            self.assertEqual(list(stream.rows), [])
        self.assert_closed()

    def test_row_numbers_restart_per_execution_without_reordering(self):
        source = self.adapter()
        for values in (["003", "001"], ["001", "003"]):
            with self.subTest(values=values):
                self.cursor.fetchmany.side_effect = [[(value,) for value in values], []]
                with source.open() as stream:
                    rows = list(stream)
                self.assertEqual([row.number for row in rows], [1, 2])
                self.assertEqual([row.values["code"] for row in rows], values)

    def test_processing_exception_survives_even_when_cleanup_fails(self):
        self.cursor.close.side_effect = self.driver.Error("password=secret")
        self.connection.close.side_effect = self.driver.Error("another secret")
        failure = RuntimeError("processing failed")
        with self.assertRaises(RuntimeError) as caught:
            with self.adapter().open() as stream:
                next(stream.rows)
                raise failure
        self.assertIs(caught.exception, failure)
        self.assert_closed()
        self.assertEqual(list(stream.rows), [])

    def test_cleanup_failure_attempts_both_closes_and_redacts_error(self):
        self.cursor.close.side_effect = self.driver.Error("password=secret")
        with self.assertRaises(ConfigError) as caught:
            self.adapter().read_schema()
        self.assertNotIn("secret", str(caught.exception))
        self.assert_closed()

    def test_connection_failure_redacts_driver_message(self):
        self.driver.connect.side_effect = self.driver.Error("password=secret")
        with self.assertRaises(ConfigError) as caught:
            self.adapter().read_schema()
        self.assertNotIn("secret", str(caught.exception))
        self.connection.close.assert_not_called()

    def test_cursor_creation_failure_closes_connection(self):
        self.connection.cursor.side_effect = self.driver.Error("password=secret")
        with self.assertRaises(ConfigError) as caught:
            self.adapter().read_schema()
        self.assertNotIn("secret", str(caught.exception))
        self.connection.close.assert_called_once_with()
        self.cursor.close.assert_not_called()

    def test_query_failure_closes_resources_and_redacts_error(self):
        self.cursor.execute.side_effect = self.driver.Error("password=secret")
        with self.assertRaises(ConfigError) as caught:
            self.adapter().read_schema()
        self.assertNotIn("secret", str(caught.exception))
        self.assert_closed()

    def test_fetch_failure_closes_resources_and_redacts_error(self):
        self.cursor.fetchmany.side_effect = [[("003",)], self.driver.Error("password=secret")]
        with self.assertRaises(ConfigError) as caught:
            with self.adapter().open() as stream:
                self.assertEqual(next(stream.rows).values["code"], "003")
                next(stream.rows)
        self.assertNotIn("secret", str(caught.exception))
        self.assert_closed()

    def test_invalid_schema_closes_resources(self):
        for description in (None, [], [("a",), ("a",)], [("",)]):
            with self.subTest(description=description):
                self.cursor.close.reset_mock()
                self.connection.close.reset_mock()
                self.cursor.description = description
                with self.assertRaises(ConfigError):
                    self.adapter().read_schema()
                self.assert_closed()

    def test_fetch_error_is_sanitized_even_if_consumer_catches_it_inside_context(self):
        self.cursor.fetchmany.side_effect = self.driver.Error("password=secret")
        with self.adapter().open() as stream:
            with self.assertRaises(ConfigError) as caught:
                next(stream.rows)
            self.assertNotIn("secret", str(caught.exception))
        self.assert_closed()

    def test_missing_dependency_and_connection_configuration(self):
        with patch.dict("sys.modules", {"pyodbc": None}), self.assertRaisesRegex(ConfigError, "needs pyodbc"):
            self.adapter().read_schema()
        with patch.dict("os.environ", {"ETL_SQL_TEST": ""}), self.assertRaisesRegex(ConfigError, "Set environment variable"):
            self.adapter().read_schema()
        self.driver.connect.assert_not_called()

    def test_legacy_wrapper_uses_one_query_and_returns_original_dict_shape(self):
        with open_source(self.spec, Path.cwd(), batch_size=2) as (headers, rows):
            self.assertEqual(headers, ["code"])
            self.assertEqual(next(rows), {"code": "003"})
        self.cursor.execute.assert_called_once()
        self.cursor.fetchmany.assert_called_once_with(2)
        self.assertEqual(list(rows), [])
        self.assert_closed()

    def test_engine_preview_and_processing_failure_close_sql_source(self):
        pipeline = {"version": 1, "name": "Preview", "source": self.spec,
                    "columns": [{"name": "code", "source": "code"}], "destination": {"kind": "csv"}}
        report = execute(pipeline, Path.cwd(), limit=1)
        self.assertEqual(report["sample"][0]["values"], {"code": "003"})
        self.cursor.fetchmany.assert_called_once_with(1000)
        self.assert_closed()
        self.cursor.reset_mock()
        self.connection.close.reset_mock()
        self.cursor.fetchmany.side_effect = [[("003",), ("004",)]]
        with patch("etl.engine.process_row", side_effect=RuntimeError("processing")), self.assertRaisesRegex(RuntimeError, "processing"):
            execute(pipeline, Path.cwd(), limit=1)
        self.assert_closed()


if __name__ == "__main__":
    unittest.main()
