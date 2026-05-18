import unittest
from _util import fresh_db
from ragbaz_frog import store


def mk(conn, slug, wf="idea", prio="p2", title=None):
    store.create_task(conn, slug=slug, repo_ref=None,
                      title=title or slug.title(), why=None, what_text=None,
                      roi_note=None, priority=prio, workflow_status=wf,
                      git_status="not_started", assigned_agent=None,
                      delegation_current=None, delegation_other=None,
                      parent_task_slug=None)


class ExportTodo(unittest.TestCase):
    def setUp(self):
        self.conn = store.connect(fresh_db())

    def tearDown(self):
        self.conn.close()

    def test_flat_ticks_done(self):
        mk(self.conn, "alpha", wf="done", prio="p1")
        mk(self.conn, "beta", wf="idea", prio="p3")
        md = store.export_tasks_markdown(self.conn)["markdown"]
        self.assertIn("- [x] alpha — Alpha (p1)", md)
        self.assertIn("- [ ] beta — Beta (p3)", md)

    def test_status_scope(self):
        mk(self.conn, "a", wf="done")
        mk(self.conn, "b", wf="idea")
        md = store.export_tasks_markdown(self.conn,
                                         workflow_status="done")["markdown"]
        self.assertIn("a", md)
        self.assertNotIn("- [ ] b", md)

    def test_tree_nests_dependents_with_connectors(self):
        mk(self.conn, "root", wf="done")
        mk(self.conn, "mid")
        mk(self.conn, "leaf")
        store.task_add_dependency(self.conn, "mid", "root", "depends_on")
        store.task_add_dependency(self.conn, "leaf", "mid", "depends_on")
        md = store.export_tasks_markdown(self.conn, tree=True)["markdown"]
        lines = md.splitlines()
        self.assertTrue(lines[0].startswith("- [x] root"))
        self.assertTrue(any(l.startswith("└─ - [ ] mid") for l in lines))
        self.assertTrue(any(l.startswith("  └─ - [ ] leaf") for l in lines))
        # connector is to the LEFT of the checkbox
        midline = next(l for l in lines if "mid" in l)
        self.assertLess(midline.index("└─"), midline.index("- ["))

    def test_tree_lists_every_task_once(self):
        mk(self.conn, "x"); mk(self.conn, "y")
        store.task_add_dependency(self.conn, "y", "x", "depends_on")
        md = store.export_tasks_markdown(self.conn, tree=True)["markdown"]
        lines = md.splitlines()
        self.assertEqual(sum(1 for l in lines if " x — " in l), 1)
        self.assertEqual(sum(1 for l in lines if " y — " in l), 1)
        # y nests under its dependency x
        self.assertTrue(any(l.startswith("└─ - [ ] y") for l in lines))


if __name__ == "__main__":
    unittest.main()
