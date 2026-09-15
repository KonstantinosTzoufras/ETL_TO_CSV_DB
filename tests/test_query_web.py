"""HTTP acceptance using the real query path with a fake SQL driver."""
import json
import os
import unittest
from unittest.mock import patch

from tests import test_web as web_support
from tests.test_queries import fake_sql, pipeline, source, query, REFERENCE


class QueryWebTests(unittest.TestCase):
    setUp = web_support.WebTests.setUp
    tearDown = web_support.WebTests.tearDown
    request = web_support.WebTests.request
    wait_run = web_support.WebTests.wait_run

    def test_approval_local_validation_and_sanitized_errors(self):
        with patch.dict(os.environ, {'ETL_QUERY_CONNECTIONS':'{}'}), patch('pyodbc.connect') as connect:
            status,body=self.request('POST','/api/query/connections',{})
            self.assertEqual((status,json.loads(body)),(200,{'connections':[]}))
            self.assertEqual(self.request('POST','/api/query/validate',{'query':query()})[0],200)
            status,body=self.request('POST','/api/columns',{'source':source()})
            self.assertEqual(status,400);self.assertEqual(json.loads(body)['code'],'QUERY_PERMISSION_DENIED')
            status,body=self.request('POST','/api/query/validate',{'query':query("EXEC SECRET_VALUE",[])})
            self.assertEqual(status,400);self.assertNotIn(b'SECRET_VALUE',body)
            connect.assert_not_called()
        self.assertEqual(self.request('POST','/api/query/connections',{}, {'X-ETL-Token':'wrong'})[0],403)
        self.assertEqual(self.request('GET','/query.js')[0],200)

    def test_query_discovery_preview_run_and_snapshot(self):
        context={'connector':'sqlserver_query','connection_env':REFERENCE}
        with fake_sql() as (sessions,_):
            status,body=self.request('POST','/api/query/connections',{})
            self.assertEqual(json.loads(body),{'connections':[REFERENCE]})
            self.assertNotIn(b'NEVER_PRINT',body)
            status,body=self.request('POST','/api/discovery/configure',{**context,'dataset_key':'sqlserver_query:'+REFERENCE,'options':{'query':query()}})
            self.assertEqual(status,200);configured=json.loads(body)
            self.assertEqual(configured,source());self.assertEqual(sessions,[])
            status,body=self.request('POST','/api/discovery/sample',{**context,'source':configured,'limit':2})
            self.assertEqual(status,200);self.assertEqual(json.loads(body)['rows'][0]['values']['Code'],'003')
            spec=pipeline()
            status,body=self.request('POST','/api/preview',{'spec':spec,'diagnostics':True})
            self.assertEqual(status,200);self.assertEqual(json.loads(body)['processed'],25)
            self.assertTrue(json.loads(body)['diagnostics'])
            status,body=self.request('POST','/api/runs',{'spec':spec})
            self.assertEqual(status,202);run_id=json.loads(body)['id']
            run=self.wait_run(run_id);self.assertEqual(run['status'],'completed')
            self.assertEqual(run['spec']['source'],configured)
            count=len(sessions)
            self.assertEqual(self.request('GET','/api/runs')[0],200)
            self.assertEqual(self.request('POST','/api/diagnostics/rejections',{'run_id':run_id})[0],200)
            self.assertEqual(len(sessions),count)

    def test_template_binding_stays_explicit_for_query(self):
        from etl.templates import apply_template, bind_template
        spec=pipeline();spec['columns']=[]
        template=self.app.templates.create({'format_version':1,'name':'Query target','processing_version':2,'fields':[{'output_name':'CustomerCode','target_type':'string'}]})
        applied=apply_template(template,spec)
        self.assertEqual(applied['bindings'],[None]);self.assertEqual(applied['pipeline']['columns'],[])
        generated=bind_template(applied['template'],spec,[{'source':'Code'}])
        self.assertEqual(generated['source'],source())
        with fake_sql():
            status,body=self.request('POST','/api/preview',{'spec':generated})
            self.assertEqual(status,200);self.assertEqual(json.loads(body)['valid'],25)
