import unittest
from etl.transforms import apply_transform
from integration.benchmark_linebreaks import OPERATION, full


class LinebreakExperimentTests(unittest.TestCase):
    def test_explicit_semantics(self):
        # linebreaks_to_space graduated to a production transform (see
        # tests/test_desired_v2_semantics.py for its full coverage); this
        # module now only benchmarks it, using the real registered operation.
        for original, expected in [(None, None), ('', ''), ('003', '003'),
                                   ('Α\r\nΒ\nΓ\rΔ', 'Α Β Γ Δ'),
                                   ('\r\n\r\n', '  '), (' a\t b ', ' a\t b ')]:
            with self.subTest(original=original):
                self.assertEqual(apply_transform(original, OPERATION, version=2), expected)
        with self.assertRaises(ValueError):
            apply_transform(123, OPERATION, version=2)

    def test_export_roundtrip(self):
        result = full(100, 1)
        self.assertEqual(result['clean'][0]['valid'], 100)
        self.assertEqual(result['baseline'][0]['valid'], 100)
