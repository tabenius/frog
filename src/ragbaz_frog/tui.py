"""frog TUI -- zero-dependency curses control room over board_snapshot.

Feature parity with `frog board` (header + ASCII frog, 4 colored
columns with counts, priority colors, ready/agent/blocker glyphs,
recent-event strip, event-driven refresh) PLUS interactive UX the
board can't have: per-column scrolling, a selected-task inspector
pane, keyboard claim/finish/next, a help overlay, resize-safety.

Pure navigation/scroll/detail logic lives in `TuiState` (unit-tested);
the curses shell is the thin untested renderer.
"""
from __future__ import annotations

import os

from ragbaz_frog import store

_COLS = [("idea", "IDEA"), ("blocked", "BLOCKED"),
         ("in_progress", "IN PROGRESS"), ("done", "DONE")]

# xterm-256 codes shared with `frog board` for a consistent palette.
_COL_C = {"idea": 39, "blocked": 203, "in_progress": 208, "done": 78}
_PRIO_C = {"p0": 196, "p1": 208, "p2": 214, "p3": 245}
_ACCENT, _FROG_C, _DIM = 208, 78, 245

_THEMES = {"ragbaz": True, "mono": False}  # mono => no color


class TuiState:
    """Column-major navigable grid + per-column scroll + selection.
    No curses here -- fully unit-testable."""

    def __init__(self, snapshot: dict, agent: str):
        self.agent = agent
        self.col = 0
        self.row = 0
        self.offset = [0] * len(_COLS)
        self.load(snapshot)

    def load(self, snapshot: dict) -> None:
        self.snapshot = snapshot
        self.grid = [snapshot["columns"].get(key, []) for key, _ in _COLS]
        self.col = max(0, min(self.col, len(_COLS) - 1))
        self._clamp_row()

    def _clamp_row(self) -> None:
        n = len(self.grid[self.col])
        self.row = 0 if n == 0 else max(0, min(self.row, n - 1))

    def counts(self) -> list[int]:
        return [len(c) for c in self.grid]

    def move(self, dcol: int, drow: int) -> None:
        if dcol:
            self.col = (self.col + dcol) % len(_COLS)
            self.row = 0
            self.offset[self.col] = 0
        if drow:
            n = len(self.grid[self.col])
            if n:
                self.row = (self.row + drow) % n
        self._clamp_row()

    def to_edge(self, top: bool) -> None:
        n = len(self.grid[self.col])
        self.row = 0 if (top or not n) else n - 1
        self._clamp_row()

    def scroll(self, body_h: int) -> tuple[int, int, bool, bool]:
        """Keep the selected row inside a body_h window; return
        (start, end, more_above, more_below) for the active column."""
        body_h = max(1, body_h)
        off = self.offset[self.col]
        if self.row < off:
            off = self.row
        elif self.row >= off + body_h:
            off = self.row - body_h + 1
        n = len(self.grid[self.col])
        off = max(0, min(off, max(0, n - body_h)))
        self.offset[self.col] = off
        return off, min(n, off + body_h), off > 0, off + body_h < n

    def window(self, ci: int, body_h: int) -> tuple[int, list]:
        """(start_index, slice) for column ci honoring its scroll offset."""
        body_h = max(1, body_h)
        items = self.grid[ci]
        if ci == self.col:
            off, end, _, _ = self.scroll(body_h)
        else:
            off = min(self.offset[ci], max(0, len(items) - body_h))
            end = min(len(items), off + body_h)
        return off, items[off:end]

    def selected(self) -> dict | None:
        items = self.grid[self.col]
        return items[self.row] if items else None

    def detail(self) -> dict | None:
        tk = self.selected()
        if not tk:
            return None
        return {
            "slug": tk["slug"],
            "title": tk.get("title") or "",
            "priority": tk.get("priority") or "p?",
            "status": tk.get("workflow_status") or "?",
            "agent": tk.get("assigned_agent"),
            "blockers": list(tk.get("unmet_deps") or []),
            "ready": tk["slug"] in self.snapshot.get("ready", []),
        }

    def jump_to_next(self) -> None:
        ready = self.snapshot.get("ready", [])
        if not ready:
            return
        target = ready[0]
        for ci, _ in enumerate(_COLS):
            for ri, tk in enumerate(self.grid[ci]):
                if tk["slug"] == target:
                    self.col, self.row = ci, ri
                    return

    def action(self, key: str):
        tk = self.selected()
        if not tk:
            return None
        if key in ("c", "C"):
            return ("claim", tk["slug"])
        if key in ("f", "F"):
            return ("finish", tk["slug"])
        return None


