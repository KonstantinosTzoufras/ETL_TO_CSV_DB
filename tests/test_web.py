import json
import tempfile
import threading
import time
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

from etl.web import Application, handler_for

ROOT = Path(__file__).resolve().parents[1]


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.spec = json.loads((ROOT / "examples/customers.json").read_text(encoding="utf-8"))
        self.spec["source"]["path"]="input.csv"
        (self.root / "input.csv").write_bytes((ROOT / "examples/customers.csv").read_bytes())
        self.app=Application(self.root,self.root / "data")
        self.server=ThreadingHTTPServer(("127.0.0.1",0),handler_for(self.app))
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.app.executor.shutdown(wait=True)
        self.thread.join()
        self.temp.cleanup()

    def request(self,method,path,body=None,headers=None):
        connection=HTTPConnection("127.0.0.1",self.server.server_port,timeout=15)
        request_headers={"Content-Type":"application/json","X-ETL-Token":self.app.token}
        request_headers.update(headers or {})
        connection.request(method,path,json.dumps(body) if body is not None else None,request_headers)
        response=connection.getresponse()
        data=response.read()
        status=response.status
        connection.close()
        return status,data

    def test_origin_host_and_token_protection(self):
        for headers in ({"Origin":"https://example.test"},{"Host":"attacker.test"},{"X-ETL-Token":"wrong"}):
            with self.subTest(headers=headers):
                self.assertEqual(self.request("POST","/api/preview",{"spec":self.spec},headers)[0],403)
        self.assertEqual(self.request("GET","/api/bootstrap",headers={"Sec-Fetch-Site":"cross-site"})[0],403)

    def test_preview_save_load_and_validation_errors(self):
        status,body=self.request("POST","/api/preview",{"spec":self.spec})
        self.assertEqual(status,200)
        self.assertEqual(json.loads(body)["invalid"],2)
        self.assertEqual(self.app.store.runs(),[])
        status,body=self.request("POST","/api/pipelines",{"spec":self.spec})
        self.assertEqual(status,200)
        saved=json.loads(body)["id"]
        status,body=self.request("GET","/api/pipelines")
        self.assertEqual(json.loads(body)[0]["id"],saved)
        self.assertEqual(self.request("POST","/api/preview",{"spec":{}})[0],400)

    def wait_run(self,run_id):
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            run=self.app.store.run(run_id)
            if run["status"] not in {"running","queued"}:
                return run
            time.sleep(.02)
        self.fail("Background run did not finish")

    def test_background_run_download_and_path_restrictions(self):
        status,body=self.request("POST","/api/runs",{"spec":self.spec})
        self.assertEqual(status,202)
        run_id=json.loads(body)["id"]
        run=self.wait_run(run_id)
        self.assertEqual(run["status"],"completed")
        status,body=self.request("GET",f"/download/{run_id}/valid.csv")
        self.assertEqual(status,200)
        self.assertIn(b"Maria Papadopoulou",body)
        self.assertNotIn(b"Eleni Demo",body)
        self.assertEqual(self.request("GET",f"/download/{run_id}/rejected.csv")[0],200)
        self.assertEqual(self.request("GET",f"/download/{run_id}/etl.sqlite3")[0],404)

    def test_failed_job_has_reason_and_no_download(self):
        self.spec["source"]["path"]="missing.csv"
        _,body=self.request("POST","/api/runs",{"spec":self.spec})
        run_id=json.loads(body)["id"]
        run=self.wait_run(run_id)
        self.assertEqual(run["status"],"failed")
        self.assertIn("not found",run["error"])
        self.assertEqual(self.request("GET",f"/download/{run_id}/valid.csv")[0],404)


if __name__ == "__main__":
    unittest.main()
