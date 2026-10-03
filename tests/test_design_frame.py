"""The frame, drawn against a fake curses window.

`docs/layout.md` requires that resizing across every degradation threshold
leaves no garbled frame. Checking that by eye needs a terminal; checking it by
recording where each `addnstr` lands does not, and it also catches the thing an
eyeball misses: a cell written one column past the right edge, or a pane that
claims more rows than the content region owns.

Every write is recorded with its coordinates, so the assertions below are about
geometry -- which is what the layout grammar is actually about.
"""

import unittest

from ragbaz_frog import design, tui


class FakeWindow:
    """Just enough curses for the draw functions, plus a record of every write
    that landed outside the window."""

    def __init__(self, height: int, width: int) -> None:
        self.height, self.width = height, width
        self.cells: dict[tuple[int, int], str] = {}
        self.escaped: list[tuple[int, int, str]] = []

    def getmaxyx(self):
        return self.height, self.width

    def erase(self) -> None:
        self.cells.clear()
        self.escaped.clear()

    def refresh(self) -> None:
        pass

    def addnstr(self, y, x, text, n, attr=0) -> None:
        for offset, ch in enumerate(str(text)[:n]):
            row, col = y, x + offset
            if not (0 <= row < self.height and 0 <= col < self.width):
                self.escaped.append((row, col, ch))
                continue
            self.cells[(row, col)] = ch

    def line(self, y: int) -> str:
        return "".join(self.cells.get((y, x), " ")
                       for x in range(self.width)).rstrip()

    def frame(self) -> list[str]:
        return [self.line(y) for y in range(self.height)]


class FlatPainter:
    """The colour part is tested elsewhere; here the frame is the subject, so
    attributes are a constant."""

    def attr(self, role: str = "ink.body", *mods: str) -> int:
        return 0

    def put(self, scr, y, x, text, attr=0, maxw=None) -> None:
        limit = maxw if maxw is not None else max(1, scr.getmaxyx()[1] - x)
        if limit <= 0 or y < 0:
            return
        scr.addnstr(y, x, design.truncate(text, limit), limit, attr)


def task(i: int, status: str, **over) -> dict:
    base = {"slug": f"{status[:4]}-{i:02d}", "title": f"A {status} task {i}",
            "priority": ["p0", "p1", "p2", "p3"][i % 4],
            "workflow_status": status, "assigned_agent": "claude",
            "unmet_deps": [], "created_at": "2026-05-19T12:00:00+00:00",
            "status_confidence_at": "2026-05-19T14:37:02+00:00"}
    base.update(over)
    return base


