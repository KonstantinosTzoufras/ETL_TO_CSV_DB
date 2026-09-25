"""Ordered pipeline steps dispatched to a worker: real local worker_agent
(127.0.0.1, no VPN needed), sharing this test's own root/data so a step's
remote output lands exactly where the coordinator expects it - the same
"shared output root" requirement documented for top-level remote runs.
"""
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from etl.ordered_runs import coordinate
from etl.worker_agent import Worker, handler_for
from tests.test_ordered import ordered
from tests.test_queries import fake_sql


class OrderedRemoteStepTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name);self.data=self.root/'data';self.output=self.data/'runs'
        self.run_id='a'*32;self.events=[]
        self.token='ordered-remote-token'
        # Same root/data as the coordinator uses, so the worker's own
        # execute() output is directly reachable/renamable by the test's
        # coordinate() call - exactly the "shared output root" precondition.
        self.worker_impl=Worker(self.root,self.data,self.token)
        self.worker_server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(self.worker_impl))
        self.worker_thread=threading.Thread(target=self.worker_server.serve_forever,daemon=True)
        self.worker_thread.start()
        self.worker_url=f'http://127.0.0.1:{self.worker_server.server_port}'
        self.workers_env=json.dumps({'TestWorker':{'url':self.worker_url,'token':self.token}})
        self.addCleanup(self._stop_worker)

    def _stop_worker(self):
        self.worker_server.shutdown();self.worker_server.server_close();self.worker_thread.join()

    def run_steps(self,spec):
        return coordinate(spec,self.root,self.output,self.run_id,persist=self.events.append)

    def test_step_pinned_to_worker_dispatches_and_completes(self):
        spec=ordered(count=1);spec['steps'][0]['execution_target']='TestWorker'
        with fake_sql() as (sessions,_),patch.dict('os.environ',{'ETL_WORKERS':self.workers_env}):
            result=self.run_steps(spec)
        self.assertEqual(result['status'],'completed')
        self.assertEqual(result['steps'][0]['processed'],25)
        self.assertEqual(len(sessions),1)  # The worker opened the only SQL connection, not the coordinator.

    def test_auto_picks_the_idle_worker(self):
        spec=ordered(count=1);spec['steps'][0]['execution_target']='auto'
        with fake_sql() as (sessions,_),patch.dict('os.environ',{'ETL_WORKERS':self.workers_env}):
            result=self.run_steps(spec)
        self.assertEqual(result['status'],'completed')
        self.assertEqual(len(sessions),1)

    def test_auto_falls_back_to_server_when_no_worker_is_configured(self):
        spec=ordered(count=1);spec['steps'][0]['execution_target']='auto'
        with fake_sql() as (sessions,_),patch.dict('os.environ',{'ETL_WORKERS':'{}'}):
            result=self.run_steps(spec)
        self.assertEqual(result['status'],'completed')
        self.assertEqual(len(sessions),1)  # Ran locally; no worker was ever contacted.

    def test_auto_falls_back_to_server_when_the_worker_is_busy(self):
        release=threading.Event()
        spec=ordered(count=1);spec['steps'][0]['execution_target']='auto'
        with fake_sql() as (sessions,_),patch.dict('os.environ',{'ETL_WORKERS':self.workers_env}):
            # Occupy the worker directly (bypassing the coordinator) so it
            # reports busy when the real step asks for an idle worker.
            self.worker_impl.current='occupying-job'
            try:
                result=self.run_steps(spec)
            finally:
                self.worker_impl.current=None
        self.assertEqual(result['status'],'completed')
        self.assertEqual(len(sessions),1)  # Ran locally, not on the busy worker.

    def test_pinned_unknown_worker_fails_only_that_step(self):
        spec=ordered(count=2,policy='continue');spec['steps'][0]['execution_target']='NoSuchWorker'
        with fake_sql() as (sessions,_),patch.dict('os.environ',{'ETL_WORKERS':self.workers_env}):
            result=self.run_steps(spec)
        self.assertEqual([s['status'] for s in result['steps']],['failed','completed'])
        self.assertIn('unknown execution target',result['steps'][0]['error']['message'].lower())
        self.assertEqual(len(sessions),1)  # Only the second (server) step ever opened a connection.


if __name__=='__main__':unittest.main()
