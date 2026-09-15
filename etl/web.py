"""Loopback-only UI for a single trusted local user."""
import csv
import json
import logging
import mimetypes
import secrets
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .engine import execute
from .sources import inspect_source
from .spec import ConfigError, require, validate
from .store import Store

ASSETS = Path(__file__).parent / "static"


class Application:
    def __init__(self, root, data):
        self.root, self.data = Path(root).resolve(), Path(data).resolve()
        self.store = Store(self.data / "etl.sqlite3")
        self.token = secrets.token_urlsafe(32)
        self.executor = ThreadPoolExecutor(max_workers=2)
        self.slots = threading.BoundedSemaphore(8)
        # Recover unfinished local jobs after a process restart.
        with self.store.connect() as db:
            db.execute("UPDATE runs SET status='interrupted', error='The application stopped before this run finished.' WHERE status IN ('queued','running')")

    def submit(self, spec):
        validate(spec)
        require(self.slots.acquire(blocking=False), "Eight runs are already queued or running; wait for one to finish")
        try:
            run_id = self.store.create_run(spec)
            self.executor.submit(self.work, run_id, spec)
            return run_id
        except Exception:
            self.slots.release()
            raise

    def work(self, run_id, spec):
        try:
            self.store.update_run(run_id, "running")
            report = execute(spec, self.root, self.data / "runs", progress=lambda counts: self.store.update_run(run_id, "running", counts))
            self.store.update_run(run_id, "completed", report)
        except (ConfigError, OSError, ValueError, csv.Error) as error:
            self.store.update_run(run_id, "failed", error=str(error))
        except Exception as error:
            logging.error("Run %s failed (%s)", run_id, type(error).__name__)
            self.store.update_run(run_id, "failed", error="Unexpected execution failure. Check the input data and server logs.")
        finally:
            self.slots.release()


def handler_for(app):
    class Handler(BaseHTTPRequestHandler):
        def respond(self, status, value, content_type="application/json; charset=utf-8"):
            body = json.dumps(value, ensure_ascii=False).encode() if not isinstance(value, bytes) else value
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body)

        def trusted(self, mutation=False):
            port = self.server.server_port
            allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
            if self.headers.get("Host") not in allowed:
                self.respond(403, {"error": "Use the local application address"})
                return False
            if self.headers.get("Sec-Fetch-Site") == "cross-site":
                self.respond(403, {"error": "Cross-site requests are not allowed"})
                return False
            origin = self.headers.get("Origin")
            if origin and origin not in {"http://" + host for host in allowed}:
                self.respond(403, {"error": "Invalid origin"})
                return False
            if mutation and not secrets.compare_digest(self.headers.get("X-ETL-Token", ""), app.token):
                self.respond(403, {"error": "Refresh the page and try again"})
                return False
            return True

        def do_GET(self):
            if not self.trusted():
                return
            path = urlsplit(self.path).path
            if path in {"/", "/app.js", "/style.css"}:
                asset = ASSETS / ("index.html" if path == "/" else path[1:])
                self.respond(200, asset.read_bytes(), (mimetypes.guess_type(asset)[0] or "text/plain") + "; charset=utf-8")
            elif path == "/api/bootstrap":
                sample_path = app.root / "examples" / "customers.json"
                self.respond(200, {"token": app.token, "example": json.loads(sample_path.read_text(encoding="utf-8-sig")) if sample_path.exists() else None})
            elif path == "/api/pipelines":
                self.respond(200, app.store.pipelines())
            elif path == "/api/runs":
                self.respond(200, app.store.runs())
            elif path.startswith("/download/"):
                parts = path.split("/")
                run = app.store.run(parts[2]) if len(parts) == 4 else None
                if not run or run["status"] != "completed" or parts[3] not in {"valid.csv", "valid.xlsx", "rejected.csv"}:
                    self.respond(404, {"error": "Export not found"})
                    return
                file = (Path(run["report"]["directory"]) / parts[3]).resolve()
                if not file.is_relative_to(app.data / "runs") or not file.is_file():
                    self.respond(404, {"error": "Export not found"})
                    return
                self.send_response(200)
                self.send_header("Content-Type", mimetypes.guess_type(file)[0] or "application/octet-stream")
                self.send_header("Content-Disposition", f'attachment; filename="{parts[3]}"')
                self.send_header("Content-Length", str(file.stat().st_size))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                with file.open("rb") as handle:
                    shutil.copyfileobj(handle, self.wfile, 65536)
            else:
                self.respond(404, {"error": "Not found"})

        def do_POST(self):
            if not self.trusted(mutation=True):
                return
            try:
                require(self.headers.get("Content-Type", "").split(";")[0] == "application/json", "Send application/json")
                size = int(self.headers.get("Content-Length", "0"))
                require(0 < size <= 1000000, "Request must be 1 byte–1 MB")
                body = json.loads(self.rfile.read(size))
                require(isinstance(body, dict), "Request must be an object")
                path = urlsplit(self.path).path
                if path == "/api/columns":
                    self.respond(200, {"columns": inspect_source(body.get("source"), app.root)})
                elif path == "/api/validate":
                    validate(body.get("spec"))
                    self.respond(200, {"ok": True})
                elif path == "/api/preview":
                    self.respond(200, execute(body.get("spec"), app.root, limit=100))
                elif path == "/api/pipelines":
                    pipeline_id = body.get("id")
                    require(pipeline_id is None or isinstance(pipeline_id, str) and len(pipeline_id) <= 64, "Invalid pipeline ID")
                    self.respond(200, {"id": app.store.save(body.get("spec"), pipeline_id)})
                elif path == "/api/runs":
                    self.respond(202, {"id": app.submit(body.get("spec"))})
                else:
                    self.respond(404, {"error": "Not found"})
            except (ConfigError, ValueError, TypeError, OSError, csv.Error) as error:
                self.respond(400, {"error": str(error)})
            except Exception as error:
                logging.error("Request failed (%s)", type(error).__name__)
                self.respond(500, {"error": "Unexpected server error"})

    return Handler


def serve(root, data, port=8765):
    app = Application(root, data)
    server = ThreadingHTTPServer(("127.0.0.1", port), handler_for(app))
    print(f"ETL Studio: http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        app.executor.shutdown(wait=True)
