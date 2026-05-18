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


    def test_width_aware_no_early_crop_and_clip(self):
        mk(self.conn, "wtask")
        snap = store.board_snapshot(self.conn)
        # long title
        self.conn.execute("UPDATE tasks SET title=? WHERE slug='wtask'",
                          ("A" * 90,))
        self.conn.commit()
        snap = store.board_snapshot(self.conn)
        wide = main_cli._board_frame(snap, color=False, width=140)
        narrow = main_cli._board_frame(snap, color=False, width=60)
        self.assertIn("A" * 80, wide, "wide terminal must not crop")
        self.assertNotIn("A" * 80, narrow, "narrow must clip")
        self.assertIn("\u2026", narrow, "clip uses an ellipsis")
        for line in narrow.splitlines():
            self.assertLessEqual(len(line), 60, "no line exceeds width")

    def test_blocked_task_shows_blocking_dep_slugs(self):
        mk(self.conn, "root1"); mk(self.conn, "leaf")
        store.task_add_dependency(self.conn, "leaf", "root1", "depends_on")
        frame = main_cli._board_frame(store.board_snapshot(self.conn),
                                      color=False, width=120)
        # the blocked task's line names the actual blocker, not just a count
        bl = [l for l in frame.splitlines() if l.strip().endswith("root1")
              or "\u2190 root1" in l]
        self.assertTrue(bl, f"expected a blocked line naming root1; got:\n{frame}")
        self.assertIn("\u26d3", frame)  # chain glyph present

if __name__ == "__main__":
    unittest.main()
