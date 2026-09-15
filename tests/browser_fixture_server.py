"""Isolated local fixtures for browser acceptance; no SQL or real run history.

Start: .venv/Scripts/python.exe -m tests.browser_fixture_server
Stop with Ctrl+C. All test files and run state are temporary.
"""
import csv
from pathlib import Path
import shutil
import tempfile

from etl.web import serve


if __name__ == "__main__":
    project = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="etl-browser-fixtures-") as temporary:
        root = Path(temporary)
        (root / "examples").mkdir()
        for name in ("customers.csv", "customers.json"):
            shutil.copyfile(project / "examples" / name, root / "examples" / name)
        with (root / "diagnostics.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, delimiter=";")
            writer.writerow(["note"])
            for _ in range(25):  # Exercise multiple stored rejection pages.
                writer.writerow(["Ελλάδα\r\nsecond line"])
        serve(root, root / "data", port=8768)
