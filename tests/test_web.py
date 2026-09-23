import json
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch
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

    def test_bootstrap_lists_references_without_credentials(self):
        with patch.dict("os.environ", {"ETL_SQL_TEST": "secret-password", "ETL_SQL_EMPTY": "", "UNRELATED_SECRET": "other-secret"}, clear=True):
            status, body = self.request("GET", "/api/bootstrap")
        self.assertEqual(status, 200)
        result = json.loads(body)
        self.assertEqual(result["source_connections"], ["ETL_SQL_TEST"])
        self.assertEqual(result["output_directory"], str(self.app.data / "runs"))
        self.assertNotIn(b"secret-password", body)
        self.assertNotIn(b"other-secret", body)

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

    def test_delete_saved_pipeline_keeps_run_snapshot_and_exports(self):
        saved = self.app.store.save(self.spec)
        other = self.app.store.save(self.spec)
        run_id = self.app.submit(self.spec)
        run = self.wait_run(run_id)
        self.assertEqual(run['status'], 'completed')
        directory = Path(run['report']['directory'])
        files = {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()}
        self.assertEqual(self.request('POST', '/api/pipelines/delete', {'id': saved}, {'X-ETL-Token': 'wrong'})[0], 403)
        self.assertEqual(len(self.app.store.pipelines()), 2)
        self.assertEqual(self.request('POST', '/api/pipelines/delete', {'id': saved})[0], 200)
        self.assertEqual([p['id'] for p in self.app.store.pipelines()], [other])
        self.assertEqual(self.app.store.run(run_id), run)
        self.assertEqual({p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()}, files)
        self.assertEqual(self.request('POST', '/api/pipelines/delete', {'id': saved})[0], 404)
        for value in (None, '', [], 12):
            self.assertEqual(self.request('POST', '/api/pipelines/delete', {'id': value})[0], 400)

    def test_v2_preview_and_background_history_encode_native_results(self):
        self.spec["version"] = 2
        self.spec["columns"] = [
            {"name": "amount", "literal": "12345678901234567890.12300", "type": "decimal"},
            {"name": "null", "literal": None}, {"name": "empty", "literal": ""},
            {"name": "bad", "literal": "003", "type": "int"},
        ]
        status, body = self.request("POST", "/api/preview", {"spec": self.spec})
        self.assertEqual(status, 200)
        preview = json.loads(body)["sample"]
        converted = preview[0]["converted_values"]
        self.assertEqual(converted["amount"], {"$type": "decimal", "value": "12345678901234567890.12300"})
        self.assertIsNone(converted["null"])
        self.assertEqual(converted["empty"], "")
        self.assertNotIn("bad", converted)
        self.assertEqual(preview[0]["errors"]["bad"][0]["code"], "invalid_type")
        status, body = self.request("POST", "/api/runs", {"spec": self.spec})
        self.assertEqual(status, 202)
        run = self.wait_run(json.loads(body)["id"])
        self.assertEqual(run["status"], "completed", run["error"])
        self.assertEqual(run["spec"]["version"], 2)
        self.assertEqual(run["report"]["sample"], preview[:20])

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

    def test_split_export_downloads_each_group_and_rejects_traversal(self):
        self.spec["destination"]["split_by"]="country"
        status,body=self.request("POST","/api/runs",{"spec":self.spec})
        self.assertEqual(status,202)
        run_id=json.loads(body)["id"]
        run=self.wait_run(run_id)
        self.assertEqual(run["status"],"completed",run.get("error"))
        files=run["report"]["files"]
        self.assertIn("rejected.csv",files)
        self.assertTrue(any("/" in f for f in files),"expected at least one GROUP/valid.csv entry")
        for name in files:
            status,body=self.request("GET",f"/download/{run_id}/{name}")
            self.assertEqual(status,200,name)
        # A plain-run filename must not be accepted once the run is split.
        self.assertEqual(self.request("GET",f"/download/{run_id}/valid.csv")[0],404)
        # The server-computed allowlist, not string matching alone, blocks escape.
        group=next(f.split("/")[0] for f in files if "/" in f)
        self.assertEqual(self.request("GET",f"/download/{run_id}/{group}/../../etl.sqlite3")[0],404)
        self.assertEqual(self.request("GET",f"/download/{run_id}/{group}/valid.xlsx")[0],404)

    def test_a_run_saved_before_split_export_still_downloads_by_its_old_names(self):
        status,body=self.request("POST","/api/runs",{"spec":self.spec})
        run_id=json.loads(body)["id"]
        run=self.wait_run(run_id)
        self.assertEqual(run["status"],"completed")
        with self.app.store.connect() as db:
            report=json.loads(db.execute("SELECT report FROM runs WHERE id=?",(run_id,)).fetchone()["report"])
            del report["files"]
            db.execute("UPDATE runs SET report=? WHERE id=?",(json.dumps(report),run_id))
        self.assertEqual(self.request("GET",f"/download/{run_id}/valid.csv")[0],200)
        self.assertEqual(self.request("GET",f"/download/{run_id}/rejected.csv")[0],200)

    def test_database_export_requires_a_saved_pipeline_id(self):
        self.spec["destination"]={"kind":"sqlserver","connection_env":"ETL_SQL_MAIN"}
        status,body=self.request("POST","/api/runs",{"spec":self.spec})
        self.assertEqual(status,400,body)
        self.assertIn("Save this pipeline",json.loads(body)["error"])
        # A pipeline_id that was never actually saved is rejected the same way.
        status,body=self.request("POST","/api/runs",{"spec":self.spec,"pipeline_id":"nope"})
        self.assertEqual(status,400,body)

    def test_database_export_runs_end_to_end_and_claims_a_table(self):
        cursor=MagicMock();connection=MagicMock();connection.cursor.return_value=cursor
        cursor.fetchone.return_value=(None,)  # SELECT OBJECT_ID(...): no real table by that name yet
        module=MagicMock();module.Error=type("FakeError",(Exception,),{})
        module.connect.return_value=connection
        _,body=self.request("POST","/api/pipelines",{"spec":self.spec})
        pipeline_id=json.loads(body)["id"]
        self.spec["destination"]={"kind":"sqlserver","connection_env":"ETL_SQL_MAIN"}
        policy=json.dumps({"ETL_SQL_MAIN":{"schema":"dbo","max_timeout_seconds":60}})
        with patch.dict("sys.modules",{"pyodbc":module}), patch.dict(os.environ,{"ETL_EXPORT_CONNECTIONS":policy,"ETL_SQL_MAIN":"NEVER_PRINT_CREDENTIALS"}):
            status,body=self.request("POST","/api/runs",{"spec":self.spec,"pipeline_id":pipeline_id})
            self.assertEqual(status,202,body)
            run=self.wait_run(json.loads(body)["id"])
        self.assertEqual(run["status"],"completed",run.get("error"))
        self.assertEqual(run["report"]["table"]["name"],"z0_customers_clean_export")
        self.assertEqual(run["report"]["files"],["rejected.csv","rejected.xlsx"])
        # A download for the (nonexistent) valid file is refused; rejected.csv works.
        self.assertEqual(self.request("GET",f"/download/{run['id']}/valid.csv")[0],404)
        self.assertEqual(self.request("GET",f"/download/{run['id']}/rejected.csv")[0],200)

    def test_export_connections_endpoint_lists_only_what_is_approved(self):
        self.assertEqual(json.loads(self.request("POST","/api/export/connections",{})[1]),{"connections":[]})
        policy=json.dumps({"ETL_SQL_MAIN":{"schema":"dbo","max_timeout_seconds":60}})
        with patch.dict(os.environ,{"ETL_EXPORT_CONNECTIONS":policy}):
            self.assertEqual(json.loads(self.request("POST","/api/export/connections",{})[1]),{"connections":["ETL_SQL_MAIN"]})

    def test_failed_job_has_reason_and_no_download(self):
        self.spec["source"]["path"]="missing.csv"
        _,body=self.request("POST","/api/runs",{"spec":self.spec})
        run_id=json.loads(body)["id"]
        run=self.wait_run(run_id)
        self.assertEqual(run["status"],"failed")
        self.assertIn("not found",run["error"])
        self.assertEqual(self.request("GET",f"/download/{run_id}/valid.csv")[0],404)

    def test_discovery_flow_is_source_only_and_preserves_processed_preview(self):
        def discovery(operation, **values):
            status, body = self.request("POST", "/api/discovery/" + operation, {"connector": "csv", **values})
            self.assertEqual(status, 200, body)
            return json.loads(body)
        with patch("etl.web.execute", side_effect=AssertionError("Discovery must not process")):
            self.assertIn("sample", discovery("capabilities")["operations"])
            dataset = discovery("datasets")["items"][0]
            source = discovery("configure", dataset_key=dataset["key"], options={"delimiter": ";", "encoding": "utf-8-sig"})
            columns = discovery("columns", source=source)
            sample = discovery("sample", source=source, limit=2)
        self.assertEqual(len(sample["rows"]), 2)
        self.assertEqual(sample["stop_reason"], "row_limit")
        self.assertEqual([r["number"] for r in sample["rows"]], [1, 2])
        self.assertEqual(list(sample["rows"][0]["values"]), [c["column"]["name"] for c in columns])
        self.assertNotIn("errors", sample["rows"][0])
        self.assertNotIn("columns", source)
        self.assertEqual(self.app.store.runs(), [])
        self.assertEqual(self.app.store.pipelines(), [])
        self.spec["source"] = source
        status, body = self.request("POST", "/api/preview", {"spec": self.spec})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["invalid"], 2)

    def test_discovery_security_and_errors(self):
        request = {"connector": "csv"}
        for headers in ({"Origin": "https://attacker.test"}, {"X-ETL-Token": "wrong"}):
            self.assertEqual(self.request("POST", "/api/discovery/datasets", request, headers)[0], 403)
        for operation, body, code in (
            ("resolve", {"locator": "../secret.csv"}, "access_denied"),
            ("datasets", {"limit": 101}, "invalid_read_options"),
            ("datasets", {"query": "SELECT 1"}, "invalid_read_options"),
            ("counts", {}, "unsupported_operation"),
            ("sample", {"source": {"kind": "csv", "path": "missing.csv"}}, "dataset_unavailable"),
        ):
            status, data = self.request("POST", "/api/discovery/" + operation, {**request, **body})
            self.assertEqual(status, 400)
            self.assertEqual(json.loads(data)["code"], code)

    def test_preview_diagnostics_reuse_one_processing_pass_for_both_versions(self):
        from etl.engine import process_row
        for version in (1, 2):
            self.spec["version"] = version
            with patch("etl.engine.process_row", wraps=process_row) as process:
                status, body = self.request("POST", "/api/preview", {"spec": self.spec, "diagnostics": True})
            self.assertEqual(status, 200, body)
            report = json.loads(body)
            self.assertEqual(process.call_count, report["processed"])
            self.assertEqual(len(report["diagnostics"]), report["processed"])
            self.assertEqual(sum(not r["valid"] for r in report["diagnostics"]), report["invalid"])
            self.assertTrue(all(not r["legacy"] for r in report["diagnostics"]))
            self.assertEqual(report["diagnostics"][0]["fields"][0]["name"], self.spec["columns"][0]["name"])
        self.assertEqual(self.app.store.runs(), [])

    def test_stored_rejections_use_snapshot_without_rerun_or_current_source(self):
        self.spec["version"] = 2
        self.spec["columns"] = [{"name": "bad", "literal": "003", "type": "int", "max_length": 2},
                                {"name": "large", "literal": "9223372036854775807", "type": "int"}]
        _, body = self.request("POST", "/api/runs", {"spec": self.spec})
        run = self.wait_run(json.loads(body)["id"])
        self.assertEqual(run["status"], "completed")
        (self.root / "input.csv").unlink()
        self.spec["columns"][0]["name"] = "edited_name"
        self.app.store.save(self.spec)
        with patch("etl.web.execute", side_effect=AssertionError("must not rerun")), patch("etl.engine.create_source", side_effect=AssertionError("source must stay closed")):
            status, body = self.request("POST", "/api/diagnostics/rejections", {"run_id": run["id"], "limit": 2})
            self.assertEqual(status, 200, body)
            first = json.loads(body)
            status, body = self.request("POST", "/api/diagnostics/rejections", {"run_id": run["id"], "cursor": first["next_cursor"]})
        self.assertEqual(status, 200)
        self.assertEqual(len(first["rows"]), 2)
        self.assertEqual(len(json.loads(body)["rows"]), 3)
        fields = first["rows"][0]["fields"]
        self.assertEqual(fields[0]["name"], "bad")
        self.assertEqual(len(fields[0]["errors"]), 2)
        self.assertFalse(fields[0]["converted"]["available"])
        self.assertEqual(fields[1]["converted"]["text"], "9223372036854775807")
        self.assertEqual(len(self.app.store.runs()), 1)

    def test_diagnostics_endpoint_protection_and_missing_run(self):
        body = {"run_id": "missing"}
        self.assertEqual(self.request("POST", "/api/diagnostics/rejections", body, {"X-ETL-Token": "wrong"})[0], 403)
        self.assertEqual(self.request("POST", "/api/diagnostics/rejections", body)[0], 400)
        self.assertEqual(self.request("GET", "/diagnostics.js")[0], 200)

    def test_template_api_copy_bind_save_run_and_revision_independence(self):
        from tests.test_templates import blueprint, draft
        def call(operation, body):
            status, payload = self.request("POST", "/api/templates/" + operation, body)
            self.assertEqual(status, 200, payload)
            return json.loads(payload)
        template = call("create", {"definition": blueprint()})
        self.assertEqual(len(call("list", {})), 1)
        self.assertEqual(call("read", {"id": template["id"], "revision": 1}), template)
        pending = call("apply", {"id": template["id"], "revision": 1, "spec": draft()})
        self.assertEqual(pending["bindings"], [None, None, None])
        generated = call("bind", {"template": pending["template"], "spec": pending["pipeline"], "bindings": [
            {"literal": "003"}, {"literal": " Name "}, {"literal": ""}]})
        _, saved = self.request("POST", "/api/pipelines", {"spec": generated})
        _, payload = self.request("POST", "/api/runs", {"spec": generated})
        run = self.wait_run(json.loads(payload)["id"])
        self.assertEqual(run["status"], "completed")
        edited = blueprint(); edited["fields"][0]["target_type"] = "int"
        second = call("revision", {"id": template["id"], "base_revision": 1, "definition": edited})
        self.assertEqual(second["revision"], 2)
        for revision in (1, 2):
            (self.app.data / "templates" / template["id"] / f"{revision}.json").unlink()
        self.assertEqual(call("bind", {"template": pending["template"], "spec": pending["pipeline"], "bindings": [
            {"literal": "003"}, {"literal": " Name "}, {"literal": ""}]}), generated)
        self.assertEqual(self.app.store.pipelines()[0]["spec"], generated)
        self.assertEqual(self.app.store.run(run["id"])["spec"], generated)
        self.assertEqual(run["report"]["valid"], 5)

    def test_template_api_blocks_unresolved_versions_existing_mappings_and_forbidden_keys(self):
        from tests.test_templates import blueprint, draft
        _, payload = self.request("POST", "/api/templates/create", {"definition": blueprint()})
        template = json.loads(payload)
        for spec in (draft(version=1), self.spec):
            self.assertEqual(self.request("POST", "/api/templates/apply", {"id": template["id"], "revision": 1, "spec": spec})[0], 400)
        self.assertEqual(self.request("POST", "/api/templates/bind", {"template": template, "spec": draft(), "bindings": [{"literal": "003"}, {"literal": "name"}, None]})[0], 400)
        self.assertEqual(self.request("POST", "/api/templates/create", {"definition": {**blueprint(), "source": self.spec["source"]}})[0], 400)
        self.assertEqual(self.request("POST", "/api/templates/list", {}, {"X-ETL-Token": "wrong"})[0], 403)
        self.assertEqual(self.request("GET", "/templates.js")[0], 200)


if __name__ == "__main__":
    unittest.main()
