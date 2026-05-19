import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from _util import fresh_db
from ragbaz_frog import store


class RepoMove(unittest.TestCase):
    def setUp(self):
        self._b = os.environ.get("FROG_BOX_ID")
        os.environ["FROG_BOX_ID"] = "boxA"
        self.conn = store.connect(fresh_db())
        self.old = "/srv/old/repo"
        store.register_repo(self.conn, repo_path=self.old, name="r",
                            kind=None, status="active",
                            third_party=False, notes=None)
        store.ensure_repo_key(self.conn, self.old)
        store.create_task(self.conn, slug="t1", repo_ref=self.old,
                          title="T1", why=None, what_text=None,
                          roi_note=None, priority="p3",
                          workflow_status="idea",
                          git_status="not_started", assigned_agent=None,
                          delegation_current=None, delegation_other=None,
                          parent_task_slug=None)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        if self._b is None:
            os.environ.pop("FROG_BOX_ID", None)
        else:
            os.environ["FROG_BOX_ID"] = self._b

    def test_move_repoints_all_refs_and_keeps_integrity(self):
        new = "/srv/new/repo"
        r = store.repo_move(self.conn, self.old, new, agent="claude")
        self.assertTrue(r["ok"], r)
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM repos WHERE repo_path=?",
                              (self.old,)).fetchone()[0], 0)
        self.assertEqual(
            self.conn.execute("SELECT repo_path FROM tasks WHERE slug='t1'"
                              ).fetchone()[0], new)
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM repo_aliases "
                              "WHERE repo_path=?", (new,)).fetchone()[0], 1)
        # FK integrity intact
        self.assertEqual(
            self.conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        # audit event recorded
        self.assertIn("repo.moved", [x[0] for x in self.conn.execute(
            "SELECT kind FROM event_log")])

    def test_refuses_unknown_old(self):
        r = store.repo_move(self.conn, "/nope", "/whatever")
        self.assertFalse(r["ok"])
        self.assertIn("no registered repo", r["error"])

    def test_refuses_when_new_already_registered(self):
        store.register_repo(self.conn, repo_path="/srv/taken", name="x",
                            kind=None, status="active",
                            third_party=False, notes=None)
        r = store.repo_move(self.conn, self.old, "/srv/taken")
        self.assertFalse(r["ok"])
        self.assertIn("already registered", r["error"])

    def test_refuses_identical(self):
        r = store.repo_move(self.conn, self.old, self.old)
        self.assertFalse(r["ok"])

    def test_cli_repo_move(self):
        d = tempfile.mkdtemp(); db = str(Path(d) / "AGENTS.db")
        env = {**os.environ, "FROG_BOX_ID": "boxA"}
        run = lambda *a: subprocess.run(
            ["python3", "bin/frog", "--db", db, *a],
            capture_output=True, text=True, env=env)
        run("db", "migrate")
        run("repo", "register", "/p/a", "--name", "a")
        r = run("repo", "move", "/p/a", "/p/b")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        lst = run("--json", "repo", "list").stdout
        self.assertIn("/p/b", lst)
        self.assertNotIn("/p/a", lst)


if __name__ == "__main__":
    unittest.main()
