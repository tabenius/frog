import json
import unittest
from _util import fresh_db
from ragbaz_frog import store


def mk(conn, slug, repo=None):
    store.create_task(conn, slug=slug, repo_ref=repo, title=slug, why=None,
                      what_text=None, roi_note=None, priority="p2",
                      workflow_status="idea", git_status="not_started",
                      assigned_agent=None, delegation_current=None,
                      delegation_other=None, parent_task_slug=None)


class Workflow(unittest.TestCase):
    def setUp(self):
        self.conn = store.connect(fresh_db())

    def tearDown(self):
        self.conn.close()

    def test_claim_assigns_locks_and_marks_in_progress(self):
        mk(self.conn, "t")
        r = store.task_claim(self.conn, slug="t", agent="claude")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["task"]["assigned_agent"], "claude")
        self.assertEqual(r["task"]["workflow_status"], "in_progress")
        locks = store.lock_list(self.conn, include_inactive=False, repo_ref=None)
        self.assertTrue(any(l["scope_key"] == "task:t" for l in locks["locks"]))

    def test_claim_blocked_when_owned_by_other(self):
        mk(self.conn, "t")
        store.task_claim(self.conn, slug="t", agent="codex")
        r = store.task_claim(self.conn, slug="t", agent="claude")
        self.assertFalse(r["ok"])
        self.assertIn("codex", r["error"])

    def test_create_and_claim_with_file_scopes(self):
        store.create_task(
            self.conn, slug="files", repo_ref=None, title="files",
            why=None, what_text=None, roi_note=None, priority="p2",
            workflow_status="idea", git_status="not_started",
            assigned_agent=None, delegation_current=None,
            delegation_other=None, parent_task_slug=None,
            files=["/tmp/frog-task-file-a"],
        )
        info = store.task_info(self.conn, "files")
        self.assertEqual(info["files"][0]["file_path"], "/tmp/frog-task-file-a")

        r = store.task_claim(
            self.conn, slug="files", agent="claude",
            files=["/tmp/frog-task-file-b"],
        )
        self.assertTrue(r["ok"], r)
        locked = r["lock"]["file_paths"]
        self.assertIn("/tmp/frog-task-file-a", locked)
        self.assertIn("/tmp/frog-task-file-b", locked)

    def test_finish_no_verify_flips_and_reports_unblock(self):
        mk(self.conn, "base")
        mk(self.conn, "dep")
        store.task_add_dependency(self.conn, "dep", "base", "depends_on")
        store.task_claim(self.conn, slug="base", agent="claude")
        r = store.task_finish(self.conn, slug="base", agent="claude",
                              verify=False)
        self.assertTrue(r["ok"])
        self.assertEqual(r["task"]["workflow_status"], "done")
        self.assertEqual(r["unblocked"], ["dep"])
        # lock released
        active = store.lock_list(self.conn, include_inactive=False, repo_ref=None)
        self.assertFalse(any(l["scope_key"] == "task:base"
                             for l in active["locks"]))

    def test_finish_not_unblocked_if_other_dep_pending(self):
        mk(self.conn, "a"); mk(self.conn, "b"); mk(self.conn, "c")
        store.task_add_dependency(self.conn, "c", "a", "depends_on")
        store.task_add_dependency(self.conn, "c", "b", "depends_on")
        store.task_finish(self.conn, slug="a", agent="x", verify=False)
        # b still pending -> c not unblocked yet
        r = store.task_finish(self.conn, slug="b", agent="x", verify=False)
        self.assertEqual(r["unblocked"], ["c"])

    def test_release_records_audit_metadata(self):
        lock = store.lock_acquire(
            self.conn,
            scope_key="s",
            repo_ref=None,
            lock_kind="edit",
            files=[],
            agent="claude",
            pid=None,
            reason="work",
            lease_seconds=1800,
            eta_minutes=None,
            force=False,
        )
        self.assertTrue(lock["ok"], lock)
        released = store.lock_release(
            self.conn,
            lock["lock"]["id"],
            force=False,
            agent="codex",
            reason="stale after adopted commit",
        )
        self.assertTrue(released["ok"], released)
        event = self.conn.execute(
            "SELECT actor, payload_json FROM event_log "
            "WHERE kind = 'lock.released' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        self.assertEqual(event["actor"], "codex")
        payload = json.loads(event["payload_json"])
        self.assertEqual(payload["lock_agent"], "claude")
        self.assertEqual(payload["released_by"], "codex")
        self.assertEqual(payload["reason"], "stale after adopted commit")


if __name__ == "__main__":
    unittest.main()
