"""frog TUI -- zero-dependency curses control room over board_snapshot.

Feature parity with `frog board` (4 columns with counts, priority emphasis,
ready/agent/blocker marks, recent-event strip, event-driven refresh) PLUS
interactive UX the board can't have: per-column scrolling, a selected-task
inspector, keyboard claim/finish/next, a paginated help overlay, resize-safety.

Styling is the ragbaz design system's, not this file's. Every colour comes from
`ragbaz_frog.design` (which reads the vendored tokens), every character comes
from a glyph key, the frame comes from `docs/layout.md`, and the keys are named
in exactly one table -- `BINDINGS` -- which both the dispatcher and the legend
read. There is no second copy of the key list to drift.

Pure navigation/scroll/detail logic lives in `TuiState` (unit-tested); the
binding table, the legend and the frame are pure too; the curses shell is the
thin untested renderer.
"""
from __future__ import annotations

from dataclasses import dataclass

from ragbaz_frog import design
from ragbaz_frog import store
from ragbaz_frog.runtime_status import runtime_lines

_COLS = [("idea", "IDEA"), ("blocked", "BLOCKED"),
         ("in_progress", "IN PROGRESS"), ("done", "DONE")]

_VIEWS = ("board", "repos", "runtime")
GLOBAL = "*"          # a binding that applies in every view
SCOPES = ("field", "global", "view", "nav")

#: `docs/layout.md` paginates the overlay `help_page` entries per page, but
#: `help_page` is not a key in `tokens.toml` -- see docs/design-system-gaps.md.
HELP_PAGE = 8

_TIME_STATUSES = {
    "done", "finished", "in_progress", "in-progress", "doing", "wip", "active",
}
_DONE_STATUSES = {"done", "finished"}
_STARTED_STATUSES = {"in_progress", "in-progress", "doing", "wip", "active"}
_SORT_KEYS = ("priority", "created", "started", "completed", "alpha")
_PRIO_RANK = {
    "p0": 0, "critical": 0, "urgent": 0,
    "p1": 1, "high": 1,
    "p2": 2, "medium": 2, "normal": 2,
    "p3": 3, "low": 3,
}


# ------------------------------------------------------------------ bindings --

@dataclass(frozen=True)
class Key:
    """One row of the only key table there is.

    `scope` is the whole of "arrows and hjkl, unless something has focus":
    `nav` is suppressed while a field has focus, `field` goes live, and where a
    field binding and a global one claim the same key the field wins. That is
    one predicate in `live_bindings`, not a special case in every view.
    """

    action: str
    keys: tuple[str, ...]
    help: str
    view: str = GLOBAL
    scope: str = "view"


# Every printable ASCII character belongs to a text field while it has focus --
# including the ones that are global actions everywhere else. Listing them as
# data is what makes `q` type a `q` instead of quitting mid-edit.
_FIELD_TEXT = tuple(chr(c) for c in range(0x20, 0x7F))

BINDINGS: tuple[Key, ...] = (
    # --- global: live in every view ----------------------------------------
    Key("quit", ("q", "Q"), "quit", GLOBAL, "global"),
    # Its own binding on purpose: while a field has focus it owns `q`, and a
    # binding is dropped whole when the field claims any of its keys -- so
    # Ctrl-C would go unreachable if it shared an entry with `q`.
    Key("interrupt", ("C-c",), "interrupt", GLOBAL, "global"),
    Key("refresh", ("r", "R"), "force refresh", GLOBAL, "global"),
    Key("view.runtime", ("x",), "runtime / URLs / signing", GLOBAL, "global"),
    Key("view.board", ("v", "V"), "switch board <-> repos", GLOBAL, "global"),
    Key("help", ("?",), "this legend", "board", "view"),
    Key("help.next", ("space",), "legend: next page", "board", "view"),
    Key("help.prev", (",",), "legend: previous page", "board", "view"),
    # --- board --------------------------------------------------------------
    Key("column.prev", ("left",), "previous column", "board", "nav"),
    Key("column.next", ("right", "tab"), "next column", "board", "nav"),
    Key("select.prev", ("up",), "previous task", "board", "nav"),
    Key("select.next", ("down",), "next task", "board", "nav"),
    Key("select.top", ("g",), "top of column", "board", "nav"),
    Key("select.bottom", ("G",), "bottom of column", "board", "nav"),
    Key("sort.cycle", ("s",), "cycle sort key", "board"),
    Key("sort.direction", ("S",), "toggle sort direction", "board"),
    Key("pick.next", ("n", "N"), "scheduler's next pick", "board"),
    Key("task.claim", ("c", "C"), "claim selected task", "board"),
    Key("task.edit", ("e", "E"), "edit title / priority", "board"),
    Key("task.finish", ("f", "F"), "finish selected task", "board"),
    # --- repos --------------------------------------------------------------
    Key("select.prev", ("up",), "previous row", "repos", "nav"),
    Key("select.next", ("down",), "next row", "repos", "nav"),
    Key("select.top", ("g",), "top", "repos", "nav"),
    Key("select.bottom", ("G",), "bottom", "repos", "nav"),
    Key("fold.toggle", ("enter", "space"), "fold / unfold", "repos", "nav"),
    Key("tree.toggle", ("t", "T"), "directory tree", "repos"),
    Key("boxes.toggle", ("b", "B"), "boxes (federation)", "repos"),
    # --- runtime ------------------------------------------------------------
    Key("scroll.up", ("up",), "scroll up", "runtime", "nav"),
    Key("scroll.down", ("down",), "scroll down", "runtime", "nav"),
    # --- field: the title / priority prompt ---------------------------------
    # While it has focus these are live and every nav binding above is not.
    Key("field.text", _FIELD_TEXT, "insert into the field", GLOBAL, "field"),
    Key("field.accept", ("enter",), "accept the value", GLOBAL, "field"),
    Key("field.cancel", ("escape",), "cancel editing", GLOBAL, "field"),
)

