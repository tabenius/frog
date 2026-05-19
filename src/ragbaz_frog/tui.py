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
_READY_C, _AGENT_C, _BLOCK_C = 220, 39, 203  # match frog board

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


_REPO_C, _UNIT_C, _DIR_C, _ACTION_C = 78, 245, 39, 208
_BOX_C = 220


class RepoView:
    """Curses-free state for the repo view: a flat repo list (folded by
    default, expand -> units + actions) that can be switched to a
    foldable directory tree. Fully unit-tested; the shell only renders
    ``visible_rows()`` and forwards keys."""

    def __init__(self, snapshot: dict):
        self.load(snapshot)
        self.tree = False
        self.box_mode = False
        self.expanded: set[str] = set()
        self.sel = 0

    def load(self, snapshot: dict) -> None:
        self.snapshot = snapshot or {"repos": [], "tree":
                                     {"dirs": [], "repos": [], "path": "/"}}

    # ---- row model -------------------------------------------------
    def _repo_rows(self, repo: dict, depth: int,
                   key_prefix: str = "") -> list[dict]:
        rp = repo["repo_path"]
        k = key_prefix + rp
        exp = k in self.expanded
        label = repo["name"]
        if repo.get("remote"):
            label += "  (remote)"
        out = [{"kind": "repo", "key": k, "depth": depth,
                "text": label, "repo": repo, "expanded": exp,
                "foldable": True}]
        if exp:
            if repo.get("remote"):
                out.append({"kind": "unit", "key": k + "::@at",
                            "depth": depth + 1, "foldable": False,
                            "text": "@ " + rp + " (units known on "
                                    "that box only)"})
            for u in repo["units"]:
                out.append({"kind": "unit", "key": k + "::" + u,
                            "depth": depth + 1, "text": u,
                            "foldable": False})
            out.append({"kind": "actions",
                        "key": k + "::@actions", "depth": depth + 1,
                        "text": "actions: " + " ".join(repo["actions"]),
                        "foldable": False})
        return out

    def _walk_tree(self, node: dict, depth: int) -> list[dict]:
        out = []
        for d in node["dirs"]:
            exp = d["path"] in self.expanded
            out.append({"kind": "dir", "key": d["path"], "depth": depth,
                        "text": d["name"] + "/", "expanded": exp,
                        "foldable": True})
            if exp:
                out.extend(self._walk_tree(d, depth + 1))
        for r in node["repos"]:
            out.extend(self._repo_rows(r, depth))
        return out

    def _box_rows(self) -> list[dict]:
        out = []
        for b in self.snapshot.get("boxes", []):
            key = "box::" + b["box_id"]
            exp = key in self.expanded
            tag = "local" if b.get("is_local") else "peer"
            label = (f"{b['box_id']} ({b.get('hostname') or '?'}) "
                     f"[{tag}]  {len(b['repos'])} repo(s)")
            out.append({"kind": "box", "key": key, "depth": 0,
                        "text": label, "expanded": exp,
                        "foldable": True, "box": b})
            if exp:
                for r in b["repos"]:
                    out.extend(self._repo_rows(
                        r, 1, key_prefix=b["box_id"] + "::"))
        return out

    def visible_rows(self) -> list[dict]:
        if self.box_mode:
            return self._box_rows()
        if self.tree:
            return self._walk_tree(self.snapshot["tree"], 0)
        rows = []
        for r in self.snapshot["repos"]:
            rows.extend(self._repo_rows(r, 0))
        return rows

    # ---- navigation ------------------------------------------------
    def _clamp(self) -> None:
        n = len(self.visible_rows())
        self.sel = 0 if n == 0 else max(0, min(self.sel, n - 1))

    def move(self, d: int) -> None:
        n = len(self.visible_rows())
        if n:
            self.sel = (self.sel + d) % n

    def to_edge(self, top: bool) -> None:
        self.sel = 0 if top else max(0, len(self.visible_rows()) - 1)

    def selected(self) -> dict | None:
        rows = self.visible_rows()
        return rows[self.sel] if rows else None

    def toggle(self) -> None:
        row = self.selected()
        if not row or not row.get("foldable"):
            return
        k = row["key"]
        if k in self.expanded:
            self.expanded.discard(k)
        else:
            self.expanded.add(k)
        self._clamp()

    def toggle_tree(self) -> None:
        self.tree = not self.tree
        self.sel = 0

    def toggle_boxes(self) -> None:
        self.box_mode = not self.box_mode
        self.sel = 0


def _use_color() -> bool:
    return (not os.environ.get("NO_COLOR")
            and _THEMES.get(os.environ.get("FROG_THEME", "ragbaz"), True))


