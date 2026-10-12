import sqlite3
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from _util import fresh_db
from ragbaz_frog import store
from ragbaz_frog.vfs import LocalVFS, current_vfs, set_vfs


class Snapshot(unittest.TestCase):
    def setUp(self):
        self.conn = store.connect(fresh_db())
        self.dest = tempfile.mkdtemp()

    def tearDown(self):
        self.conn.close()

    def _mk(self, slug):
        store.create_task(
            self.conn, slug=slug, repo_ref=None, title=slug,
            why=None, what_text=None, roi_note=None, priority="p3",
            workflow_status="idea", git_status="not_started",
            assigned_agent=None, delegation_current=None,
            delegation_other=None, parent_task_slug=None)

    def test_backs_up_the_db_file_consistently(self):
        self._mk("t1")
        r = store.snapshot_workspace(self.conn, dest=self.dest)
        self.assertTrue(r["ok"], r)
        last = Path(r["last"])
        self.assertTrue(last.exists())
        self.assertEqual(last.name, "AGENTS.db.last")
        self.assertGreater(r["bytes"], 0)
        # the backup is a real, queryable DB containing the task
        c = sqlite3.connect(str(last))
        try:
            n = c.execute(
                "SELECT count(*) FROM tasks WHERE slug='t1'").fetchone()[0]
        finally:
            c.close()
        self.assertEqual(n, 1)

    def test_rotates_last_to_prev(self):
        store.snapshot_workspace(self.conn, dest=self.dest)
        self._mk("t2")
        r = store.snapshot_workspace(self.conn, dest=self.dest)
        self.assertTrue(Path(r["prev"]).exists())
        self.assertTrue(Path(r["last"]).exists())
        # prev is the older generation (no t2), last has t2
        cp = sqlite3.connect(r["prev"]); cl = sqlite3.connect(r["last"])
        try:
            self.assertEqual(
                cp.execute("SELECT count(*) FROM tasks WHERE slug='t2'"
                           ).fetchone()[0], 0)
            self.assertEqual(
                cl.execute("SELECT count(*) FROM tasks WHERE slug='t2'"
                           ).fetchone()[0], 1)
        finally:
            cp.close(); cl.close()

    def test_records_event(self):
        store.snapshot_workspace(self.conn, dest=self.dest)
        kinds = [r[0] for r in self.conn.execute(
            "SELECT kind FROM event_log").fetchall()]
        self.assertIn("workspace.snapshot", kinds)

    def test_absolute_destination_is_not_prefixed_with_workspace(self):
        previous = current_vfs()
        self.addCleanup(set_vfs, previous)
        with tempfile.TemporaryDirectory() as workspace:
            set_vfs(LocalVFS(workspace))
            result = store.snapshot_workspace(self.conn, dest=self.dest)
            self.assertTrue(result["ok"], result)
            self.assertEqual(Path(result["last"]), Path(self.dest) / "AGENTS.db.last")
            self.assertFalse((Path(workspace) / self.dest.lstrip("/")).exists())

    def test_absolute_destination_rotation_with_workspace_vfs(self):
        previous = current_vfs()
        self.addCleanup(set_vfs, previous)
        with tempfile.TemporaryDirectory() as workspace:
            vfs = LocalVFS(workspace)
            set_vfs(vfs)
            self.test_rotates_last_to_prev()
            self.assertIs(current_vfs(), vfs)

    def test_tilde_destination_expands_to_host_home(self):
        previous = current_vfs()
        self.addCleanup(set_vfs, previous)
        with tempfile.TemporaryDirectory() as workspace, tempfile.TemporaryDirectory() as home:
            set_vfs(LocalVFS(workspace))
            with patch.dict(os.environ, {"HOME": home}):
                result = store.snapshot_workspace(self.conn, dest="~/snapshots")
            self.assertTrue(result["ok"], result)
            self.assertEqual(Path(result["last"]), Path(home) / "snapshots/AGENTS.db.last")

    def test_relative_destination_stays_in_workspace(self):
        previous = current_vfs()
        self.addCleanup(set_vfs, previous)
        with tempfile.TemporaryDirectory() as workspace:
            set_vfs(LocalVFS(workspace))
            result = store.snapshot_workspace(self.conn, dest="snapshots")
            self.assertTrue(result["ok"], result)
            self.assertEqual(Path(result["last"]), Path(workspace) / "snapshots/AGENTS.db.last")


if __name__ == "__main__":
    unittest.main()
