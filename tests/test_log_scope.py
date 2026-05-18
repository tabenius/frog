import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from _util import fresh_db
from ragbaz_frog import main_cli, store


class LogScope(unittest.TestCase):
    def setUp(self):
        self.db = fresh_db()
        self.conn = store.connect(self.db)
        self.repo = tempfile.mkdtemp(prefix="frog-log-")
        store.register_repo(
            self.conn,
            repo_path=self.repo,
            name="logrepo",
            kind=None,
            status="active",
            third_party=False,
            notes=None,
        )

    def tearDown(self):
        self.conn.close()

    def test_log_defaults_to_cwd_repo(self):
        old = os.getcwd()
        try:
            os.chdir(self.repo)
            args = SimpleNamespace(all=False, repo_ref=None)
            self.assertEqual(main_cli._log_repo_ref(self.conn, args), str(Path(self.repo).resolve()))
        finally:
            os.chdir(old)

    def test_log_all_keeps_workspace_scope(self):
        args = SimpleNamespace(all=True, repo_ref=None)
        self.assertIsNone(main_cli._log_repo_ref(self.conn, args))

    def test_log_explicit_repo_wins(self):
        args = SimpleNamespace(all=False, repo_ref="logrepo")
        self.assertEqual(main_cli._log_repo_ref(self.conn, args), "logrepo")

    def test_log_without_repo_outside_workspace_reports_missing_scope(self):
        old = os.getcwd()
        with tempfile.TemporaryDirectory(prefix="frog-log-out-") as outside:
            try:
                os.chdir(outside)
                args = SimpleNamespace(all=False, repo_ref=None)
                self.assertEqual(main_cli._log_repo_ref(self.conn, args), "")
            finally:
                os.chdir(old)


if __name__ == "__main__":
    unittest.main()
