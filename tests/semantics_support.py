"""Small fixtures shared by legacy characterization and future-v2 contracts."""
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from etl.engine import execute, map_row, legacy_projection
from etl.models import SourceColumn, SourceRow
from etl.sources import SourceStream
from unittest.mock import Mock


def field(**options):
    return {"name": "value", "source": "value", **options}


def mapped(value, **options):
    return map_row({"value": value}, [field(**options)], {})


class SemanticsTestCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def execute_rows(self, rows, columns=None, limit=None, observed=None, version=1):
        # Use the real execution loop and writers, with an in-memory source so
        # SQL-native None and Decimal values can be characterized without a DB.
        spec = {
            "version": version, "name": "Semantics fixture",
            "source": {"kind": "csv", "path": "fixture.csv"},
            "columns": columns or [field()], "destination": {"kind": "csv"},
        }

        @contextmanager
        def source():
            yield SourceStream(tuple(SourceColumn(name) for name in rows[0]),
                               iter(SourceRow(n, row) for n, row in enumerate(rows, 1)))

        def record(result):
            if observed is not None:
                observed.append((dict(result.original_values), legacy_projection(result)) if version == 1 else result)

        adapter = Mock()
        adapter.open = source
        with patch("etl.engine.create_source", return_value=adapter):
            return execute(spec, self.root, self.root / "out" if limit is None else None,
                           limit=limit, on_row=record)