#: Which scope wins a key claimed twice. `field` first, always.
_SCOPE_RANK = {scope: rank for rank, scope in enumerate(SCOPES)}


def live_bindings(view: str, *, editing: bool = False) -> list[Key]:
    """The bindings reachable in `view`, in resolution order.

    A field takes over: `nav` and `view` go, `field` arrives, and field
    bindings sort *first* because a field owns the printable keys. Without that,
    the global `q` would silently eat the letter while a title is being typed.
    """
    allowed = ("global", "field") if editing else ("global", "view", "nav")
    live = [b for b in BINDINGS if b.view in (view, GLOBAL) and b.scope in allowed]
    live.sort(key=lambda b: _SCOPE_RANK[b.scope])
    seen: set[str] = set()
    out: list[Key] = []
    for binding in live:
        if any(key in seen for key in binding.keys):
            continue
        seen.update(binding.keys)
        out.append(binding)
    return out


def resolve_key(key: str, *, view: str, editing: bool = False) -> Key | None:
    """The one lookup. The dispatcher and the legend both go through it, so the
    help can never advertise a binding the dispatcher lacks."""
    return next((b for b in live_bindings(view, editing=editing)
                 if key in b.keys), None)


def actions_for(view: str) -> set[str]:
    return {b.action for b in live_bindings(view)}


def legend_line(view: str, width: int, *, editing: bool = False) -> str:
    """The always-visible hint line, built from the *current page* of the
    binding table rather than the full legend."""
    sep = f" {design.glyph('box_v')} "
    parts = []
    for binding in live_bindings(view, editing=editing):
        # A field owns every printable character; the strip names the field's
        # control keys instead of listing ninety-five of them.
        if binding.action == "field.text":
            continue
        part = f"{'/'.join(binding.keys[:2])} {binding.help}"
        if design.visible_len(sep.join(parts + [part])) > width:
            break
        parts.append(part)
    return design.truncate(sep.join(parts) or " ", width)


# --------------------------------------------------------------- task models --

class TuiState:
    """Column-major navigable grid + per-column scroll + selection.
    No curses here -- fully unit-testable."""

    def __init__(self, snapshot: dict, agent: str):
        self.agent = agent
        self.col = 0
        self.row = 0
        self.offset = [0] * len(_COLS)
        self.sort_key = "priority"
        self.sort_desc = False
        self.load(snapshot)

    def load(self, snapshot: dict) -> None:
        selected_slug = None
        if hasattr(self, "grid"):
            selected = self.selected()
            selected_slug = selected["slug"] if selected else None
        self.snapshot = snapshot
        self.grid = [self._sorted(snapshot["columns"].get(key, []))
                     for key, _ in _COLS]
        self.col = max(0, min(self.col, len(_COLS) - 1))
        self._restore_selection(selected_slug)

    def _sort_value(self, tk: dict):
        if self.sort_key == "priority":
            return _PRIO_RANK.get(str(tk.get("priority") or "p3").lower(), 99)
        if self.sort_key == "alpha":
            return str(tk.get("slug") or "").lower()
        if self.sort_key == "created":
            return tk.get("created_at") or ""
        status = (tk.get("workflow_status") or "").lower()
        if self.sort_key == "started":
            return (tk.get("status_confidence_at") or ""
                    if status in _STARTED_STATUSES else "")
        if self.sort_key == "completed":
            return (tk.get("status_confidence_at") or ""
                    if status in _DONE_STATUSES else "")
        return ""

    def _sorted(self, items: list[dict]) -> list[dict]:
        present = []
        missing = []
        for tk in items:
            value = self._sort_value(tk)
            (present if value != "" else missing).append((value, tk))
        present.sort(
            key=lambda item: (item[0], str(item[1].get("slug") or "").lower()),
            reverse=self.sort_desc,
        )
        missing.sort(key=lambda item: str(item[1].get("slug") or "").lower())
        return [tk for _, tk in present + missing]

    def _restore_selection(self, slug: str | None = None) -> None:
        if slug:
            for ci, items in enumerate(self.grid):
                for ri, tk in enumerate(items):
                    if tk["slug"] == slug:
                        self.col, self.row = ci, ri
                        self.offset[self.col] = min(self.offset[self.col], ri)
                        self._clamp_row()
                        return
        self._clamp_row()

    def cycle_sort(self) -> None:
        idx = _SORT_KEYS.index(self.sort_key)
        self.sort_key = _SORT_KEYS[(idx + 1) % len(_SORT_KEYS)]
        selected = self.selected()
        self.grid = [self._sorted(items) for items in self.grid]
        self.offset = [0] * len(_COLS)
        self._restore_selection(selected["slug"] if selected else None)

    def toggle_sort_direction(self) -> None:
        self.sort_desc = not self.sort_desc
        selected = self.selected()
        self.grid = [self._sorted(items) for items in self.grid]
        self.offset = [0] * len(_COLS)
        self._restore_selection(selected["slug"] if selected else None)

    def sort_label(self) -> str:
        arrow = "desc" if self.sort_desc else "asc"
        return f"{self.sort_key} {arrow}"

    def _clamp_row(self) -> None:
        n = len(self.grid[self.col])
        self.row = 0 if n == 0 else max(0, min(self.row, n - 1))

    def counts(self) -> list[int]:
        return [len(c) for c in self.grid]

    def total(self) -> int:
        return sum(self.counts())

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
        if key in ("e", "E"):
            return ("edit", tk["slug"])
        return None


