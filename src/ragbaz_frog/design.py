"""frog's binding to the ragbaz design system.

One place that answers every styling question both surfaces ask, so the curses
TUI and the ANSI CLI cannot drift apart and neither can hold a literal colour,
a literal glyph or a second colour policy.

The token values live in the vendored `ragbaz_frog._design` (a verbatim copy of
`design-system/generated/ragbaz_design.py`). This module adds no value of its
own: it resolves roles through the vendored ladder, draws the frame described
in `design-system/docs/layout.md`, and maps *frog's* vocabulary onto roles.
The vocabulary map belongs here rather than in either renderer precisely so that
`frog board` and the TUI keep board parity without either one owning the answer.

The six behaviours a conformant renderer provides:

    layout(width, height) -> Regions   the frame + degradation ladder
    resolve(role, mode)    -> bytes    truecolor -> ansi256 -> ansi16 -> none
    glyph(key)             -> str      narrowed nerd -> unicode -> ascii
    bindings()             -> table    in ragbaz_frog.tui (the TUI owns its keys)
    resolve_key(...)       -> action   in ragbaz_frog.tui (same reason)
    truncate(text, width)  -> str      escape- and width-safe

Environment:
    FROG_THEME   one of ragbaz | ragbaz-night | ragbaz-white | mono.
                 Defaults to the token file's `default_theme`. `mono` is a real
                 theme, not a fallback: it emits no colour at all, only bold,
                 dim and reverse.
    NO_COLOR     any value turns colour off.
    FORCE_COLOR  turns it on and beats NO_COLOR.
    TERM=dumb    off. Not a TTY: off.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from ragbaz_frog import _design
from ragbaz_frog import cli_render

#: Every theme the token file ships. `mono` is deliberately colourless.
THEMES = tuple(sorted(_design.THEMES))
DEFAULT_THEME = _design.DEFAULT_THEME
THEME_ENV = "FROG_THEME"
#: The themes that resolve every role to nothing. Not fallbacks: requests.
COLORLESS_THEMES = tuple(_design.COLORLESS_THEMES)

#: Every role name the token file declares, in token order.
ROLE_NAMES = tuple(r["name"] for r in _design.ROLES)

#: Every glyph key the token file declares. Callers name a key from here and
#: never a character, which is what makes the narrowing and `mono` possible.
GLYPH_KEYS = frozenset(_design.GLYPHS)

#: The space scale, in cells, exactly as tokens.toml states it. `space.1` is
#: zero cells on purpose: it is a hairline, not a margin. The first whole-cell
#: step -- and therefore the only one usable as terminal padding -- is
#: `SPACE[2]`, i.e. `space.2`.
SPACE = tuple(_design.SPACE_CELLS)
PAD_CELLS = SPACE[2]

# cli_render.py already owns ANSI-aware width arithmetic for frog; these are
# re-exports, not a second implementation.
ANSI_RE = cli_render.ANSI_RE
RESET = cli_render.RESET

_MODES = ("truecolor", "ansi256", "ansi16", "none")

#: Pane geometry from docs/layout.md. These are counts and clamps, not
#: preferences; a second surface reusing them must not re-derive them.
NAV_FRACTION = 0.20
NAV_MIN, NAV_MAX = 12, 24
DETAIL_FRACTION = 0.40
DETAIL_MIN, DETAIL_MAX = 24, 48
MAIN_FLOOR = 20
DETAIL_WIDTH_MIN = 76
NAV_WIDTH_MIN = 52
LEGEND_HEIGHT_MIN = 10
STATUS_HEIGHT_MIN = 6
MINIMUM_HEIGHT = 4
CHROME_ROWS = 3  # title 1 + status 1 + legend 1; only content flexes


# ------------------------------------------------------------------- themes --

def theme(name: str | None = None) -> str:
    """The active theme. `FROG_THEME` selects it; the token file decides the
    default. An unknown name is a configuration error and falls back to the
    default rather than silently rendering nothing."""
    chosen = name or os.environ.get(THEME_ENV) or DEFAULT_THEME
    return chosen if chosen in _design.THEMES else DEFAULT_THEME


def is_mono(name: str | None = None) -> bool:
    """A colourless theme is a first-class theme, not a degraded one."""
    return theme(name) in COLORLESS_THEMES


# -------------------------------------------------------------------- colour --

def role_hex(role: str, *, name: str | None = None) -> str | None:
    """`role` -> hex, through the active theme. None for a role the theme does
    not define, which is only ever true of `mono`."""
    return _design.role(theme(name), role)


def _index(role: str, mode: str, *, name: str | None = None) -> int | None:
    """role -> the colour index the requested rung can actually render.

    Every rung is the vendored nearest-colour search; nothing here re-derives
    it. Unknown roles return None so a caller can degrade to an attribute
    rather than inventing a colour.
    """
    hexv = role_hex(role, name=name)
    if hexv is None:
        return None
    if mode == "truecolor":
        return _hex_index(hexv)
    if mode == "ansi256":
        return _design.nearest256(hexv)
    if mode == "ansi16":
        return _design.nearest16(hexv)
    return None


def _hex_index(hexv: str) -> int:
    """truecolor is emitted as 24-bit components, so the "index" is the packed
    triple rather than a palette position."""
    r, g, b = _design.rgb(hexv)
    return (r << 16) | (g << 8) | b


def colour_mode(*, name: str | None = None, requested: bool = False,
                floor: str | None = None) -> str:
    """The rung to render at, from the shared policy: NO_COLOR off, FORCE_COLOR
    on and beating NO_COLOR, TERM=dumb off, not a TTY off unless forced, and a
    colourless theme off because it is an explicit request.

    `requested` says the caller has already decided colour is wanted (the CLI's
    `--no-color` switch is the negative of it, and a caller embedding frog may
    set it directly). `floor` is the weakest rung such a caller may be given,
    so an explicitly-enabled caller on a TERM-less stream still gets colour
    rather than silently nothing.
    """
    mode = _design.color_mode(theme=theme(name))
    if mode != "none":
        return mode
    if requested and floor in _MODES:
        return floor
    return "none"


def colour_on(*, name: str | None = None, requested: bool = False) -> bool:
    return colour_mode(name=name, requested=requested) != "none"


def sgr(role: str, *, mode: str | None = None, name: str | None = None,
        bold: bool = False, dim: bool = False) -> str:
    """`role` -> an SGR prefix, at the best rung this output can render.

    In a colourless theme the colour part is absent and only the attributes
    remain: `mono` is bold/dim/reverse, never a colour.
    """
    if mode is None:
        mode = colour_mode(name=name)
    attrs = (("1" if bold else "") + ("2" if dim else "")).strip()
    index = _index(role, mode, name=name) if role else None
    if index is None:
        return f"\033[{attrs}m" if attrs else ""
    if mode == "truecolor":
        colour = f"38;2;{(index >> 16) & 0xff};{(index >> 8) & 0xff};{index & 0xff}"
    elif mode == "ansi16":
        # The 16 base colours are two SGR banks: 30-37 and its bright 90-97.
        colour = str(30 + index) if index < 8 else str(90 + index - 8)
    else:
        colour = f"38;5;{index}"
    return f"\033[{attrs};{colour}m" if attrs else f"\033[{colour}m"


def paint(text: object, role: str, *, mode: str | None = None,
          name: str | None = None, bold: bool = False, dim: bool = False) -> str:
    """`role`-coloured text, or the text untouched when there is no colour."""
    prefix = sgr(role, mode=mode, name=name, bold=bold, dim=dim)
    body = str(text)
    return f"{prefix}{body}{RESET}" if prefix else body


def curses_pair(role: str, *, mode: str | None = None,
                name: str | None = None) -> int | None:
    """`role` -> a foreground index for `curses.init_pair`. None means the
    active theme does not define the role (i.e. mono), and the caller must
    render with attributes alone."""
    if mode is None:
        mode = colour_mode(name=name)
    if mode == "truecolor":
        # curses has no 24-bit pair; the nearest 256 index is the honest rung.
        mode = "ansi256"
    return _index(role, mode, name=name)


# --------------------------------------------------------------------- glyph --

def glyph(key: str) -> str:
    """Resolve a glyph key through the active set (nerd only on explicit
    opt-in, then unicode, then ascii). Raises on an unknown key, so a typo is a
    crash at the call site rather than a box that never draws."""
    return _design.glyph(key)


def glyph_mode() -> str:
    return _design.glyph_mode()


# -------------------------------------------------------------------- width --

# A third width function is exactly the bug that makes coloured tables
# misalign, so these are the *same* objects rather than wrappers around them.
visible_len = cli_render.visible_len
pad_visible = cli_render.pad_visible


def clip_visible(value: object, width: int | None, *, reset: str = RESET) -> str:
    return cli_render.clip_visible(value, width, reset=reset,
                                   mark=glyph("ellipsis"))


def truncate(text: object, width: int) -> str:
    """Clip to `width` cells, escape-safe by construction: escapes are stripped
    first, so a split can never land inside one."""
    if width <= 0:
        return ""
    return cli_render.ANSI_RE.sub("", str(text))[:width]


def clip(text: object, width: int, *, reset: str = RESET) -> str:
    """`truncate`, but visibly: a clipped value must not read as a complete
    one. "not installed" clipped to 9 cells is "not install", a different and
    wrong value."""
    return clip_visible(text, width, reset=reset)


# -------------------------------------------------------------------- layout --

@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    @property
    def right(self) -> int:
        return self.x + self.w


@dataclass(frozen=True)
class Regions:
    """The frame from docs/layout.md. `nav`/`detail`/`legend` are None or
    zero-sized when the degradation ladder has dropped them; `merged_status`
    means the status line lost its row and lives in the title bar's right side;
    `refuse` means the terminal is too small to frame at all."""

    title: Rect
    nav: Rect | None
    main: Rect
    detail: Rect | None
    status: Rect
    legend: Rect
    merged_status: bool = False
    refuse: bool = False


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def layout(width: int, height: int) -> Regions:
    """The frame, as a pure function of `(width, height)`.

    Chrome heights are counts, never percentages: title 1, status 1, legend 1,
    content `height - 3`. Panes are adjacent -- separation is *drawn* with the
    box glyphs, because a reserved gutter column makes the panes sum to
    `width + gutters` and run off the right edge.
    """
    w, h = max(0, int(width)), max(0, int(height))
    if h < MINIMUM_HEIGHT:
        # A garbled frame is worse than a refusal.
        return Regions(Rect(0, 0, w, h), None, Rect(0, 0, w, h), None,
                       Rect(0, 0, 0, 0), Rect(0, 0, 0, 0),
                       merged_status=True, refuse=True)

    merged_status = h < STATUS_HEIGHT_MIN
    show_status = not merged_status
    show_legend = h >= LEGEND_HEIGHT_MIN

    title = Rect(0, 0, w, 1)
    status = Rect(0, max(0, h - 2), w, 1) if show_status else Rect(0, 0, 0, 0)
    legend = Rect(0, max(0, h - 1), w, 1) if show_legend else Rect(0, 0, 0, 0)

    content_y = 1
    content_h = max(0, h - CHROME_ROWS)

    # --- the degradation ladder, applied in order ---------------------------
    show_nav = w >= NAV_WIDTH_MIN
    show_detail = w >= DETAIL_WIDTH_MIN

    nav_w = _clamp(round(w * NAV_FRACTION), NAV_MIN, NAV_MAX) if show_nav else 0
    rest = max(0, w - nav_w)
    detail_w = 0
    if show_detail:
        detail_w = _clamp(round(rest * DETAIL_FRACTION), DETAIL_MIN, DETAIL_MAX)
        if rest - detail_w < MAIN_FLOOR:  # main has a floor of 20 columns
            detail_w = 0
    main_w = rest - detail_w

    nav = Rect(0, content_y, nav_w, content_h) if nav_w else None
    main = Rect(nav_w, content_y, max(0, main_w), content_h)
    detail = Rect(nav_w + main_w, content_y, detail_w, content_h) if detail_w else None
    return Regions(title, nav, main, detail, status, legend, merged_status)


def scrollbar(visible: int, total: int, height: int) -> str | None:
    """One column of scrollbar, and only when the content exceeds the viewport.
    An always-present empty scrollbar trains people to ignore it."""
    if height <= 0 or total <= visible:
        return None
    filled = max(1, round(height * visible / total))
    return glyph("bar_full") * filled + glyph("bar_empty") * (height - filled)


# ------------------------------------------- frog's vocabulary -> roles ------
#
# frog's own concepts mapped onto semantic roles. This lives here so `frog board`
# and the TUI keep board parity from one list. The priority ramp is the *ink*
# ramp, not a red/amber/green ladder: priority is emphasis, and the design
# language maps the type scale to emphasis rather than size or hue. The `p0..p3`
# text is always printed beside it, so priority never depends on colour.

COLUMN_ROLE = {
    "idea": "status.pending",
    "blocked": "status.error",
    "in_progress": "status.info",
    "done": "status.ok",
}

PRIORITY_ROLE = {
    "p0": "ink.emphasis",
    "p1": "ink.body",
    "p2": "ink.muted",
    "p3": "ink.faint",
}

PRIORITY_ALIAS = {
    "critical": "p0", "urgent": "p0",
    "high": "p1",
    "medium": "p2", "normal": "p2",
    "low": "p3",
}

REPO_ROW_ROLE = {
    "repo": "ink.body",
    "unit": "ink.muted",
    "dir": "ink.faint",
    "actions": "ink.link",
    "box": "ink.code",
}

#: The CLI's historical colour vocabulary, kept as *names* so no call site
#: changes, each now resolved to a semantic role.
CLI_ROLE = {
    "muted": "ink.muted",
    "meta": "ink.code",
    "path": "ink.link",
    "success": "status.ok",
    "claim": "status.info",
    "lock": "status.warn",
    "warn": "status.warn",
    "error": "status.error",
    "p0": PRIORITY_ROLE["p0"],
    "p1": PRIORITY_ROLE["p1"],
    "p2": PRIORITY_ROLE["p2"],
    "p3": PRIORITY_ROLE["p3"],
}

#: The CLI's *status* vocabulary, so `_color(value, _status_role(value))` keeps
#: working at ~300 call sites while the colour behind each name comes from a
#: role. Asserted against `STATUS_ROLE` in tests/test_design_conformance.py, so
#: the two tables cannot drift.
CLI_STATUS_NAME = {
    "done": "success", "finished": "success", "released": "success",
    "passed": "success", "active": "success", "clean": "success",
    "ok": "success",
    "in_progress": "claim", "claimed": "claim",
    "assigned": "claim", "running": "claim",
    "blocked": "error", "failed": "error",
    "missing": "error", "error": "error",
    "stale": "warn", "warn": "warn", "warning": "warn",
}

#: Status word -> role. Extensions of the convention the fleet already follows
#: (`sylvae/color.py`: ok->green, failed->red, unavailable->yellow).
STATUS_ROLE = {
    "done": "status.ok", "finished": "status.ok", "released": "status.ok",
    "passed": "status.ok", "active": "status.ok", "clean": "status.ok",
    "ok": "status.ok",
    "in_progress": "status.info", "claimed": "status.info",
    "assigned": "status.info", "running": "status.info",
    "blocked": "status.error", "failed": "status.error",
    "missing": "status.error", "error": "status.error",
    "stale": "status.warn", "warn": "status.warn", "warning": "status.warn",
}

#: `status.*` has a non-colour spelling too, so `mono` is legible rather than
#: merely pale: the glyph plus the word. From docs/layout.md.
STATUS_GLYPH = {
    "status.ok": "check",
    "status.warn": "warn",
    "status.error": "cross",
    "status.info": "dot",
    "status.pending": "dot_empty",
}

EVENT_ROLE = {
    "task.finished": "status.ok",
    "lock.released": "status.ok",
    "task.unblocked": "status.ok",
    "task.claimed": "status.info",
    "task.assigned": "status.info",
}

#: Kinds that only carry a prefix. First match wins, so the exact table above
#: always beats a prefix.
EVENT_PREFIX_ROLE = (
    ("task.dependency", "ink.faint"),
    ("task.status", "ink.faint"),
    ("lock.", "status.warn"),
    ("task.", "ink.muted"),
    ("repo.run", "status.ok"),
    ("repo.", "status.ok"),
    ("file.", "ink.muted"),
    ("workspace.", "ink.muted"),
    ("frog.command", "ink.faint"),
)


def event_role(kind: str) -> str:
    """Event kind -> role. One table for `log`, `ps` and `frog board`, so the
    three cannot disagree about what a kind looks like."""
    exact = EVENT_ROLE.get(kind)
    if exact:
        return exact
    for prefix, role in EVENT_PREFIX_ROLE:
        if kind.startswith(prefix):
            return role
    return "ink.muted"


def priority_role(priority: object) -> str:
    text = str(priority or "p3").strip().lower()
    text = PRIORITY_ALIAS.get(text, text)
    return PRIORITY_ROLE.get(text, PRIORITY_ROLE["p3"])


def status_role(value: object) -> str:
    return STATUS_ROLE.get(str(value or "").strip().lower(), "ink.faint")


def status_name(value: object) -> str:
    """The CLI vocabulary name for a status word, so `_color` keeps its
    signature and the colour still comes from a role."""
    return CLI_STATUS_NAME.get(str(value or "").strip().lower(), "meta")


def cli_status_text(value: object, *, mode: str | None = None,
                    name: str | None = None) -> str:
    """A status word in its role's colour.

    The word is the non-colour signal and is always printed, which is what makes
    the reading survive `mono`, a pipe and a log. `status_glyph_text` adds the
    glyph for the surfaces whose job is to show status *as* status.
    """
    word = str(value or "").strip().lower()
    return paint(word, status_role(word), mode=mode, name=name)


def status_glyph_text(value: object, *, mode: str | None = None,
                      name: str | None = None) -> str:
    """Glyph + word + colour. What docs/layout.md asks a status indicator to
    be, so `mono` reads as a first-class rendering rather than a pale one."""
    word = str(value or "").strip().lower()
    role = status_role(word)
    return paint(f"{glyph(STATUS_GLYPH.get(role, 'dot'))} {word or 'unknown'}",
                 role, mode=mode, name=name)