def _use_color() -> bool:
    return (not os.environ.get("NO_COLOR")
            and _THEMES.get(os.environ.get("FROG_THEME", "ragbaz"), True))


def run(conn, *, agent: str) -> int:  # pragma: no cover - curses shell
    import curses
    from ragbaz_frog.main_cli import _conn_db_path, _db_fingerprint, _FROG_ART

    db_path = _conn_db_path(conn)
    want_color = _use_color()

    def _loop(scr):
        curses.curs_set(0)
        scr.nodelay(True)
        scr.keypad(True)
        pairs: dict[int, int] = {}
        have_color = want_color and curses.has_colors()
        if have_color:
            curses.start_color()
            curses.use_default_colors()

        def C(code, *, bold=False, rev=False):
            a = 0
            if have_color:
                if code not in pairs and len(pairs) < 240:
                    idx = len(pairs) + 1
                    fg = code if curses.COLORS >= 256 else (code % 8)
                    try:
                        curses.init_pair(idx, fg, -1)
                        pairs[code] = idx
                    except curses.error:
                        pairs[code] = 0
                a = curses.color_pair(pairs.get(code, 0))
            if bold:
                a |= curses.A_BOLD
            if rev:
                a |= curses.A_REVERSE
            return a

        def put(y, x, s, attr=0, maxw=None):
            try:
                scr.addnstr(y, x, s, maxw if maxw is not None else max(1, len(s)), attr)
            except curses.error:
                pass

        st = TuiState(store.board_snapshot(conn), agent)
        last_fp = None
        status = "claimed nothing yet"
        show_help = False

        while True:
            fp = _db_fingerprint(db_path)
            if fp != last_fp:
                st.load(store.board_snapshot(conn))
                last_fp = fp
            scr.erase()
            h, w = scr.getmaxyx()

            # ---- header: RAGBAZ/frog + ASCII frog + TASK BOARD ----
            put(0, 2, "RAGBAZ", C(_ACCENT, bold=True))
            put(0, 8, "/frog", C(39, bold=True))
            put(0, 14, "  TASK BOARD", C(_DIM))
            for i, ln in enumerate(_FROG_ART):
                put(1 + i, 2, ln, C(_FROG_C))
            top = 1 + len(_FROG_ART) + 1

            # ---- columns ----
            ncol = len(_COLS)
            colw = max(16, w // ncol)
            detail_h = 4
            body_h = max(1, h - top - detail_h - 2)
            for ci, (key, label) in enumerate(_COLS):
                x = ci * colw
                items = st.grid[ci]
                put(top, x, f"{label} ({len(items)})".ljust(colw - 1),
                    C(_COL_C[key], bold=True), colw - 1)
                start, vis = st.window(ci, body_h)
                if start > 0:
                    put(top + 1, x + colw - 3, "▲", C(_DIM))
                for r, tk in enumerate(vis):
                    gy = top + 2 + r
                    if gy >= top + 2 + body_h:
                        break
                    idx = start + r
                    sel = (ci == st.col and idx == st.row)
                    pc = _PRIO_C.get((tk.get("priority") or "p3").lower(), 245)
                    suffix = ""
                    if tk["slug"] in st.snapshot.get("ready", []):
                        suffix += " ★"
                    if tk.get("assigned_agent"):
                        suffix += f" ◆{tk['assigned_agent']}"
                    if tk.get("unmet_deps"):
                        d = tk["unmet_deps"]
                        suffix += f" ⛓{len(d)}←{','.join(d[:2])}"
                    label_txt = f"{tk['priority']} {tk['slug']}"
                    avail = colw - 2
                    line = (label_txt + suffix)[:avail].ljust(avail)
                    put(gy, x, line, C(pc, bold=sel, rev=sel), avail)
                if start + len(vis) < len(items):
                    put(top + 1 + body_h, x + colw - 3, "▼", C(_DIM))

            # ---- inspector for the selected task ----
            d = st.detail()
            iy = h - detail_h - 1
            put(iy, 0, "─" * (w - 1), C(_DIM))
            if d:
                put(iy + 1, 1,
                    f"{d['priority']} {d['slug']}  {d['title']}"[: w - 2],
                    C(_PRIO_C.get(d['priority'], 245), bold=True))
                meta = f"status={d['status']}  agent={d['agent'] or '-'}"
                if d["ready"]:
                    meta += "  ★ ready"
                put(iy + 2, 1, meta[: w - 2], C(_DIM))
                bl = ("blocked on: " + ", ".join(d["blockers"])
                      if d["blockers"] else "no unmet dependencies")
                put(iy + 3, 1, bl[: w - 2],
                    C(203 if d["blockers"] else _FROG_C))
            # ---- recent strip + help/status ----
            rec = st.snapshot.get("recent", [])
            if rec:
                e = rec[-1]
                put(h - 2, 1,
                    f"recent: {e['created_at'][11:19]} {e['kind']} {e['summary']}"[: w - 2],
                    C(_DIM))
            hint = ("?: help  " if not show_help else "")
            put(h - 1, 1,
                f"{hint}q quit  ←/→ col  ↑/↓ task  c claim  f finish  n next  r refresh   | {status}"[: w - 2],
                C(_DIM))
            if show_help:
                lines = ["KEYS",
                         " ←/→ or Tab  switch column",
                         " ↑/↓         move selection (scrolls)",
                         " g / G       top / bottom of column",
                         " c           claim selected task",
                         " f           finish selected task",
                         " n           jump to scheduler's next pick",
                         " r           force refresh   q  quit"]
                for i, ln in enumerate(lines):
                    put(top + 2 + i, w // 2 - 18, ln.ljust(36),
                        C(_ACCENT, bold=(i == 0), rev=True))
            scr.refresh()

            try:
                ch = scr.getch()
            except curses.error:
                ch = -1
            if ch == -1:
                curses.napms(150)
                continue
            c = chr(ch) if 0 <= ch < 256 else ""
            if c in ("q", "Q"):
                return 0
            if c == "?":
                show_help = not show_help
            elif ch in (curses.KEY_LEFT,) or ch == curses.KEY_BTAB:
                st.move(-1, 0)
            elif ch in (curses.KEY_RIGHT, 9):  # 9 = Tab
                st.move(1, 0)
            elif ch == curses.KEY_UP:
                st.move(0, -1)
            elif ch == curses.KEY_DOWN:
                st.move(0, 1)
            elif c == "g":
                st.to_edge(True)
            elif c == "G":
                st.to_edge(False)
            elif c in ("r", "R"):
                last_fp = None
            elif c in ("n", "N"):
                st.jump_to_next()
            else:
                act = st.action(c)
                if act:
                    verb, slug = act
                    try:
                        if verb == "claim":
                            store.task_claim(conn, slug=slug, agent=agent)
                            status = f"claimed {slug}"
                        else:
                            store.task_finish(conn, slug=slug, agent=agent,
                                              verify=False)
                            status = f"finished {slug}"
                    except Exception as e:
                        status = f"{verb} {slug} failed: {e}"
                    last_fp = None

    return curses.wrapper(_loop)
