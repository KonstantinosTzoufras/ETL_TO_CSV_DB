"""Ordered execution contracts. SQL is mocked; no live account is used."""
import copy
import json
import os
from dataclasses import FrozenInstanceError
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pyodbc
from etl.models import OrderedQueryPipeline, QueryExportStep
from etl.ordered import from_dict, to_dict, preflight, preview_spec
from etl.ordered_runs import coordinate, initial_report, interrupted_report, download_path, step_history, StateWriteError
from etl.queries import QueryError
from etl.diagnostics import rejected_page
from etl.store import Store
from etl.web import Application
from tests.test_queries import fake_sql, pipeline, REFERENCE


def ordered(policy='stop', count=3):
    steps=[]
    for identifier in ('customers','orders','balances')[:count]:
        single=pipeline()
        steps.append({'id':identifier,'name':identifier.title(),'query':single['source']['query'],
                      'processing_version':2,'columns':single['columns'],'destination':single['destination']})
    return {'kind':'ordered_query_export','format_version':1,'name':'Ordered extracts',
            'connection_env':REFERENCE,'failure_policy':policy,'steps':steps}


class OrderedTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name);self.data=self.root/'data';self.output=self.data/'runs'
        self.run_id='a'*32;self.events=[]

    def run_steps(self,spec):
        return coordinate(spec,self.root,self.output,self.run_id,persist=self.events.append)

    def test_models_codec_immutability_and_defaults(self):
        spec=ordered();model=from_dict(spec)
        self.assertIsInstance(model,OrderedQueryPipeline);self.assertIsInstance(model.steps[0],QueryExportStep)
        self.assertEqual(to_dict(model),spec)
        spec['steps'][0]['columns'][0]['name']='changed'
        self.assertEqual(model.steps[0].columns[0].name,'Code')
        with self.assertRaises(FrozenInstanceError):model.steps[0].name='changed'
        with self.assertRaises(TypeError):model.steps[0].destination['kind']='xlsx'
        spec=ordered();del spec['failure_policy'];self.assertEqual(from_dict(spec).failure_policy,'stop')
        self.assertEqual([s.id for s in from_dict(ordered()).steps],['customers','orders','balances'])

    def test_definition_guards(self):
        for identifier in ('../x','CON','con','nul','x.csv','x/y','a'*49,'1bad'):
            spec=ordered();spec['steps'][0]['id']=identifier
            with self.subTest(identifier=identifier),self.assertRaises(ValueError):from_dict(spec)
        for mutate in [lambda s:s.update(steps=[]),lambda s:s.update(steps=s['steps']*7),
                       lambda s:s['steps'][1].update(id='customers'),lambda s:s['steps'][0].update(connection_env='ETL_SQL_OTHER'),
                       lambda s:s.update(failure_policy='retry'),lambda s:s['steps'][0].update(depends_on=['orders'])]:
            spec=ordered();mutate(spec)
            with self.assertRaises(ValueError):from_dict(spec)

    def test_validate_later_step_before_any_execution(self):
        spec=ordered();spec['steps'][2]['query']['sql']='DELETE FROM dbo.BRANDS'
        with fake_sql() as (_,connect),self.assertRaises(QueryError):self.run_steps(spec)
        connect.assert_not_called()

    def test_preflight_approval_for_all_steps(self):
        spec=ordered();spec['steps'][2]['query']['sql']='SELECT Code FROM dbo.Unapproved WHERE Code <> ?'
        with fake_sql() as (_,connect):
            result=self.run_steps(spec)
            self.assertEqual(result['status'],'failed');connect.assert_not_called()
            self.assertTrue(all(s['status']=='skipped' for s in result['steps']))

    def test_sequential_fresh_connections_and_generated_outputs(self):
        with fake_sql() as (sessions,connect):
            base=connect.side_effect
            def fresh(*args,**kwargs):
                if sessions:
                    for resource in sessions[-1]:resource.close.assert_called_once()
                return base(*args,**kwargs)
            connect.side_effect=fresh
            result=self.run_steps(ordered())
        self.assertEqual(result['status'],'completed');self.assertEqual(len(sessions),3)
        self.assertEqual([e['id'] for e in result['steps']],['customers','orders','balances'])
        for i,entry in enumerate(result['steps'],1):
            self.assertEqual((entry['processed'],entry['valid'],entry['invalid']),(25,25,0))
            self.assertIsNone(entry['partial_directory']);self.assertTrue(entry['started']);self.assertTrue(entry['finished'])
            path=self.output/self.run_id/f'{i:03d}-{entry["id"]}'
            self.assertEqual(Path(entry['directory']).resolve(),path.resolve())
            self.assertTrue((path/f'{entry["id"]}.csv').is_file());self.assertTrue((path/'rejected.csv').is_file())
            self.assertTrue((path/'report.json').is_file())
        self.assertTrue(result['started']);self.assertTrue(result['finished'])
        self.assertEqual(self.events[0]['steps'][0]['status'],'pending')

    def fail_second(self,connect,sessions):
        base=connect.side_effect
        def fresh(*args,**kwargs):
            connection=base(*args,**kwargs)
            if len(sessions)==2:
                cursor=sessions[-1][1];batch=cursor.fetchmany.side_effect(1000)
                cursor.fetchmany.side_effect=[batch,pyodbc.Error('HYT00','sensitive driver details')]
            return connection
        connect.side_effect=fresh

    def test_stop_failure_keeps_completed_output_and_hides_partial(self):
        spec=ordered()
        with fake_sql() as (sessions,connect):
            self.fail_second(connect,sessions);result=self.run_steps(spec)
        self.assertEqual(result['status'],'failed')
        self.assertEqual([e['status'] for e in result['steps']],['completed','failed','skipped'])
        self.assertEqual(len(sessions),2)
        entry=result['steps'][1];self.assertEqual(entry['processed'],25)
        self.assertEqual(entry['counts_basis'],'observed_before_failure');self.assertIsNone(entry['output'])
        self.assertTrue(list(Path(entry['partial_directory']).glob('*/valid.csv')))
        run={'id':self.run_id,'name':spec['name'],'spec':spec,'report':result,'status':'failed'}
        self.assertTrue(download_path(run,'customers','customers.csv',self.data).is_file())
        with self.assertRaises(ValueError):download_path(run,'orders','orders.csv',self.data)
        with self.assertRaises(ValueError):download_path(run,'customers','../orders/valid.csv',self.data)
        self.assertNotIn('sensitive',json.dumps(result))
        for connection,cursor in sessions:connection.close.assert_called_once();cursor.close.assert_called_once()

    def test_continue_attempts_later_steps_in_order(self):
        with fake_sql() as (sessions,connect):
            self.fail_second(connect,sessions);result=self.run_steps(ordered('continue'))
        self.assertEqual(result['status'],'completed_with_errors')
        self.assertEqual([e['status'] for e in result['steps']],['completed','failed','completed'])
        self.assertEqual(len(sessions),3)

    def test_rejections_are_completed_not_step_failures(self):
        spec=ordered();spec['steps'][0]['columns'][0]['type']='int'
        with fake_sql():result=self.run_steps(spec)
        self.assertEqual(result['status'],'completed')
        self.assertEqual(result['steps'][0]['invalid'],25)
        self.assertEqual(result['steps'][0]['valid'],0)

    def test_revocation_stops_even_continue(self):
        from etl.engine import execute
        def revoke(*args,**kwargs):
            result=execute(*args,**kwargs);os.environ['ETL_QUERY_CONNECTIONS']='{}';return result
        with fake_sql() as (sessions,_),patch('etl.ordered_runs.execute',side_effect=revoke):
            result=self.run_steps(ordered('continue'))
        self.assertEqual(result['status'],'failed');self.assertEqual(len(sessions),1)
        self.assertEqual([s['status'] for s in result['steps']],['completed','failed','skipped'])

    def test_state_failure_stops_before_next_source(self):
        def persist(report):
            if report['steps'][1]['status']=='running':raise OSError('store unavailable')
        with fake_sql() as (sessions,_),self.assertRaises(StateWriteError):
            coordinate(ordered('continue'),self.root,self.output,self.run_id,persist=persist)
        self.assertEqual(len(sessions),1)

    def test_static_lookup_restrictions(self):
        spec=ordered();spec['steps'][0]['columns'][0]['lookup']={'source':{'kind':'sqlserver','connection_env':'ETL_SQL_OTHER','schema':'dbo','table':'BRANDS'},'column':'Code'}
        with self.assertRaisesRegex(ValueError,'shared connection'):from_dict(spec)
        spec['steps'][0]['columns'][0]['lookup']['source']['connection_env']=REFERENCE
        with fake_sql():preflight(from_dict(spec),self.root,self.output)
        csv_path=self.output/'old.csv';csv_path.parent.mkdir(parents=True);csv_path.write_text('Code\n003\n')
        spec['steps'][0]['columns'][0]['lookup']['source']={'kind':'csv','path':'data/runs/old.csv'}
        with fake_sql(),self.assertRaisesRegex(ValueError,'Run outputs'):preflight(from_dict(spec),self.root,self.output)

    def test_selected_preview_does_not_execute_other_steps(self):
        spec=ordered();spec['steps'][1]['columns']=[];spec['steps'][1]['query']['sql']=''
        from etl.engine import execute
        with fake_sql() as (sessions,_):
            selected=preview_spec(spec,'customers',self.root,self.output)
            result=execute(selected,self.root,limit=2)
        self.assertEqual(len(sessions),1);self.assertEqual(result['processed'],2)
        self.assertFalse(self.output.exists())
        with self.assertRaises(ValueError):preview_spec(spec,None,self.root,self.output)

    def test_history_uses_snapshot_and_no_sources(self):
        spec=ordered();spec['steps'][0]['columns'][0]['type']='int'
        with fake_sql():report=self.run_steps(spec)
        run={'id':self.run_id,'name':spec['name'],'spec':copy.deepcopy(spec),'report':report,'status':'completed'}
        spec['steps'][0]['columns'][0]['name']='edited'
        with patch('pyodbc.connect',side_effect=AssertionError('history reread')):
            projected=step_history(run,'customers',self.data)
            page=rejected_page(projected,self.data,'secret',limit=2)
        self.assertEqual(len(page['rows']),2);self.assertEqual(page['rows'][0]['fields'][0]['name'],'Code')
        with self.assertRaises(ValueError):
            rejected_page(step_history(run,'orders',self.data),self.data,'secret',cursor=page['next_cursor'],limit=2)

    def test_restart_marks_active_and_pending_steps_without_resume(self):
        spec=ordered();store=Store(self.data/'etl.sqlite3');run_id=store.create_run(spec)
        report=initial_report(spec,run_id);report['steps'][0]['status']='completed';report['steps'][1]['status']='running'
        store.update_run(run_id,'running',report)
        with patch('pyodbc.connect',side_effect=AssertionError('restart queried source')):
            app=Application(self.root,self.data)
            try:run=app.store.run(run_id)
            finally:app.shutdown()
        self.assertEqual(run['status'],'interrupted');self.assertTrue(run['finished'])
        self.assertEqual([s['status'] for s in run['report']['steps']],['completed','interrupted','skipped'])
        self.assertEqual(run['report']['steps'][1]['counts_basis'],'last_checkpoint')

    def test_real_interruption_retains_completed_files_on_restart(self):
        spec=ordered();store=Store(self.data/'etl.sqlite3');run_id=store.create_run(spec)
        with fake_sql() as (sessions,connect):
            base=connect.side_effect
            def fresh(*args,**kwargs):
                connection=base(*args,**kwargs)
                if len(sessions)==2:sessions[-1][1].fetchmany.side_effect=KeyboardInterrupt()
                return connection
            connect.side_effect=fresh
            with self.assertRaises(KeyboardInterrupt):
                coordinate(spec,self.root,self.output,run_id,persist=lambda r:store.update_run(run_id,r['status'],r))
            for connection,cursor in sessions:connection.close.assert_called_once();cursor.close.assert_called_once()
            before=len(sessions);app=Application(self.root,self.data)
            try:run=app.store.run(run_id)
            finally:app.shutdown()
            self.assertEqual(len(sessions),before)
        self.assertEqual([s['status'] for s in run['report']['steps']],['completed','interrupted','skipped'])
        self.assertTrue(download_path(run,'customers','customers.csv',self.data).is_file())
        with self.assertRaises(ValueError):download_path(run,'orders','orders.csv',self.data)

    def test_large_steps_keep_streaming_and_bounded_reports(self):
        from decimal import Decimal
        with fake_sql(rows=[('003','Name',Decimal('1.2300'))]*10001) as (sessions,_):
            result=self.run_steps(ordered(count=2))
        self.assertEqual(result['status'],'completed')
        self.assertEqual([s['processed'] for s in result['steps']],[10001,10001])
        for connection,cursor in sessions:
            cursor.fetchall.assert_not_called()
            self.assertTrue(all(call.args==(1000,) for call in cursor.fetchmany.call_args_list))
        self.assertLess(len(json.dumps(result)),10000)
        self.assertNotIn('sample',json.dumps(result))

    def test_no_overwrite_or_resume(self):
        with fake_sql() as (sessions,_):
            first=self.run_steps(ordered());second=self.run_steps(ordered())
        self.assertEqual(first['status'],'completed');self.assertEqual(second['status'],'failed')
        self.assertEqual(len(sessions),3)

    def test_cli_selected_preview_and_ordered_run(self):
        import contextlib
        import io
        from etl.__main__ import main
        path=self.root/'ordered.json';path.write_text(json.dumps(ordered(count=1)),encoding='utf-8')
        base=['etl','--root',str(self.root),'--data',str(self.data)]
        with fake_sql(),contextlib.redirect_stdout(io.StringIO()) as output:
            with patch('sys.argv',base+['preview',str(path),'--step','customers']):
                self.assertEqual(main(),0)
            self.assertEqual(json.loads(output.getvalue())['processed'],25)
        self.assertFalse((self.data/'etl.sqlite3').exists())
        with fake_sql(),contextlib.redirect_stdout(io.StringIO()) as output:
            with patch('sys.argv',base+['run',str(path)]):self.assertEqual(main(),0)
            self.assertEqual(json.loads(output.getvalue())['status'],'completed')
        self.assertEqual(Store(self.data/'etl.sqlite3').runs()[0]['status'],'completed')

    def test_mixed_destinations_and_independent_parameters(self):
        spec=ordered(count=2);spec['steps'][1]['destination']={'kind':'xlsx'}
        spec['steps'][1]['query']['parameters'][0]['value']='independent'
        with fake_sql() as (sessions,_):report=self.run_steps(spec)
        self.assertEqual(report['status'],'completed')
        self.assertEqual(report['steps'][1]['output'],'orders.xlsx')
        self.assertEqual(sessions[0][1].execute.call_args.args[1],('no match',))
        self.assertEqual(sessions[1][1].execute.call_args.args[1],('independent',))


if __name__=='__main__':unittest.main()
