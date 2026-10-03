"""Conformance of frog's two terminal surfaces with the ragbaz design system.

Every check here needs no terminal and no database: they read the source, the
vendored tokens and the pure parts of the renderer, which is the point -- a
style rule that can only be checked by looking at a screen is not enforced, it
is hoped for.

The rules being enforced are the design system's, not frog's: no literal colour
or glyph, one binding table, field scope outranking global scope, `mono`
emitting no colour at all, panes that sum to the terminal width, and a
degradation ladder that fires at the documented thresholds.

See `docs/design-system-gaps.md` for what the tokens could not express.
"""

import ast
import importlib.util
import io
import os
import re
import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ragbaz_frog import _design, cli_render, design, main_cli, tui  # noqa: E402

# The files that draw a terminal surface. `_design.py` is excluded on purpose:
# it *is* the token values, vendored verbatim, so it is where a colour is
# allowed to be a literal.
RESTYLED = ("tui.py", "design.py", "main_cli.py", "cli_render.py")


def source(name: str) -> str:
    return (SRC / "ragbaz_frog" / name).read_text()


# --------------------------------------------------------------- no literals --

class NoLiteralColour(unittest.TestCase):
    """A colour is a role, never a value. This is the most direct enforcement
    available without a terminal: grep the surface for a value."""

    #: `#rrggbb` / `#rgb`
    HEX = re.compile(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b")
    #: a literal SGR that selects a colour: \033[31m, \033[90m, \033[38;5;…m
    SGR_COLOUR = re.compile(
        r"\\033\[(?:3[0-7]m|[49][0-7]m|10[0-7]m|38;[25];|48;[25];|58;5;)")
    #: a literal colour index handed to curses rather than resolved from a role
    INIT_PAIR = re.compile(r"init_pair\(\s*-?\d")
    COLOR_PAIR = re.compile(r"color_pair\(\s*\d")

    def test_no_hex_colour_in_any_restyled_file(self):
        for name in RESTYLED:
            for line in source(name).splitlines():
                with self.subTest(file=name, line=line.strip()[:70]):
                    self.assertIsNone(self.HEX.search(line),
                                      f"{name} holds a literal colour")

    def test_no_literal_sgr_colour_in_any_restyled_file(self):
        for name in RESTYLED:
            for line in source(name).splitlines():
                with self.subTest(file=name, line=line.strip()[:70]):
                    self.assertIsNone(self.SGR_COLOUR.search(line),
                                      f"{name} holds a literal SGR colour")

    def test_no_literal_colour_index_reaches_curses(self):
        for name in ("tui.py",):
            text = source(name)
            self.assertIsNone(self.INIT_PAIR.search(text),
                              "init_pair must take a role-resolved index")
            self.assertIsNone(self.COLOR_PAIR.search(text),
                              "color_pair must take a registered pair number")

    def test_every_colour_in_the_package_goes_through_the_tokens(self):
        # No file outside the vendored token module may hold a colour literal,
        # not just the ones this change touched.
        package = SRC / "ragbaz_frog"
        offenders = []
        for path in sorted(package.glob("*.py")):
            if path.name == "_design.py":
                continue
            text = path.read_text()
            if self.HEX.search(text) or self.SGR_COLOUR.search(text):
                offenders.append(path.name)
        self.assertEqual(offenders, [])

    def test_every_role_resolves_through_the_ladder(self):
        for theme in design.THEMES:
            for role in design.ROLE_NAMES:
                with self.subTest(theme=theme, role=role):
                    if design.is_mono(theme):
                        self.assertIsNone(design.role_hex(role, name=theme))
                        continue
                    hexv = design.role_hex(role, name=theme)
                    self.assertRegex(hexv, r"^#[0-9a-f]{6}$")
                    for mode in ("truecolor", "ansi256", "ansi16"):
                        self.assertIsNotNone(design.sgr(role, mode=mode,
                                                        name=theme))

    def test_the_256_and_16_rungs_are_the_vendored_search(self):
        for role in design.ROLE_NAMES:
            hexv = design.role_hex(role)
            self.assertEqual(design._index(role, "ansi256"),
                             _design.nearest256(hexv))
            self.assertEqual(design._index(role, "ansi16"),
                             _design.nearest16(hexv))


# --------------------------------------------------------------- the palette --

class VocabularyMapsToRoles(unittest.TestCase):
    """frog's own words -- a column, a priority, a row kind -- must resolve to
    a role the token file actually declares."""

    MAPS = {
        "COLUMN_ROLE": design.COLUMN_ROLE,
        "PRIORITY_ROLE": design.PRIORITY_ROLE,
        "REPO_ROW_ROLE": design.REPO_ROW_ROLE,
        "CLI_ROLE": design.CLI_ROLE,
    }

    def test_every_mapped_value_is_a_declared_role(self):
        for label, table in self.MAPS.items():
            for key, role in table.items():
                with self.subTest(table=label, key=key):
                    self.assertIn(role, design.ROLE_NAMES)

    def test_every_event_role_is_declared(self):
        for kind, role in design.EVENT_ROLE.items():
            with self.subTest(kind=kind):
                self.assertIn(role, design.ROLE_NAMES)

    def test_every_event_kind_resolves_to_a_declared_role(self):
        kinds = list(design.EVENT_ROLE) + [
            "task.dependency", "task.status", "lock.acquired", "task.whatever",
            "repo.run", "repo.registered", "file.changed", "workspace.sync",
            "frog.command", "something.entirely.new"]
        for kind in kinds:
            with self.subTest(kind=kind):
                self.assertIn(design.event_role(kind), design.ROLE_NAMES)

    def test_an_exact_event_kind_beats_a_prefix_rule(self):
        self.assertEqual(design.event_role("task.finished"), "status.ok")
        self.assertEqual(design.event_role("task.status"), "ink.faint")
        self.assertEqual(design.event_role("lock.acquired"), "status.warn")

    def test_every_status_word_maps_to_a_declared_role(self):
        for word in design.STATUS_ROLE:
            with self.subTest(word=word):
                self.assertIn(design.STATUS_ROLE[word], design.ROLE_NAMES)
                self.assertIn(design.status_role(word), design.ROLE_NAMES)

    def test_cli_status_names_agree_with_the_status_roles(self):
        # Two tables, one answer: the CLI's colour-name vocabulary must land on
        # the same role as the canonical status table, or `ps` and `board`
        # would disagree about what "blocked" looks like.
        for word in design.STATUS_ROLE:
            with self.subTest(word=word):
                name = design.status_name(word)
                self.assertIn(name, design.CLI_ROLE)
                self.assertEqual(design.CLI_ROLE[name], design.STATUS_ROLE[word])

    def test_the_board_and_the_tui_share_one_palette(self):
        # `frog board` and the TUI used to carry two copies of this.
        self.assertEqual(
            [(key, role) for key, _label, role in main_cli._COL_ORDER],
            sorted(design.COLUMN_ROLE.items(), key=lambda kv: [
                "idea", "blocked", "in_progress", "done"].index(kv[0])))
        self.assertEqual({key for key, _ in tui._COLS}, set(design.COLUMN_ROLE))


# ------------------------------------------------------------- the glyph set --

class GlyphKeysOnly(unittest.TestCase):
    GLYPH_CALL = re.compile(r"""(?:design\.)?g(?:lyph)?\(\s*["']([a-z_]+)["']""")

    def test_every_glyph_key_used_by_the_code_exists(self):
        found = set()
        for name in RESTYLED:
            found.update(self.GLYPH_CALL.findall(source(name)))
        # `_design.py` holds the table itself; the callers must all be keys in it.
        self.assertTrue(found, "no glyph calls found -- the check is vacuous")
        for key in sorted(found):
            with self.subTest(key=key):
                self.assertIn(key, design.GLYPH_KEYS)
                self.assertTrue(design.glyph(key))

    def test_every_status_glyph_is_a_key(self):
        for role, key in design.STATUS_GLYPH.items():
            with self.subTest(role=role):
                self.assertIn(key, design.GLYPH_KEYS)

    def test_selection_marker_is_not_the_status_dot(self):
        # docs/layout.md: using `dot` for selection makes a selected row
        # indistinguishable from a status dot.
        self.assertNotEqual(design.glyph("marker_select"), design.glyph("dot"))

    def test_glyph_set_narrows_to_ascii_without_unicode(self):
        class AsciiStream(io.StringIO):
            encoding = "ascii"

            def isatty(self):
                return True

        real, sys.stdout = sys.stdout, AsciiStream()
        try:
            self.assertEqual(design.glyph_mode(), "ascii")
            self.assertEqual(design.glyph("box_h"), "-")
            self.assertEqual(design.glyph("marker_select"), "*")
            for key in design.GLYPH_KEYS:
                with self.subTest(key=key):
                    design.glyph(key).encode("ascii")  # never raises
        finally:
            sys.stdout = real
        self.assertEqual(design.glyph_mode(), "unicode")

    def test_nerd_font_is_opt_in_and_never_detected(self):
        # No env var, a perfectly capable UTF-8 terminal: still not nerd. A TUI
        # whose column widths move because a font changed is worse than a
        # slightly plainer one.
        os.environ.pop("RAGBAZ_NERD_FONT", None)
        self.assertEqual(design.glyph_mode(), "unicode")
        os.environ["RAGBAZ_NERD_FONT"] = "1"
        try:
            self.assertEqual(design.glyph_mode(), "nerd")
            self.assertEqual(design.glyph("marker_select"), "\ueb85")
        finally:
            del os.environ["RAGBAZ_NERD_FONT"]


# ---------------------------------------------------------------- mono theme --

class MonoEmitsNoColour(unittest.TestCase):
    """`mono` is a first-class theme, not a fallback. It emits no colour
    attribute at all -- only bold, dim and reverse."""

    COLOUR_CODE = re.compile(r"\033\[(?:3[0-7]|[49][0-7]|10[0-7]|38;|48;|58;)")

    def test_every_role_resolves_to_nothing(self):
        for role in design.ROLE_NAMES:
            with self.subTest(role=role):
                self.assertEqual(design.sgr(role, name="mono"), "")
                self.assertIsNone(design.role_hex(role, name="mono"))
                self.assertIsNone(design.curses_pair(role, name="mono"))

    def test_no_rung_produces_a_colour_for_mono(self):
        for mode in ("truecolor", "ansi256", "ansi16", "none"):
            for role in design.ROLE_NAMES:
                with self.subTest(mode=mode, role=role):
                    self.assertIsNone(self.COLOUR_CODE.search(
                        design.sgr(role, mode=mode, name="mono")))

    def test_attributes_survive_because_they_are_not_colour(self):
        self.assertEqual(design.sgr("status.error", name="mono", bold=True),
                         "\033[1m")
        self.assertEqual(design.sgr("ink.muted", name="mono", dim=True),
                         "\033[2m")

    def test_mono_beats_force_color(self):
        os.environ["FORCE_COLOR"] = "1"
        try:
            self.assertEqual(design.colour_mode(name="mono"), "none")
        finally:
            del os.environ["FORCE_COLOR"]

    def test_the_board_frame_is_byte_identical_to_the_colourless_rendering(self):
        snapshot = {"columns": {"idea": [{"slug": "a", "priority": "p1",
                                          "title": "A", "assigned_agent": "x",
                                          "unmet_deps": ["b"]}],
                               "blocked": [], "in_progress": [], "done": []},
                   "ready": ["a"],
                   "recent": [{"created_at": "2026-05-18T12:00:00+00:00",
                               "kind": "task.claimed", "summary": "x"}]}
        os.environ["FROG_THEME"] = "mono"
        try:
            mono = main_cli._board_frame(snapshot, color=True, width=100)
        finally:
            del os.environ["FROG_THEME"]
        plain = main_cli._board_frame(snapshot, color=False, width=100)
        self.assertEqual(mono, plain)
        self.assertNotIn("\033[", mono)
        self.assertIsNone(self.COLOUR_CODE.search(mono))


# ------------------------------------------------------------ the frame ------

class FrameGeometry(unittest.TestCase):
    """docs/layout.md. Chrome heights are counts, not percentages; pane widths
    come from the space scale with stated clamps; the ladder is a pure function
    of `(width, height)`."""

    #: the worked examples in docs/layout.md, verbatim
    WORKED = [
        (120, 40, 24, 58, 38),
        (100, 30, 20, 48, 32),
        (80, 24, 16, 38, 26),
        (76, 24, 15, 37, 24),
        (75, 24, 15, 60, 0),
        (60, 20, 12, 48, 0),
        (40, 12, 0, 40, 0),
    ]

    def test_worked_examples_reproduce_exactly(self):
        for width, height, nav, main, detail in self.WORKED:
            with self.subTest(terminal=f"{width}x{height}"):
                r = design.layout(width, height)
                self.assertEqual(r.nav.w if r.nav else 0, nav)
                self.assertEqual(r.main.w, main)
                self.assertEqual(r.detail.w if r.detail else 0, detail)

    def test_panes_sum_to_exactly_the_width(self):
        for width in (20, 40, 52, 60, 75, 76, 80, 100, 120, 200, 240):
            for height in (4, 6, 10, 24, 40, 60):
                with self.subTest(terminal=f"{width}x{height}"):
                    r = design.layout(width, height)
                    panes = [r.main.w] + [p.w for p in (r.nav, r.detail) if p]
                    self.assertEqual(sum(panes), width)

    def test_board_lanes_sum_to_exactly_the_width(self):
        for width in range(20, 241):
            lanes = tui.lanes(width, len(tui._COLS))
            with self.subTest(width=width):
                self.assertEqual(len(lanes), len(tui._COLS))
                self.assertEqual(sum(lanes), width)
                self.assertLessEqual(max(lanes) - min(lanes), 1)

    def test_chrome_is_three_fixed_rows(self):
        for height in range(design.MINIMUM_HEIGHT, 61):
            r = design.layout(120, height)
            with self.subTest(height=height):
                self.assertEqual(r.title.h, 1)
                self.assertEqual(r.title.y, 0)
                self.assertEqual(r.main.y, 1)
                self.assertEqual(r.main.h, height - design.CHROME_ROWS)
                self.assertEqual(r.status.h, 0 if r.merged_status else 1)
                self.assertEqual(r.legend.h, 0 if height < design.LEGEND_HEIGHT_MIN else 1)
                # content never overlaps the chrome
                self.assertLessEqual(r.main.y + r.main.h, height)

    def test_ladder_rung_2_no_detail_below_76(self):
        self.assertIsNotNone(design.layout(76, 24).detail)
        for width in range(1, 76):
            with self.subTest(width=width):
                self.assertIsNone(design.layout(width, 24).detail)

    def test_ladder_rung_3_no_nav_below_52(self):
        self.assertIsNotNone(design.layout(52, 24).nav)
        for width in range(1, 52):
            with self.subTest(width=width):
                self.assertIsNone(design.layout(width, 24).nav)

    def test_nav_is_never_dropped_before_detail(self):
        # "navigation disappearing is more confusing than a detail panel
        # disappearing"
        for width in range(20, 200):
            r = design.layout(width, 24)
            if r.nav is None:
                with self.subTest(width=width):
                    self.assertIsNone(r.detail)

    def test_ladder_rung_4_no_legend_below_10_rows(self):
        self.assertEqual(design.layout(120, 10).legend.h, 1)
        for height in range(design.MINIMUM_HEIGHT, 10):
            with self.subTest(height=height):
                self.assertEqual(design.layout(120, height).legend.h, 0)

    def test_ladder_rung_5_no_status_line_below_6_rows(self):
        self.assertFalse(design.layout(120, 6).merged_status)
        for height in range(design.MINIMUM_HEIGHT, 6):
            with self.subTest(height=height):
                self.assertTrue(design.layout(120, height).merged_status)

    def test_ladder_rung_6_refuses_below_4_rows(self):
        self.assertFalse(design.layout(120, 4).refuse)
        for height in range(0, design.MINIMUM_HEIGHT):
            with self.subTest(height=height):
                self.assertTrue(design.layout(120, height).refuse)

    def test_main_keeps_its_floor_of_20(self):
        for width in range(20, 200):
            r = design.layout(width, 24)
            with self.subTest(width=width):
                self.assertGreaterEqual(r.main.w, min(width, 20))

    def test_padding_is_a_whole_cell_step(self):
        # space.1 rounds to zero cells: right for a hairline, wrong for a
        # margin. The first step that resolves to a cell is space.2.
        self.assertEqual(design.SPACE[1], 0)
        self.assertEqual(design.PAD_CELLS, 1)
        self.assertEqual(design.SPACE[2], design.PAD_CELLS)

    def test_scrollbar_only_when_the_content_overflows(self):
        self.assertIsNone(design.scrollbar(10, 10, 10))
        self.assertIsNone(design.scrollbar(3, 3, 4))
        self.assertIsNone(design.scrollbar(4, 4, 4))
        self.assertIsNone(design.scrollbar(4, 8, 0))
        bar = design.scrollbar(4, 8, 4)
        self.assertEqual(len(bar), 4)
        self.assertTrue(set(bar) <= {design.glyph("bar_full"),
                                      design.glyph("bar_empty")})

    def test_reverse_video_is_one_route_not_two(self):
        # Either the reverse attribute or an inverted pair, never both.
        text = source("tui.py")
        self.assertNotIn('"surface.chrome"', text,
                         "painting an inverted pair *and* the reverse attribute "
                         "inverts twice and lands back on the original colours")


# ------------------------------------------------------------ one key table --

class OneBindingTable(unittest.TestCase):
    def test_every_live_key_resolves_to_its_own_binding(self):
        for view in tui._VIEWS:
            for binding in tui.live_bindings(view):
                for key in binding.keys:
                    with self.subTest(view=view, key=key, action=binding.action):
                        self.assertIs(tui.resolve_key(key, view=view), binding)

    def test_the_legend_only_advertises_keys_that_resolve(self):
        for view in tui._VIEWS:
            for page in range(tui.help_page_count(view)):
                text = "\n".join(tui.help_overlay_lines("x", view=view, page=page))
                for line in text.splitlines():
                    match = re.match(r"\s*([\w/]+)\s{2,}", line)
                    if not match:
                        continue
                    first = match.group(1).split("/")[0]
                    if first in ("printable", "sort", "now") or first.startswith("keys"):
                        continue
                    with self.subTest(view=view, page=page, key=first):
                        self.assertIsNotNone(tui.resolve_key(first, view=view))

    def test_the_overlay_is_generated_from_the_table(self):
        for view in tui._VIEWS:
            pages = tui.help_page_count(view)
            seen = set()
            for page in range(pages):
                seen.update(b.action for b in tui.live_bindings(view))
                for binding in tui.live_bindings(view):
                    text = "\n".join(tui.help_overlay_lines(
                        "x", view=view, page=page))
                    if binding not in tui.live_bindings(view)[page * tui.HELP_PAGE:
                                                             (page + 1) * tui.HELP_PAGE]:
                        continue
                    with self.subTest(view=view, page=page, action=binding.action):
                        self.assertIn(binding.help, text)
            self.assertEqual(len(seen), len(tui.live_bindings(view)))

    def test_the_strip_is_generated_from_the_table(self):
        for view in tui._VIEWS:
            strip = tui.legend_line(view, 4096)
            for binding in tui.live_bindings(view):
                if binding.action == "field.text":
                    continue
                with self.subTest(view=view, action=binding.action):
                    self.assertIn(binding.help, strip)

    def test_the_strip_fits_the_width_it_is_given(self):
        for view in tui._VIEWS:
            for width in (8, 20, 40, 80, 200):
                with self.subTest(view=view, width=width):
                    strip = tui.legend_line(view, width)
                    self.assertLessEqual(design.visible_len(strip), width)

    def test_a_key_is_listed_once(self):
        for view in tui._VIEWS:
            for editing in (False, True):
                seen = set()
                for binding in tui.live_bindings(view, editing=editing):
                    for key in binding.keys:
                        with self.subTest(view=view, key=key, editing=editing):
                            self.assertNotIn(key, seen)
                        seen.add(key)

    def test_every_scopes_value_is_one_of_the_four(self):
        for binding in tui.BINDINGS:
            with self.subTest(action=binding.action):
                self.assertIn(binding.scope, tui.SCOPES)
                self.assertTrue(binding.keys)
                self.assertTrue(binding.help)
                self.assertIn(binding.view, tuple(tui._VIEWS) + (tui.GLOBAL,))

    def test_a_field_binding_outranks_a_global_one_on_the_same_key(self):
        for view in tui._VIEWS:
            collisions = [(b, g) for b in tui.BINDINGS if b.scope == "field"
                          for g in tui.BINDINGS
                          if g.scope == "global" and set(b.keys) & set(g.keys)]
            self.assertTrue(collisions, "no key is claimed by both a field and "
                                        "a global binding -- nothing to assert")
            for field, glob in collisions:
                for key in set(field.keys) & set(glob.keys):
                    with self.subTest(view=view, key=key):
                        self.assertIs(tui.resolve_key(key, view=view,
                                                      editing=True), field)
                        self.assertIs(tui.resolve_key(key, view=view), glob)

    def test_nav_is_suppressed_while_a_field_has_focus(self):
        for view in tui._VIEWS:
            live = tui.live_bindings(view, editing=True)
            with self.subTest(view=view):
                self.assertTrue(live)
                self.assertTrue(any(b.scope == "field" for b in live))
                self.assertFalse(any(b.scope == "nav" for b in live))
                self.assertFalse(any(b.scope == "view" for b in live))

    def test_a_global_without_a_field_claim_stays_reachable_while_editing(self):
        # Only the *claimed* keys move; C-c has no field binding, so it must not
        # be swallowed by the fact that `q` was.
        self.assertEqual(tui.resolve_key("C-c", view="board",
                                         editing=True).action, "interrupt")

    def test_every_declared_action_is_handled_by_the_dispatcher(self):
        handled = self._dispatcher_actions()
        for view in tui._VIEWS:
            for binding in tui.live_bindings(view):
                if binding.scope == "field":
                    continue  # curses' getstr owns the field; see edit_task
                with self.subTest(view=view, action=binding.action):
                    self.assertIn(binding.action, handled)

    def test_the_dispatcher_handles_nothing_the_table_does_not_declare(self):
        declared = {b.action for b in tui.BINDINGS}
        for action in self._dispatcher_actions():
            with self.subTest(action=action):
                self.assertIn(action, declared)

    def test_field_actions_are_not_reachable_from_the_dispatcher(self):
        handled = self._dispatcher_actions()
        for binding in tui.BINDINGS:
            if binding.scope == "field":
                with self.subTest(action=binding.action):
                    self.assertNotIn(binding.action, handled)

    @staticmethod
    def _dispatcher_actions() -> set[str]:
        """Every action literal the curses dispatcher compares against,
        read out of the AST rather than a second hand-kept list."""
        tree = ast.parse(source("tui.py"))
        actions: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare) or not isinstance(
                    node.left, ast.Name) or node.left.id != "action":
                continue
            for op, comparator in zip(node.ops, node.comparators):
                if isinstance(op, (ast.Eq, ast.In)):
                    targets = (comparator.elts if isinstance(comparator, ast.Tuple)
                               else [comparator])
                    for target in targets:
                        if isinstance(target, ast.Constant) and isinstance(
                                target.value, str):
                            actions.add(target.value)
        return actions

    def test_selection_is_two_signals_in_every_list(self):
        # A gutter marker glyph *and* reverse video. Reverse alone vanishes in
        # a log and in a terminal that renders it poorly.
        for name in ("draw_board", "draw_repos"):
            with self.subTest(function=name):
                body = self._function_source("tui.py", name)
                self.assertIn('"marker_select"', body)
                self.assertIn('"reverse"', body)
                self.assertIn("ink.emphasis", body)

    @staticmethod
    def _function_source(filename: str, function: str) -> str:
        tree = ast.parse(source(filename))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == function:
                return ast.get_source_segment(source(filename), node) or ""
        raise AssertionError(f"{function} not found in {filename}")


