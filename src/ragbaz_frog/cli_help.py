from __future__ import annotations

from collections.abc import Callable


SECTION_HEADERS = {
    "positional arguments:",
    "options:",
    "commands:",
    "subcommands:",
    "JSON:",
    "Repo addressing:",
    "Examples:",
    "Grammar:",
}


def colorize_help(
    text: str,
    *,
    use_color: Callable[[], bool],
    color: Callable[[object, str], str],
) -> str:
    if not use_color():
        return text
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if line.startswith("usage:"):
            lines.append(color("usage:", "muted") + line[len("usage:"):])
        elif stripped in SECTION_HEADERS:
            lines.append(line.replace(stripped, color(stripped, "meta"), 1))
        elif stripped.startswith("frog "):
            lines.append(line.replace(stripped, color(stripped, "claim"), 1))
        else:
            lines.append(line)
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")
