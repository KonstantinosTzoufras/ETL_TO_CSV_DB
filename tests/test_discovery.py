"""Bounded discovery acceptance: original values, no processing, no writes."""
import json
import tempfile
import unittest
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pyodbc

from etl.discovery import (CsvDiscovery, Dataset, DiscoveryError, SourceDiscovery,
                           SqlServerDiscovery, dispatch, to_dict)
from etl.models import SourceColumn, SourceDefinition, SourceRow
from etl.serialization import json_default
from etl.sources import SourceStream
from etl.spec import source_spec


class CsvDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "input.csv"
        self.path.write_text('code;note\n003;"  Ελληνικά\ntext  "\n004;""\n005;   \n', encoding="utf-8", newline="")
        self.discovery = CsvDiscovery(self.root)

    def source(self, **options):
        return self.discovery.configure(self.discovery.resolve_dataset("input.csv"), options)

    def assertError(self, code, call):
        with self.assertRaises(DiscoveryError) as caught:
            call()
        self.assertEqual(caught.exception.code, code)

    def test_interface_and_nested_immutability(self):
        self.assertIsInstance(self.discovery, SourceDiscovery)
        namespace = ["folder"]
        dataset = Dataset("key", "csv", "file", namespace, "x.csv")
        namespace.append("changed")
        self.assertEqual(dataset.namespace, ("folder",))
        with self.assertRaises(FrozenInstanceError):
            dataset.name = "changed"
        sample = self.discovery.sample(self.source())
        with self.assertRaises(TypeError):
            sample.rows[0].values["code"] = "3"
        with self.assertRaises(TypeError):
            sample.source.options["path"] = "changed"

    def test_browse_pages_folders_csv_files_and_scoped_cursor(self):
        for name in ("a.csv", "b.CSV", "ignore.txt", ".hidden.csv"):
            (self.root / name).write_text("x\n", encoding="utf-8")
        (self.root / "folder").mkdir()
        (self.root / ".secret").mkdir()
        self.assertEqual(self.discovery.list_namespaces().items, (("folder",),))
        first = self.discovery.list_datasets(limit=1)
        second = self.discovery.list_datasets(limit=1, cursor=first.next_cursor)
        self.assertEqual([first.items[0].name, second.items[0].name], ["a.csv", "b.CSV"])
        self.assertEqual(len(self.discovery.list_datasets().items), 3)
        self.assertError("invalid_read_options", lambda: self.discovery.list_namespaces(cursor=first.next_cursor))
        self.assertError("invalid_read_options", lambda: self.discovery.list_datasets(("folder",), cursor=first.next_cursor))

    def test_source_definition_compatible_no_mapping_or_inference(self):
        source = self.source()
        source_spec(to_dict(source))
        self.assertEqual(to_dict(source), {"kind": "csv", "path": "input.csv", "delimiter": ";", "encoding": "utf-8-sig"})
        columns = self.discovery.inspect(source)
        self.assertEqual([c.column.name for c in columns], ["code", "note"])
        self.assertTrue(all(c.column.native_type == "str" and c.declared_type is None and c.column.nullable is None for c in columns))

    def test_sample_is_original_with_multiline_positions_and_empty_whitespace(self):
        with patch("etl.engine.process_row", side_effect=AssertionError("must not process")):
            sample = self.discovery.sample(self.source())
        self.assertEqual(sample.stop_reason, "end_of_source")
        self.assertEqual([(r.number, r.line_start, r.line_end) for r in sample.rows], [(1, 2, 3), (2, 4, 4), (3, 5, 5)])
        self.assertEqual([dict(r.values) for r in sample.rows], [
            {"code": "003", "note": "  Ελληνικά\ntext  "}, {"code": "004", "note": ""}, {"code": "005", "note": "   "}])

    def test_no_extra_row_read_and_limit_does_not_claim_more_rows(self):
        self.path.write_text("code\n003\nwrong;structure\n", encoding="utf-8")
        self.assertEqual(self.discovery.sample(self.source(), limit=1).stop_reason, "row_limit")
        self.assertError("malformed_source", lambda: self.discovery.sample(self.source(), limit=2))
        self.path.write_text("code\n003\n", encoding="utf-8")
        self.assertEqual(self.discovery.sample(self.source(), limit=1).stop_reason, "row_limit")

    def test_encoding_and_delimiter_are_explicit(self):
        for encoding in ("utf-8", "utf-8-sig"):
            for delimiter in (",", ";", "|", "\t"):
                with self.subTest(encoding=encoding, delimiter=delimiter):
                    self.path.write_bytes(f'code{delimiter}text\n003{delimiter}"Ελλάδα{delimiter}x"\n'.encode(encoding))
                    result = self.discovery.sample(self.source(encoding=encoding, delimiter=delimiter))
                    self.assertEqual(result.rows[0].values["text"], f"Ελλάδα{delimiter}x")
        self.path.write_bytes("code\n003\n".encode("utf-8-sig"))
        self.assertEqual(self.discovery.inspect(self.source(encoding="utf-8"))[0].column.name, "\ufeffcode")

    def test_invalid_encoding_headers_and_missing_structure(self):
        for contents, code in (("", "invalid_headers"), ("a;a\n", "invalid_headers"), ("a; \n", "invalid_headers"), ("a;b\nx\n", "malformed_source"), ('a\n"unterminated', "malformed_source")):
            with self.subTest(contents=contents):
                self.path.write_text(contents, encoding="utf-8")
                self.assertError(code, lambda: self.discovery.sample(self.source()))
        self.path.write_bytes(b"a\n\xff\n")
        self.assertError("invalid_read_options", lambda: self.discovery.sample(self.source()))
        for options in ({"delimiter": "xx"}, {"encoding": "made-up"}, {"query": "SELECT 1"}):
            self.assertError("invalid_read_options", lambda: self.source(**options))

    def test_header_only_source(self):
        self.path.write_text("code\n", encoding="utf-8")
        sample = self.discovery.sample(self.source())
        self.assertEqual(sample.rows, ())
        self.assertEqual(sample.stop_reason, "end_of_source")

    def test_paths_and_changed_dataset(self):
        for path in ("../outside.csv", str(self.path.resolve()), "C:\\outside.csv", "folder/../../outside.csv"):
            self.assertError("access_denied", lambda: self.discovery.resolve_dataset(path))
        self.assertError("dataset_unavailable", lambda: self.discovery.resolve_dataset("gone.csv"))
        dataset = self.discovery.resolve_dataset("input.csv")
        self.path.unlink()
        self.assertError("dataset_unavailable", lambda: self.discovery.configure(dataset, {}))

    def test_path_resolution_rejects_external_link_target(self):
        # Exercise the resolved-target boundary without requiring Windows symlink privileges.
        original = Path.resolve
        outside = self.root.parent / "outside.csv"
        with patch.object(Path, "resolve", lambda p: outside if p.name == "escape.csv" else original(p)):
            self.assertError("access_denied", lambda: self.discovery.resolve_dataset("escape.csv"))

    def test_limits_and_malformed_tokens(self):
        for limit in (0, -1, 101, True, 1.5, "20", None):
            self.assertError("invalid_read_options", lambda: self.discovery.sample(self.source(), limit=limit))
            self.assertError("invalid_read_options", lambda: self.discovery.list_datasets(limit=limit))
        for token in ("!", "W10=", "e30=", "null"):
            self.assertError("invalid_read_options", lambda: self.discovery.list_datasets(cursor=token))

    def test_dataset_keys_are_bound_to_workspace(self):
        dataset = self.discovery.resolve_dataset("input.csv")
        other = CsvDiscovery(self.root.parent)
        self.assertError("invalid_read_options", lambda: other.resolve_dataset(dataset.key))

    def test_resources_closed_early_end_and_error(self):
        original = Path.open
        for limit, bad in ((1, False), (20, False), (20, True)):
            handles = []
            if bad:
                self.path.write_text("a;b\nx\n", encoding="utf-8")
            def track(path, *args, **kwargs):
                handle = original(path, *args, **kwargs)
                handles.append(handle)
                return handle
            source = self.source()
            with patch.object(Path, "open", track):
                if bad:
                    self.assertError("malformed_source", lambda: self.discovery.sample(source, limit=limit))
                else:
                    self.discovery.sample(source, limit=limit)
            self.assertTrue(handles and all(h.closed for h in handles))

    def test_oversized_sample_closes_adapter_without_partial_response(self):
        closed = []
        @contextmanager
        def stream():
            try:
                yield SourceStream((SourceColumn("text"),), iter([SourceRow(1, {"text": "x" * 1024 * 1024})]))
            finally:
                closed.append(True)
        source = self.source()
        with patch("etl.discovery.create_source") as create:
            create.return_value.open = stream
            self.assertError("sample_too_large", lambda: self.discovery.sample(source))
        self.assertEqual(closed, [True])


class SqlDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict("os.environ", {"ETL_SQL_TEST": "secret-connection"})
        self.env.start(); self.addCleanup(self.env.stop)
        self.connect = patch("pyodbc.connect").start()
        self.addCleanup(patch.stopall)
        self.connection = self.connect.return_value
        self.cursor = self.connection.cursor.return_value
        self.cursor.fetchone.return_value = ("BRANDS",)
        self.cursor.description = [("code", str, None, None, None, None, True), ("amount", Decimal, None, None, 28, 4, True)]
        self.discovery = SqlServerDiscovery("ETL_SQL_TEST", Path.cwd())
        self.source = SourceDefinition("sqlserver", {"connection_env": "ETL_SQL_TEST", "schema": "dbo", "table": "BRANDS"})

    def assertError(self, code, call):
        with self.assertRaises(DiscoveryError) as caught:
            call()
        self.assertEqual(caught.exception.code, code)
        self.assertNotIn("secret", str(caught.exception))

    def test_capabilities_schemas_tables_views_pagination(self):
        self.assertIsInstance(self.discovery, SourceDiscovery)
        self.assertEqual(self.discovery.capabilities().dataset_kinds, ("table", "view"))
        self.cursor.fetchmany.return_value = [("dbo",), ("other",)]
        page = self.discovery.list_namespaces(limit=1)
        self.assertEqual(page.items, (("dbo",),))
        self.assertIsNotNone(page.next_cursor)
        self.discovery.list_namespaces(cursor=page.next_cursor, limit=1)
        self.assertEqual(self.cursor.execute.call_args.args[1:], (2, "dbo"))
        self.cursor.fetchmany.return_value = [("BRANDS", "table"), ("BrandView", "view")]
        datasets = self.discovery.list_datasets(("dbo",))
        self.assertEqual([d.kind for d in datasets.items], ["table", "view"])
        self.connect.assert_called_with("secret-connection", timeout=10, autocommit=True, readonly=True)
        self.cursor.fetchall.assert_not_called()

    def test_configure_existing_definition_and_scoped_keys(self):
        dataset = self.discovery._dataset("dbo", "BRANDS", "table")
        self.assertEqual(self.discovery.configure(dataset, {}), self.source)
        source_spec(to_dict(self.source))
        other = SqlServerDiscovery("ETL_SQL_OTHER", Path.cwd())
        self.assertError("invalid_read_options", lambda: other.resolve_dataset(dataset.key))
        self.assertError("access_denied", lambda: other.sample(self.source))
        self.assertError("invalid_read_options", lambda: self.discovery.configure(dataset, {"query": "SELECT 1"}))

    def test_identifiers_are_parameters_or_escaped_never_sql_fragments(self):
        name = "x]; DROP TABLE x;--"
        self.cursor.fetchone.return_value = (name,)
        self.cursor.fetchmany.return_value = []
        source = self.discovery.configure(self.discovery._dataset("strange]schema", name, "view"), {})
        self.discovery.sample(source)
        queries = [call.args[0] for call in self.cursor.execute.call_args_list]
        self.assertIn("SELECT * FROM [strange]]schema].[x]]; DROP TABLE x;--]", queries)
        self.assertTrue(all(q.startswith("SELECT ") for q in queries))
        self.assertTrue(all(" JOIN " not in q.upper() and "COUNT(" not in q.upper() for q in queries))

    def test_declared_metadata_separate_from_driver_and_unknowns(self):
        self.cursor.fetchmany.side_effect = [[("code", "nvarchar", -1), ("amount", "decimal", 13)], []]
        columns = self.discovery.inspect(self.source)
        self.assertEqual((columns[0].column.native_type, columns[0].declared_type, columns[0].max_length_bytes), ("str", "nvarchar", -1))
        self.assertEqual((columns[1].column.precision, columns[1].column.scale, columns[1].column.nullable), (28, 4, True))
        self.cursor.fetchmany.side_effect = [[]]
        self.assertTrue(all(c.declared_type is None and c.max_length_bytes is None for c in self.discovery.inspect(self.source)))

    def test_sample_batched_native_exact_no_fetchall_and_no_processing(self):
        self.cursor.fetchmany.side_effect = [[("003", Decimal("12345678901234567890.4500")), ("", None)]]
        with patch("etl.engine.process_row", side_effect=AssertionError("not a preview")):
            sample = self.discovery.sample(self.source, limit=2)
        self.cursor.fetchmany.assert_called_once_with(2)
        self.cursor.fetchall.assert_not_called()
        self.assertEqual(sample.stop_reason, "row_limit")
        self.assertEqual([r.number for r in sample.rows], [1, 2])
        self.assertTrue(all(r.line_start is None and r.line_end is None for r in sample.rows))
        self.assertEqual(sample.rows[0].values["code"], "003")
        encoded = json.loads(json.dumps(to_dict(sample), default=json_default))
        self.assertEqual(encoded["rows"][0]["values"]["amount"], {"$type": "decimal", "value": "12345678901234567890.4500"})
        self.assertEqual(encoded["rows"][1]["values"], {"code": "", "amount": None})
        self.assertEqual(self.cursor.close.call_count, 2)
        self.assertEqual(self.connection.close.call_count, 2)

    def test_sample_end_of_source_and_fetch_failure_close(self):
        self.cursor.fetchmany.side_effect = [[("003", None)], []]
        sample = self.discovery.sample(self.source, limit=20)
        self.assertEqual(sample.stop_reason, "end_of_source")
        self.assertEqual(len(sample.rows), 1)
        self.cursor.fetchmany.side_effect = pyodbc.Error("HY000", "secret details")
        self.assertError("source_read_failed", lambda: self.discovery.sample(self.source))
        self.assertEqual(self.connection.close.call_count, 4)

    def test_large_sql_integers_remain_native_and_cross_json_exactly(self):
        self.cursor.description = [("id", int, None, None, 19, 0, False)]
        self.cursor.fetchmany.side_effect = [[(9223372036854775807,), (-9223372036854775808,)]]
        sample = self.discovery.sample(self.source, limit=2)
        self.assertIs(type(sample.rows[0].values["id"]), int)
        wire = to_dict(sample)
        self.assertEqual(wire["rows"][0]["values"]["id"], {"$type": "integer", "value": "9223372036854775807"})
        self.assertEqual(wire["rows"][1]["values"]["id"]["value"], "-9223372036854775808")
        self.assertEqual(to_dict(123), 123)

    def test_unavailable_or_hidden_object_no_source_read(self):
        self.cursor.fetchone.return_value = None
        with patch("etl.discovery.create_source") as create:
            self.assertError("dataset_unavailable", lambda: self.discovery.sample(self.source))
            create.assert_not_called()

    def test_errors_sanitized_and_all_resources_closed(self):
        for state, code in (("HYT00", "timeout"), ("28000", "access_denied"), ("42000", "source_read_failed")):
            self.cursor.execute.side_effect = pyodbc.Error(state, "secret password")
            self.assertError(code, lambda: self.discovery.list_namespaces())
        self.assertEqual(self.connection.close.call_count, 3)
        self.assertEqual(self.cursor.close.call_count, 3)
        self.connect.side_effect = pyodbc.Error("08001", "secret host")
        self.assertError("connection_failed", lambda: self.discovery.list_namespaces())

    def test_close_failure_does_not_skip_connection_close(self):
        self.cursor.fetchmany.return_value = []
        self.cursor.close.side_effect = RuntimeError("secret")
        self.assertError("source_read_failed", lambda: self.discovery.list_namespaces())
        self.connection.close.assert_called_once()

    def test_unsupported_operations_and_unknown_options(self):
        self.assertError("unsupported_operation", lambda: self.discovery.list_namespaces(("dbo",)))
        self.assertError("invalid_read_options", lambda: self.discovery.list_datasets(()))
        self.assertError("unsupported_operation", lambda: dispatch(Path.cwd(), "counts", {"connector": "csv"}))
        self.assertError("invalid_read_options", lambda: dispatch(Path.cwd(), "sample", {"connector": "csv", "query": "SELECT 1"}))
        self.assertError("unsupported_operation", lambda: dispatch(Path.cwd(), "capabilities", {"connector": "other"}))


if __name__ == "__main__":
    unittest.main()
