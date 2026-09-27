"""Task priorities: one spelling (p0-p3), whatever was typed.

Found 2026-09-27: `task create --priority 2` stored "2", which
_priority_rank could not read, so every such task was scheduled last
(rank 9) and `task next` ignored the priorities; `task edit` meanwhile
refused "2". Both now take 0-3 or p0-p3 and store p0-p3.
"""
import unittest

from _util import fresh_db
from ragbaz_frog import store


def create(conn, slug, priority):
    return store.create_task(
        conn, slug=slug, repo_ref=None, title=slug, why=None, what_text=None,
        roi_note=None, priority=priority, workflow_status="todo",
        git_status="not_started", assigned_agent=None, delegation_current=None,
        delegation_other=None, parent_task_slug=None)


class Priority(unittest.TestCase):
    def setUp(self):
        self.conn = store.connect(fresh_db())

    def tearDown(self):
        self.conn.close()

    def stored(self, slug):
        return self.conn.execute("SELECT priority FROM tasks WHERE slug = ?", (slug,)).fetchone()[0]

    def test_normalize(self):
        for given, expected in [("p2", "p2"), ("P1", "p1"), ("0", "p0"), (3, "p3"), (" p3 ", "p3"), (None, None)]:
            self.assertEqual(store.normalize_priority(given), expected, given)
        for bad in ["p4", "high", "", "-1", "12"]:
            with self.assertRaises(ValueError, msg=bad):
                store.normalize_priority(bad)

    def test_create_and_edit_store_one_spelling(self):
        self.assertTrue(create(self.conn, "a", "2")["ok"])
        self.assertEqual(self.stored("a"), "p2")
        self.assertFalse(create(self.conn, "b", "urgent")["ok"])
        self.assertTrue(store.task_edit(self.conn, "a", priority="1")["ok"])
        self.assertEqual(self.stored("a"), "p1")
        self.assertFalse(store.task_edit(self.conn, "a", priority="p9")["ok"])

    def test_next_ranks_by_priority_including_old_bare_digits(self):
        create(self.conn, "low", "p3")
        create(self.conn, "high", "p1")
        # A row written before the fix, with a bare digit.
        create(self.conn, "legacy", "p2")
        self.conn.execute("UPDATE tasks SET priority = '0' WHERE slug = 'legacy'")
        order = [t["slug"] for t in store.task_next(self.conn, agent="a", limit=3)["tasks"]]
        self.assertEqual(order, ["legacy", "high", "low"])

    def test_the_migration_normalizes_old_rows(self):
        import sqlite3, tempfile, pathlib
        db = str(pathlib.Path(tempfile.mkdtemp()) / "AGENTS.db")
        store.migrate(db)
        conn = sqlite3.connect(db)
        conn.executemany(
            "INSERT INTO tasks(slug, title, priority, workflow_status, git_status, created_at, updated_at, status_confidence_at)"
            " VALUES(?, ?, ?, 'todo', 'not_started', '2026-01-01', '2026-01-01', '2026-01-01')",
            [("a", "a", "2"), ("b", "b", "P1"), ("c", "c", "p3")])
        conn.execute("DELETE FROM schema_migrations WHERE name LIKE '014_%'")
        conn.commit()
        conn.close()
        store.migrate(db)
        conn = sqlite3.connect(db)
        rows = dict(conn.execute("SELECT slug, priority FROM tasks"))
        self.assertEqual(rows, {"a": "p2", "b": "p1", "c": "p3"})


if __name__ == "__main__":
    unittest.main()
