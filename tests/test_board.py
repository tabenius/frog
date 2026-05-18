import sys
import unittest
from pathlib import Path

from _util import fresh_db
from ragbaz_frog import store

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
from ragbaz_frog import main_cli


def mk(conn, slug, wf="idea", prio="p2"):
    store.create_task(conn, slug=slug, repo_ref=None, title=f"T {slug}",
                      why=None, what_text=None, roi_note=None, priority=prio,
                      workflow_status=wf, git_status="not_started",
                      assigned_agent=None, delegation_current=None,
                      delegation_other=None, parent_task_slug=None)


class Board(unittest.TestCase):
    def setUp(self):
        self.conn = store.connect(fresh_db())

    def tearDown(self):
        self.conn.close()

    def test_snapshot_buckets_and_ready(self):
        mk(self.conn, "a")                       # idea, no deps -> ready
        mk(self.conn, "b")
        mk(self.conn, "c", wf="in_progress")
        mk(self.conn, "d", wf="done")
        store.task_add_dependency(self.conn, "b", "a", "depends_on")
        snap = store.board_snapshot(self.conn)
        cols = snap["columns"]
        self.assertIn("a", [t["slug"] for t in cols["idea"]])
        self.assertIn("b", [t["slug"] for t in cols["blocked"]])  # unmet dep
        self.assertIn("c", [t["slug"] for t in cols["in_progress"]])
        self.assertIn("d", [t["slug"] for t in cols["done"]])
        self.assertIn("a", snap["ready"])
        self.assertNotIn("b", snap["ready"])

    def test_unblock_shows_in_recent_and_moves_column(self):
        mk(self.conn, "base"); mk(self.conn, "dep")
        store.task_add_dependency(self.conn, "dep", "base", "depends_on")
        store.task_claim(self.conn, slug="base", agent="claude")
        store.task_finish(self.conn, slug="base", agent="claude", verify=False)
        snap = store.board_snapshot(self.conn)
        kinds = [e["kind"] for e in snap["recent"]]
        self.assertIn("task.unblocked", kinds)
        # dep now has all deps done -> ready/idea, not blocked
        self.assertIn("dep", [t["slug"] for t in snap["columns"]["idea"]])
        self.assertIn("dep", snap["ready"])

    def test_frame_renders_columns_plaintext(self):
        mk(self.conn, "x", prio="p0")
        snap = store.board_snapshot(self.conn)
        frame = main_cli._board_frame(snap, color=False)
        self.assertIn("IDEA", frame)
        self.assertIn("IN PROGRESS", frame)
        self.assertIn("DONE", frame)
        self.assertIn("x", frame)
        self.assertNotIn("\033[", frame, "color=False must emit no ANSI")

    def test_frame_colors_when_enabled(self):
        mk(self.conn, "y")
        frame = main_cli._board_frame(store.board_snapshot(self.conn), color=True)
        self.assertIn("\033[38;5;", frame)


if __name__ == "__main__":
    unittest.main()
