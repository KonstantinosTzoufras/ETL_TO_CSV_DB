"""Bounded query-source acceptance. All SQL connections here are fake."""
import copy
import csv
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from datetime import date, datetime
from decimal import Decimal
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import pyodbc
from etl.discovery import dispatch, SourceDiscovery
from etl.engine import execute
from etl.models import QueryDefinition, QueryParameter, SourceDefinition
from etl.queries import QueryError, authorize, parameter_value, query_from_dict, query_to_dict, validate_sql
from etl.query_discovery import SqlServerQueryDiscovery
from etl.query_source import SqlServerQuerySource
from etl.serialization import pipeline_from_dict, pipeline_to_dict
from etl.sources import create_source, Source
from etl.store import Store

REFERENCE = 'ETL_SQL_QUERY_TEST'
POLICY = {REFERENCE: {'read_only': True, 'objects': [['dbo', 'BRANDS'], ['dbo', 'Regions']], 'max_timeout_seconds': 120}}


def query(sql='SELECT Code, Name, Amount FROM dbo.BRANDS WHERE Code <> ?', parameters=None):
    return {'format_version': 1, 'dialect': 'tsql', 'sql': sql,
            'parameters': parameters if parameters is not None else [{'name': 'excluded', 'type': 'string', 'value': 'no match'}], 'timeout_seconds': 60}


def source(q=None):
    return {'kind': 'sqlserver_query', 'connection_env': REFERENCE, 'query': q or query()}


def pipeline():
    return {'version': 2, 'name': 'Query acceptance', 'source': source(),
            'columns': [{'name': 'Code', 'source': 'Code', 'type': 'string'},
                        {'name': 'Name', 'source': 'Name', 'type': 'string', 'transforms': ['trim']},
                        {'name': 'Amount', 'source': 'Amount', 'type': 'decimal'}],
            'destination': {'kind': 'csv', 'encoding': 'utf-8', 'delimiter': ';'}}


@contextmanager
def fake_sql(rows=None, description=None):
    rows = rows if rows is not None else [('003', ' Ελλάδα\r\nline two ', Decimal('123.4500'))] * 25
    description = description if description is not None else [('Code', str, None, 20, None, None, False), ('Name', str, None, 200, None, None, True), ('Amount', Decimal, None, None, 28, 4, True)]
    sessions = []
    def connect(*args, **kwargs):
        connection, cursor = MagicMock(), MagicMock()
        connection.cursor.return_value = cursor
        cursor.description = description
        cursor.fetchall.side_effect = AssertionError('fetchall is forbidden')
        cursor.execute.return_value = cursor
        remaining = iter(rows)
        def fetchmany(size):
            result = []
            for _ in range(size):
                try: result.append(next(remaining))
                except StopIteration: break
            return result
        cursor.fetchmany.side_effect = fetchmany
        sessions.append((connection, cursor))
        return connection
    with patch.dict(os.environ, {'ETL_QUERY_CONNECTIONS': json.dumps(POLICY), REFERENCE: 'NEVER_PRINT_CREDENTIALS'}), patch('pyodbc.connect', side_effect=connect) as mocked:
        yield sessions, mocked


