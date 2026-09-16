"""Loopback-only UI for a single trusted local user."""
import csv
from .serialization import json_default
import json
import logging
import mimetypes
import os
import re
import secrets
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .engine import execute
from .ordered import is_ordered, validate_definition, from_dict as ordered_from_dict, to_dict as ordered_to_dict, preflight, preview_spec
from .ordered_runs import coordinate, interrupted_report, failed_report, step_history, download_path
from .discovery import DiscoveryError, dispatch as discover
from .diagnostics import present_result, rejected_page
from .queries import QueryError, connection_policies, query_from_dict
from .sources import inspect_source
from .spec import ConfigError, require, validate, MAX_OUTPUT_COLUMNS
from .store import Store
from .templates import TemplateStore, dispatch as template_request

ASSETS = Path(__file__).parent / "static"


class Application:
    def __init__(self, root, data):
        self.root, self.data = Path(root).resolve(), Path(data).resolve()
        self.store = Store(self.data / "etl.sqlite3")
        self.templates = TemplateStore(self.data / "templates")
        self.token = secrets.token_urlsafe(32)
        self.executor = ThreadPoolExecutor(max_workers=2)
        self.slots = threading.BoundedSemaphore(8)
        # Recover ordered runs without resuming any step or querying sources.
        with self.store.connect() as db:
            unfinished = [dict(row) for row in db.execute("SELECT * FROM runs WHERE status IN ('queued','running')")]
        for row in unfinished:
            snapshot = json.loads(row["spec"])
            if is_ordered(snapshot):
                report = interrupted_report(snapshot, row["id"], json.loads(row["report"]))
                self.store.update_run(row["id"], "interrupted", report, "Application stopped; ordered steps are not resumed")
        # Recover unfinished local jobs after a process restart.
        with self.store.connect() as db:
            db.execute("UPDATE runs SET status='interrupted', error='The application stopped before this run finished.' WHERE status IN ('queued','running')")

    def submit(self, spec):
        validate_definition(spec)
        if is_ordered(spec):
            model = ordered_from_dict(spec)
            preflight(model, self.root, self.data / "runs")
            spec = ordered_to_dict(model)  # Own the queued snapshot, not caller containers.
        require(self.slots.acquire(blocking=False), "Eight runs are already queued or running; wait for one to finish")
        try:
            run_id = self.store.create_run(spec)
            self.executor.submit(self.work, run_id, spec)
            return run_id
        except Exception:
            self.slots.release()
            raise

    def fail(self, run_id, spec, message):
        if is_ordered(spec):
            previous = self.store.run(run_id)
            report = failed_report(spec, run_id, previous["report"] if previous else {}, message)
            self.store.update_run(run_id, "failed", report, message)
        else:
            self.store.update_run(run_id, "failed", error=message)

    def work(self, run_id, spec):
        try:
            if is_ordered(spec):
                coordinate(spec, self.root, self.data / "runs", run_id,
                           persist=lambda report: self.store.update_run(run_id, report["status"], report, report.get("error")))
                return
            self.store.update_run(run_id, "running")
            report = execute(spec, self.root, self.data / "runs", progress=lambda counts: self.store.update_run(run_id, "running", counts))
            self.store.update_run(run_id, "completed", report)
        except (ConfigError, OSError, ValueError, csv.Error) as error:
            self.fail(run_id, spec, str(error))
        except Exception as error:
            logging.error("Run %s failed (%s)", run_id, type(error).__name__)
            self.fail(run_id, spec, "Unexpected execution failure. Check the input data and server logs.")
        finally:
            self.slots.release()