# ------------------------------------------------------------- the width gate --

class Truncation(unittest.TestCase):
    def test_truncate_never_splits_an_escape(self):
        coloured = "\033[38;5;88mblocked\033[0m on a very long dependency name"
        for width in range(0, 40):
            with self.subTest(width=width):
                out = design.truncate(coloured, width)
                self.assertEqual(len(out), min(width, len(
                    cli_render.ANSI_RE.sub("", coloured))))
                self.assertIsNone(cli_render.ANSI_RE.search(out + "\033"))

    def test_clip_marks_a_clipped_value(self):
        out = design.clip("a dependency slug that is far too long", 12)
        self.assertEqual(cli_render.visible_len(out), 12)
        self.assertTrue(out.endswith(design.glyph("ellipsis")))

    def test_pad_visible_counts_cells_not_escapes(self):
        painted = design.paint("x", "status.error", mode="ansi256")
        self.assertGreater(len(painted), 1)
        self.assertEqual(cli_render.visible_len(painted), 1)
        self.assertEqual(
            cli_render.visible_len(design.pad_visible(painted, 10)), 10)

    def test_one_width_implementation_only(self):
        # cli_render.py owns the arithmetic; design re-exports it rather than
        # adding a third answer to "how wide does this look".
        self.assertIs(design.visible_len, cli_render.visible_len)
        self.assertIs(design.pad_visible, cli_render.pad_visible)
        self.assertIs(design.ANSI_RE, cli_render.ANSI_RE)


