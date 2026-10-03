"""ANSI-aware width arithmetic for frog, and the only implementation of it.

Colouring a value changes its length; a table that pads by `len()` misaligns as
soon as one cell is coloured and the next is not. Everything that needs to know
how wide a string *looks* goes through here, so there is one answer rather than
one per surface.

Width is measured in codepoints, not terminal cells: see
`docs/design-system-gaps.md` for the double-width caveat.
"""

from __future__ import annotations

import re


ANSI_RE = re.compile(r"\033\[[0-9;]*m")
RESET = "\033[0m"

#: Only used when a caller has no glyph set to ask (the ASCII rendering of the
#: ellipsis). Surfaces pass `design.glyph("ellipsis")` so the token set decides.
ASCII_MARK = "..."


def visible_len(value: object) -> int:
    return len(ANSI_RE.sub("", str(value)))


def pad_visible(value: object, width: int) -> str:
    text = str(value)
    return text + " " * max(0, width - visible_len(text))


def clip_visible(value: object, width: int | None, *, reset: str = RESET,
                 mark: str = ASCII_MARK) -> str:
    """Clip to `width` visible cells without ever splitting an escape sequence:
    escapes are copied through whole and do not consume the budget. `mark` says
    the value was clipped, so a truncated value cannot read as a complete one."""
    text = str(value)
    if width is None or width <= 0 or visible_len(text) <= width:
        return text
    span = len(mark)
    if width <= span:
        return mark[:width]
    target = width - span
    out = []
    visible = 0
    index = 0
    while index < len(text) and visible < target:
        match = ANSI_RE.match(text, index)
        if match:
            out.append(match.group(0))
            index = match.end()
            continue
        out.append(text[index])
        visible += 1
        index += 1
    out.append(mark)
    if "\033[" in text:
        out.append(reset)
    return "".join(out)
