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
        self.assertEqual(r["skipped_summary"]["owner"], 1)
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

    def test_zero_eligible_explains_skipped_categories(self):
        self._mk("done", "p0", wf="done")
        self._mk("base", "p2")
        self._mk("dependent", "p0")
        store.task_add_dependency(self.conn, "dependent", "base", "depends_on")
        store.task_assign(self.conn, "base", "claude", None)

        r = store.task_next(self.conn, agent="codex", limit=5)
        self.assertEqual(r["tasks"], [])
        self.assertEqual(r["eligible"], 0)
        self.assertEqual(r["skipped_summary"]["done"], 1)
        self.assertEqual(r["skipped_summary"]["owner"], 1)
        self.assertEqual(r["skipped_summary"]["deps"], 1)

    def test_task_list_can_filter_done_tasks(self):
        self._mk("todo", "p1")
        self._mk("done", "p0", wf="done")
        r = store.task_list(
            self.conn,
            repo_ref=None,
            workflow_status=None,
            assigned_agent=None,
            include_done=False,
        )
        self.assertEqual([t["slug"] for t in r["tasks"]], ["todo"])
        self.assertFalse(r["include_done"])

    def test_task_edit_updates_fields_and_records_diff(self):
        self._mk("edit-me", "p3")
        r = store.task_edit(
            self.conn,
            "edit-me",
            title="Edited title",
            what_text="Changed scope",
            priority="P1",
            actor="codex",
        )
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["changed"])
        self.assertEqual(r["task"]["title"], "Edited title")
        self.assertEqual(r["task"]["priority"], "p1")
        self.assertEqual(r["changes"]["title"]["before"], "edit-me")
        event = self.conn.execute(
            "SELECT actor, payload_json FROM event_log "
            "WHERE kind = 'task.edited' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        self.assertEqual(event["actor"], "codex")
        self.assertIn('"title"', event["payload_json"])

    def test_task_edit_is_idempotent(self):
        self._mk("stable", "p2")
        before = self.conn.execute(
            "SELECT COUNT(*) AS n FROM event_log WHERE kind = 'task.edited'"
        ).fetchone()["n"]
        r = store.task_edit(self.conn, "stable", title="stable", priority="p2")
        after = self.conn.execute(
            "SELECT COUNT(*) AS n FROM event_log WHERE kind = 'task.edited'"
        ).fetchone()["n"]
        self.assertTrue(r["ok"], r)
        self.assertFalse(r["changed"])
        self.assertEqual(before, after)

    def test_task_edit_validates_priority_and_repo(self):
        self._mk("bad", "p2")
        r = store.task_edit(self.conn, "bad", priority="p9")
        self.assertFalse(r["ok"])
        self.assertIn("priority", r["error"])
        r = store.task_edit(self.conn, "bad", repo_ref="missing")
        self.assertFalse(r["ok"])
        self.assertIn("repo not found", r["error"])


if __name__ == "__main__":
    unittest.main()
