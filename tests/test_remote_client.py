import json
import socket
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from etl import remote
from etl.spec import ConfigError
from etl.worker_agent import Worker, handler_for

ROOT = Path(__file__).resolve().parents[1]


class RemoteClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.spec = json.loads((ROOT / "examples/customers.json").read_text(encoding="utf-8"))
        self.spec["source"]["path"] = "input.csv"
        (self.root / "input.csv").write_bytes((ROOT / "examples/customers.csv").read_bytes())
        self.token = "client-test-token"
        self.worker = Worker(self.root, self.root / "data", self.token)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(self.worker))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def test_dispatch_then_poll_reaches_completion(self):
        job = remote.dispatch(self.url, self.token, "r1", self.spec)
        self.assertEqual(job["status"], "running")
        for _ in range(200):
            job = remote.poll(self.url, self.token, "r1")
            if job["status"] != "running":
                break
        self.assertEqual(job["status"], "completed")
        self.assertGreater(job["report"]["processed"], 0)

    def test_poll_unknown_run_id_returns_none(self):
        self.assertIsNone(remote.poll(self.url, self.token, "never-submitted"))

    def test_dispatch_with_wrong_token_raises(self):
        with self.assertRaises(ConfigError):
            remote.dispatch(self.url, "wrong-token", "r1", self.spec)

    def test_dispatch_invalid_spec_raises_with_worker_message(self):
        with self.assertRaisesRegex(ConfigError, "pipeline"):
            remote.dispatch(self.url, self.token, "r1", {"not": "a pipeline"})

    def test_health_reports_online_and_idle(self):
        self.assertEqual(remote.health(self.url, self.token), {"online": True, "idle": True, "current_run_id": None})

    def test_unreachable_worker_reports_offline_not_an_exception(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            dead_port = probe.getsockname()[1]
        result = remote.health(f"http://127.0.0.1:{dead_port}", self.token, timeout=1)
        self.assertFalse(result["online"])

    def test_unreachable_worker_dispatch_raises_config_error(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            dead_port = probe.getsockname()[1]
        with self.assertRaises(ConfigError):
            remote.dispatch(f"http://127.0.0.1:{dead_port}", self.token, "r1", self.spec, timeout=1)


if __name__ == "__main__":
    unittest.main()
