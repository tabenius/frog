import unittest
from _util import fresh_db
from ragbaz_frog import store, tui


def snap_with(conn):
    def mk(slug, wf="idea", prio="p2"):
        store.create_task(conn, slug=slug, repo_ref=None, title=slug,
                          why=None, what_text=None, roi_note=None,
                          priority=prio, workflow_status=wf,
                          git_status="not_started", assigned_agent=None,
                          delegation_current=None, delegation_other=None,
                          parent_task_slug=None)
    mk("a"); mk("b"); mk("c", wf="in_progress"); mk("d", wf="done")
    store.task_add_dependency(conn, "b", "a", "depends_on")
    return store.board_snapshot(conn)


class TuiStateTests(unittest.TestCase):
    def setUp(self):
        self.conn = store.connect(fresh_db())
        self.st = tui.TuiState(snap_with(self.conn), "claude")

    def tearDown(self):
        self.conn.close()

    def test_column_and_row_navigation_wraps(self):
        self.assertEqual(self.st.col, 0)
        self.st.move(-1, 0)               # wrap left -> last column (done)
        self.assertEqual(self.st.col, len(tui._COLS) - 1)
        self.st.move(1, 0)                # wrap right -> first
        self.assertEqual(self.st.col, 0)
        sel0 = self.st.selected()
        self.st.move(0, 1)
        # idea column has a + (b is blocked) -> at least 1 item; row moved or wrapped
        self.assertIsNotNone(self.st.selected())

    def test_selected_and_action_mapping(self):
        # column 0 = idea; pick its first task
        tk = self.st.selected()
        self.assertIsNotNone(tk)
        self.assertEqual(self.st.action("c"), ("claim", tk["slug"]))
        self.assertEqual(self.st.action("f"), ("finish", tk["slug"]))
        self.assertEqual(self.st.action("e"), ("edit", tk["slug"]))
        self.assertIsNone(self.st.action("z"))

    def test_jump_to_next_selects_ready(self):
        # 'a' is ready (no deps); jump should land on it
        self.st.jump_to_next()
        self.assertEqual(self.st.selected()["slug"],
                         self.st.snapshot["ready"][0])

    def test_empty_column_action_is_none(self):
        # move to a column then clear selection scenario: blocked has 'b'
        st = tui.TuiState({"columns": {"idea": [], "blocked": [],
                                       "in_progress": [], "done": []},
                           "ready": []}, "x")
        self.assertIsNone(st.selected())
        self.assertIsNone(st.action("c"))



class TuiRichTests(unittest.TestCase):
    def _state(self, n):
        conn = store.connect(fresh_db())
        for i in range(n):
            store.create_task(conn, slug=f"t{i:02d}", repo_ref=None,
                              title=f"task {i}", why=None, what_text=None,
                              roi_note=None, priority="p2",
                              workflow_status="idea", git_status="not_started",
                              assigned_agent=None, delegation_current=None,
                              delegation_other=None, parent_task_slug=None)
        st = tui.TuiState(store.board_snapshot(conn), "claude")
        conn.close()
        return st

    def test_scroll_keeps_selection_visible(self):
        st = self._state(20)              # 20 tasks in idea (col 0)
        st.col = 0
        st.row = 0
        start, end, above, below = st.scroll(5)
        self.assertEqual((start, above, below), (0, False, True))
        st.row = 12
        start, end, above, below = st.scroll(5)
        self.assertTrue(start <= 12 < end)
        self.assertTrue(above)
        st.to_edge(False)                 # bottom
        start, end, above, below = st.scroll(5)
        self.assertFalse(below)
        self.assertTrue(end == len(st.grid[0]))

    def test_window_returns_offset_slice(self):
        st = self._state(10)
        st.col = 0
        st.row = 7
        start, vis = st.window(0, 4)
        self.assertEqual(len(vis), 4)
        self.assertTrue(start <= 7 < start + 4)

    def test_detail_reports_selected(self):
        conn = store.connect(fresh_db())
        store.create_task(conn, slug="root", repo_ref=None, title="Root",
                          why=None, what_text=None, roi_note=None,
                          priority="p1", workflow_status="idea",
                          git_status="not_started", assigned_agent=None,
                          delegation_current=None, delegation_other=None,
                          parent_task_slug=None)
        store.create_task(conn, slug="leaf", repo_ref=None, title="Leaf",
                          why=None, what_text=None, roi_note=None,
                          priority="p2", workflow_status="idea",
                          git_status="not_started", assigned_agent=None,
                          delegation_current=None, delegation_other=None,
                          parent_task_slug=None)
        store.task_add_dependency(conn, "leaf", "root", "depends_on")
        st = tui.TuiState(store.board_snapshot(conn), "claude")
        conn.close()
        # select the blocked 'leaf'
        for ci, _ in enumerate(tui._COLS):
            for ri, tk in enumerate(st.grid[ci]):
                if tk["slug"] == "leaf":
                    st.col, st.row = ci, ri
        d = st.detail()
        self.assertEqual(d["slug"], "leaf")
        self.assertEqual(d["blockers"], ["root"])
        self.assertFalse(d["ready"])

    def test_move_resets_scroll_on_column_change(self):
        st = self._state(8)
        st.col = 0
        st.row = 6
        st.scroll(3)
        self.assertGreater(st.offset[0], 0)
        st.move(1, 0)                      # change column
        self.assertEqual(st.offset[st.col], 0)
        self.assertEqual(st.row, 0)


class TuiSegmentColors(unittest.TestCase):
    def test_segments_match_board_color_coding(self):
        tk = {"slug": "demo", "priority": "p1", "assigned_agent": "claude",
              "unmet_deps": ["a", "b", "c"]}
        segs = tui.task_segments(tk, ["demo"])
        m = {text.strip().split()[0] if " " in text else text: code
             for text, code in segs}
        codes = {c for _, c in segs}
        # base uses the p1 priority colour, NOT a generic one
        self.assertEqual(segs[0][1], tui._PRIO_C["p1"])
        # board parity: ready=220, agent=39, blockers=203
        self.assertIn(tui._READY_C, codes)
        self.assertIn(tui._AGENT_C, codes)
        self.assertIn(tui._BLOCK_C, codes)
        agent_seg = next(s for s in segs if "◆" in s[0])
        self.assertEqual(agent_seg[1], 39)
        self.assertIn("claude", agent_seg[0])

    def test_segments_minimal_when_no_marks(self):
        tk = {"slug": "x", "priority": "p3"}
        segs = tui.task_segments(tk, [])
        self.assertEqual(len(segs), 1)
        self.assertEqual(segs[0][1], tui._PRIO_C["p3"])

if __name__ == "__main__":
    unittest.main()
