"""Path resolution that survives a workspace relocation.

frog records paths in SQLite: a repo's location, each target's working
directory, each lock's files. Those are *derived* state, and a workspace moves
-- between `/home/xyzzy/src/ragbaz`, `/srv/ragbaz/src/vendors/ragbaz/head`, and
whatever comes next. An absolute path recorded yesterday is a fact about
yesterday.

Repairing the rows once is not resistance; the next move repeats the damage.
So the rule here is:

    **Store repo-relative where you can. Resolve against the repo's current root
    at the moment of use. Never trust a stored absolute path.**

`resolve` handles three shapes, in order:

1. a path already relative to the repo root -> joined to the current root;
2. an absolute path that still exists -> used as-is;
3. an absolute path under some *previous* root -> recovered by taking the
   longest trailing run of components that still exists under the current root.

Case 3 is what makes existing databases heal without a migration: a stored
`/old/root/BAZ.AI-Governance/src/governance-node` is, relative to its repo,
`src/governance-node`, and that suffix is re-joinable to wherever the repo lives
now.

Resolution is deliberately conservative. A recovered path is reported as
relocated rather than silently substituted, and nothing outside the repo root is
ever returned -- `..` in a stored path cannot escape.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath


@dataclass(frozen=True)
class Resolution:
    """The outcome of resolving one stored path.

    `path` is None only when `ok` is False. `relocated` means the stored value
    pointed somewhere else and was recovered from its repo-relative suffix.
    """

    path: str | None
    ok: bool
    relocated: bool = False
    reason: str = ""

    def __bool__(self) -> bool:  # pragma: no cover - convenience
        return self.ok


def repo_relative(repo_root: str | Path, workdir: str | Path | None) -> str:
    """Express `workdir` relative to `repo_root`, or "." when it is the root.

    A stored path that lies under the current root reduces directly. A stored
    path under some *previous* root cannot be reduced by inspection, because
    the current root is not an ancestor of it -- so it is recovered by finding
    the longest trailing component run that does exist under the current root.
    That is what keeps a cache key stable across a relocation.

    A path with no recoverable suffix is returned unchanged: the caller cannot
    invent a relative form for something it cannot locate.
    """
    if not workdir:
        return "."
    root = Path(repo_root).resolve()
    candidate = Path(workdir)
    if not candidate.is_absolute():
        # Already relative; normalise without inventing a prefix.
        return PurePosixPath(candidate.as_posix()).as_posix() or "."
    try:
        return candidate.resolve().relative_to(root).as_posix() or "."
    except ValueError:
        pass
    for suffix in _suffix_candidates(candidate):
        if (root / suffix).exists():
            return suffix.as_posix()
    # The stored path was the repo root itself. The repo moved but kept its
    # name, so it is still "." -- the commonest case by far, since a build
    # target registered at the repo root is what breaks first.
    if candidate.name and candidate.name == root.name:
        return "."
    return str(candidate)


def _suffix_candidates(stored: Path) -> list[Path]:
    """Trailing component runs of `stored`, longest first, excluding the empty one.

    `/a/b/c/d` yields `b/c/d`, `c/d`, `d` -- each tried against the current root.
    """
    parts = stored.parts
    # Drop the anchor and any leading root marker so PurePosixPath stays relative.
    tail = [p for p in parts if p not in ("/", "\\") and not (len(p) == 2 and p[1] == ":")]
    out: list[Path] = []
    for start in range(1, len(tail)):
        out.append(Path(*tail[start:]))
    return out


def resolve(repo_root: str | Path, stored: str | Path | None) -> Resolution:
    """Resolve a stored path against the repo's current root.

    Resolution is relative-first: a repo-relative stored value always wins, so
    the value that survives a move is the one the caller should have written.
    """
    root = Path(repo_root)
    if not str(root or ""):
        return Resolution(path=None, ok=False, reason="no repo root to resolve against")

    if not stored or str(stored) in (".", ""):
        return Resolution(path=str(root), ok=True)

    candidate = Path(stored)

    # 1. Already repo-relative.
    if not candidate.is_absolute():
        joined = (root / candidate).resolve()
        if _within(joined, root):
            return Resolution(path=str(joined), ok=True)
        return Resolution(
            path=None, ok=False,
            reason=f"relative path {stored!r} escapes the repo root",
        )

    # 2. Absolute and present.
    if candidate.is_dir():
        return Resolution(path=str(candidate), ok=True)

    # 3. Absolute, absent: recover from the repo-relative suffix.
    for suffix in _suffix_candidates(candidate):
        recovered = (root / suffix).resolve()
        if recovered.is_dir() and _within(recovered, root):
            return Resolution(
                path=str(recovered), ok=True, relocated=True,
                reason=(
                    f"stored path {stored!r} no longer exists; recovered as "
                    f"{recovered.name!r} relative to the current repo root"
                ),
            )

    # The stored path was the repo root itself, recorded before the workspace
    # moved. This is the commonest case by far: a build target registered at the
    # repo root is the first thing to break, and it has no suffix to recover
    # from. The repo kept its name, so the suffix is empty.
    if candidate.name and candidate.name == root.name:
        return Resolution(
            path=str(root), ok=True, relocated=True,
            reason=(
                f"stored path {stored!r} was the repo root before the workspace "
                "moved; resolved to the current root"
            ),
        )

    return Resolution(
        path=None, ok=False,
        reason=f"stored path {stored!r} does not exist and could not be recovered",
    )


def resolve_file(repo_root: str | Path, stored: str | Path | None) -> Resolution:
    """As `resolve`, but the target is a file rather than a directory."""
    root = Path(repo_root)
    if not str(root or ""):
        return Resolution(path=None, ok=False, reason="no repo root to resolve against")
    if not stored or str(stored) in (".", ""):
        return Resolution(path=str(root), ok=True)

    candidate = Path(stored)

    if not candidate.is_absolute():
        joined = (root / candidate).resolve()
        if _within(joined, root):
            return Resolution(path=str(joined), ok=True)
        return Resolution(path=None, ok=False,
                          reason=f"relative path {stored!r} escapes the repo root")

    if candidate.exists():
        return Resolution(path=str(candidate), ok=True)

    if candidate.is_dir():
        return Resolution(path=None, ok=False,
                          reason=f"{stored!r} names a directory, not a file")

    for suffix in _suffix_candidates(candidate):
        recovered = (root / suffix).resolve()
        if recovered.is_file() and _within(recovered, root):
            return Resolution(
                path=str(recovered), ok=True, relocated=True,
                reason=(
                    f"stored path {stored!r} no longer exists; recovered as "
                    f"{recovered.name!r} relative to the current repo root"
                ),
            )

    return Resolution(
        path=None, ok=False,
        reason=f"stored path {stored!r} does not exist and could not be recovered",
    )


def _within(candidate: Path, root: Path) -> bool:
    """True when `candidate` is `root` or lives under it. Never follows `..` out."""
    try:
        candidate.relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False
