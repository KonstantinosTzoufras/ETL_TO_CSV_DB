"""Local streaming benchmark; SQL requires an explicit --allow-sql flag.

Run from the repository root: python -m integration.benchmark --help.
No credentials or row values are printed. Outputs live in a temporary directory.
"""
import argparse
from contextlib import ExitStack
import csv
from itertools import islice
import json
from pathlib import Path
import tempfile
from time import perf_counter
from types import MappingProxyType
from unittest.mock import patch

from etl import engine
from etl.exporters import OutputWriter, RejectedWriter
from etl.models import RowResult, _freeze, _SCALARS
from etl.serialization import pipeline_from_dict
from etl.sources import create_source


def ownership_candidate(source, transformed, converted, errors=()):
    """Benchmark-only prototype: adopt owned dicts, freeze non-scalar values.

    Caller transfers both dicts and must never mutate them again. Unlike a
    shallow proxy, nested containers still receive defensive freezing.
    """
    for mapping in (transformed, converted):
        for key, value in mapping.items():
            if not isinstance(key, str):
                raise TypeError('Domain mappings require string keys')
            if type(value) not in _SCALARS:
                mapping[key] = _freeze(value)
    result = object.__new__(RowResult)
    for name, value in [('source', _freeze(source)),
                        ('transformed_values', MappingProxyType(transformed)),
                        ('converted_values', MappingProxyType(converted)),
                        ('errors', _freeze(errors))]:
        object.__setattr__(result, name, value)
    return result


def measure(spec, root, output, rows):
    """One source pass: open/read, process, export/finalize, plus wall time."""
    pipeline = pipeline_from_dict(spec)
    if any('lookup' in column for column in spec['columns']):
        raise ValueError('Benchmark pipelines must not contain lookups')
    prepared = engine.prepare_columns(pipeline)
    timings = dict(read_seconds=0.0, process_seconds=0.0, write_seconds=0.0,
                   processed=0, valid=0, rejected=0)
    start = perf_counter()
    with ExitStack() as stack:
        tick = perf_counter()
        stream = stack.enter_context(create_source(pipeline.source, root).open())
        timings['read_seconds'] += perf_counter() - tick
        names = {column.name for column in stream.schema}
        if any(c.source is not None and c.source not in names for c in pipeline.columns):
            raise ValueError('Mapping references a missing source column')
        with ExitStack() as files:
            tick = perf_counter()
            writer = OutputWriter(output, [c.name for c in pipeline.columns],
                                  spec['destination'], files, version=pipeline.version)
            rejected = RejectedWriter(output, spec['destination'], files, version=pipeline.version)
            timings['write_seconds'] += perf_counter() - tick
            iterator = iter(islice(stream, rows))
            try:
                while True:
                    tick = perf_counter()
                    try:
                        row = next(iterator)
                    except StopIteration:
                        timings['read_seconds'] += perf_counter() - tick
                        break
                    timings['read_seconds'] += perf_counter() - tick
                    tick = perf_counter()
                    result = engine.process_row(pipeline, row, {}, prepared)
                    timings['process_seconds'] += perf_counter() - tick
                    timings['processed'] += 1
                    timings['valid' if result.valid else 'rejected'] += 1
                    tick = perf_counter()
                    (writer if result.valid else rejected).write_result(result)
                    timings['write_seconds'] += perf_counter() - tick
            finally:
                tick = perf_counter()
                writer.finish()
                files.close()
                timings['write_seconds'] += perf_counter() - tick
    timings['total_seconds'] = perf_counter() - start
    return timings


def generated(root, columns, rows):
    names = [f'field_{i}' for i in range(columns)]
    path = root / 'benchmark.csv'
    with path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(names)
        for _ in range(rows):
            writer.writerow([' 003 ', ' Ελληνικά\ntext ', '123.4500'] * (columns // 3)
                            + [' value '] * (columns % 3))
    return {'version': 2, 'name': 'Benchmark',
            'source': {'kind': 'csv', 'path': path.name, 'encoding': 'utf-8', 'delimiter': ','},
            'columns': [{'name': n, 'source': n, 'type': 'string', 'transforms': ['trim']} for n in names],
            'destination': {'kind': 'csv', 'delimiter': ';'}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pipeline', type=Path, help='Existing JSON pipeline; otherwise generate local CSV')
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('--rows', type=int, default=8000, help='Maximum input rows per pass (SQL may prefetch one batch)')
    parser.add_argument('--columns', type=int, default=173, help='Generated CSV width')
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--allow-sql', action='store_true', help='Explicitly permit repeated SQL reads; existing query approval still applies')
    parser.add_argument('--probe-ownership', action='store_true', help='Alternate current constructor and benchmark-only ownership prototype')
    args = parser.parse_args()
    if not (1 <= args.rows <= 10_000_000 and 1 <= args.columns <= 4096 and 1 <= args.rounds <= 20):
        parser.error('Rows, columns or rounds outside supported bounds')
    with tempfile.TemporaryDirectory(prefix='etl-benchmark-') as directory:
        workspace = Path(directory)
        spec = json.loads(args.pipeline.read_text(encoding='utf-8-sig')) if args.pipeline else generated(workspace, args.columns, args.rows)
        if any('lookup' in c for c in spec['columns']):
            parser.error('Lookup pipelines are excluded from this benchmark')
        if spec['source']['kind'] != 'csv':
            if not args.allow_sql:
                parser.error('SQL benchmark disabled: explicit --allow-sql required')
            from etl.config import load_workspace_env
            load_workspace_env(args.root)
        root = args.root if args.pipeline else workspace
        results = {'current': []}
        if args.probe_ownership:
            results['ownership'] = []
        for round_number in range(args.rounds):
            variants = list(results)
            if round_number % 2:
                variants.reverse()
            for variant in variants:
                output = workspace / f'{round_number}-{variant}'
                output.mkdir()
                with patch.object(engine, 'RowResult', ownership_candidate if variant == 'ownership' else RowResult):
                    result = measure(spec, root, output, args.rows)
                results[variant].append(result)
        print(json.dumps(results, indent=2))
        if args.probe_ownership:
            print('Best wall-time ratio current/ownership:',
                  min(r['total_seconds'] for r in results['current']) /
                  min(r['total_seconds'] for r in results['ownership']))


if __name__ == '__main__':
    main()