def priority_role(priority: object) -> str:
    """Priority is emphasis, so it takes the ink ramp; the `p0`..`p3` text is
    printed beside it and priority never depends on colour."""
    return design.priority_role(priority)


def task_segments(tk: dict, ready: set | list) -> list[tuple[str, str]]:
    """[(text, role)] for one task row, colour-coded exactly like
    `frog board`: base=priority, check=ready, bullet=agent."""
    g = design.glyph
    prio = (tk.get("priority") or "p?")
    timestamp = task_time(tk)
    prefix = f"{timestamp} " if timestamp else ""
    segs = [(f"{prefix}{prio} {tk['slug']}", priority_role(prio))]
    if tk["slug"] in (ready or ()):
        segs.append((f" {g('check')}", "status.ok"))
    if tk.get("assigned_agent"):
        segs.append((f" {g('bullet')}{tk['assigned_agent']}", "ink.code"))
    return segs


def task_time(tk: dict) -> str:
    status = (tk.get("workflow_status") or "").lower()
    if status not in _TIME_STATUSES:
        return ""
    stamp = (
        tk.get("status_confidence_at")
        or tk.get("updated_at")
        or tk.get("created_at")
        or ""
    )
    return stamp[11:16] if len(stamp) >= 16 else ""


def task_lines(tk: dict, ready: set | list) -> list[list[tuple[str, str]]]:
    g = design.glyph
    lines = [task_segments(tk, ready)]
    if tk.get("unmet_deps"):
        for dep in tk["unmet_deps"]:
            lines.append([(f"  {g('corner')} {g('lock')} {dep}", "status.error")])
    return lines


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

    def nav_rows(self) -> list[dict]:
        """The nav pane: the boxes, when the box view is the one on screen."""
        return self._box_rows() if self.box_mode else []

    def nav_hint(self) -> str:
        """Rung 3 of the ladder: with no box list to show, nav collapses to a
        one-line hint naming what the pane is standing in for."""
        mode = "boxes" if self.box_mode else "tree" if self.tree else "flat"
        return f"{mode} {design.glyph('box_v')} {len(self.visible_rows())} row(s)"

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


# --------------------------------------------------------------------- legend --

