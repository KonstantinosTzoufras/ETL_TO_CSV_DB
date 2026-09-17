import unittest
from etl.transforms import apply_transform
from etl.spec import ConfigError
from integration.benchmark_linebreaks import experimental_transform, OPERATION, full


class LinebreakExperimentTests(unittest.TestCase):
    def test_explicit_semantics_and_scoped_registration(self):
        with experimental_transform():
            for original, expected in [(None, None), ('', ''), ('003', '003'),
                                       ('Α\r\nΒ\nΓ\rΔ', 'Α Β Γ Δ'),
                                       ('\r\n\r\n', '  '), (' a\t b ', ' a\t b ')]:
                with self.subTest(original=original):
                    self.assertEqual(apply_transform(original, OPERATION, version=2), expected)
            with self.assertRaises(ValueError):
                apply_transform(123, OPERATION, version=2)
        with self.assertRaises(ConfigError):
            apply_transform('a\nb', OPERATION, version=2)

    def test_export_roundtrip(self):
        result = full(100, 1)
        self.assertEqual(result['clean'][0]['valid'], 100)
        self.assertEqual(result['baseline'][0]['valid'], 100)
