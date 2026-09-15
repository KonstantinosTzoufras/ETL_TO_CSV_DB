"""Ordered API history/download/preview contracts with mock SQL only."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from tests import test_web as support
from tests.test_queries import fake_sql
from tests.test_ordered import ordered
from tests import test_ordered as ordered_support


class OrderedWebTests(unittest.TestCase):
    setUp=support.WebTests.setUp
    tearDown=support.WebTests.tearDown
    request=support.WebTests.request
    wait_run=support.WebTests.wait_run

    def test_save_run_completed_step_downloads_after_later_failure(self):
        spec=ordered()
        with fake_sql() as (sessions,connect):
            ordered_support.OrderedTests.fail_second(self,connect,sessions)
            status,body=self.request('POST','/api/pipelines',{'spec':spec});self.assertEqual(status,200)
            saved=json.loads(body)['id']
            status,body=self.request('POST','/api/runs',{'spec':spec});self.assertEqual(status,202)
            run_id=json.loads(body)['id'];run=self.wait_run(run_id)
        self.assertEqual(run['status'],'failed');self.assertTrue(run['finished'])
        self.assertEqual([s['status'] for s in run['report']['steps']],['completed','failed','skipped'])
        self.assertEqual(self.request('GET',f'/download/{run_id}/customers/customers.csv')[0],200)
        self.assertEqual(self.request('GET',f'/download/{run_id}/orders/orders.csv')[0],404)
        self.assertEqual(self.request('GET',f'/download/{run_id}/balances/balances.csv')[0],404)
        self.assertEqual(self.request('GET',f'/download/{run_id}/valid.csv')[0],404)
        self.assertEqual(self.request('GET',f'/download/{run_id}/customers/unknown.csv')[0],404)
        spec['steps'][0]['name']='Later edited name'
        self.request('POST','/api/pipelines',{'id':saved,'spec':spec})
        self.assertEqual(self.app.store.run(run_id)['spec']['steps'][0]['name'],'Customers')
        with patch('pyodbc.connect',side_effect=AssertionError('history reread')):
            status,body=self.request('POST','/api/diagnostics/rejections',{'run_id':run_id,'step_id':'orders'})
            self.assertEqual(status,200);self.assertTrue(json.loads(body)['partial'])
            self.assertEqual(self.request('GET','/api/runs')[0],200)

    def test_selected_step_preview_and_no_combined_preview(self):
        spec=ordered();spec['steps'][1]['query']['sql']='';spec['steps'][1]['columns']=[]
        with fake_sql() as (sessions,_):
            status,body=self.request('POST','/api/ordered/preview',{'spec':spec,'step_id':'customers','diagnostics':True})
            self.assertEqual(status,200);self.assertEqual(json.loads(body)['processed'],25)
            self.assertTrue(json.loads(body)['diagnostics']);self.assertEqual(len(sessions),1)
        self.assertEqual(self.request('POST','/api/preview',{'spec':ordered()})[0],400)
        self.assertEqual(self.request('POST','/api/ordered/preview',{'spec':ordered()})[0],400)
        self.assertEqual(self.app.store.runs(),[])

    def test_continue_status_and_step_diagnostics_pagination(self):
        spec=ordered('continue');spec['steps'][0]['columns'][0]['type']='int'
        with fake_sql() as (sessions,connect):
            ordered_support.OrderedTests.fail_second(self,connect,sessions)
            status,body=self.request('POST','/api/runs',{'spec':spec});self.assertEqual(status,202)
            run_id=json.loads(body)['id'];run=self.wait_run(run_id)
        self.assertEqual(run['status'],'completed_with_errors');self.assertTrue(run['finished'])
        with patch('pyodbc.connect',side_effect=AssertionError('history reread')):
            status,body=self.request('POST','/api/diagnostics/rejections',{'run_id':run_id,'step_id':'customers','limit':20})
            self.assertEqual(status,200);page=json.loads(body);self.assertEqual(len(page['rows']),20)
            status,body=self.request('POST','/api/diagnostics/rejections',{'run_id':run_id,'step_id':'customers','cursor':page['next_cursor'],'limit':20})
            self.assertEqual(status,200);self.assertEqual(len(json.loads(body)['rows']),5)
        self.assertEqual(self.request('GET',f'/download/{run_id}/balances/balances.csv')[0],200)
        self.assertEqual(self.request('GET','/ordered.js')[0],200)

    def test_submit_owns_snapshot_and_preflight_rejects_before_queue(self):
        with fake_sql(),patch.object(self.app.executor,'submit') as submit:
            spec=ordered();run_id=self.app.submit(spec)
            spec['steps'][0]['query']['parameters'][0]['value']='MUTATED'
            queued=submit.call_args.args[2]
            self.assertNotEqual(queued['steps'][0]['query']['parameters'][0]['value'],'MUTATED')
            self.assertEqual(self.app.store.run(run_id)['spec'],queued)
            self.app.slots.release()  # Worker was intentionally not started.
            invalid=ordered();invalid['steps'][2]['columns']=[]
            before=len(self.app.store.runs())
            self.assertEqual(self.request('POST','/api/runs',{'spec':invalid})[0],400)
            self.assertEqual(len(self.app.store.runs()),before)
