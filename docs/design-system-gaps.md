# What the ragbaz design tokens could not express for frog

Recorded here rather than in the design system, and rather than fixed by
forking a palette, adding a role or inventing a glyph. This file is the
"report the gaps honestly" step of
`design-system/skills/ragbaz-renderer-adapter/SKILL.md`: frog adapted to the
tokens, and where the tokens could not carry something, it is written down
instead of approximated away.

None of these are blockers. Each says what was done and what a *design system*
change — not a frog change — would be needed to do better.

## Glyphs frog used that have no key

Four of frog's marks predate the token file and have no `tokens.toml` glyph
key. Rather than add keys from a renderer, each was mapped to the nearest
existing one, which changes the character:

| frog used | for | now | note |
|---|---|---|---|
| `★` | task is ready | `check` (`✓` / `+`) | arguably *better*: `check` is the key layout.md pairs with `status.ok` |
| `◆` | assigned agent | `bullet` (`•` / `*`) | the meaning is unchanged; the shape is a generic mark |
| `⛓` | unmet dependency | `lock` (`▣` / `=`) | a closed box reads as "held", which is what a blocker is |
| `←` | "depends on" in `frog board` | `corner` (`└─`) | see the next item |
| `·` | empty column | `dot_empty` (`○` / `o`) | |
| `v` / `>` | fold / unfold in the repo tree | `dot` / `dot_empty` | see below |

**Missing: a horizontal arrow.** `tokens.toml` has `arrow_up` and `arrow_dn`
and nothing else. `frog board` printed `⛓ 2 ← dep-a, dep-b` to say "this task
depends on those". There is no glyph key for `←`, so the dependency edge is now
drawn with `corner` (`└─`): `▣ 2 └─ dep-a, dep-b`. Direction survives — it
reads as a tree edge — but it is a connector rather than an arrow, and it is
the one place where the substitution is visible rather than neutral.
*A design-system change adding `arrow_left`/`arrow_right` would fix it.*

**Missing: a collapsed-disclosure glyph.** The repo tree marked folded rows with
`>` and unfolded with `v`. There is no right-pointing triangle in the token set,
and `marker_select` (`▸`) is reserved for selection — using it for expansion
would make a selected row indistinguishable from an expanded one, which is the
mistake layout.md warns about with `dot`. frog uses `dot` for unfolded and
`dot_empty` for folded: fill means open, hollow means closed.
*A design-system change adding `disclosure_open`/`disclosure_closed` would fix
it.*

**Missing: the frog wordmark.** `_FROG_ART` in `main_cli.py` is ASCII art with
no glyph key behind it and no `ascii` rendering, so it cannot be expressed
through the glyph set and cannot be narrowed. It is kept for `frog board` and
recorded here; it was **removed from the TUI**, because the TUI can only obey
"no literal characters" by dropping it, and the title bar now carries
`RAGBAZ/frog` in `ink.link` + `ink.code`, which is the same information. The
costs of keeping it would have been five content rows on a 24-row terminal and
a hard-coded box-drawing character.
*A design-system change (a `wordmark` key, or an explicit exemption for
decorative ASCII art) would let it come back.*

## Colours the tokens cannot reach at the `ansi16` rung

The four themes are complete role tables, but `nearest16()` over the xterm base
palette collapses several of them. In the light themes (`ragbaz`,
`ragbaz-white`) at `ansi16`:

| role | nearest16 |
|---|---|
| `ink.muted`, `ink.faint`, `ink.code`, `edge.control`, `edge.focus`, `status.ok`, `status.warn`, `status.pending`, `delta.added` | `8` (bright black) |
| `ink.link`, `status.error`, `delta.moved`, `delta.removed` | `1` (red) |
| `ink.emphasis`, `ink.body` | `0` (black) |

So on a 16-colour terminal the light themes lose most of their ramp, and
`status.ok` ("ready", green) and `status.error` ("blocked", red) reduce to grey
and red. The words and glyphs are always printed beside the colour, so nothing
becomes *unreadable* — but a 16-colour terminal is not a good host for a
warm-sepia light palette. `ragbaz-night` keeps the ramp at `ansi256` and is the
right choice on a dark terminal; `mono` is the right choice with no colour.
*A design-system change adding an `ansi16` role table per theme would fix it
properly; nothing was added here.*

## The default theme assumes a light background, and nothing can tell frog otherwise

`themes.toml` records `background = "light"` per theme, but the generated module
frog vendors (`ragbaz_design.py`) carries only the role → hex tables — the
background metadata is not in the projection. frog paints foregrounds over
whatever background the terminal has (`curses.use_default_colors()`), so with
the default theme on a dark terminal `ink.body` (`#2b2822`, near-black) is
nearly invisible.

