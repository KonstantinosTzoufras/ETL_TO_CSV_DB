"""Authored query datasets, separate from table/view catalog discovery."""
from dataclasses import replace
from pathlib import Path

from .discovery import Dataset, DiscoveryCapabilities, DiscoveredColumn, SourceSample, _Discovery, _limit, _size, MAX_SAMPLE_BYTES, DiscoveryError
from .models import SourceDefinition
from .queries import authorize, check, query_from_dict
from .query_source import SqlServerQuerySource
from .serialization import _source_to_dict, _source_from_dict
from .spec import source_spec


class SqlServerQueryDiscovery(_Discovery):
    def __init__(self, connection_env, root):
        self.root = Path(root).resolve()
        self.connection_env = connection_env
        self.context = 'sqlserver_query:' + str(connection_env)

    def capabilities(self):
        return DiscoveryCapabilities(('resolve_dataset', 'configure', 'inspect', 'sample'), ('query',),
                                     ('name', 'ordinal', 'native_type', 'nullable', 'precision', 'scale', 'record_number'))

    def list_namespaces(self, namespace=(), *, cursor=None, limit=100):
        raise DiscoveryError('unsupported_operation', 'Query datasets are authored, not catalog entries')

    list_datasets = list_namespaces

    def resolve_dataset(self, locator):
        check(locator == self.context, 'QUERY_INVALID', 'Choose the query dataset for this connection')
        return Dataset(self.context, 'sqlserver_query', 'query', (), 'SQL Query')

    def configure(self, dataset, options):
        check(dataset == self.resolve_dataset(self.context), 'QUERY_INVALID', 'Dataset belongs to another connection')
        check(isinstance(options, dict) and set(options) == {'query'}, 'QUERY_INVALID', 'Query definition required')
        query = query_from_dict(options['query'])
        return self._validate_source(SourceDefinition('sqlserver_query', {'connection_env': self.connection_env, 'query': query}))

    def _validate_source(self, source):
        check(isinstance(source, SourceDefinition) and source.kind == 'sqlserver_query', 'QUERY_INVALID', 'Expected a query source')
        raw = _source_to_dict(source)
        source_spec(raw)
        source = _source_from_dict(raw)
        check(source.options['connection_env'] == self.connection_env, 'QUERY_PERMISSION_DENIED', 'Source belongs to another connection')
        authorize(self.connection_env, source.options['query'])
        return source

    def inspect(self, source):
        source = self._validate_source(source)
        return tuple(DiscoveredColumn(column) for column in SqlServerQuerySource(source, timeout_cap=15).read_schema())

    def sample(self, source, *, limit=20):
        _limit(limit)
        source = self._validate_source(source)
        with SqlServerQuerySource(source, batch_size=limit, timeout_cap=15).open() as stream:
            sample = SourceSample(source, stream.schema, (), limit, 'end_of_source')
            size = _size(sample)
            check(size <= MAX_SAMPLE_BYTES, 'sample_too_large', 'Sample schema exceeds 1 MiB')
            rows = []
            for _ in range(limit):
                try:
                    row = next(stream.rows)
                except StopIteration:
                    break
                size += _size(row) + 2
                check(size <= MAX_SAMPLE_BYTES, 'sample_too_large', 'Sample exceeds 1 MiB; reduce the limit')
                rows.append(row)
            else:
                sample = replace(sample, stop_reason='row_limit')
            return replace(sample, rows=tuple(rows))
