import unittest
from pathlib import Path

from _util import fresh_db
from ragbaz_frog import store


class Causality(unittest.TestCase):
    def setUp(self):
        self.conn = store.connect(fresh_db())

    def tearDown(self):
        self.conn.close()

    def test_log_why_collects_task_timeline(self):
        store.create_task(self.conn, slug="t1", repo_ref=None, title="T1",
                          why=None, what_text=None, roi_note=None,
                          priority="p2", workflow_status="idea",
                          git_status="not_started", assigned_agent=None,
                          delegation_current=None, delegation_other=None,
                          parent_task_slug=None)
        store.task_set_status(self.conn, slug="t1",
                              workflow_status="in_progress",
                              git_status=None, note="started")
        store.task_assign(self.conn, "t1", "claude", None)
        r = store.log_why(self.conn, "t1")
        self.assertTrue(r["ok"])
        self.assertEqual(r["task"]["slug"], "t1")
        self.assertTrue(len(r["history"]) >= 1)
        self.assertTrue(len(r["assignments"]) >= 1)
        self.assertFalse(store.log_why(self.conn, "nope")["ok"])

    def test_log_blame_links_file_to_lock(self):
        f = "/tmp/frog-blame-target.txt"
        Path(f).write_text("x")
        store.lock_acquire(self.conn, scope_key="s", repo_ref=None,
                           lock_kind="edit", files=[f], agent="codex",
                           pid=None, reason=None, lease_seconds=1800,
                           eta_minutes=None, force=False)
        r = store.log_blame(self.conn, f)
        self.assertTrue(r["ok"])
        self.assertEqual(len(r["locks"]), 1)
        self.assertEqual(r["locks"][0]["agent_name"], "codex")


if __name__ == "__main__":
    unittest.main()