There is no honest way for frog to detect this from the tokens, and guessing
from `COLORFGBG` would be exactly the "auto-detect the environment" behaviour
the design system forbids for Nerd Fonts. **On a dark terminal, set
`FROG_THEME=ragbaz-night`** — or `FROG_THEME=mono`.

*A design-system change projecting `background` into the generated module would
let a renderer pick sensibly.*

## `FORCE_COLOR` and `NO_COLOR` together: frog and sylvae disagree

This is the one place where frog's colour policy does not match
`sylvae/src/sylvae/color.py`, and it is deliberate.

* sylvae tests `NO_COLOR` first, so setting both turns colour **off**.
* The design system decided the opposite in prose — "the established
  convention is that `FORCE_COLOR` wins, and that is what we implement" — and
  generated it into `ragbaz_design.color_mode()`, which frog is forbidden to
  edit.

frog follows the design system. `tests/test_design_conformance.py` asserts
agreement with sylvae across thirteen environment/TTY combinations, and pins
this one disagreement separately so that a change to either side is noticed.
*This needs a decision in the design system, not a change in frog.*

## `help_page` is not a token

`docs/layout.md` says the `?` overlay paginates "`help_page` entries per page",
but `help_page` is not a key in `tokens.toml` and not exported by the generated
module. frog uses `tui.HELP_PAGE = 8`, a plain constant, documented as a gap.
*A design-system change adding `help_page` to the space or layout scale would
make the page size a token like every other number.*

## Width is counted in codepoints, not cells

`docs/layout.md` requires that truncation never cut a double-width character.
`cli_render.visible_len` counts codepoints, as does the vendored
`ragbaz_design.visible_len`, and the vendored module cannot be edited. Neither
the design system nor frog has a `wcwidth`.

frog's own content is ASCII plus box-drawing (all single-width), so this is
latent rather than active — but `runtime_lines()` passes through
Minotaur's snapshot text, which is operator-supplied and could contain CJK or
emoji. *A design-system change adding an `east_asian_width`-based width
function (stdlib `unicodedata` is enough) would fix it for the whole fleet.*

## Content that does not fit the three-pane frame

`docs/layout.md` permits recording this rather than fighting it: "any surface
whose content genuinely does not fit the three-pane frame."

The board is four fixed lanes. With `nav` at 24 columns and `detail` at 38, a
120×40 terminal leaves 58 columns for four lanes — 14 each, below the 16 a task
row needs (`HH:MM p0 slug ◆agent ▣ 2 └─ dep` is already 30). So the **board view
uses the chrome, the ladder and the selection treatment, and fills the whole
content width with four lanes that sum to exactly the terminal width.** The
**repos view** does use `nav | main | detail` as written, because a repo list
is a list.

Both are asserted in `tests/test_design_frame.py`: `tui.lanes()` sums to exactly
`width` for every width from 20 to 240, and nothing is ever written outside the
window at any documented degradation threshold.

## Smaller judgement calls

* **Priority is the ink ramp, not a hue.** frog coloured p0 red / p1 orange /
  p2 yellow / p3 grey. There is no priority role, and the type scale maps to
  *emphasis* rather than size or hue, so p0→`ink.emphasis`, p1→`ink.body`,
  p2→`ink.muted`, p3→`ink.faint`, with p0 bold. The `p0`..`p3` text is printed
  beside it, so priority never depended on colour and still does not. The
  visible change is that p0 is no longer red.
* **`surface.chrome` is unused by the TUI.** layout.md says to choose *either*
  the reverse attribute *or* an inverted pair, never both. frog uses the
  attribute, so no pair is ever painted for a bar.
* **The status word, not a status glyph, in CLI tables.** layout.md asks a
  status *indicator* to be glyph + word + colour. frog's CLI prints the status
  word in a table cell; adding a glyph to every cell of every table was judged
  a change of information architecture rather than a restyle, and the word is
  already the non-colour signal. The glyph + word + colour form
  (`design.status_glyph_text`) is used where the surface's job is to show status
  as status: the TUI status line and the inspector.
* **`store.py` still draws `└─`** in `frog export todo`. `store.py` is not a
  terminal-styled surface and was out of scope for this change; the character is
  `corner` in the token set whenever it gets restyled.
* **Two keys were added to the TUI**: `space` and `,` page the `?` overlay.
  layout.md requires the overlay to be paginated, and neither key was bound to
  anything before, so this is new surface rather than changed behaviour.
* **`C-c` is its own binding.** A binding is dropped whole when a field claims
  any of its keys, so `quit` on `q Q` plus `interrupt` on `C-c` keeps Ctrl-C
  reachable while a text field has focus. Without the split, the global `q`
  would take `C-c` down with it.
