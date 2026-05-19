import os
import tempfile
import unittest
from pathlib import Path

from _util import fresh_db
from ragbaz_frog import store


class Causality(unittest.TestCase):
    def setUp(self):
        self._box = os.environ.get("FROG_BOX_ID")
        os.environ["FROG_BOX_ID"] = "box-test"
        self.conn = store.connect(fresh_db())

    def tearDown(self):
        self.conn.close()
        if self._box is None:
            os.environ.pop("FROG_BOX_ID", None)
        else:
            os.environ["FROG_BOX_ID"] = self._box

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
        self.assertTrue(all(e["origin_box_id"] == "box-test" for e in r["events"]))
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

    def test_log_blame_repo_events_include_origin_box(self):
        repo = tempfile.mkdtemp(prefix="frog-causality-")
        file_path = str(Path(repo) / "a.txt")
        Path(file_path).write_text("x")
        store.register_repo(self.conn, repo_path=repo, name="r", kind=None,
                            status="active", third_party=False, notes=None)
        store.upsert_file(
            self.conn,
            file_path=file_path,
            repo_path=repo,
            file_type="source",
            source_of_truth=None,
            notes=None,
        )
        r = store.log_blame(self.conn, file_path)
        self.assertTrue(r["ok"])
        self.assertTrue(r["events"])
        self.assertTrue(all(e["origin_box_id"] == "box-test" for e in r["events"]))

    def test_event_log_origin_columns_exist(self):
        columns = store.table_columns(self.conn, "event_log")
        self.assertIn("origin_box_id", columns)
        self.assertIn("origin_host", columns)


if __name__ == "__main__":
    unittest.main()