class QueryValidationTests(unittest.TestCase):
    def test_supported_subset(self):
        cases = [
            'SELECT b.Code, r.Name, b.Amount-1 AS Net FROM dbo.BRANDS b LEFT JOIN dbo.Regions r ON r.Id=b.RegionId WHERE b.Code=? ORDER BY b.Code;',
            'SELECT DISTINCT TOP (10) Code FROM dbo.BRANDS',
            'SELECT x.Code FROM (SELECT Code FROM dbo.BRANDS) x WHERE EXISTS (SELECT 1 FROM dbo.Regions r WHERE r.Code=x.Code)',
            'SELECT Name, COUNT(*) AS n, SUM(Amount) AS a FROM dbo.BRANDS GROUP BY Name HAVING COUNT(*) > 1',
            "SELECT CASE WHEN Name IS NULL THEN N'κενό' ELSE Name END AS Name FROM dbo.BRANDS",
            'SELECT COALESCE(Name, ?), NULLIF(Name, ?), UPPER(Name), LOWER(Name), LEN(Name), ABS(Amount), ROUND(Amount,2), AVG(Amount), MIN(Amount), MAX(Amount) FROM dbo.BRANDS',
            'SELECT Code FROM dbo.BRANDS WHERE Amount BETWEEN ? AND ? AND Code IN (?, ?) AND NOT Name LIKE ?',
            # TRIM, LTRIM and RTRIM share one node; CHAR(n) padding is the
            # common reason to clean a column in the query rather than in Python.
            'SELECT TRIM(Code) AS Code, LTRIM(RTRIM(Name)) AS Name FROM dbo.BRANDS',
            "SELECT TRIM(BOTH ' ' FROM Code) AS Code FROM dbo.BRANDS WHERE RTRIM(Name)=?",
            'SELECT b.Code FROM dbo.BRANDS b RIGHT OUTER JOIN dbo.Regions r ON b.Code=r.Code',
            'SELECT b.Code FROM dbo.BRANDS b FULL JOIN dbo.Regions r ON b.Code=r.Code',
            'SELECT b.Code FROM dbo.BRANDS b CROSS JOIN dbo.Regions r',
        ]
        for sql in cases:
            with self.subTest(sql=sql): validate_sql(sql)

    def test_prohibited_and_unsupported_before_connect(self):
        cases = [
            'INSERT INTO dbo.BRANDS VALUES (1)', 'UPDATE dbo.BRANDS SET Code=1', 'DELETE FROM dbo.BRANDS',
            'MERGE dbo.BRANDS USING dbo.Regions ON 1=1 WHEN MATCHED THEN DELETE;',
            'CREATE TABLE dbo.x (id int)', 'ALTER TABLE dbo.BRANDS ADD x int', 'DROP TABLE dbo.BRANDS',
            'EXEC dbo.proc', 'EXECUTE dbo.proc', 'SELECT 1; DELETE FROM dbo.BRANDS', 'SELECT 1; SELECT 2',
            'SELECT 1;;', ';SELECT 1', 'SELECT 1\nGO', 'SELECT 1 INTO dbo.x', 'SELECT 1 INTO #x',
            'SELECT * FROM #x', 'SELECT * FROM dbo.[#x]', 'SELECT * FROM db.dbo.BRANDS',
            'SELECT * FROM server.db.dbo.BRANDS', "SELECT * FROM OPENQUERY(server,'SELECT 1')", 
            "SELECT * FROM OPENROWSET('x','y','z')", 'SELECT NEXT VALUE FOR dbo.seq',
            'SELECT * FROM dbo.BRANDS WITH (NOLOCK)', 'SELECT * FROM dbo.BRANDS OPTION (MAXDOP 1)',
            'SELECT * FROM dbo.BRANDS b INNER LOOP JOIN dbo.Regions r ON 1=1',
            'SELECT @x=Code FROM dbo.BRANDS', 'SELECT @@VERSION', 'SELECT dbo.fn(Code) FROM dbo.BRANDS',
            'SELECT * FROM dbo.fn()', 'SELECT * FROM BRANDS',
            'WITH x AS (SELECT 1 a) SELECT a FROM x', 'SELECT 1 UNION SELECT 2',
            'SELECT Code FROM dbo.BRANDS FOR XML AUTO', 'SELECT ROW_NUMBER() OVER(ORDER BY Code) FROM dbo.BRANDS',
            'SELECT x.Code FROM (SELECT Code INTO #x FROM dbo.BRANDS) x',
            'SELECT 1 WHERE EXISTS (SELECT NEXT VALUE FOR dbo.seq)', 'SELECT :named',
        ]
        with patch('pyodbc.connect') as connect:
            for sql in cases:
                with self.subTest(sql=sql), self.assertRaises(QueryError):
                    SqlServerQuerySource(source(query(sql, []))).read_schema()
            connect.assert_not_called()

    def test_comments_quotes_and_marker_count(self):
        sql = "/* SELECT INTO x; ? */ SELECT [Odd;?Name], ';?EXEC' AS Text FROM [dbo].[BRANDS] WHERE Code=?; -- ? DELETE"
        self.assertEqual(validate_sql(sql)[0], 1)
        query_from_dict(query(sql))
        with self.assertRaisesRegex(QueryError, 'marker count'): query_from_dict(query(sql, []))
        with self.assertRaises(QueryError): query_from_dict(query('SELECT ?', []))

    def test_parameter_types_precision_and_null(self):
        cases = [('string', '003', '003'), ('string', '', ''), ('string', ' \n ', ' \n '),
                 ('string', "'; DROP TABLE x; --", "'; DROP TABLE x; --"), ('bool', True, True),
                 ('int', '9223372036854775807', 9223372036854775807),
                 ('decimal', '12345678901234567890.1234500', Decimal('12345678901234567890.1234500')),
                 ('date', '2024-02-29', date(2024,2,29)), ('datetime', '2024-02-29T12:30:00.123456', datetime(2024,2,29,12,30,0,123456))]
        for kind, value, expected in cases:
            with self.subTest(kind=kind, value=value):
                actual = parameter_value(QueryParameter('p', kind, value))
                self.assertEqual(actual, expected)
                self.assertEqual(type(actual), type(expected))
        for kind in ('string','int','decimal','bool','date','datetime'):
            self.assertIsNone(parameter_value(QueryParameter('p',kind,None)))

    def test_parameter_invalid_representations(self):
        for kind, value in [('int','003'),('int',' 3'),('int',True),('int',9223372036854775807),('int','9223372036854775808'),('decimal',1.2),('decimal','1e2'),('decimal','NaN'),('decimal','0.'+'1'*39),('date','2023-02-29'),('date','01/02/2024'),('datetime','2024-01-01'),('datetime','2024-01-01T12:00:00Z'),('bool','true'),('string',3)]:
            with self.subTest(kind=kind,value=value), self.assertRaises(QueryError):
                parameter_value(QueryParameter('p',kind,value))

    def test_strict_models_and_roundtrip(self):
        raw=pipeline(); model=pipeline_from_dict(raw)
        self.assertIsInstance(model.source.options['query'],QueryDefinition)
        self.assertEqual(pipeline_to_dict(model),raw)
        raw['source']['query']['parameters'][0]['value']='changed'
        self.assertEqual(model.source.options['query'].parameters[0].value,'no match')
        with self.assertRaises(FrozenInstanceError): model.source.options['query'].sql='SELECT 1'
        with self.assertRaises(FrozenInstanceError): model.source.options['query'].parameters[0].value='x'
        for extra in ('credentials','connection_string','source'):
            invalid=query(); invalid[extra]='secret'
            with self.assertRaises(QueryError): query_from_dict(invalid)
        for invalid in [dict(query(), timeout_seconds=0), dict(query(), timeout_seconds=True), dict(query(), format_version=2), dict(query(), dialect='mysql')]:
            with self.assertRaises(QueryError): query_from_dict(invalid)

    def test_query_lookup_dependencies_are_out_of_scope(self):
        spec=pipeline()
        spec['columns'][0]['lookup']={'source':source(), 'column':'Code'}
        with self.assertRaisesRegex(ValueError,'lookup dependencies'):
            pipeline_from_dict(spec)

    def test_parser_diagnostics_never_log_sql(self):
        with patch('sqlglot.parser.logger.warning') as warning:
            for sql in ["EXEC SECRET_VALUE", "SELECT 'SECRET_VALUE' BROKEN (", "SELECT * FROM dbo.BRANDS OPTION(RECOMPILE)"]:
                with self.assertRaises(QueryError) as error: validate_sql(sql)
                self.assertNotIn('SECRET_VALUE',str(error.exception))
            warning.assert_not_called()


