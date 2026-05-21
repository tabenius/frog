import io
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from _util import fresh_db
from ragbaz_frog import store
from ragbaz_frog.main_cli import main


def _init_git_repo(path: str) -> None:
    subprocess.run(["git", "init", "-q", path], check=True)
    subprocess.run(["git", "-C", path, "config", "user.email", "a@b"],
                   check=True)
    subprocess.run(["git", "-C", path, "config", "user.name", "a"],
                   check=True)


def _register_repo(conn, repo_path: str) -> dict:
    return store.register_repo(
        conn, repo_path=repo_path, name=os.path.basename(repo_path),
        kind=None, status="active", third_party=False, notes=None)


class LockCheckFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="frog-audit-")
        self.repo_path = str(Path(self.tmp, "r"))
        os.makedirs(self.repo_path)
        _init_git_repo(self.repo_path)
        self.db = fresh_db()
        self.conn = store.connect(self.db)
        _register_repo(self.conn, self.repo_path)

    def tearDown(self):
        self.conn.close()

    def _claim_with_file(self, slug: str, agent: str, file_path: str):
        store.create_task(self.conn, slug=slug, repo_ref=self.repo_path,
                          title=slug, why=None, what_text=None,
                          roi_note=None, priority="p2",
                          workflow_status="idea", git_status="not_started",
                          assigned_agent=None, delegation_current=None,
                          delegation_other=None, parent_task_slug=None,
                          files=[file_path])
        r = store.task_claim(self.conn, slug=slug, agent=agent)
        self.assertTrue(r["ok"], r)

    def test_covered_by_self(self):
        fp = str(Path(self.repo_path, "src/foo.py"))
        self._claim_with_file("t", "claude", fp)
        r = store.lock_check_file(
            self.conn, repo_ref=self.repo_path, agent="claude", file_path=fp)
        self.assertTrue(r["ok"])
        self.assertEqual(r["status"], "covered")
        self.assertTrue(r["covered"])

    def test_conflict_with_other_agent(self):
        fp = str(Path(self.repo_path, "src/foo.py"))
        self._claim_with_file("t", "codex", fp)
        r = store.lock_check_file(
            self.conn, repo_ref=self.repo_path, agent="claude", file_path=fp)
        self.assertEqual(r["status"], "conflict")
        self.assertFalse(r["covered"])
        self.assertIn("codex", r["holders"])
        self.assertEqual(len(r["conflicting_locks"]), 1)
        self.assertEqual(r["conflicting_locks"][0]["holder_agent"], "codex")

    def test_uncovered_when_no_lock(self):
        fp = str(Path(self.repo_path, "src/bar.py"))
        r = store.lock_check_file(
            self.conn, repo_ref=self.repo_path, agent="claude", file_path=fp)
        self.assertEqual(r["status"], "uncovered")
        self.assertFalse(r["covered"])
        self.assertEqual(r["conflicting_locks"], [])

    def test_relative_file_resolves_under_repo(self):
        fp_abs = str(Path(self.repo_path, "src/foo.py"))
        self._claim_with_file("t", "claude", fp_abs)
        r = store.lock_check_file(
            self.conn, repo_ref=self.repo_path, agent="claude",
            file_path="src/foo.py")
        self.assertEqual(r["status"], "covered")

    def test_cli_renderer_exit_codes(self):
        fp = str(Path(self.repo_path, "src/foo.py"))
        self._claim_with_file("t", "codex", fp)
        self.conn.close()
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            rc = main([
                "--db", self.db,
                "lock", "check-file", fp,
                "--repo", self.repo_path,
                "--agent", "claude",
            ])
        self.assertEqual(rc, 1)
        self.assertIn("CONFLICT", err_buf.getvalue())
        self.assertIn("codex", err_buf.getvalue())

        # Uncovered file returns 2
        out_buf2 = io.StringIO()
        err_buf2 = io.StringIO()
        other = str(Path(self.repo_path, "src/unrelated.py"))
        with redirect_stdout(out_buf2), redirect_stderr(err_buf2):
            rc2 = main([
                "--db", self.db,
                "lock", "check-file", other,
                "--repo", self.repo_path,
                "--agent", "claude",
            ])
        self.assertEqual(rc2, 2)
        self.assertIn("UNCOVERED", err_buf2.getvalue())

        # Covered file returns 0
        out_buf3 = io.StringIO()
        with redirect_stdout(out_buf3):
            rc3 = main([
                "--db", self.db,
                "lock", "check-file", fp,
                "--repo", self.repo_path,
                "--agent", "codex",
            ])
        self.assertEqual(rc3, 0)
        self.assertIn("COVERED", out_buf3.getvalue())
        # reopen the conn so tearDown can close it
        self.conn = store.connect(self.db)


class LockAuditWarn(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="frog-audit-warn-")
        self.repo_path = str(Path(self.tmp, "r"))
        os.makedirs(self.repo_path)
        _init_git_repo(self.repo_path)
        self.db = fresh_db()
        self.conn = store.connect(self.db)
        _register_repo(self.conn, self.repo_path)
        # create a dirty file under no active lock
        f = Path(self.repo_path, "dirt.txt")
        f.write_text("uncovered\n")

    def tearDown(self):
        self.conn.close()

    def test_warn_mode_exits_zero_even_with_findings(self):
        self.conn.close()
        out_buf = io.StringIO()
        with redirect_stdout(out_buf):
            rc = main([
                "--db", self.db,
                "lock", "audit",
                "--repo", self.repo_path,
                "--agent", "claude",
                "--warn",
                "--json",
            ])
        import json as _json
        body = _json.loads(out_buf.getvalue())
        self.assertEqual(rc, 0)
        self.assertTrue(body["ok"])
        self.assertTrue(body.get("advisory"))
        self.assertGreaterEqual(len(body["findings"]), 1)
        self.conn = store.connect(self.db)

    def test_blocking_mode_exits_nonzero_with_findings(self):
        self.conn.close()
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            rc = main([
                "--db", self.db,
                "lock", "audit",
                "--repo", self.repo_path,
                "--agent", "claude",
                "--json",
            ])
        import json as _json
        body = _json.loads(out_buf.getvalue())
        self.assertEqual(rc, 1)
        self.assertFalse(body["ok"])
        self.conn = store.connect(self.db)


if __name__ == "__main__":
    unittest.main()
