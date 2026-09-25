"""Standalone remote execution agent for one worker PC.

Phase 1 POC: a small, separate HTTP service - never bound into web.py's
loopback-only UI server, never sharing its token or its trust model. The
main server pushes a job here; this process runs it with the *exact same*
etl.engine.execute() used locally, then the caller polls for the result.

Deliberately minimal: one job at a time, no ordered-pipeline support, no
queueing beyond that. The goal is only to prove that a remote worker can
safely run the same ETL workload as the server - not to build a scheduler.
"""
import argparse
import json
import logging
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .config import load_workspace_env
from .engine import execute
from .ordered import is_ordered
from .serialization import json_default
from .spec import ConfigError, require, validate
from .store import now


class Worker:
    def __init__(self, root, data, token):
        self.root, self.data, self.token = Path(root).resolve(), Path(data).resolve(), token
        self.lock = threading.Lock()
        self.jobs = {}  # run_id -> job record; never pruned in Phase 1 (process is short-lived per POC run)
        self.current = None  # run_id presently executing, or None

    def health(self):
        with self.lock:
            return {"idle": self.current is None, "current_run_id": self.current}

    def status(self, run_id):
        with self.lock:
            job = self.jobs.get(run_id)
            return dict(job) if job else None

    def submit(self, run_id, spec):
        """Returns (http_status, job_record). Idempotent on run_id; refuses a
        second, different job while one is already running (one at a time)."""
        with self.lock:
            existing = self.jobs.get(run_id)
            if existing is not None:
                return 200, dict(existing)  # A retry of the same run_id, not a new job.
            if self.current is not None:
                return 409, {"error": f"Worker is already running {self.current}"}
            require(not is_ordered(spec), "Ordered pipelines are not supported by the worker agent yet")
            validate(spec)
            job = {"run_id": run_id, "status": "running", "started": now(), "finished": None, "report": None, "error": None}
            self.jobs[run_id] = job
            self.current = run_id
        threading.Thread(target=self._run, args=(run_id, spec), daemon=True).start()
        with self.lock:
            return 202, dict(self.jobs[run_id])

    def _run(self, run_id, spec):
        try:
            report = execute(spec, self.root, self.data / "runs")
            outcome = {"status": "completed", "report": report, "error": None}
        except (ConfigError, ValueError, TypeError, OSError) as error:
            outcome = {"status": "failed", "report": None, "error": str(error)}
        except Exception as error:
            logging.error("Worker job %s failed (%s)", run_id, type(error).__name__)
            outcome = {"status": "failed", "report": None, "error": "Unexpected execution failure. Check the input data and worker logs."}
        with self.lock:
            self.jobs[run_id].update(outcome, finished=now())
            self.current = None


def handler_for(worker):
    class Handler(BaseHTTPRequestHandler):
        def respond(self, status, value):
            body = json.dumps(value, ensure_ascii=False, default=json_default).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def authorized(self):
            if not secrets.compare_digest(self.headers.get("X-Worker-Token", ""), worker.token):
                self.respond(403, {"error": "Invalid or missing worker token"})
                return False
            return True

        def do_GET(self):
            if not self.authorized():
                return
            path = urlsplit(self.path).path
            if path == "/health":
                self.respond(200, worker.health())
            elif path.startswith("/status/"):
                run_id = path.removeprefix("/status/")
                job = worker.status(run_id)
                self.respond(200, job) if job else self.respond(404, {"error": "Unknown run_id"})
            else:
                self.respond(404, {"error": "Not found"})

        def do_POST(self):
            if not self.authorized():
                return
            try:
                require(self.headers.get("Content-Type", "").split(";")[0] == "application/json", "Send application/json")
                size = int(self.headers.get("Content-Length", "0"))
                require(0 < size <= 5_000_000, "Request must be 1 byte-5 MB")
                body = json.loads(self.rfile.read(size))
                require(isinstance(body, dict), "Request must be an object")
                path = urlsplit(self.path).path
                if path == "/run":
                    run_id = body.get("run_id")
                    require(isinstance(run_id, str) and run_id, "run_id is required")
                    status, job = worker.submit(run_id, body.get("spec"))
                    self.respond(status, job)
                else:
                    self.respond(404, {"error": "Not found"})
            except ConfigError as error:
                self.respond(400, {"error": str(error)})
            except (ValueError, TypeError) as error:
                self.respond(400, {"error": str(error)})
            except Exception as error:
                logging.error("Worker request failed (%s)", type(error).__name__)
                self.respond(500, {"error": "Unexpected worker error"})

        def log_message(self, format, *args):
            logging.info("%s - %s", self.address_string(), format % args)

    return Handler


def serve(root, data, token, host="127.0.0.1", port=8790):
    worker = Worker(root, data, token)
    server = ThreadingHTTPServer((host, port), handler_for(worker))
    print(f"ETL worker agent: http://{host}:{server.server_port} (root={worker.root})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description="ETL Studio - remote worker agent (Phase 1 POC)")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Workspace containing source files")
    parser.add_argument("--data", type=Path, help="State and output directory (default: ROOT/data)")
    # No 0.0.0.0 default: an operator must explicitly choose the VPN/internal
    # interface to listen on. Defaulting to loopback keeps an unconfigured
    # worker inert rather than silently network-reachable.
    parser.add_argument("--host", default="127.0.0.1", help="Interface to bind - the VPN/internal IP, not 0.0.0.0")
    parser.add_argument("--port", type=int, default=8790)
    args = parser.parse_args()
    root = args.root.resolve()
    data = (args.data or root / "data").resolve()
    load_workspace_env(root)
    import os
    token = os.environ.get("ETL_WORKER_TOKEN")
    if not token:
        raise SystemExit("Set ETL_WORKER_TOKEN in the environment or .env before starting the worker agent")
    serve(root, data, token, args.host, args.port)


if __name__ == "__main__":
    main()
