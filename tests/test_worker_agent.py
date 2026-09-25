import json
import tempfile
import threading
import time
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from etl.worker_agent import Worker, handler_for

ROOT = Path(__file__).resolve().parents[1]


class WorkerAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.spec = json.loads((ROOT / "examples/customers.json").read_text(encoding="utf-8"))
        self.spec["source"]["path"] = "input.csv"
        (self.root / "input.csv").write_bytes((ROOT / "examples/customers.csv").read_bytes())
        self.token = "worker-secret-token"
        self.worker = Worker(self.root, self.root / "data", self.token)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(self.worker))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=15)
        request_headers = {"Content-Type": "application/json", "X-Worker-Token": self.token}
        request_headers.update(headers or {})
        request_headers = {k: v for k, v in request_headers.items() if v is not None}
        connection.request(method, path, json.dumps(body) if body is not None else None, request_headers)
        response = connection.getresponse()
        data = json.loads(response.read())
        status = response.status
        connection.close()
        return status, data

    def poll_until_finished(self, run_id, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status, job = self.request("GET", f"/status/{run_id}")
            self.assertEqual(status, 200)
            if job["status"] != "running":
                return job
            time.sleep(0.02)
        self.fail("Job never finished")

    def test_missing_or_wrong_token_is_refused(self):
        for headers in ({"X-Worker-Token": None}, {"X-Worker-Token": "wrong"}):
            with self.subTest(headers=headers):
                status, _ = self.request("GET", "/health", headers=headers)
                self.assertEqual(status, 403)

    def test_health_reports_idle_before_and_after_a_run(self):
        self.assertEqual(self.request("GET", "/health")[1], {"idle": True, "current_run_id": None})
        job = self.poll_until_finished(self.request("POST", "/run", {"run_id": "r1", "spec": self.spec})[1]["run_id"])
        self.assertEqual(job["status"], "completed")
        self.assertEqual(self.request("GET", "/health")[1], {"idle": True, "current_run_id": None})

    def test_real_export_runs_and_reports_processed_rows(self):
        status, job = self.request("POST", "/run", {"run_id": "r1", "spec": self.spec})
        self.assertEqual(status, 202)
        self.assertEqual(job["status"], "running")
        job = self.poll_until_finished("r1")
        self.assertEqual(job["status"], "completed")
        self.assertIsNone(job["error"])
        self.assertGreater(job["report"]["processed"], 0)

    def test_duplicate_run_id_does_not_restart_the_job(self):
        with patch("etl.worker_agent.execute") as mocked:
            mocked.return_value = {"processed": 1, "valid": 1, "invalid": 0, "sample": [], "preview": False, "directory": str(self.root)}
            first = self.request("POST", "/run", {"run_id": "same-id", "spec": self.spec})
            self.poll_until_finished("same-id")
            second = self.request("POST", "/run", {"run_id": "same-id", "spec": self.spec})
        self.assertEqual(mocked.call_count, 1, "A retried run_id must not execute the pipeline twice")
        self.assertEqual(first[1]["run_id"], second[1]["run_id"])
        self.assertEqual(second[0], 200)

    def test_a_second_different_run_id_is_refused_while_busy(self):
        release = threading.Event()

        def blocking_execute(*args, **kwargs):
            release.wait(5)
            return {"processed": 1, "valid": 1, "invalid": 0, "sample": [], "preview": False, "directory": str(self.root)}

        with patch("etl.worker_agent.execute", side_effect=blocking_execute):
            self.request("POST", "/run", {"run_id": "busy-1", "spec": self.spec})
            status, body = self.request("POST", "/run", {"run_id": "busy-2", "spec": self.spec})
            self.assertEqual(status, 409)
            self.assertIn("busy-1", body["error"])
            release.set()
            self.poll_until_finished("busy-1")

    def test_unknown_run_id_status_is_404(self):
        self.assertEqual(self.request("GET", "/status/never-submitted")[0], 404)

    def test_ordered_pipeline_is_rejected_with_a_clear_reason(self):
        ordered = {"kind": "ordered_query_export", "format_version": 1, "name": "x",
                   "connection_env": "ETL_SQL_TEST", "failure_policy": "stop", "steps": []}
        status, body = self.request("POST", "/run", {"run_id": "o1", "spec": ordered})
        self.assertEqual(status, 400)
        self.assertIn("Ordered pipelines are not supported", body["error"])

    def test_invalid_spec_is_rejected_without_starting_a_job(self):
        status, body = self.request("POST", "/run", {"run_id": "bad1", "spec": {"not": "a pipeline"}})
        self.assertEqual(status, 400)
        self.assertEqual(self.request("GET", "/health")[1]["idle"], True)


if __name__ == "__main__":
    unittest.main()
