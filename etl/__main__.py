import argparse
import csv
from .serialization import json_default
import json
import sys
from pathlib import Path

from .engine import execute
from .store import Store
from .ordered import is_ordered, preview_spec
from .ordered_runs import coordinate, interrupted_report, failed_report
from .config import load_workspace_env


def main():
    parser = argparse.ArgumentParser(description="ETL Studio — local pipeline runner")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Workspace containing source files")
    parser.add_argument("--data", type=Path, help="State and output directory (default: ROOT/data)")
    commands = parser.add_subparsers(dest="command", required=True)
    web = commands.add_parser("serve", help="Open the local web application")
    web.add_argument("--port", type=int, default=8765)
    for name in ("preview", "run"):
        command = commands.add_parser(name)
        command.add_argument("pipeline", type=Path)
        if name == "preview":
            command.add_argument("--step", help="Required step ID for an ordered pipeline preview")
    args = parser.parse_args()
    root = args.root.resolve()
    data = (args.data or root / "data").resolve()
    run_id = None
    store = None
    try:
        load_workspace_env(root)
        if args.command == "serve":
            from .web import serve
            serve(root, data, args.port)
            return 0
        spec = json.loads(args.pipeline.read_text(encoding="utf-8-sig"))
        if args.command == "run":
            store = Store(data / "etl.sqlite3")
            run_id = store.create_run(spec)
            store.update_run(run_id, "running")
        if is_ordered(spec):
            if args.command == "preview":
                selected = preview_spec(spec, args.step, root, data / "runs")
                report = execute(selected, root, limit=100)
            else:
                report = coordinate(spec, root, data / "runs", run_id,
                                    persist=lambda value: store.update_run(run_id, value["status"], value, value.get("error")))
            print(json.dumps(report, ensure_ascii=True, indent=2, default=json_default))
            return 0 if report.get("status", "completed") == "completed" else 1
        report = execute(spec, root, data / "runs" if run_id else None, limit=100 if args.command == "preview" else None)
        if run_id:
            store.update_run(run_id, "completed", report)
        print(json.dumps(report, ensure_ascii=True, indent=2, default=json_default))
        return 0
    except KeyboardInterrupt:
        if run_id and is_ordered(spec):
            current = store.run(run_id)
            store.update_run(run_id, "interrupted", interrupted_report(spec, run_id, current["report"]), "CLI execution interrupted; no automatic resume")
            return 130
        raise
    except (ValueError, TypeError, OSError, csv.Error) as error:
        if run_id:
            if is_ordered(spec):
                store.update_run(run_id, "failed", failed_report(spec, run_id, store.run(run_id)["report"], str(error)), str(error))
            else:
                store.update_run(run_id, "failed", error=str(error))
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except Exception as error:
        message = f"Execution failed ({type(error).__name__}). Check the input and dependencies."
        if run_id:
            if is_ordered(spec):
                store.update_run(run_id, "failed", failed_report(spec, run_id, store.run(run_id)["report"], message), message)
            else:
                store.update_run(run_id, "failed", error=message)
        print(message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
