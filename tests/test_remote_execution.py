"""Application <-> worker_agent integration, using a real local worker agent
(no network beyond 127.0.0.1) so this proves the whole dispatch/poll wiring
without requiring the VPN or a second machine.
"""
import json
import socket
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from etl.spec import ConfigError
from etl.web import Application
from etl.worker_agent import Worker, handler_for

ROOT = Path(__file__).resolve().parents[1]


class RemoteExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.spec = json.loads((ROOT / "examples/customers.json").read_text(encoding="utf-8"))
        self.spec["source"]["path"] = "input.csv"
        (self.root / "input.csv").write_bytes((ROOT / "examples/customers.csv").read_bytes())
        self.token = "integration-worker-token"
        self.worker_impl = Worker(self.root, self.root / "data-worker", self.token)
        self.worker_server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(self.worker_impl))
        self.worker_thread = threading.Thread(target=self.worker_server.serve_forever, daemon=True)
        self.worker_thread.start()
        self.worker_url = f"http://127.0.0.1:{self.worker_server.server_port}"
        self.workers_env = json.dumps({"TestWorker": {"url": self.worker_url, "token": self.token}})
        self.app = Application(self.root, self.root / "data-server", remote_poll_interval=0.05, remote_run_timeout=600)

    def tearDown(self):
        self.app.shutdown()
        self.worker_server.shutdown()
        self.worker_server.server_close()
        self.worker_thread.join()
        self.temp.cleanup()

    def wait_for_terminal(self, run_id, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            run = self.app.store.run(run_id)
            if run["status"] not in ("queued", "running"):
                return run
            time.sleep(0.02)
        self.fail("Run never reached a terminal state")

    def test_remote_run_completes_and_matches_local_processing_counts(self):
        with patch.dict("os.environ", {"ETL_WORKERS": self.workers_env}):
            remote_run_id = self.app.submit(self.spec, target="TestWorker")
            remote_run = self.wait_for_terminal(remote_run_id)
        self.assertEqual(remote_run["status"], "completed")
        self.assertEqual(remote_run["execution_target"], "TestWorker")
        local_run_id = self.app.submit(self.spec)  # target="server", for comparison
        local_run = self.wait_for_terminal(local_run_id)
        self.assertEqual(local_run["execution_target"], "server")
        self.assertEqual(local_run["report"]["processed"], remote_run["report"]["processed"])
        self.assertEqual(local_run["report"]["valid"], remote_run["report"]["valid"])

    def test_unknown_execution_target_is_refused_immediately(self):
        with patch.dict("os.environ", {"ETL_WORKERS": self.workers_env}):
            with self.assertRaisesRegex(ConfigError, "Unknown execution target"):
                self.app.submit(self.spec, target="NoSuchWorker")

    def test_ordered_pipeline_cannot_target_a_worker(self):
        ordered = {"kind": "ordered_query_export", "format_version": 1, "name": "x",
                   "connection_env": "ETL_SQL_TEST", "failure_policy": "stop",
                   "steps": [{"id": "step-1", "name": "Step 1", "processing_version": 2,
                              "query": {"format_version": 1, "dialect": "tsql", "sql": "SELECT 1 AS a", "parameters": [], "timeout_seconds": 60},
                              "columns": [{"name": "a", "source": "a"}], "destination": {"kind": "csv"}}]}
        with patch.dict("os.environ", {"ETL_WORKERS": self.workers_env}):
            with self.assertRaisesRegex(ConfigError, "Ordered pipelines cannot run on a remote worker"):
                self.app.submit(ordered, target="TestWorker")

    def test_remote_failure_is_recorded_with_the_workers_reason(self):
        with patch.dict("os.environ", {"ETL_WORKERS": self.workers_env}), \
             patch("etl.worker_agent.execute", side_effect=ValueError("synthetic worker failure")):
            run_id = self.app.submit(self.spec, target="TestWorker")
            run = self.wait_for_terminal(run_id)
        self.assertEqual(run["status"], "failed")
        self.assertIn("synthetic worker failure", run["error"])

    def test_worker_that_never_responds_times_out(self):
        release = threading.Event()

        def blocking_execute(*args, **kwargs):
            release.wait(5)
            return {"processed": 1, "valid": 1, "invalid": 0, "sample": [], "preview": False, "directory": str(self.root)}

        self.app.remote_run_timeout = 1.5
        with patch.dict("os.environ", {"ETL_WORKERS": self.workers_env}), \
             patch("etl.worker_agent.execute", side_effect=blocking_execute):
            run_id = self.app.submit(self.spec, target="TestWorker")
            run = self.wait_for_terminal(run_id, timeout=5)
            release.set()
        self.assertEqual(run["status"], "failed")
        self.assertIn("timeout", run["error"])

    def test_slot_is_released_after_a_remote_run_completes(self):
        with patch.dict("os.environ", {"ETL_WORKERS": self.workers_env}):
            for i in range(9):  # More than the 8-slot cap would allow if a slot ever leaked.
                run_id = self.app.submit(self.spec, target="TestWorker")
                self.wait_for_terminal(run_id)

    def test_unreachable_worker_at_dispatch_time_fails_fast_and_releases_its_slot(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            dead_port = probe.getsockname()[1]
        env = json.dumps({"TestWorker": {"url": f"http://127.0.0.1:{dead_port}", "token": self.token}})
        with patch.dict("os.environ", {"ETL_WORKERS": env}):
            with self.assertRaises(ConfigError):
                self.app.submit(self.spec, target="TestWorker")
            # The slot must have been released by the failed dispatch, not leaked.
            for _ in range(9):
                with self.assertRaises(ConfigError):
                    self.app.submit(self.spec, target="TestWorker")


if __name__ == "__main__":
    unittest.main()
