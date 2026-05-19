from __future__ import annotations

import re


ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def visible_len(value: object) -> int:
    return len(ANSI_RE.sub("", str(value)))


def pad_visible(value: object, width: int) -> str:
    text = str(value)
    return text + " " * max(0, width - visible_len(text))


def clip_visible(value: object, width: int | None, *, reset: str = "\033[0m") -> str:
    text = str(value)
    if width is None or width <= 0 or visible_len(text) <= width:
        return text
    if width <= 3:
        return "." * width
    target = width - 3
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
    out.append("...")
    if "\033[" in text:
        out.append(reset)
    return "".join(out)
