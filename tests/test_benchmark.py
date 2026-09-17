import tempfile
import unittest
from pathlib import Path
from decimal import Decimal
from unittest.mock import patch

from etl.models import RowResult, SourceRow
from integration.benchmark import generated, measure, ownership_candidate, main


class BenchmarkTests(unittest.TestCase):
    def test_candidate_nested_values_and_public_copy(self):
        source = SourceRow(1, {'code': '003'})
        nested = ['x']
        result = ownership_candidate(source, {'a': nested}, {'b': Decimal('1.2300')})
        nested.append('later')
        self.assertEqual(result.transformed_values['a'], ('x',))
        self.assertEqual(result, RowResult(source, {'a': ['x']}, {'b': Decimal('1.2300')}))
        with self.assertRaises(TypeError):
            result.converted_values['b'] = 2
        values = {'a': 'before'}
        public = RowResult(source, values, {})
        values['a'] = 'after'
        self.assertEqual(public.transformed_values['a'], 'before')

    def test_streamed_measure_limit_and_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = generated(root, 3, 10)
            output = root / 'output'
            output.mkdir()
            result = measure(spec, root, output, 4)
            self.assertEqual((result['processed'], result['valid'], result['rejected']), (4, 4, 0))
            self.assertGreater(result['total_seconds'], 0)

    def test_sql_requires_explicit_opt_in_before_loading_env(self):
        import json
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'pipeline.json'
            path.write_text(json.dumps({'source': {'kind': 'sqlserver'}, 'columns': []}))
            with patch('sys.argv', ['benchmark', '--pipeline', str(path)]), patch('etl.config.load_workspace_env') as load:
                with self.assertRaises(SystemExit):
                    main()
                load.assert_not_called()
