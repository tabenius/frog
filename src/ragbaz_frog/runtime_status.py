"""Read-only ragbaz.runtime-status.v1 projection; no host probes in drawing."""
import datetime as dt
import json
import os
from pathlib import Path


def runtime_lines():
    path = os.environ.get("RAGBAZ_RUNTIME_STATUS")
    if not path:
        return ["Runtime not configured; set RAGBAZ_RUNTIME_STATUS to Minotaur's snapshot.",
                "Native evidence / human review do not require Ephor."]
    try:
        with Path(path).expanduser().open("rb") as stream:
            raw = stream.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError("oversized")
        data = json.loads(raw)
        if data.get("schema") != "ragbaz.runtime-status.v1":
            raise ValueError("schema")
        stamp = dt.datetime.fromisoformat(data["observed_at"].replace("Z", "+00:00"))
        age = (dt.datetime.now(dt.timezone.utc) - stamp).total_seconds()
        lines = data["summary"]
        if not isinstance(lines, list) or len(lines) > 1024 or not all(isinstance(s, str) for s in lines):
            raise ValueError("summary")
        state = "current" if 0 <= age <= 120 else "stale" if age > 120 else "clock-skew"
        return ["Runtime snapshot: " + state + " (observations, not authority)"] + [
            "".join(c if c.isprintable() else " " for c in s) for s in lines]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return ["Runtime snapshot unavailable or invalid; optional components are unknown."]