def snapshot(tasks: int = 6) -> dict:
    """Every lane populated, so the frame is exercised with overflow, marks,
    blockers and a real selection rather than an empty board."""
    columns = {"idea": [task(i, "idea") for i in range(tasks)],
               "blocked": [task(i, "blocked", assigned_agent=None,
                                unmet_deps=["root-a", "root-b"][: 1 + i % 2])
                           for i in range(max(1, tasks // 2))],
               "in_progress": [task(i, "in_progress") for i in range(tasks)],
               "done": [task(i, "done") for i in range(max(1, tasks // 3))]}
    ready = [t["slug"] for t in columns["idea"]]
    return {
        "columns": columns, "ready": ready,
        "recent": [{"created_at": "2026-05-19T12:00:00+00:00",
                    "kind": "task.claimed", "summary": "claude claimed it"}],
    }


def repo_snapshot(count: int = 4) -> dict:
    return {
        "repos": [{"repo_path": f"/p/r{i}", "name": f"repo-{i}",
                   "repo_key": f"k{i}", "status": "active", "third_party": False,
                   "units": [f"u{i}a", f"u{i}b"], "actions": ["build", "test"]}
                  for i in range(count)],
        "tree": {"name": "/p", "path": "/p", "dirs": [], "repos": []},
        "boxes": [{"box_id": f"box{i}", "hostname": f"host{i}",
                   "is_local": i == 0, "repos": []} for i in range(2)],
    }


SIZES = [(120, 40), (100, 30), (80, 24), (76, 24), (75, 24), (60, 20),
         (52, 24), (51, 24), (40, 12), (120, 10), (120, 9), (120, 6), (120, 5),
         (120, 4), (200, 60), (37, 8)]


class FrameGeometry(unittest.TestCase):
    def _state(self, tasks=6):
        return tui.TuiState(snapshot(tasks), "claude")

    def _putter(self, win, painter):
        """The shape `run()` closes over: put(y, x, text, attr, maxw)."""
        def put(y, x, text, attr=0, maxw=None):
            painter.put(win, y, x, text, attr, maxw)
        return put

    def _draw_board(self, width, height, tasks=6):
        win = FakeWindow(height, width)
        painter = FlatPainter()
        put = self._putter(win, painter)
        r = design.layout(width, height)
        st = self._state(tasks)
        if r.refuse:
            tui.draw_refusal(put, painter, r)
            return win, r, st
        tui.put_title(win, painter, r, "board", st, tui.RepoView(repo_snapshot()))
        left = tui.draw_board(put, painter, r, st)
        if r.legend.h:
            put(r.legend.y, r.legend.x,
                design.truncate(" " + tui.legend_line("board", r.legend.w - 1),
                                r.legend.w), 0, r.legend.w)
        tui.put_status_line(win, painter, r, left, "4 task(s)")
        return win, r, st

    def test_nothing_is_written_outside_the_window_at_any_size(self):
        for width, height in SIZES:
            with self.subTest(terminal=f"{width}x{height}"):
                win, r, st = self._draw_board(width, height)
                self.assertEqual(win.escaped, [],
                                 f"wrote outside {width}x{height}: "
                                 f"{win.escaped[:4]}")

    def test_the_chrome_rows_are_where_the_grammar_says(self):
        for width, height in SIZES:
            r = design.layout(width, height)
            win, r, st = self._draw_board(width, height)
            with self.subTest(terminal=f"{width}x{height}"):
                if r.refuse:
                    self.assertIn("too small", win.line(0))
                    continue
                self.assertIn("RAGBAZ/frog", win.line(0))
                if r.merged_status:
                    # rung 5: the status line loses its row and moves into the
                    # title bar's right side
                    self.assertIn("task(s)", win.line(0))
                    self.assertEqual(r.status.h, 0)
                else:
                    self.assertIn("task(s)", win.line(height - 2))
                if r.legend.h:
                    self.assertIn("quit", win.line(height - 1))

    def test_the_board_lanes_start_where_the_split_says(self):
        width, height = 120, 40
        win, r, st = self._draw_board(width, height, tasks=40)
        widths = tui.lanes(width, len(tui._COLS))
        self.assertEqual(sum(widths), width)
        header = win.line(r.main.y)
        x = 0
        for (_key, label), lane in zip(tui._COLS, widths):
            with self.subTest(label=label):
                self.assertIn(label, header[x:x + lane])
            x += lane

    def test_every_lane_keeps_its_own_columns_at_every_size(self):
        # The label is truncated when a lane is narrower than the label, which is
        # the honest degradation; what must hold is that each lane's header
        # starts where the split puts it and stays inside its own lane.
        for width, height in SIZES:
            if height < design.MINIMUM_HEIGHT:
                continue
            win, r, st = self._draw_board(width, height)
            header = win.line(design.layout(width, height).main.y)
            x = 0
            for lane, (_key, label) in zip(tui.lanes(width, len(tui._COLS)),
                                           tui._COLS):
                with self.subTest(terminal=f"{width}x{height}", label=label):
                    cells = header[x:x + lane]
                    self.assertTrue(cells.startswith(design.glyph("box_h")))
                    if lane >= len(label) + 4:
                        self.assertIn(label, cells)
                x += lane
            self.assertEqual(x, width)

    def test_selection_is_a_gutter_marker_plus_reverse(self):
        width, height = 100, 30
        win = FakeWindow(height, width)

        class Recording(FlatPainter):
            """Attributes become readable strings so the test can assert on them
            instead of on a curses constant."""

            def __init__(self):
                self.writes = []

            def attr(self, role="ink.body", *mods):
                return " ".join((role,) + mods)

            def put(self, scr, y, x, text, attr=0, maxw=None):
                self.writes.append((y, x, str(text), str(attr)))
                super().put(scr, y, x, text, attr, maxw)

        painter = Recording()
        r = design.layout(width, height)
        st = self._state()
        tui.draw_board(self._putter(win, painter), painter, r, st)

        marker = design.glyph("marker_select")
        marked = [w for w in painter.writes if marker in w[2]]
        self.assertTrue(marked, "no row carries the selection marker")
        for _y, x, _text, attr in marked:
            # the marker is in a gutter column and carries the emphasis role
            self.assertEqual(x, 0)
            self.assertIn("ink.emphasis", attr)
            self.assertIn("reverse", attr)
        # ... and the row itself is reversed, which is the second signal
        row = marked[0][0]
        reversed_on_row = [w for w in painter.writes
                           if w[0] == row and "reverse" in w[3]]
        self.assertTrue(reversed_on_row,
                        "reverse video alone is one signal; the row needs both")

    def test_unselected_rows_carry_no_selection_marker(self):
        width, height = 100, 30
        win = FakeWindow(height, width)
        painter = FlatPainter()
        r = design.layout(width, height)
        st = self._state()
        tui.draw_board(self._putter(win, painter), painter, r, st)
        markers = [pos for pos, ch in win.cells.items()
                   if ch == design.glyph("marker_select")]
        self.assertTrue(markers)
        for row, col in markers:
            lane = 0
            for index, width_ in enumerate(tui.lanes(width, len(tui._COLS))):
                if col < sum(tui.lanes(width, len(tui._COLS))[:index + 1]):
                    lane = index
                    break
            with self.subTest(row=row, lane=lane):
                self.assertEqual(lane, st.col,
                                 "only the selected lane is marked")

    def test_the_refusal_row_is_the_only_thing_drawn_when_too_small(self):
        # height 0 cannot be tested: curses would refuse to start, and there is
        # no row to write the refusal to.
        for height in range(1, design.MINIMUM_HEIGHT):
            win, r, st = self._draw_board(60, height)
            with self.subTest(height=height):
                self.assertTrue(r.refuse)
                self.assertEqual(win.escaped, [])
                self.assertIn("too small", win.line(0))

    def test_the_repos_view_obeys_the_three_pane_grammar(self):
        width, height = 120, 40
        win = FakeWindow(height, width)
        painter = FlatPainter()
        r = design.layout(width, height)
        rv = tui.RepoView(repo_snapshot())
        rv.toggle_boxes()          # the box list is what the nav pane shows
        left = tui.draw_repos(self._putter(win, painter), painter, r, rv)
        self.assertEqual(win.escaped, [])
        self.assertIsNotNone(r.nav)
        self.assertIsNotNone(r.detail)
        self.assertIn("boxes", win.line(r.nav.y))
        self.assertIn("detail", win.line(r.detail.y))
        self.assertIn("box0", win.line(r.main.y))
        self.assertIn("local", win.line(r.detail.y + 1))
        self.assertTrue(left)

    def test_the_repos_view_puts_the_selection_in_the_gutter(self):
        width, height = 120, 40
        win = FakeWindow(height, width)
        painter = FlatPainter()
        r = design.layout(width, height)
        rv = tui.RepoView(repo_snapshot())
        tui.draw_repos(self._putter(win, painter), painter, r, rv)
        self.assertEqual(win.cells.get((r.main.y, r.main.x)),
                         design.glyph("marker_select"))

    def test_the_repos_view_narrows_without_a_nav_or_detail_pane(self):
        for width, height in ((80, 24), (50, 20), (40, 12)):
            win = FakeWindow(height, width)
            painter = FlatPainter()
            r = design.layout(width, height)
            rv = tui.RepoView(repo_snapshot())
            with self.subTest(terminal=f"{width}x{height}"):
                self.assertTrue(tui.draw_repos(self._putter(win, painter),
                                               painter, r, rv))
                self.assertEqual(win.escaped, [])

    def test_the_runtime_view_fits(self):
        lines = [f"runtime line {i} " + "x" * 200 for i in range(500)]
        for width, height in SIZES:
            win = FakeWindow(height, width)
            painter = FlatPainter()
            r = design.layout(width, height)
            if r.refuse:
                continue
            with self.subTest(terminal=f"{width}x{height}"):
                left = tui.draw_runtime(self._putter(win, painter), painter,
                                        r, lines, 12)
                self.assertEqual(win.escaped, [])
                self.assertIn("runtime", left)

    def test_a_long_snapshot_still_fits(self):
        # Enough tasks that every lane overflows, so every lane grows a
        # scrollbar, at every size the grammar names.
        for width, height in SIZES:
            if height < design.MINIMUM_HEIGHT:
                continue
            with self.subTest(terminal=f"{width}x{height}"):
                win, r, st = self._draw_board(width, height, tasks=200)
                self.assertEqual(win.escaped, [])

    def test_the_nav_pane_is_never_empty(self):
        # rung 3: with no box list to show, nav collapses to a one-line hint
        # rather than a blank column.
        rv = tui.RepoView(repo_snapshot())
        self.assertEqual(rv.nav_rows(), [])
        self.assertIn("flat", rv.nav_hint())
        width, height = 120, 24
        win = FakeWindow(height, width)
        painter = FlatPainter()
        r = design.layout(width, height)
        tui.draw_repos(self._putter(win, painter), painter, r, rv)
        self.assertIn("flat", win.line(r.nav.y + 1))

    def test_a_merged_status_line_never_eats_the_title(self):
        # Below 6 rows the status merges into the title bar, but the title bar
        # still identifies the surface: it keeps at least half the row.
        for width, height in ((120, 5), (60, 5), (40, 4), (30, 5)):
            win = FakeWindow(height, width)
            painter = FlatPainter()
            r = design.layout(width, height)
            st = self._state()
            left = tui.draw_board(self._putter(win, painter), painter, r, st)
            tail, right = f"{left}   claimed nothing yet", "6 task(s)"
            reserve = r.title.w // 2 if r.merged_status else 0
            tui.put_title(win, painter, r, "board", st,
                          tui.RepoView(repo_snapshot()), reserve=reserve)
            tui.put_status_line(win, painter, r, tail, right)
            with self.subTest(terminal=f"{width}x{height}"):
                self.assertEqual(win.escaped, [])
                if r.merged_status:
                    title = win.line(0)
                    self.assertIn("RAGBAZ", title,
                                  "the title keeps its half of a merged row")
                    self.assertTrue(title.strip())

    def test_the_help_overlay_fits_inside_the_content_region(self):
        st = self._state()
        for width, height in SIZES:
            if height < design.MINIMUM_HEIGHT:
                continue
            win = FakeWindow(height, width)
            painter = FlatPainter()
            r = design.layout(width, height)
            with self.subTest(terminal=f"{width}x{height}"):
                tui.draw_overlay(win, self._putter(win, painter), painter, r,
                                 st, "board", 0)
                self.assertEqual(win.escaped, [])
                if r.main.h < tui.OVERLAY_MIN_ROWS:
                    self.assertIn("too short", win.line(r.main.y))
        win = FakeWindow(24, 80)
        painter = FlatPainter()
        r = design.layout(80, 24)
        tui.draw_overlay(win, self._putter(win, painter), painter, r, st,
                         "board", 0)
        text = "\n".join(win.frame())
        self.assertIn("KEYS", text)
        self.assertIn("quit", text)

    def test_the_overlay_is_a_closed_box_at_every_width(self):
        st = self._state()
        for width in range(12, 130):
            rows = tui.help_overlay_lines("priority asc", width=width)
            with self.subTest(width=width):
                self.assertEqual(len({len(row) for row in rows}), 1,
                                 "every row of a box is the same width")
                self.assertLessEqual(len(rows[0]), width)
                g = design.glyph
                self.assertTrue(rows[0].startswith(g("box_tl")))
                self.assertTrue(rows[0].endswith(g("box_tr")))
                self.assertTrue(rows[-1].startswith(g("box_bl")))
                self.assertTrue(rows[-1].endswith(g("box_br")))

    def test_the_overlay_names_the_field_scope_without_listing_95_keys(self):
        pages = tui.help_page_count("board")
        text = "\n".join("\n".join(tui.help_overlay_lines("x", view="board",
                                                            page=page))
                         for page in range(pages))
        # `nav` and `view` are gone while a field has focus, `field` is live --
        # the legend says so from the same table the dispatcher reads.
        editing = tui.help_overlay_lines("x", view="board", page=0, editing=True)
        editing_text = "\n".join(editing)
        self.assertIn("printable", editing_text)
        self.assertIn("enter", editing_text)
        self.assertIn("escape", editing_text)
        self.assertIn("[field]", editing_text)
        self.assertNotIn("previous column", editing_text)
        # ... and the non-editing legend is about the non-editing keys.
        self.assertNotIn("printable", text)
        self.assertIn("previous column", text)


if __name__ == "__main__":
    unittest.main()