def task_segments(tk: dict, ready: set | list) -> list[tuple[str, int]]:
    """[(text, xterm256_code)] for one task row, colour-coded exactly like
    `frog board`: base=priority, ★=220, ◆agent=39, ⛓blockers=203."""
    prio = (tk.get("priority") or "p?")
    segs = [(f"{prio} {tk['slug']}", _PRIO_C.get(prio.lower(), 245))]
    if tk["slug"] in (ready or ()):
        segs.append((" ★", _READY_C))
    if tk.get("assigned_agent"):
        segs.append((f" ◆{tk['assigned_agent']}", _AGENT_C))
    if tk.get("unmet_deps"):
        d = tk["unmet_deps"]
        segs.append((f" ⛓{len(d)}←{','.join(d[:2])}", _BLOCK_C))
    return segs


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
        rv = RepoView(store.repo_tree_snapshot(conn))
        view = "board"  # board | repos
        last_fp = None
        status = "claimed nothing yet"
        show_help = False

        while True:
            fp = _db_fingerprint(db_path)
            if fp != last_fp:
                st.load(store.board_snapshot(conn))
                rv.load(store.repo_tree_snapshot(conn))
                last_fp = fp
            scr.erase()
            h, w = scr.getmaxyx()

            # ---- header: RAGBAZ/frog + ASCII frog + TASK BOARD ----
            put(0, 2, "RAGBAZ", C(_ACCENT, bold=True))
            put(0, 8, "/frog", C(39, bold=True))
            _rlabel = ("  REPOS (boxes)" if rv.box_mode
                       else "  REPOS (tree)" if rv.tree
                       else "  REPOS")
            put(0, 14, "  TASK BOARD" if view == "board"
                else _rlabel, C(_DIM))
            for i, ln in enumerate(_FROG_ART):
                put(1 + i, 2, ln, C(_FROG_C))
            top = 1 + len(_FROG_ART) + 1

            if view == "board":
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
                        avail = colw - 2
                        cx = 0
                        for stext, scode in task_segments(
                                tk, st.snapshot.get("ready", [])):
                            if cx >= avail:
                                break
                            chunk = stext[: avail - cx]
                            put(gy, x + cx, chunk,
                                C(scode, bold=sel, rev=sel), len(chunk))
                            cx += len(chunk)
                        if cx < avail and sel:
                            put(gy, x + cx, " " * (avail - cx),
                                C(_PRIO_C.get((tk.get("priority") or "p3").lower(),
                                              245), rev=True), avail - cx)
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
            else:
                rows = rv.visible_rows()
                vis_h = max(1, h - top - 3)
                off = max(0, min(getattr(rv, "_off", 0),
                                 max(0, len(rows) - vis_h)))
                if rv.sel < off:
                    off = rv.sel
                elif rv.sel >= off + vis_h:
                    off = rv.sel - vis_h + 1
                rv._off = off
                _kc = {"repo": _REPO_C, "unit": _UNIT_C,
                       "dir": _DIR_C, "actions": _ACTION_C,
                       "box": _BOX_C}
                if not rows:
                    put(top, 2, "no registered repos "
                        "(frog repo register <path>)", C(_DIM))
                for i, row in enumerate(rows[off:off + vis_h]):
                    gy = top + i
                    idx = off + i
                    sel = idx == rv.sel
                    marker = ""
                    if row.get("foldable"):
                        marker = "v " if row.get("expanded") else "> "
                    line = ("  " * row["depth"]) + marker + row["text"]
                    put(gy, 2, line[: w - 4],
                        C(_kc.get(row["kind"], _DIM),
                          bold=sel, rev=sel), w - 4)
                d = rv.selected()
                iy = h - 2
                put(iy, 0, "-" * (w - 1), C(_DIM))
                if d and d["kind"] == "repo":
                    r = d["repo"]
                    put(iy + 1, 1, (r["name"] + "  " + r["repo_path"]
                                    + "  [" + str(r.get("status") or "-")
                                    + "]  " + str(len(r["units"]))
                                    + " unit(s)")[: w - 2], C(_REPO_C))
                elif d:
                    put(iy + 1, 1, d["text"][: w - 2], C(_DIM))
                put(h - 1, 1,
                    ("q quit  v board  t tree  b boxes  up/dn move  "
                     "enter/space fold  g/G ends  r refresh"
                     "   | " + status)[: w - 2], C(_DIM))
            scr.refresh()

            try:
                ch = scr.getch()
            except curses.error:
                ch = -1
            if ch == -1:
                curses.napms(150)
                continue
            c = chr(ch) if 0 <= ch < 256 else ""
            if c in ("q", "Q") or ch == 3:  # 3 = Ctrl-C (ETX)
                return 0
            if c in ("v", "V"):
                view = "repos" if view == "board" else "board"
                continue
            if view == "repos":
                if c in ("t", "T"):
                    rv.toggle_tree()
                elif c in ("b", "B"):
                    rv.toggle_boxes()
                elif ch == curses.KEY_UP:
                    rv.move(-1)
                elif ch == curses.KEY_DOWN:
                    rv.move(1)
                elif ch in (10, 13, curses.KEY_ENTER, 32):
                    rv.toggle()
                elif c == "g":
                    rv.to_edge(True)
                elif c == "G":
                    rv.to_edge(False)
                elif c in ("r", "R"):
                    last_fp = None
                continue
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

    try:
        return curses.wrapper(_loop)
    except KeyboardInterrupt:
        return 0
