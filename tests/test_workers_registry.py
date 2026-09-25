import unittest
from unittest.mock import patch

from etl.spec import ConfigError
from etl.workers import worker_registry


class WorkerRegistryTests(unittest.TestCase):
    def test_empty_by_default(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(worker_registry(), {})

    def test_valid_registry_round_trips(self):
        raw = '{"Worker-PC-01": {"url": "http://10.0.0.5:8790", "token": "secret"}}'
        with patch.dict("os.environ", {"ETL_WORKERS": raw}, clear=True):
            self.assertEqual(worker_registry(), {"Worker-PC-01": {"url": "http://10.0.0.5:8790", "token": "secret"}})

    def test_multiple_workers(self):
        raw = ('{"A": {"url": "http://10.0.0.1:8790", "token": "t1"}, '
               '"B": {"url": "https://10.0.0.2:8790", "token": "t2"}}')
        with patch.dict("os.environ", {"ETL_WORKERS": raw}, clear=True):
            self.assertEqual(set(worker_registry()), {"A", "B"})

    def test_invalid_shapes_are_rejected(self):
        cases = [
            "not json",
            "[]",
            '{"bad name!": {"url": "http://x:1", "token": "t"}}',
            '{"A": {"url": "http://x:1"}}',
            '{"A": {"url": "http://x:1", "token": "t", "extra": 1}}',
            '{"A": {"url": "not-a-url", "token": "t"}}',
            '{"A": {"url": "http://x:1", "token": ""}}',
            '{"A": "http://x:1"}',
        ]
        for raw in cases:
            with self.subTest(raw=raw), patch.dict("os.environ", {"ETL_WORKERS": raw}, clear=True):
                with self.assertRaises(ConfigError):
                    worker_registry()


if __name__ == "__main__":
    unittest.main()
