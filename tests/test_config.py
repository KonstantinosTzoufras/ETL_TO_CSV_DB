import tempfile
import unittest
from pathlib import Path

from etl.config import load_workspace_env


class StartupConfigTests(unittest.TestCase):
    def load(self, content, env=None, drivers=None):
        env = {} if env is None else env
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            if content is not None:
                (root / ".env").write_text(content, encoding="utf-8-sig")
            load_workspace_env(root, environ=env, drivers=drivers or ["ODBC Driver 18 for SQL Server"])
        return env

    def test_missing_file_is_optional(self):
        self.assertEqual(self.load(None), {})

    def test_literal_password_and_odbc_escaping(self):
        env = self.load('DB_HOST=local\nDB_PORT=1433\nDB_NAME=test\nDB_USER=reader\nDB_PASS="a;PWD=x}#${TOKEN}\\path"\nALLOW_TEST_DB_LOGIN=true\n')
        self.assertIn('SERVER={local,1433}', env['ETL_SQL_MAIN'])
        self.assertIn('PWD={a;PWD=x}}#${TOKEN}}\\path}', env['ETL_SQL_MAIN'])
        self.assertIn('TrustServerCertificate={no}', env['ETL_SQL_MAIN'])
        self.assertNotIn('ETL_QUERY_CONNECTIONS', env)

    def test_existing_environment_wins(self):
        env = self.load('ETL_SQL_MAIN=file-value\nDB_PASS=file-password', {'ETL_SQL_MAIN': 'process-value', 'DB_PASS': 'process-password'})
        self.assertEqual(env['ETL_SQL_MAIN'], 'process-value')
        self.assertEqual(env['DB_PASS'], 'process-password')

    def test_quoted_direct_reference(self):
        self.assertEqual(self.load("# comment\nexport ETL_SQL_MAIN='DRIVER={test};PWD={a=b#c};'\n")['ETL_SQL_MAIN'], 'DRIVER={test};PWD={a=b#c};')

    def test_driver_fallback_and_explicit_tls(self):
        env = self.load('DB_HOST=local\nDB_NAME=test\nDB_USER=u\nDB_PASS=p\nDB_TRUST_SERVER_CERTIFICATE=yes', drivers=['ODBC Driver 17 for SQL Server'])
        self.assertIn('DRIVER={ODBC Driver 17 for SQL Server}', env['ETL_SQL_MAIN'])
        self.assertIn('TrustServerCertificate={yes}', env['ETL_SQL_MAIN'])

    def test_errors_never_include_values(self):
        for content in ('secret-not-an-assignment', 'DB_PASS="secret', 'DB_HOST=secret', 'DB_HOST=h\nDB_NAME=n\nDB_USER=u\nDB_PASS=secret\nDB_PORT=secret'):
            with self.subTest(content=content):
                with self.assertRaises(ValueError) as caught:
                    self.load(content)
                self.assertNotIn('secret', str(caught.exception))