def handler_for(app):
    class Handler(BaseHTTPRequestHandler):
        def respond(self, status, value, content_type="application/json; charset=utf-8"):
            body = json.dumps(value, ensure_ascii=False, default=json_default).encode() if not isinstance(value, bytes) else value
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
            if path in {"/", "/app.js", "/style.css", "/diagnostics.js", "/templates.js", "/query.js", "/ordered.js", "/controls.js", "/vendor/jquery-3.7.1.min.js", "/vendor/select2-4.0.13.min.js", "/vendor/select2-4.0.13.min.css"}:
                asset = ASSETS / ("index.html" if path == "/" else path[1:])
                self.respond(200, asset.read_bytes(), (mimetypes.guess_type(asset)[0] or "text/plain") + "; charset=utf-8")
            elif path == "/api/bootstrap":
                sample_path = app.root / "examples" / "customers.json"
                self.respond(200, {"token": app.token, "example": json.loads(sample_path.read_text(encoding="utf-8-sig")) if sample_path.exists() else None,
                                   "source_connections": sorted(name for name, value in os.environ.items() if re.fullmatch(r"ETL_SQL_[A-Z0-9_]+", name) and value),
                                   "output_directory": str(app.data / "runs"), "max_output_columns": MAX_OUTPUT_COLUMNS})
            elif path == "/api/pipelines":
                self.respond(200, app.store.pipelines())
            elif path == "/api/runs":
                self.respond(200, app.store.runs())
            elif path.startswith("/download/"):
                parts = path.split("/")
                run = app.store.run(parts[2]) if len(parts) in (4, 5) else None
                if run and is_ordered(run["spec"]) and len(parts) == 5:
                    try:
                        file = download_path(run, parts[3], parts[4], app.data)
                    except (ConfigError, OSError):
                        self.respond(404, {"error": "Completed step output not found"})
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", mimetypes.guess_type(file)[0] or "application/octet-stream")
                    self.send_header("Content-Disposition", f'attachment; filename="{file.name}"')
                    self.send_header("Content-Length", str(file.stat().st_size))
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()
                    with file.open("rb") as handle:
                        shutil.copyfileobj(handle, self.wfile, 65536)
                    return
                if not run or is_ordered(run["spec"]) or len(parts) != 4 or run["status"] != "completed" or parts[3] not in {"valid.csv", "valid.xlsx", "rejected.csv"}:
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
                if path == "/api/query/connections":
                    self.respond(200, {"connections": sorted(connection_policies())})
                elif path == "/api/query/validate":
                    query_from_dict(body.get("query"))
                    self.respond(200, {"ok": True})
                elif path.startswith("/api/templates/"):
                    self.respond(200, template_request(app.templates, path.removeprefix("/api/templates/"), body))
                elif path.startswith("/api/discovery/"):
                    self.respond(200, discover(app.root, path.removeprefix("/api/discovery/"), body))
                elif path == "/api/columns":
                    self.respond(200, {"columns": inspect_source(body.get("source"), app.root)})
                elif path == "/api/validate":
                    validate_definition(body.get("spec"))
                    self.respond(200, {"ok": True})
                elif path in ("/api/preview", "/api/ordered/preview"):
                    diagnostics = []
                    spec = body.get("spec")
                    if path == "/api/ordered/preview":
                        spec = preview_spec(spec, body.get("step_id"), app.root, app.data / "runs")
                    else:
                        require(not is_ordered(spec), "Select one ordered step for preview")
                    include = body.get("diagnostics", False)
                    require(type(include) is bool, "diagnostics must be a boolean")
                    callback = (lambda row: diagnostics.append(present_result(row, spec))) if include else None
                    report = execute(spec, app.root, limit=100, on_row=callback)
                    if include:
                        report["diagnostics"] = diagnostics
                    self.respond(200, report)
                elif path == "/api/diagnostics/rejections":
                    require(isinstance(body.get("run_id"), str), "Run ID is required")
                    run = app.store.run(body["run_id"])
                    if run and is_ordered(run["spec"]):
                        run = step_history(run, body.get("step_id"), app.data, allow_partial=True)
                    page = rejected_page(run, app.data, app.token, cursor=body.get("cursor"), limit=body.get("limit", 20))
                    if run and "step_status" in run:
                        page.update(step_id=body.get("step_id"), step_status=run["step_status"], partial=run["partial"])
                    self.respond(200, page)
                elif path == "/api/pipelines":
                    pipeline_id = body.get("id")
                    require(pipeline_id is None or isinstance(pipeline_id, str) and len(pipeline_id) <= 64, "Invalid pipeline ID")
                    self.respond(200, {"id": app.store.save(body.get("spec"), pipeline_id)})
                elif path == "/api/runs":
                    self.respond(202, {"id": app.submit(body.get("spec"))})
                else:
                    self.respond(404, {"error": "Not found"})
            except (DiscoveryError, QueryError) as error:
                self.respond(400, {"error": str(error), "code": error.code})
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
