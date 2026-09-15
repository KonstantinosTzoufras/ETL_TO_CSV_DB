"""Small fixtures shared by legacy characterization and future-v2 contracts."""
import copy
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from etl.engine import execute, map_row


def field(**options):
    return {"name": "value", "source": "value", **options}


def mapped(value, **options):
    return map_row({"value": value}, [field(**options)], {})


class SemanticsTestCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def execute_rows(self, rows, columns=None, limit=None, observed=None):
        # Use the real execution loop and writers, with an in-memory source so
        # SQL-native None and Decimal values can be characterized without a DB.
        spec = {
            "version": 1, "name": "Semantics fixture",
            "source": {"kind": "csv", "path": "fixture.csv"},
            "columns": columns or [field()], "destination": {"kind": "csv"},
        }

        @contextmanager
        def source(*args, **kwargs):
            yield list(rows[0]), iter(rows)

        def record(row, mappings, lookups):
            result = map_row(row, mappings, lookups)
            if observed is not None:
                observed.append(copy.deepcopy((row, result)))
            return result

        with patch("etl.engine.open_source", source), patch("etl.engine.map_row", side_effect=record):
            return execute(spec, self.root, self.root / "out" if limit is None else None, limit=limit)
