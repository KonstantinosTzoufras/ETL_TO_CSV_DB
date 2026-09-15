"""Isolated query browser server. pyodbc.connect is replaced for its lifetime."""
from pathlib import Path
import shutil
import tempfile
from etl.web import serve
from tests.test_queries import fake_sql

if __name__=='__main__':
    project=Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='etl-query-browser-') as temporary, fake_sql():
        root=Path(temporary);(root/'examples').mkdir()
        for name in ('customers.csv','customers.json'):
            shutil.copyfile(project/'examples'/name,root/'examples'/name)
        serve(root,root/'data',port=8769)
