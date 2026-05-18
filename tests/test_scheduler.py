import unittest

from _util import fresh_db
from ragbaz_frog import store


class Scheduler(unittest.TestCase):
    def setUp(self):
        self.conn = store.connect(fresh_db())

    def tearDown(self):
        self.conn.close()

    def _mk(self, slug, prio="p3", wf="idea"):
        store.create_task(self.conn, slug=slug, repo_ref=None, title=slug,
                          why=None, what_text=None, roi_note=None,
                          priority=prio, workflow_status=wf,
                          git_status="not_started", assigned_agent=None,
                          delegation_current=None, delegation_other=None,
                          parent_task_slug=None)

    def _mk_repo_task(self, slug, repo, prio="p3", wf="idea"):
        store.create_task(self.conn, slug=slug, repo_ref=repo, title=slug,
                          why=None, what_text=None, roi_note=None,
                          priority=prio, workflow_status=wf,
                          git_status="not_started", assigned_agent=None,
                          delegation_current=None, delegation_other=None,
                          parent_task_slug=None)

    def test_priority_order_and_done_excluded(self):
        self._mk("low", "p3")
        self._mk("hi", "p0")
        self._mk("gone", "p0", wf="done")
        r = store.task_next(self.conn, agent="claude", limit=5)
        slugs = [t["slug"] for t in r["tasks"]]
        self.assertEqual(slugs[0], "hi")
        self.assertNotIn("gone", slugs)

    def test_dependency_gate(self):
        self._mk("base")
        self._mk("dependent", "p0")
        store.task_add_dependency(self.conn, "dependent", "base", "depends_on")
        r = store.task_next(self.conn, agent="claude", limit=5)
        slugs = [t["slug"] for t in r["tasks"]]
        self.assertNotIn("dependent", slugs, "blocked: base not done")
        self.assertIn("dependent", [s["slug"] for s in r["skipped"]])
        store.task_set_status(self.conn, slug="base",
                              workflow_status="done", git_status=None, note=None)
        r2 = store.task_next(self.conn, agent="claude", limit=5)
        self.assertIn("dependent", [t["slug"] for t in r2["tasks"]])

    def test_conflict_in_progress_blocks(self):
        self._mk("a", "p0")
        self._mk("b", "p0", wf="in_progress")
        store.task_add_conflict(self.conn, "a", "b", "shared file")
        r = store.task_next(self.conn, agent="claude", limit=5)
        self.assertNotIn("a", [t["slug"] for t in r["tasks"]])

    def test_owned_by_other_agent_blocks(self):
        self._mk("x", "p0")
        store.task_assign(self.conn, "x", "codex", None)
        r = store.task_next(self.conn, agent="claude", limit=5)
        self.assertNotIn("x", [t["slug"] for t in r["tasks"]])
        # the owner can still take it
        r2 = store.task_next(self.conn, agent="codex", limit=5)
        self.assertIn("x", [t["slug"] for t in r2["tasks"]])

    def test_task_scope_lock_does_not_block_other_repo_tasks(self):
        repo = "/tmp/frog-scheduler-repo"
        store.register_repo(self.conn, repo_path=repo, name="frog", kind=None,
                            status="active", third_party=False, notes=None)
        self._mk_repo_task("gh-sync", repo, "p0")
        self._mk_repo_task("scheduler-fix", repo, "p1")
        store.task_claim(self.conn, slug="gh-sync", agent="claude")

        r = store.task_next(self.conn, agent="codex", repo_ref=repo, limit=5)
        self.assertIn("scheduler-fix", [t["slug"] for t in r["tasks"]])
        skipped = {s["slug"]: s["reason"] for s in r["skipped"]}
        self.assertEqual(skipped.get("gh-sync"), "owned by claude")


if __name__ == "__main__":
    unittest.main()
