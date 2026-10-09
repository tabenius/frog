"""Provenance for this frog: which commit, how old, and how far from the remote.

`frog --version` answers "what am I running". On this box the answer is not a
version number, because the installed `frog` is a launcher that puts the live
working tree on `sys.path` (`/home/xyzzy/.local/bin/frog` inserts
`/srv/ragbaz/src/vendors/ragbaz/head/frog/src`). So the code executing may be
code that has never been committed, and the honest report has to say so rather
than print a hex that does not describe the running bytes.

The fields, and why each is here:

- `revision` / `short_revision` -- the commit HEAD points at, git's own terms.
- `dirty` / `dirty_files` -- uncommitted changes in the tree frog is running from.
- `matches_a_commit` -- the field that matters. False when `dirty` is true: the
  running code corresponds to no commit at all, so quoting a hex alone would be
  a lie of omission.
- `describe` -- `git describe --tags --always --dirty`, the de facto standard for
  "version of a checkout". There are no tags in this repo yet, so it degrades to
  the short hex plus a `-dirty` suffix.
- `commit_time` / `commit_date` -- when the code was written, not when it was run.
- `built_at` -- when this process started, so a long-lived shell can tell that
  its `frog` predates the checkout it is looking at.
- `upstream` -- `ahead` / `behind` against the remote's default branch, which is
  the actual question behind "is my frog up to date".

Machine-readable shape follows the conventions already in this CLI: the payload
carries `ok`, and `--json` routes it through `_emit`. `schema` names the payload
version so a consumer can tell the shape rather than guess it.

Nothing here writes, and nothing here reaches the network: `ahead`/`behind` are
computed from refs already fetched. Use `frog sync pull` to refresh them.
"""
from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

#: Bumped when the payload's shape changes incompatibly.
SCHEMA = "ragbaz.frog.version/1"

REPO_MARKERS = ("pyproject.toml", ".git")


def repo_root(start: Path | None = None) -> Path | None:
    """Walk up for the frog checkout, so this works from any subdirectory."""
    here = (start or Path(__file__).resolve().parent)
    for candidate in [here, *here.parents]:
        if (candidate / "pyproject.toml").is_file() and (candidate / ".git").exists():
            return candidate
    return None


def _git(root: Path, *args: str, timeout: float = 10) -> str | None:
    """One bounded git call. `None` means "git did not answer", never a guess."""
    try:
        done = subprocess.run(["git", "-C", str(root), *args], text=True,
                              capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0:
        return None
    return done.stdout.strip()


def _declared_version(root: Path) -> str:
    """The version in pyproject.toml, which is a label and not a build id."""
    try:
        for line in (root / "pyproject.toml").read_text().splitlines():
            if line.strip().startswith("version"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except (OSError, IndexError):
        pass
    return "unknown"


def _stamp(value: str | None) -> str:
    return value.replace(" ", "T", 1) if value else ""


def upstream(root: Path, revision: str, default_branch: str = "origin/main") -> dict:
    """How far the checkout is from the remote's default branch.

    Only refs already fetched are consulted. `behind` therefore means "behind
    what we last fetched", which is the honest bound: after `frog sync pull`
    it means behind the remote.
    """
    reference = _git(root, "rev-parse", default_branch)
    if not reference:
        return {"available": False, "ref": default_branch,
                "note": f"{default_branch} is not fetched; run `frog sync pull`"}
    counts = _git(root, "rev-list", "--left-right", "--count",
                  f"HEAD...{default_branch}")
    ahead = behind = None
    if counts:
        parts = counts.split()
        if len(parts) == 2 and all(part.isdigit() for part in parts):
            ahead, behind = int(parts[0]), int(parts[1])
    return {
        "available": ahead is not None,
        "ref": default_branch,
        "remote": (_git(root, "remote", "get-url", "origin") or ""),
        "remote_revision": reference,
        "ahead": ahead,
        "behind": behind,
        "up_to_date": ahead == 0 and behind == 0,
    }


def report(root: Path | None = None) -> dict:
    """Everything known about this frog's provenance. Never raises."""
    found = root or repo_root()
    if found is None:
        return {"ok": True, "schema": SCHEMA, "name": "ragbaz-frog",
                "source": "unknown", "note": "no frog checkout found",
                "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "python": sys.version.split()[0]}

    revision = _git(found, "rev-parse", "HEAD") or ""
    status = _git(found, "status", "--porcelain")
    dirty_files = [line[3:] for line in (status or "").splitlines() if line.strip()]
    short = _git(found, "rev-parse", "--short", "HEAD") or revision[:7]
    payload = {
        "ok": True,
        "schema": SCHEMA,
        "name": "ragbaz-frog",
        "version": _declared_version(found),
        "revision": revision,
        "short_revision": short,
        "branch": _git(found, "rev-parse", "--abbrev-ref", "HEAD") or "",
        "describe": _git(found, "describe", "--tags", "--always", "--dirty") or short,
        "tag": _git(found, "describe", "--tags", "--abbrev=0") or "",
        "dirty": bool(dirty_files),
        "dirty_files": dirty_files,
        # The honest answer to "compiled and installed with no commit yet": the
        # running tree is not any commit, so no hex describes it exactly.
        "matches_a_commit": not dirty_files,
        "source": "working-tree" if dirty_files else "commit",
        "commit_time": _stamp(_git(found, "show", "-s", "--format=%cI", "HEAD")),
        "commit_date": (_git(found, "show", "-s", "--format=%cs", "HEAD") or ""),
        "commit_subject": _git(found, "show", "-s", "--format=%s", "HEAD") or "",
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "checkout": str(found),
        "installed_at": _installed_path(),
        "python": sys.version.split()[0],
    }
    payload["upstream"] = upstream(found, revision) if revision else {
        "available": False, "note": "no HEAD"}
    return payload


def _installed_path() -> str:
    """Which file is actually being executed, as opposed to the checkout."""
    return str(Path(sys.argv[0]).resolve()) if sys.argv and sys.argv[0] else ""


def one_line(payload: dict) -> str:
    """The human form: name, label, where it stands, and how old.

    Reads left to right as an answer: what it is, what state the code is in,
    where that state sits relative to the remote, and when it was written.
    """
    if payload.get("source") == "unknown":
        return f"{payload['name']} {payload.get('version', '?')} (no checkout found)"
    parts = [payload["name"], payload.get("version", "?")]
    parts.append(payload.get("describe") or payload.get("short_revision") or "?")
    if payload.get("branch") and payload["branch"] != "HEAD":
        parts.append(f"({payload['branch']})")
    parts.append("UNCOMMITTED" if not payload.get("matches_a_commit", True) else "clean")
    state = payload.get("upstream", {})
    if state.get("available"):
        relation = ("up to date" if state["up_to_date"] else
                    f"{state['ahead']} ahead, {state['behind']} behind")
        parts.append(relation)
    elif state.get("note"):
        parts.append(state["note"])
    if payload.get("commit_time"):
        parts.append(f"committed {payload['commit_time']}")
    return " · ".join(parts)