# ------------------------------------------------------------ colour policy --

class ColourPolicy(unittest.TestCase):
    """The off-switch is the fleet's, not frog's: `NO_COLOR` (any value) off,
    `FORCE_COLOR` on and beating it, `TERM=dumb` off, not a TTY off."""

    class Stream:
        """Just enough of a stream for the policy: an `isatty` answer and an
        encoding, both of which the policy reads and neither of which can be
        assigned on a real `StringIO`."""

        encoding = "utf-8"

        def __init__(self, tty: bool) -> None:
            self._tty = tty

        def isatty(self) -> bool:
            return self._tty

        def write(self, text: str) -> int:
            return len(text)

        def flush(self) -> None:
            pass

    def _mode(self, env: dict, tty: bool) -> str:
        import contextlib

        saved = {k: os.environ.get(k) for k in
                 ("NO_COLOR", "FORCE_COLOR", "TERM", "COLORTERM", "FROG_THEME")}
        for key in saved:
            os.environ.pop(key, None)
        os.environ.update(env)
        real, sys.stdout = sys.stdout, self.Stream(tty)
        try:
            with contextlib.redirect_stdout(sys.stdout):
                return design.colour_mode()
        finally:
            sys.stdout = real
            for key, value in saved.items():
                os.environ.pop(key, None)
                if value is not None:
                    os.environ[key] = value

    def test_no_color_off_even_when_empty(self):
        for value in ("1", "", "0", "false"):
            with self.subTest(value=value):
                self.assertEqual(self._mode({"NO_COLOR": value}, True), "none")

    def test_force_color_on_without_a_tty(self):
        self.assertNotEqual(
            self._mode({"FORCE_COLOR": "1", "TERM": "xterm-256color"}, False),
            "none")

    def test_term_dumb_off(self):
        self.assertEqual(self._mode({"TERM": "dumb"}, True), "none")
        self.assertEqual(self._mode({"TERM": "dumb"}, False), "none")

    def test_a_pipe_is_plain_text(self):
        # "piped output contains no escape sequences" is a conformance check.
        for term in ("xterm-256color", "xterm"):
            with self.subTest(term=term):
                self.assertEqual(self._mode({"TERM": term}, False), "none")

    def test_a_tty_gets_the_best_rung_it_advertises(self):
        self.assertEqual(
            self._mode({"TERM": "xterm-256color", "COLORTERM": "truecolor"}, True),
            "truecolor")
        self.assertEqual(self._mode({"TERM": "xterm-256color"}, True), "ansi256")
        self.assertEqual(self._mode({"TERM": "xterm"}, True), "ansi16")

    def test_agrees_with_sylvae_on_the_same_environment(self):
        sylvae = self._sylvae_colour()
        if sylvae is None:
            self.skipTest("sylvae is not on this box (set SYLVAE_SRC)")
        tty256 = {"TERM": "xterm-256color"}
        cases = [
            (dict(tty256), True), (dict(tty256), False),
            ({"TERM": "xterm"}, True), ({"TERM": "xterm"}, False),
            ({"TERM": "dumb"}, True), ({"TERM": "dumb"}, False),
            ({"TERM": "dumb", "FORCE_COLOR": "1"}, True),
            ({"TERM": "dumb", "FORCE_COLOR": "1"}, False),
            ({"TERM": "xterm-256color", "NO_COLOR": "1"}, True),
            ({"TERM": "xterm-256color", "NO_COLOR": ""}, True),
            ({"TERM": "xterm-256color", "NO_COLOR": "1"}, False),
            # `NO_COLOR` together with `FORCE_COLOR` is the one documented
            # disagreement; see the next test and docs/design-system-gaps.md.
        ]
        for env, tty in cases:
            with self.subTest(env=env, tty=tty):
                self.assertEqual(self._mode(env, tty) != "none",
                                 self._sylvae_on(env, tty, sylvae),
                                 f"frog and sylvae disagree on {env} tty={tty}")

    def test_diverges_from_sylvae_only_where_the_design_system_decided(self):
        sylvae = self._sylvae_colour()
        if sylvae is None:
            self.skipTest("sylvae is not on this box (set SYLVAE_SRC)")
        # 1. Both set: sylvae's ladder tests NO_COLOR first, so it turns colour
        #    off; the design system decided in prose that FORCE_COLOR wins and
        #    generated that into the module we are forbidden to edit. Recorded
        #    in docs/design-system-gaps.md rather than forked here.
        env = {"TERM": "xterm-256color", "NO_COLOR": "1", "FORCE_COLOR": "1"}
        self.assertEqual(self._mode(env, True), "ansi256")
        self.assertFalse(self._sylvae_on(env, True, sylvae))
        # 2. No TERM at all: sylvae answers a yes/no and says yes; the ladder has
        #    to name a rung and there is none to name.
        self.assertEqual(self._mode({"TERM": ""}, True), "none")
        self.assertTrue(self._sylvae_on({"TERM": ""}, True, sylvae))
        self.assertEqual(self._mode({"TERM": "", "FORCE_COLOR": "1"}, True),
                         "none")
        self.assertTrue(self._sylvae_on({"TERM": "", "FORCE_COLOR": "1"},
                                        True, sylvae))

    @staticmethod
    def _sylvae_colour():
        here = Path(__file__).resolve()
        candidates = [Path(p) for p in (
            os.environ.get("SYLVAE_SRC", ""),) if p]
        candidates += [here.parents[2] / "sylvae" / "src",
                       here.parents[3] / "sylvae" / "src"]
        for root in candidates:
            module = root / "sylvae" / "color.py"
            if not module.exists():
                continue
            spec = importlib.util.spec_from_file_location("sylvae_color_ref",
                                                          module)
            loaded = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(loaded)
            return loaded
        return None

    def _sylvae_on(self, env: dict, tty: bool, sylvae) -> bool:
        saved = {k: os.environ.get(k) for k in ("NO_COLOR", "FORCE_COLOR", "TERM")}
        for key in saved:
            os.environ.pop(key, None)
        os.environ.update(env)
        try:
            return bool(sylvae.color_enabled(ColourPolicy.Stream(tty)))
        finally:
            for key, value in saved.items():
                os.environ.pop(key, None)
                if value is not None:
                    os.environ[key] = value


