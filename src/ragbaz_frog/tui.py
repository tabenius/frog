"""frog TUI -- zero-dependency curses kanban over board_snapshot.

Increment 1: navigable kanban columns + key-driven claim/finish/next,
live (DB-change-driven) refresh, themeable colors. Dependency-graph
pane, lock map and per-workspace tabs are the tracked follow-on.

The navigation/action logic lives in the pure `TuiState` (unit-tested);
the curses render + event loop is the thin untested shell.
"""
from __future__ import annotations

import os

from ragbaz_frog import store

_COLS = [("idea", "IDEA"), ("blocked", "BLOCKED"),
         ("in_progress", "IN PROGRESS"), ("done", "DONE")]

_THEMES = {
    "ragbaz": {"idea": 6, "blocked": 1, "in_progress": 3, "done": 2,
               "accent": 3, "dim": 8},
    "mono": {k: 7 for k in ("idea", "blocked", "in_progress", "done",
                            "accent", "dim")},
}


class TuiState:
    """Pure: flattens the snapshot into a column-major navigable grid and
    resolves key actions to store calls. No curses here."""

    def __init__(self, snapshot: dict, agent: str):
        self.agent = agent
        self.col = 0
        self.row = 0
        self.load(snapshot)

    def load(self, snapshot: dict) -> None:
        self.snapshot = snapshot
        self.grid = [snapshot["columns"].get(key, []) for key, _ in _COLS]
        self.col = max(0, min(self.col, len(_COLS) - 1))
        self._clamp_row()

    def _clamp_row(self) -> None:
        n = len(self.grid[self.col])
        self.row = 0 if n == 0 else max(0, min(self.row, n - 1))

    def move(self, dcol: int, drow: int) -> None:
        if dcol:
            self.col = (self.col + dcol) % len(_COLS)
            self.row = 0
        if drow:
            n = len(self.grid[self.col])
            if n:
                self.row = (self.row + drow) % n
        self._clamp_row()

    def selected(self) -> dict | None:
        items = self.grid[self.col]
        return items[self.row] if items else None

    def jump_to_next(self) -> None:
        """Select the scheduler's top ready task if present on the board."""
        ready = self.snapshot.get("ready", [])
        if not ready:
            return
        target = ready[0]
        for ci, (key, _) in enumerate(_COLS):
            for ri, tk in enumerate(self.grid[ci]):
                if tk["slug"] == target:
                    self.col, self.row = ci, ri
                    return

    def action(self, key: str):
        """Return ('claim'|'finish', slug) or None -- caller executes it."""
        tk = self.selected()
        if not tk:
            return None
        if key in ("c", "C"):
            return ("claim", tk["slug"])
        if key in ("f", "F"):
            return ("finish", tk["slug"])
        return None


def _theme() -> dict:
    return _THEMES.get(os.environ.get("FROG_THEME", "ragbaz"), _THEMES["ragbaz"])


def run(conn, *, agent: str) -> int:  # pragma: no cover - curses shell
    import curses
    from ragbaz_frog.main_cli import _conn_db_path, _db_fingerprint

    db_path = _conn_db_path(conn)
    theme = _theme()
    use_color = not os.environ.get("NO_COLOR")

    def _loop(scr):
        curses.curs_set(0)
        scr.nodelay(True)
        if use_color and curses.has_colors():
            curses.start_color()
            curses.use_default_colors()
            for i in range(1, 9):
                curses.init_pair(i, i % 8, -1)
        st = TuiState(store.board_snapshot(conn), agent)
        last_fp = None
        status = "←/→ column  ↑/↓ task  c claim  f finish  n next  q quit"
        while True:
            fp = _db_fingerprint(db_path)
            if fp != last_fp:
                st.load(store.board_snapshot(conn))
                last_fp = fp
            scr.erase()
            h, w = scr.getmaxyx()
            colw = max(18, w // len(_COLS))
            for ci, (key, label) in enumerate(_COLS):
                x = ci * colw
                items = st.grid[ci]
                cpair = curses.color_pair(theme[key]) if use_color else 0
                scr.addnstr(0, x, f"{label} ({len(items)})", colw - 1,
                            cpair | curses.A_BOLD)
                for ri, tk in enumerate(items):
                    sel = (ci == st.col and ri == st.row)
                    line = f"{tk['priority']} {tk['slug']}"
                    if tk.get("assigned_agent"):
                        line += f" ◆{tk['assigned_agent']}"
                    if tk.get("unmet_deps"):
                        line += f" ⛓{len(tk['unmet_deps'])}"
                    attr = curses.A_REVERSE if sel else 0
                    if 1 + ri < h - 1:
                        scr.addnstr(1 + ri, x, line, colw - 1, attr)
            scr.addnstr(h - 1, 0, status[: w - 1], w - 1,
                        curses.color_pair(theme["dim"]) if use_color else 0)
            scr.refresh()
            try:
                ch = scr.getch()
            except curses.error:
                ch = -1
            if ch == -1:
                curses.napms(200)
                continue
            c = chr(ch) if 0 <= ch < 256 else ""
            if c in ("q", "Q"):
                return 0
            if ch in (curses.KEY_LEFT,):
                st.move(-1, 0)
            elif ch in (curses.KEY_RIGHT,):
                st.move(1, 0)
            elif ch in (curses.KEY_UP,):
                st.move(0, -1)
            elif ch in (curses.KEY_DOWN,):
                st.move(0, 1)
            elif c in ("n", "N"):
                st.jump_to_next()
            else:
                act = st.action(c)
                if act:
                    verb, slug = act
                    try:
                        if verb == "claim":
                            store.task_claim(conn, slug=slug, agent=agent)
                        else:
                            store.task_finish(conn, slug=slug, agent=agent,
                                              verify=False)
                    except Exception as e:  # surface, don't crash the TUI
                        status = f"{verb} {slug} failed: {e}"
                    last_fp = None  # force reload

    return curses.wrapper(_loop)