def help_page_count(view: str, page_size: int = HELP_PAGE, *,
                    editing: bool = False) -> int:
    """How many pages the legend needs. `docs/layout.md` calls the page size
    `help_page`, which is not a key in `tokens.toml` -- see
    docs/design-system-gaps.md."""
    return max(1, -(-len(live_bindings(view, editing=editing)) // max(1, page_size)))


def overlay_page_size(available: int) -> int:
    """Entries per page that fit `available` rows. The box costs a top border,
    a header, a `sort now` row and a bottom border, so four rows are overhead.
    On a short terminal the legend paginates harder rather than overflowing into
    the status line."""
    return max(1, available - 4)


#: The smallest box that still says something: two borders, a header and the
#: `sort now` row.
OVERLAY_MIN_ROWS = 5


def _overlay_rows(view: str, sort_label: str, page: int, page_size: int,
                  editing: bool) -> tuple[list[str], int]:
    live = live_bindings(view, editing=editing)
    page_size = max(1, page_size)
    pages = max(1, -(-len(live) // page_size))
    page = max(0, min(page, pages - 1))
    rows = [f"KEYS  {view}  {page + 1}/{pages}"]
    for binding in live[page * page_size:(page + 1) * page_size]:
        # `field.text` owns every printable character; listing 95 of them would
        # be noise, so the legend names the set instead. `field.accept` and
        # `field.cancel` have ordinary keys and are listed as such.
        keys = ("printable" if binding.action == "field.text"
                else "/".join(binding.keys[:3]))
        rows.append(f"{keys.ljust(11)}{binding.help}  [{binding.scope}]")
    rows.append(f"{'sort now'.ljust(11)}{sort_label}")
    return rows, pages


def help_overlay_lines(sort_label: str, *, view: str = "board", page: int = 0,
                       width: int | None = None, page_size: int = HELP_PAGE,
                       editing: bool = False) -> list[str]:
    """The paginated legend, rendered *from* `BINDINGS`.

    Generated from the same table the dispatcher resolves against, so it cannot
    describe a binding that does not exist -- which is the entire reason it is
    not a list of hand-typed strings. The frame comes from glyph keys, so the
    ascii set draws `+-|` and never a half-width box.

    `editing=True` renders the legend as it looks while a text field has focus:
    `nav` gone, `field` live. frog's own field is modal and swallows `?`, so the
    TUI only ever draws the non-editing page; the parameter exists because a
    second surface reusing this table will want it and must not rediscover the
    rule.
    """
    g = design.glyph
    pad = " " * design.SPACE[5]   # space.5: the smallest step that is 2 cells
    lines, pages = _overlay_rows(view, sort_label, page, page_size, editing)
    inner = max(design.visible_len(line) for line in lines) + 2 * len(pad)
    inner = max(inner, 30)
    if width:
        # Never wider than the terminal, and never so narrow that the box closes
        # on itself: the floor is the padding plus two cells of content.
        inner = max(2 * len(pad) + 2, min(inner, width - 2))
    title = design.truncate(f" keys {view} {page + 1}/{pages} ", inner)
    top = g("box_tl") + title + g("box_h") * max(
        0, inner - design.visible_len(title)) + g("box_tr")
    span = inner - 2 * len(pad)
    body = [g("box_v") + pad
            + design.truncate(line, span).ljust(span)
            + pad + g("box_v") for line in lines]
    bottom = g("box_bl") + g("box_h") * inner + g("box_br")
    return [top, *body, bottom]


# ------------------------------------------------------------------ the frame --

def _use_color() -> bool:
    """The shared policy: `NO_COLOR` off, `FORCE_COLOR` on and beating it,
    `TERM=dumb` off, not a TTY off, and a colourless theme off because `mono`
    is an explicit request rather than a fallback."""
    return design.colour_on(requested=True)


def lanes(width: int, count: int) -> list[int]:
    """Split `width` into `count` adjacent lanes that sum to exactly `width`.

    Reserving a gutter column instead is the documented trap: the panes then sum
    to `width + gutters` and run off the right edge.
    """
    count = max(1, count)
    base, extra = divmod(max(0, width), count)
    return [base + (1 if i < extra else 0) for i in range(count)]


def repo_row_role(kind: str) -> str:
    return design.REPO_ROW_ROLE.get(kind, "ink.muted")


def _disclosure(expanded: bool) -> str:
    """`dot` open, `dot_empty` closed. There is no horizontal-arrow or
    disclosure glyph key -- see docs/design-system-gaps.md."""
    return design.glyph("dot") if expanded else design.glyph("dot_empty")


def board_status_line(st: "TuiState") -> str:
    """The status line's left half: a one-line answer to "what am I looking
    at", which is what replaces the detail pane when the ladder drops it."""
    detail = st.detail()
    if not detail:
        return f"{design.glyph('dot_empty')} no task selected"
    marks = [design.glyph("check")] if detail["ready"] else []
    if detail["blockers"]:
        marks.append(f"{design.glyph('lock')} {len(detail['blockers'])}")
    return " ".join([
        f"{detail['priority']}",
        detail["slug"],
        detail["status"],
        *(marks or []),
    ])


# --------------------------------------------------------------- curses shell --

class Painter:
    """Resolves roles through the shared ladder, then into curses pairs.

    `mono` never reaches `init_pair` at all, because a colourless theme resolves
    every role to nothing.
    """

    def __init__(self, scr, *, want_color: bool = True) -> None:  # pragma: no cover
        import curses

        self.mode = design.colour_mode() if want_color else "none"
        self.pairs: dict[str, int] = {}
        if self.mode == "none" or not curses.has_colors():
            self.mode = "none"
            return
        curses.start_color()
        curses.use_default_colors()
        # curses has no 24-bit pair, so a terminal advertising truecolor still
        # gets the nearest 256 index, and one advertising 8 colours gets the
        # nearest of those 8. Both are the vendored search, not a guess.
        rung = "ansi16" if curses.COLORS < 256 else self.mode
        for index, role in enumerate(design.ROLE_NAMES, start=1):
            fg = design.curses_pair(role, mode=rung)
            if fg is None:
                continue
            try:
                curses.init_pair(index, fg, -1)
                self.pairs[role] = index
            except curses.error:
                pass

    def attr(self, role: str = "ink.body", *mods: str) -> int:  # pragma: no cover
        import curses

        a = curses.A_NORMAL
        if "dim" in mods:
            a |= curses.A_DIM
        if "bold" in mods:
            a |= curses.A_BOLD
        if "reverse" in mods:
            a |= curses.A_REVERSE
        pair = self.pairs.get(role)
        return curses.color_pair(pair) | a if pair else a

    def put(self, scr, y: int, x: int, text: str, attr: int = 0,  # pragma: no cover
            maxw: int | None = None) -> None:
        import curses

        limit = maxw if maxw is not None else max(1, scr.getmaxyx()[1] - x)
        if limit <= 0 or y < 0:
            return
        try:
            scr.addnstr(y, x, design.truncate(text, limit), limit, attr)
        except curses.error:
            pass


def draw_refusal(put, painter, r) -> None:  # pragma: no cover
    """Rung 6: a garbled frame is worse than a refusal, so the terminal is told
    it is too small and nothing else is drawn."""
    put(0, 0, design.truncate(
        f"terminal too small: need at least {design.MINIMUM_HEIGHT} rows",
        max(0, r.title.w - 1)), painter.attr("status.error", "bold"))


def put_title(scr, painter, r, view: str, st, rv, *, reserve: int = 0) -> None:  # pragma: no cover
    """Title bar: exactly 1 row, bold + reverse, `space.2` of padding at each end
    and a `box_h` filler so the bar reads as continuous.

    One rule about reverse video: the attribute is set *or* an inverted pair is
    painted, never both -- doing both inverts twice and lands back on the
    original colours with a wasted attribute. This uses the attribute.
    """
    g = design.glyph
    pad = " " * design.PAD_CELLS
    parts = [f"{pad}RAGBAZ/frog", view]
    if view == "board":
        parts.append(f"sort {st.sort_label()}")
    elif view == "repos":
        parts.append("boxes" if rv.box_mode else "tree" if rv.tree else "flat")
    bar = f" {g('box_v')} "
    left = bar.join(parts) + " "
    recent = st.snapshot.get("recent", [])
    right = (f" {recent[-1]['created_at'][11:19]} {recent[-1]['kind']} "
             if recent else " ")
    # When the ladder has merged the status line into this row, `reserve` is the
    # number of cells the status will take on the right; the filler stops short
    # of it rather than being painted over a moment later.
    right = "" if reserve else right
    width = max(0, r.title.w - reserve)
    fill = max(0, width - design.visible_len(left) - design.visible_len(right))
    line = design.truncate(left + g("box_h") * fill + right, width)
    painter.put(scr, r.title.y, r.title.x, line,
                painter.attr("ink.invert", "bold", "reverse"), width)


def put_status_line(scr, painter, r, left: str, right: str) -> None:  # pragma: no cover
    """Status line: exactly 1 row, dim, `ink.muted`. Below 6 rows the ladder
    gives it no row at all, and it merges into the title bar's right side --
    the only place left to put it."""
    gap = " " * design.PAD_CELLS
    width = r.title.w
    if r.merged_status:
        # Rung 5: the right half of the title bar, and never more -- the title
        # is the one region that always says which surface this is, so the
        # summary is clipped rather than allowed to grow over it.
        half = width // 2
        tail = design.truncate(f"{left}{gap}{right}", max(1, width - half))
        painter.put(scr, 0, max(0, width - design.visible_len(tail)), tail,
                    painter.attr("ink.muted", "dim"), width)
        return
    line = f"{gap}{left}"
    fill = max(0, width - design.visible_len(line) - design.visible_len(right) - 1)
    painter.put(scr, r.status.y, r.status.x,
                design.truncate(line + " " * fill + right, width),
                painter.attr("ink.muted", "dim"), width)


def draw_board(put, painter, r, st) -> str:  # pragma: no cover
    """Four lanes that sum to exactly the terminal width, one scrollbar column
    each when they overflow, and a two-signal selection: a `marker_select` in
    the gutter *and* reverse video on the row.

    The lanes are the content region rather than a nav/main/detail split: four
    lanes beside a 24-column nav and a 38-column detail leave 14 columns a lane
    at 120x40, below the 16 a task row needs. `docs/layout.md` allows this --
    "identical chrome, different content" -- and it is recorded in
    docs/design-system-gaps.md.
    """
    g = design.glyph
    top, height = r.main.y, max(0, r.main.h)
    widths = lanes(r.title.w, len(_COLS))
    inspector = 4 if height >= 7 else 0
    body_y = top + 1
    body_h = max(1, height - 1 - inspector)

    x = 0
    for ci, (key, label) in enumerate(_COLS):
        width = widths[ci]
        head = f"{g('box_h')} {label} ({len(st.grid[ci])})"
        put(top, x, design.truncate(head, max(0, width - 1)),
            painter.attr(design.COLUMN_ROLE[key], "bold"), max(0, width - 1))
        start, visible = st.window(ci, body_h)
        bar = design.scrollbar(len(visible), max(1, len(st.grid[ci])), body_h)
        bar_col = max(0, width - 1)
        gy = body_y
        for row, tk in enumerate(visible):
            index = start + row
            selected = ci == st.col and index == st.row
            mark = g("marker_select") if selected else " "
            put(gy, x, mark,
                painter.attr("ink.emphasis", *(("reverse",) if selected else ())), 1)
            for line_index, segments in enumerate(task_lines(
                    tk, st.snapshot.get("ready", []))):
                if gy >= body_y + body_h:
                    break
                avail = max(1, width - 1 - (1 if bar else 0))
                cx = 1
                first = selected and line_index == 0
                for text, role in segments:
                    if cx >= avail:
                        break
                    chunk = design.truncate(text, avail - cx)
                    put(gy, x + cx, chunk,
                        painter.attr(role, *(("bold", "reverse") if first else ())),
                        len(chunk))
                    cx += len(chunk)
                if first and cx < avail:
                    put(gy, x + cx, " " * (avail - cx),
                        painter.attr(priority_role(tk.get("priority")), "reverse"),
                        avail - cx)
                gy += 1
            if gy >= body_y + body_h:
                break
        if bar:
            for i, cell in enumerate(bar[:body_h]):
                put(body_y + i, x + bar_col, cell,
                    painter.attr("edge.rule"), 1)
        x += width

    if inspector:
        iy = body_y + body_h
        put(iy, 0, design.truncate(g("box_h") * max(0, r.title.w - 1), r.title.w - 1),
            painter.attr("edge.rule"), r.title.w - 1)
        detail = st.detail()
        if detail:
            put(iy + 1, 1, design.truncate(
                f"{detail['priority']} {detail['slug']}  {detail['title']}",
                r.title.w - 2),
                painter.attr(design.priority_role(detail["priority"]), "bold"),
                r.title.w - 2)
            meta = (f"status {detail['status']}   "
                    f"agent {detail['agent'] or '-'}")
            if detail["ready"]:
                meta += f"   {g('check')} ready"
            put(iy + 2, 1, design.truncate(meta, r.title.w - 2),
                painter.attr("ink.muted", "dim"), r.title.w - 2)
            blockers = (f"blocked on {g('lock')} " + ", ".join(detail["blockers"])
                        if detail["blockers"] else
                        f"{g('check')} no unmet dependencies")
            put(iy + 3, 1, design.truncate(blockers, r.title.w - 2),
                painter.attr("status.error" if detail["blockers"] else "status.ok"),
                r.title.w - 2)
        else:
            put(iy + 1, 1, "no task selected",
                painter.attr("ink.faint", "dim"), r.title.w - 2)
    return board_status_line(st)


def draw_repos(put, painter, r, rv) -> str:  # pragma: no cover
    """nav | main | detail, straight from docs/layout.md: nav is the box list
    when the box view is on screen, detail is the selected row's facts."""
    g = design.glyph
    if r.nav:
        boxes = rv.nav_rows()
        put(r.nav.y, r.nav.x, design.truncate("boxes", r.nav.w),
            painter.attr("ink.muted", "dim"), r.nav.w)
        # Never an empty pane: with no box list the hint says what it stands in
        # for, which is rung 3 of the ladder.
        entries = boxes or [{"kind": "hint", "text": rv.nav_hint()}]
        for i, row in enumerate(entries[:max(0, r.nav.h - 1)]):
            fold = ("" if row["kind"] == "hint"
                    else _disclosure(row.get("expanded")) + " ")
            put(r.nav.y + 1 + i, r.nav.x, design.truncate(
                f"{fold}{row['text']}", r.nav.w),
                painter.attr("ink.faint" if row["kind"] == "hint"
                             else repo_row_role(row["kind"])), r.nav.w)
    rows = rv.visible_rows()
    visible_h = max(1, r.main.h)
    offset = max(0, min(getattr(rv, "_off", 0), max(0, len(rows) - visible_h)))
    if rv.sel < offset:
        offset = rv.sel
    elif rv.sel >= offset + visible_h:
        offset = rv.sel - visible_h + 1
    rv._off = offset
    bar = design.scrollbar(min(visible_h, len(rows)), max(1, len(rows)), visible_h)
    if not rows:
        put(r.main.y, r.main.x + 1, design.truncate(
            "no registered repos (frog repo register <path>)", r.main.w - 2),
            painter.attr("ink.faint", "dim"), r.main.w - 2)
    for i, row in enumerate(rows[offset:offset + visible_h]):
        gy = r.main.y + i
        selected = (offset + i) == rv.sel
        put(gy, r.main.x, g("marker_select") if selected else " ",
            painter.attr("ink.emphasis" if selected else "edge.rule",
                         *(("reverse",) if selected else ())), 1)
        indent = "  " * row["depth"]
        fold = (_disclosure(row["expanded"]) + " ") if row.get("foldable") else ""
        put(gy, r.main.x + 1, design.truncate(f"{indent}{fold}{row['text']}",
                                              r.main.w - 2),
            painter.attr(repo_row_role(row["kind"]),
                         *(("bold", "reverse") if selected else ())), r.main.w - 2)
    if bar:
        for i, cell in enumerate(bar[:visible_h]):
            put(r.main.y + i, r.main.right - 1, cell, painter.attr("edge.rule"), 1)
    if r.detail:
        put(r.detail.y, r.detail.x, design.truncate("detail", r.detail.w),
            painter.attr("ink.muted", "dim"), r.detail.w)
        row = rv.selected()
        if row and row["kind"] == "repo":
            repo = row["repo"]
            put(r.detail.y + 1, r.detail.x, design.truncate(repo["name"], r.detail.w),
                painter.attr(repo_row_role("repo"), "bold"), r.detail.w)
            put(r.detail.y + 2, r.detail.x,
                design.truncate(repo["repo_path"], r.detail.w),
                painter.attr("ink.link"), r.detail.w)
            put(r.detail.y + 3, r.detail.x, design.truncate(
                f"status {repo.get('status') or '-'}    "
                f"{len(repo['units'])} unit(s)    "
                f"{len(repo['actions'])} action(s)", r.detail.w),
                painter.attr("ink.muted", "dim"), r.detail.w)
        elif row:
            put(r.detail.y + 1, r.detail.x, design.truncate(row["text"], r.detail.w),
                painter.attr("ink.muted", "dim"), r.detail.w)
        else:
            put(r.detail.y + 1, r.detail.x, "nothing selected",
                painter.attr("ink.faint"), r.detail.w)
    selected = rv.selected()
    if not selected:
        return f"{g('dot_empty')} nothing selected"
    return f"{selected['kind']}  {selected['text']}"


def draw_runtime(put, painter, r, runtime, offset) -> str:  # pragma: no cover
    g = design.glyph
    offset = max(0, min(offset, max(0, len(runtime) - max(1, r.main.h))))
    bar = design.scrollbar(min(r.main.h, len(runtime) - offset),
                           max(1, len(runtime)), max(1, r.main.h))
    for i, line in enumerate(runtime[offset:offset + r.main.h]):
        put(r.main.y + i, r.main.x, design.truncate(line, r.main.w - 1),
            painter.attr("ink.body"), r.main.w - 1)
    if bar:
        for i, cell in enumerate(bar[:r.main.h]):
            put(r.main.y + i, r.main.right - 1, cell, painter.attr("edge.rule"), 1)
    return (f"runtime   {len(runtime)} line(s)   "
            f"offset {offset + 1}-{min(len(runtime), offset + max(1, r.main.h))}   "
            f"{g('box_v')} refresh: r")


def edit_task(scr, put, painter, st, conn, agent, w, h) -> str:  # pragma: no cover
    """The title / priority prompt -- the one text field the TUI has.

    Every printable key belongs to it while it is open: `BINDINGS` says so with
    a `field`-scoped binding, so the precedence over the global actions is
    stated once and a second surface reusing the table inherits it. curses'
    own `getstr` still does the line editing, which is why the field's control
    keys never reach the dispatcher.
    """
    import curses

    task = st.selected() or {}
    slug = task.get("slug")
    old_title = task.get("title") or ""
    old_priority = task.get("priority") or "p3"
    y = max(2, h - 7)
    put(y, 1, "Edit title (blank keeps current):".ljust(max(0, w - 2)),
        painter.attr("ink.emphasis", "bold"))
    put(y + 1, 1, design.truncate(old_title, max(0, w - 2)),
        painter.attr("ink.muted", "dim"))
    scr.nodelay(False)
    curses.echo()
    try:
        curses.curs_set(1)
    except curses.error:
        pass
    title_raw = scr.getstr(y + 2, 1, max(1, w - 3)).decode("utf-8", "replace").strip()
    put(y + 3, 1,
        f"Edit priority p0/p1/p2/p3 (blank keeps {old_priority}):".ljust(max(0, w - 2)),
        painter.attr("ink.emphasis", "bold"))
    prio_raw = scr.getstr(y + 4, 1, 8).decode("utf-8", "replace").strip()
    curses.noecho()
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    scr.nodelay(True)
    result = store.task_edit(conn, slug, title=title_raw or None,
                             priority=prio_raw or None, actor=agent)
    return f"edited {slug}" if result.get("ok") else result.get(
        "error", f"edit {slug} failed")


def run(conn, *, agent: str) -> int:  # pragma: no cover - curses shell
    import curses
    from ragbaz_frog.main_cli import _conn_db_path, _db_fingerprint

    db_path = _conn_db_path(conn)

    def _loop(scr):
        curses.curs_set(0)
        scr.nodelay(True)
        scr.keypad(True)
        painter = Painter(scr, want_color=_use_color())
        st = TuiState(store.board_snapshot(conn), agent)
        rv = RepoView(store.repo_tree_snapshot(conn))
        view = "board"          # board | repos | runtime
        last_fp = None
        status = "claimed nothing yet"
        show_help = False
        help_page = 0
        runtime = runtime_lines()
        runtime_offset = 0

        def put(y, x, s, attr=0, maxw=None):
            painter.put(scr, y, x, s, attr, maxw)

        while True:
            fp = _db_fingerprint(db_path)
            if fp != last_fp:
                st.load(store.board_snapshot(conn))
                rv.load(store.repo_tree_snapshot(conn))
                last_fp = fp
            scr.erase()
            h, w = scr.getmaxyx()
            r = design.layout(w, h)

            if r.refuse:
                draw_refusal(put, painter, r)
                scr.refresh()
                curses.napms(200)
                continue

            # Content first, chrome second: when the ladder merges the status
            # line into the title bar, the title has to know how many cells the
            # status will take before it fills the row.
            if view == "runtime":
                left = draw_runtime(put, painter, r, runtime, runtime_offset)
            elif view == "board":
                left = draw_board(put, painter, r, st)
            else:
                left = draw_repos(put, painter, r, rv)

            right = (f"page {help_page + 1}/"
                     f"{help_page_count(view, overlay_page_size(max(4, r.main.h)))}"
                     if show_help else f"{st.total()} task(s)")
            tail = f"{left}   {status}"
            # Exactly the half `put_status_line` will use, so the two agree
            # about where one stops and the other starts.
            reserve = r.title.w // 2 if r.merged_status else 0
            put_title(scr, painter, r, view, st, rv, reserve=reserve)

            if r.legend.h:
                # The strip is generated from the same page of the same table
                # the overlay and the dispatcher read, so it cannot advertise a
                # binding that does not exist.
                put(r.legend.y, r.legend.x, design.truncate(
                    " " + legend_line(view, r.legend.w - 1), r.legend.w),
                    painter.attr("ink.muted", "dim"), r.legend.w)

            put_status_line(scr, painter, r, tail, right)

            if show_help:
                draw_overlay(scr, put, painter, r, st, view, help_page)

            scr.refresh()

            try:
                ch = scr.getch()
            except curses.error:
                ch = -1
            if ch == -1:
                curses.napms(150)
                continue
            binding = resolve_key(key_name(ch), view=view)
            if binding is None:
                continue
            action = binding.action

            if action in ("quit", "interrupt"):
                return 0
            if action == "help":
                show_help = not show_help
            elif action == "help.next":
                size = overlay_page_size(max(OVERLAY_MIN_ROWS, r.main.h))
                help_page = (help_page + 1) % help_page_count(view, size)
            elif action == "help.prev":
                size = overlay_page_size(max(4, r.main.h))
                help_page = (help_page - 1) % help_page_count(view, size)
            elif action == "refresh":
                last_fp = None
            elif action == "view.runtime":
                view = "board" if view == "runtime" else "runtime"
                runtime = runtime_lines()
                runtime_offset = 0
            elif action == "view.board":
                view = "repos" if view == "board" else "board"
            elif view == "runtime":
                if action == "scroll.down":
                    runtime_offset += 1
                elif action == "scroll.up":
                    runtime_offset = max(0, runtime_offset - 1)
            elif view == "repos":
                if action == "tree.toggle":
                    rv.toggle_tree()
                elif action == "boxes.toggle":
                    rv.toggle_boxes()
                elif action == "select.next":
                    rv.move(1)
                elif action == "select.prev":
                    rv.move(-1)
                elif action == "fold.toggle":
                    rv.toggle()
                elif action == "select.top":
                    rv.to_edge(True)
                elif action == "select.bottom":
                    rv.to_edge(False)
            else:
                if action == "column.next":
                    st.move(1, 0)
                elif action == "column.prev":
                    st.move(-1, 0)
                elif action == "select.next":
                    st.move(0, 1)
                elif action == "select.prev":
                    st.move(0, -1)
                elif action == "select.top":
                    st.to_edge(True)
                elif action == "select.bottom":
                    st.to_edge(False)
                elif action == "pick.next":
                    st.jump_to_next()
                elif action == "sort.cycle":
                    st.cycle_sort()
                    status = f"sort={st.sort_label()}"
                elif action == "sort.direction":
                    st.toggle_sort_direction()
                    status = f"sort={st.sort_label()}"
                elif action in ("task.claim", "task.finish", "task.edit"):
                    task = st.selected()
                    if not task:
                        continue
                    verb = action.split(".", 1)[1]
                    slug = task["slug"]
                    try:
                        if verb == "claim":
                            store.task_claim(conn, slug=slug, agent=agent)
                            status = f"claimed {slug}"
                        elif verb == "finish":
                            store.task_finish(conn, slug=slug, agent=agent,
                                              verify=False)
                            status = f"finished {slug}"
                        else:
                            status = edit_task(scr, put, painter, st, conn,
                                               agent, w, h)
                    except Exception as exc:
                        status = f"{verb} {slug} failed: {exc}"
                    last_fp = None

    try:
        return curses.wrapper(_loop)
    except KeyboardInterrupt:
        return 0


def draw_overlay(scr, put, painter, r, st, view, page) -> None:  # pragma: no cover
    """The paginated legend overlay, centred, drawn with glyph keys and the
    reverse attribute (never both the attribute and an inverted pair)."""
    if r.main.h < OVERLAY_MIN_ROWS:
        # Nothing honest fits: say so in one line rather than drawing a box that
        # runs over the status line.
        put(r.main.y, r.main.x, design.truncate(
            f"terminal too short for the legend (need {OVERLAY_MIN_ROWS} rows)",
            r.main.w), painter.attr("status.warn", "dim"), r.main.w)
        return
    size = overlay_page_size(r.main.h)
    rows = help_overlay_lines(st.sort_label(), view=view, page=page,
                              width=r.title.w, page_size=size)
    height = len(rows)
    y = r.main.y + max(0, (r.main.h - height) // 2)
    x = max(0, (r.title.w - design.visible_len(rows[0])) // 2)
    for i, row in enumerate(rows):
        put(y + i, x, row, painter.attr("ink.invert", "bold", "reverse"),
            len(row))


_CURSES_NAMES: dict[int, str] | None = None


def key_name(ch: int) -> str:
    """A curses key code -> the name the binding table uses."""
    global _CURSES_NAMES
    import curses

    if _CURSES_NAMES is None:
        _CURSES_NAMES = {
            curses.KEY_LEFT: "left", curses.KEY_RIGHT: "right",
            curses.KEY_UP: "up", curses.KEY_DOWN: "down",
            9: "tab", curses.KEY_BTAB: "backtab",
            10: "enter", 13: "enter", curses.KEY_ENTER: "enter",
            27: "escape", 32: "space", 3: "C-c",
        }
    name = _CURSES_NAMES.get(ch)
    if name is not None:
        return name
    return chr(ch) if 0 <= ch < 256 else f"code{ch}"