# ------------------------------------------------------------------ the theme --

class ThemeSelection(unittest.TestCase):
    def test_default_is_the_token_files_default(self):
        os.environ.pop("FROG_THEME", None)
        self.assertEqual(design.theme(), design.DEFAULT_THEME)
        self.assertEqual(design.DEFAULT_THEME, _design.DEFAULT_THEME)

    def test_all_four_shipped_themes_are_available(self):
        self.assertEqual(set(design.THEMES),
                         {"mono", "ragbaz", "ragbaz-night", "ragbaz-white"})

    def test_env_var_selects_the_theme(self):
        os.environ["FROG_THEME"] = "ragbaz-night"
        try:
            self.assertEqual(design.theme(), "ragbaz-night")
            self.assertEqual(design.role_hex("ink.body"),
                             _design.THEMES["ragbaz-night"]["ink.body"])
        finally:
            del os.environ["FROG_THEME"]

    def test_an_unknown_theme_falls_back_rather_than_rendering_nothing(self):
        self.assertEqual(design.theme("no-such-theme"), design.DEFAULT_THEME)

    def test_mono_is_a_real_theme_not_a_fallback(self):
        os.environ["FROG_THEME"] = "mono"
        try:
            self.assertTrue(design.is_mono())
            self.assertEqual(design.colour_on(), False)
        finally:
            del os.environ["FROG_THEME"]
        self.assertFalse(design.is_mono())