class QuerySourceTests(unittest.TestCase):
    def test_approval_is_required_and_rechecked(self):
        adapter=SqlServerQuerySource(source())
        with patch.dict(os.environ, {'ETL_QUERY_CONNECTIONS':'{}'}), patch('pyodbc.connect') as connect:
            with self.assertRaises(QueryError) as error: adapter.read_schema()
            self.assertEqual(error.exception.code,'QUERY_PERMISSION_DENIED');connect.assert_not_called()
        with fake_sql() as (sessions, connect):
            adapter.read_schema()
            with patch.dict(os.environ, {'ETL_QUERY_CONNECTIONS':'{}'}):
                with self.assertRaises(QueryError): adapter.read_schema()
            self.assertEqual(connect.call_count,1)

    def test_object_and_timeout_policy(self):
        with fake_sql() as (_, connect):
            for q in [query('SELECT Code FROM dbo.Unapproved', []), dict(query(),timeout_seconds=121)]:
                with self.assertRaises(QueryError): SqlServerQuerySource(source(q)).read_schema()
            connect.assert_not_called()

    def test_schema_no_fetch_and_bound_values(self):
        value="'; DROP TABLE dbo.BRANDS; --"
        spec=source(query(parameters=[{'name':'p','type':'string','value':value}]))
        with fake_sql() as (sessions, connect):
            adapter=create_source(spec,'.');self.assertIsInstance(adapter,Source)
            columns=adapter.read_schema();connection,cursor=sessions[0]
            self.assertEqual([c.name for c in columns],['Code','Name','Amount'])
            self.assertEqual((columns[2].native_type,columns[2].precision,columns[2].scale),('Decimal',28,4))
            self.assertIsNone(columns[0].precision)
            cursor.execute.assert_called_once_with(spec['query']['sql'],(value,))
            self.assertNotIn(value,cursor.execute.call_args.args[0])
            cursor.fetchmany.assert_not_called();cursor.fetchall.assert_not_called()
            cursor.setinputsizes.assert_called_once();cursor.close.assert_called_once();connection.close.assert_called_once()
            self.assertEqual(connect.call_args.kwargs,{'timeout':10,'autocommit':True,'readonly':True})

    def test_streaming_positions_and_early_close(self):
        with fake_sql() as (sessions,_):
            adapter=SqlServerQuerySource(source(),batch_size=3)
            with adapter.open() as stream:
                rows=list(stream)
            self.assertEqual([r.number for r in rows],list(range(1,26)))
            self.assertIsNone(rows[0].line_start)
            self.assertEqual(rows[0].values['Code'],'003')
            self.assertTrue(all(call.args==(3,) for call in sessions[0][1].fetchmany.call_args_list))
            with adapter.open() as stream: next(stream.rows)
            for connection,cursor in sessions:
                cursor.close.assert_called_once();connection.close.assert_called_once();cursor.fetchall.assert_not_called()
            self.assertEqual(sessions[1][1].fetchmany.call_count,1)

    def test_processing_exception_cleanup(self):
        with fake_sql() as (sessions,_):
            with self.assertRaisesRegex(RuntimeError,'consumer'):
                with SqlServerQuerySource(source()).open() as stream:
                    next(stream.rows);raise RuntimeError('consumer')
            for resource in sessions[0]: resource.close.assert_called_once()

    def test_driver_error_sanitized_and_both_resources_closed(self):
        for operation in ('execute','fetchmany'):
            connection,cursor=MagicMock(),MagicMock();connection.cursor.return_value=cursor
            cursor.description=[('Code',str,None,None,None,None,None)]
            getattr(cursor,operation).side_effect=pyodbc.Error('HYT00','SECRET parameter connection_string')
            with fake_sql(),patch('pyodbc.connect',return_value=connection):
                with self.assertRaises(QueryError) as error:
                    with SqlServerQuerySource(source()).open() as stream:next(stream.rows)
                self.assertEqual(error.exception.code,'QUERY_TIMEOUT');self.assertNotIn('SECRET',str(error.exception))
                cursor.close.assert_called_once();connection.close.assert_called_once()

    def test_bad_schema_closes_and_has_actionable_error(self):
        for headers in [[''],['x','x'],['X','x']]:
            with fake_sql(description=[(h,str,None,None,None,None,None) for h in headers]) as (sessions,_):
                with self.assertRaises(QueryError) as error:SqlServerQuerySource(source()).read_schema()
                self.assertEqual(error.exception.code,'QUERY_SCHEMA_INVALID')
                for resource in sessions[0]:resource.close.assert_called_once()

    def test_preview_full_equivalence_and_exact_export(self):
        with tempfile.TemporaryDirectory() as temporary, fake_sql() as (sessions,_):
            root=Path(temporary); previews=[];full=[]
            preview=execute(pipeline(),root,limit=3,on_row=previews.append)
            result=execute(pipeline(),root,root/'runs',on_row=full.append)
            self.assertEqual(previews,full[:3]);self.assertEqual(preview['processed'],3);self.assertEqual(result['processed'],25)
            self.assertEqual(sessions[0][1].fetchmany.call_args_list[0].args,(3,))
            self.assertEqual(previews[0].original_values['Name'],' Ελλάδα\r\nline two ')
            self.assertEqual(previews[0].transformed_values['Name'],'Ελλάδα\r\nline two')
            with (Path(result['directory'])/'valid.csv').open(encoding='utf-8',newline='') as handle: rows=list(csv.reader(handle,delimiter=';'))
            self.assertEqual(len(rows),26);self.assertEqual(rows[1],['003','Ελλάδα\r\nline two','123.4500'])

    def test_large_execution_is_batched(self):
        with tempfile.TemporaryDirectory() as temporary, fake_sql(rows=[('003','name',Decimal('1.0000'))]*10001) as (sessions,_):
            result=execute(pipeline(),Path(temporary),Path(temporary)/'runs')
            self.assertEqual(result['processed'],10001)
            cursor=sessions[0][1];cursor.fetchall.assert_not_called()
            self.assertEqual(cursor.fetchmany.call_count,12)
            self.assertTrue(all(c.args==(1000,) for c in cursor.fetchmany.call_args_list))

    def test_discovery_source_sample_is_raw_bounded_and_has_no_processing(self):
        with fake_sql() as (sessions,_):
            discovery=SqlServerQueryDiscovery(REFERENCE,'.');self.assertIsInstance(discovery,SourceDiscovery)
            dataset=discovery.resolve_dataset('sqlserver_query:'+REFERENCE)
            configured=discovery.configure(dataset,{'query':query()})
            sample=discovery.sample(configured,limit=2)
            self.assertEqual(len(sample.rows),2);self.assertEqual(sample.stop_reason,'row_limit')
            self.assertEqual(sample.rows[0].values['Name'],' Ελλάδα\r\nline two ')
            self.assertEqual(sessions[0][0].timeout,15)
            sessions[0][1].fetchmany.assert_called_once_with(2)
            self.assertEqual(discovery.inspect(configured)[0].declared_type,None)
            self.assertEqual(sessions[1][0].timeout,15)
            sessions[1][1].fetchmany.assert_not_called()
            wire=dispatch('.', 'sample', {'connector':'sqlserver_query','connection_env':REFERENCE,'source':source(),'limit':1})
            self.assertEqual(wire['source'],source())
            self.assertEqual(wire['rows'][0]['values']['Code'],'003')

    def test_typed_binding_sizes_and_nulls(self):
        params = [{'name':'p','type':kind,'value':value} for kind,value in [
            ('decimal','12345678901234567890.1234500'), ('int','9223372036854775807'),
            ('date','2024-02-29'), ('datetime','2024-02-29T12:00:00.123456'),
            ('bool',True), ('string',''), ('string',None), ('decimal',None), ('string','\U0001f600')]]
        q=query('SELECT '+', '.join('? AS p'+str(i) for i in range(len(params))),params)
        with fake_sql() as (sessions,_):
            SqlServerQuerySource(source(q)).read_schema()
            cursor=sessions[0][1]
            bound=cursor.execute.call_args.args[1]
            self.assertEqual(str(bound[0]),'12345678901234567890.1234500')
            self.assertIsInstance(bound[0],Decimal)
            self.assertEqual(bound[1],9223372036854775807)
            self.assertEqual(bound[5:8],('',None,None))
            sizes=cursor.setinputsizes.call_args.args[0]
            self.assertEqual(sizes[0],(pyodbc.SQL_DECIMAL,27,7))
            self.assertEqual(sizes[8],(pyodbc.SQL_WVARCHAR,2,0))

    def test_sample_limits_size_empty_rows_and_schema(self):
        with fake_sql(rows=[]):
            discovery=SqlServerQueryDiscovery(REFERENCE,'.')
            configured=discovery.configure(discovery.resolve_dataset('sqlserver_query:'+REFERENCE),{'query':query()})
            sample=discovery.sample(configured)
            self.assertEqual(sample.rows,());self.assertEqual(sample.stop_reason,'end_of_source')
            self.assertEqual(len(sample.columns),3)
            for limit in (0,101,True):
                with self.assertRaises(ValueError):discovery.sample(configured,limit=limit)
        with fake_sql(rows=[('003','x'*1048576,Decimal('1'))]) as (sessions,_):
            with self.assertRaises(QueryError):discovery.sample(configured,limit=1)
            for resource in sessions[0]:resource.close.assert_called_once()

    def test_close_failure_does_not_mask_consumer_exception(self):
        with fake_sql() as (sessions,_):
            with self.assertRaisesRegex(RuntimeError,'original'):
                with SqlServerQuerySource(source()).open() as stream:
                    sessions[0][1].close.side_effect=RuntimeError('close')
                    raise RuntimeError('original')
            sessions[0][0].close.assert_called_once()

    def test_query_source_preserves_processing_version_boundary(self):
        rows=[('003','',None)]
        for version, expected in [(1,None),(2,'')]:
            with self.subTest(version=version),fake_sql(rows=rows):
                spec=pipeline();spec['version']=version;spec['destination']={'kind':'csv'}
                results=[];execute(spec,Path('.'),limit=1,on_row=results.append)
                self.assertEqual(results[0].converted_values['Name'],expected)
                self.assertIsNone(results[0].converted_values['Amount'])

    def test_snapshot_does_not_follow_later_edits(self):
        with tempfile.TemporaryDirectory() as temporary:
            store=Store(Path(temporary)/'etl.sqlite3');spec=pipeline()
            run=store.create_run(spec);saved=store.save(spec)
            spec['source']['query']['parameters'][0]['value']='edited'
            store.save(spec,saved)
            snapshot=store.run(run)['spec']
            self.assertEqual(snapshot['source']['query']['parameters'][0]['value'],'no match')
            self.assertEqual(pipeline_to_dict(pipeline_from_dict(snapshot)),snapshot)


if __name__=='__main__':unittest.main()
