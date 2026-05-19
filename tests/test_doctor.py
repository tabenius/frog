import tempfile
import unittest
from _util import fresh_db
from ragbaz_frog import store


class Doctor(unittest.TestCase):
    def setUp(self):
        self.conn = store.connect(fresh_db())

    def tearDown(self):
        self.conn.close()

    def _mk(self, slug, wf="idea", repo=None):
        store.create_task(self.conn, slug=slug, repo_ref=repo, title=slug,
                          why=None, what_text=None, roi_note=None,
                          priority="p3", workflow_status=wf,
                          git_status="not_started", assigned_agent=None,
                          delegation_current=None, delegation_other=None,
                          parent_task_slug=None)

    def test_clean_db_is_all_clear(self):
        r = store.doctor(self.conn)
        self.assertTrue(r["ok"])
        self.assertEqual(r["findings"], [])

    def test_detects_ready_task_drift(self):
        self._mk("base")
        self._mk("dep")  # idea, has a dep
        store.task_add_dependency(self.conn, "dep", "base", "depends_on")
        store.task_set_status(self.conn, slug="base",
                              workflow_status="done", git_status=None, note=None)
        r = store.doctor(self.conn)
        codes = {f["code"] for f in r["findings"]}
        self.assertIn("ready_tasks", codes)

    def test_repairs_stale_lock_by_default(self):
        store.lock_acquire(self.conn, scope_key="s", repo_ref=None,
                           lock_kind="x", files=[], agent="a", pid=None,
                           reason=None, lease_seconds=1, eta_minutes=None,
                           force=False)
        self.conn.execute("UPDATE locks SET updated_at='2000-01-01T00:00:00+00:00'")
        self.conn.commit()
        r = store.doctor(self.conn)
        repair_codes = {f["code"] for f in r["repairs"]}
        stale = self.conn.execute(
            "SELECT COUNT(*) c FROM locks WHERE status = 'stale'"
        ).fetchone()["c"]
        self.assertIn("stale_locks", repair_codes)
        self.assertEqual(stale, 0)
        self.assertTrue(r["ok"])

    def test_no_fix_reports_stale_lock(self):
        store.lock_acquire(self.conn, scope_key="s", repo_ref=None,
                           lock_kind="x", files=[], agent="a", pid=None,
                           reason=None, lease_seconds=1, eta_minutes=None,
                           force=False)
        self.conn.execute("UPDATE locks SET updated_at='2000-01-01T00:00:00+00:00'")
        self.conn.commit()
        r = store.doctor(self.conn, fix=False)
        codes = {f["code"] for f in r["findings"]}
        self.assertIn("stale_locks", codes)
        self.assertFalse(r["ok"], "stale locks are a warn -> not ok with --no-fix")

    def test_repairs_done_task_active_assignment(self):
        self._mk("done", wf="done")
        store.task_assign(self.conn, "done", "claude", None)

        r = store.doctor(self.conn)
        repair_codes = {f["code"] for f in r["repairs"]}
        active = self.conn.execute(
            "SELECT COUNT(*) c FROM task_assignments WHERE task_slug='done' AND active=1"
        ).fetchone()["c"]
        self.assertIn("done_task_active_assignments", repair_codes)
        self.assertEqual(active, 0)

    def test_repairs_done_task_active_lock(self):
        self._mk("done", wf="done")
        lock = store.lock_acquire(
            self.conn, scope_key="task:done", repo_ref=None,
            lock_kind="edit", files=[], agent="claude", pid=None,
            reason=None, lease_seconds=1800, eta_minutes=None,
            force=False,
        )
        self.assertTrue(lock["ok"], lock)

        r = store.doctor(self.conn)
        repair_codes = {f["code"] for f in r["repairs"]}
        active = self.conn.execute(
            "SELECT COUNT(*) c FROM locks WHERE scope_key='task:done' AND status='active'"
        ).fetchone()["c"]
        self.assertIn("done_task_active_locks", repair_codes)
        self.assertEqual(active, 0)

    def test_repairs_missing_repo_alias(self):
        repo = tempfile.mkdtemp(prefix="frog-doctor-repo-")
        store.register_repo(self.conn, repo_path=repo, name="r", kind=None,
                            status="active", third_party=False, notes=None)
        self.conn.execute("UPDATE repos SET repo_key = NULL WHERE repo_path = ?", (repo,))
        self.conn.execute("DELETE FROM repo_aliases WHERE repo_path = ?", (repo,))
        self.conn.commit()

        r = store.doctor(self.conn)
        repair_codes = {f["code"] for f in r["repairs"]}
        row = self.conn.execute(
            "SELECT repo_key FROM repos WHERE repo_path = ?", (repo,)
        ).fetchone()
        alias_count = self.conn.execute(
            "SELECT COUNT(*) c FROM repo_aliases WHERE repo_path = ?", (repo,)
        ).fetchone()["c"]
        self.assertIn("repo_alias_missing", repair_codes)
        self.assertTrue(row["repo_key"])
        self.assertGreater(alias_count, 0)

    def test_reports_missing_repo_path(self):
        repo = "/tmp/frog-doctor-missing-repo"
        store.register_repo(self.conn, repo_path=repo, name="missing", kind=None,
                            status="active", third_party=False, notes=None)

        r = store.doctor(self.conn, fix=False)
        codes = {f["code"] for f in r["findings"]}
        self.assertIn("repo_path_missing", codes)
        self.assertFalse(r["ok"])

if __name__ == "__main__":
    unittest.main()