# --------------------------------------------------------------- provenance --

class VendoredTokens(unittest.TestCase):
    def test_the_vendored_body_is_verbatim(self):
        body = source("_design.py")
        self.assertTrue(body.startswith(
            "# Vendored from design-system/generated/ragbaz_design.py"))
        self.assertIn("source-digest: " + _design.source_digest, body)
        upstream = Path(os.environ.get("RAGBAZ_DESIGN_SYSTEM", "")) if \
            os.environ.get("RAGBAZ_DESIGN_SYSTEM") else None
        candidates = [upstream] if upstream else []
        here = Path(__file__).resolve()
        candidates += [here.parents[2] / "design-system" / "generated"
                       / "ragbaz_design.py",
                       here.parents[3] / "design-system" / "generated"
                       / "ragbaz_design.py"]
        for candidate in candidates:
            if candidate is not None and candidate.exists():
                with self.subTest(upstream=str(candidate)):
                    self.assertTrue(
                        body.endswith(candidate.read_text()),
                        "the vendored body was hand-edited; regenerate it in "
                        "the design system and re-vendor instead")
                return
        self.skipTest("the design system is not on this box")

    def test_the_exports_the_surfaces_need_are_present(self):
        for name in ("ROLES", "SPACE_CELLS", "THEMES", "COLORLESS_THEMES",
                     "DEFAULT_THEME", "GLYPHS", "nearest256", "nearest16",
                     "color_mode", "glyph_mode", "glyph", "clip",
                     "visible_len", "role"):
            with self.subTest(name=name):
                self.assertTrue(hasattr(_design, name))

    def test_no_new_dependency_was_added(self):
        # frog is a public, dependency-free repository. The vendored token module
        # is stdlib-only too, so this change had to stay inside that budget.
        pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml")
        self.assertIn("dependencies = []", pyproject.read_text())

    def test_no_module_level_import_outside_the_stdlib(self):
        # Optional third-party imports (pyyaml, pyinotify, mcp) already exist and
        # are all guarded or declared as extras; what must stay true is that none
        # of them is needed to *import* the package.
        allowed = set(sys.stdlib_module_names) | {"__future__", "ragbaz_frog"}
        offenders = []
        for path in sorted((SRC / "ragbaz_frog").glob("*.py")):
            for node in ast.parse(path.read_text()).body:
                if isinstance(node, ast.Import):
                    roots = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    if node.level:      # a relative import stays in the package
                        continue
                    roots = [(node.module or "").split(".")[0]]
                else:
                    continue
                offenders += [(path.name, root) for root in roots
                              if root not in allowed]
        self.assertEqual(offenders, [])

    def test_the_tokens_are_stdlib_only(self):
        allowed = set(sys.stdlib_module_names) | {"__future__"}
        for node in ast.walk(ast.parse(source("_design.py"))):
            if isinstance(node, ast.Import):
                roots = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    continue
                roots = [(node.module or "").split(".")[0]]
            else:
                continue
            for root in roots:
                with self.subTest(imported=root):
                    self.assertIn(root, allowed)


if __name__ == "__main__":
    unittest.main()
