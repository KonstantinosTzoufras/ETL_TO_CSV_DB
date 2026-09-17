"""Isolated one-column newline-cleaning experiment; no production registration.

python -m integration.benchmark_linebreaks --rows 1000000 --rounds 3
"""
import argparse
from contextlib import contextmanager
import csv
import json
from pathlib import Path
import statistics
import tempfile
from time import perf_counter
from unittest.mock import patch

from etl import spec, transforms
from integration.benchmark import measure

OPERATION = 'linebreaks_to_space'


def linebreaks_to_space(value):
    # CRLF must be replaced first, as one newline, not two spaces.
    return value.replace('\r\n', ' ').replace('\r', ' ').replace('\n', ' ')


@contextmanager
def experimental_transform():
    # Only this isolated benchmark process can see the additional operation.
    with patch.object(spec, 'TRANSFORMS', spec.TRANSFORMS | {OPERATION}), \
         patch.object(transforms, '_OPERATIONS', transforms._OPERATIONS | {OPERATION}), \
         patch.dict(transforms._STRING_OPS, {OPERATION: linebreaks_to_space}):
        yield


def values(length, percent):
    plain = ('Ελληνικό κείμενο 003; "abc" ' * (length // 26 + 1))[:length]
    broken = plain[:length // 2] + '\r\n' + plain[length // 2:]
    return tuple(broken if i < percent else plain for i in range(100))


def micro(rows, rounds):
    results = []
    with experimental_transform():
        for length in (100, 1000):
            for percent in (0, 10, 100):
                samples = values(length, percent)
                times = {'baseline': [], 'clean': []}
                for round_number in range(rounds):
                    for mode in (('baseline', 'clean') if round_number % 2 == 0 else ('clean', 'baseline')):
                        operations = (OPERATION,) if mode == 'clean' else ()
                        start = perf_counter()
                        for i in range(rows):
                            transforms.apply_transforms(samples[i % 100], operations, version=2)
                        times[mode].append(perf_counter() - start)
                results.append({'characters': length, 'percent_with_crlf': percent,
                                'seconds': times,
                                'median_added_seconds': statistics.median(times['clean']) - statistics.median(times['baseline'])})
    return results


def full(rows, rounds):
    samples = values(100, 10)
    results = {'baseline': [], 'clean': []}
    with tempfile.TemporaryDirectory(prefix='etl-linebreaks-') as directory, experimental_transform():
        root = Path(directory)
        with (root / 'input.csv').open('w', encoding='utf-8', newline='') as handle:
            writer = csv.writer(handle)
            writer.writerow(['description'])
            for i in range(rows):
                writer.writerow([samples[i % 100]])
        for round_number in range(rounds):
            for mode in (('baseline', 'clean') if round_number % 2 == 0 else ('clean', 'baseline')):
                pipeline = {'version': 2, 'name': 'Newline benchmark',
                            'source': {'kind': 'csv', 'path': 'input.csv', 'delimiter': ',', 'encoding': 'utf-8'},
                            'columns': [{'name': 'description', 'source': 'description', 'type': 'string',
                                         'transforms': [OPERATION] if mode == 'clean' else []}],
                            'destination': {'kind': 'csv', 'delimiter': ';', 'encoding': 'utf-8'}}
                # Separate temporary outputs for each pass, removed after verification.
                with tempfile.TemporaryDirectory(dir=root) as output:
                    result = measure(pipeline, root, Path(output), rows)
                    assert result['processed'] == result['valid'] == rows and result['rejected'] == 0
                    # Independent output oracle, outside the timed section.
                    with (Path(output) / 'valid.csv').open(encoding='utf-8', newline='') as handle:
                        reader = csv.reader(handle, delimiter=';')
                        assert next(reader) == ['description']
                        count = 0
                        for count, record in enumerate(reader, 1):
                            original = samples[(count - 1) % 100]
                            expected = original.replace('\r\n', ' ') if mode == 'clean' else original
                            assert record == [expected]
                        assert count == rows
                    results[mode].append(result)
                    print(f'Full pass {round_number + 1} {mode}: {result["total_seconds"]:.3f}s; {rows} rows verified', flush=True)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rows', type=int, default=1_000_000)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--output', type=Path, default=Path('data/linebreak-benchmark.json'))
    args = parser.parse_args()
    if not 1 <= args.rows <= 1_000_000 or not 1 <= args.rounds <= 10:
        parser.error('Use 1–1,000,000 rows and 1–10 rounds')
    result = {'rows_per_pass': args.rows, 'rounds': args.rounds,
              'micro': micro(args.rows, args.rounds)}
    print('Transform-only measurements complete; starting streamed CSV exports.', flush=True)
    result['full_csv_100_characters_10_percent_crlf'] = full(args.rows, args.rounds)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(f'Results saved to {args.output}', flush=True)


if __name__ == '__main__':
    main()
