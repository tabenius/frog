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

if __name__ == "__main__":
    unittest.main()
