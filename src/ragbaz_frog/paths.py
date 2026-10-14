"""Repository-relative paths, including recovery of pre-relocation rows."""

from pathlib import Path


def resolve_repo_path(repo_root: str, value: str) -> Path:
    root = Path(repo_root).expanduser().resolve()
    path = Path(value).expanduser()
    if not path.is_absolute():
        resolved = (root / path).resolve()
        resolved.relative_to(root)  # relative repository paths cannot escape
        return resolved
    try:
        path.relative_to(root)
        return path
    except ValueError:
        pass
    # Legacy rows used the old workspace prefix. Only recover an unambiguous
    # repo-name boundary; unrelated absolute paths retain their meaning.
    matches = [i for i, part in enumerate(path.parts) if part == root.name]
    if len(matches) == 1:
        return root.joinpath(*path.parts[matches[0] + 1 :])
    return path


def relative_repo_path(repo_root: str, value: str) -> str:
    resolved = resolve_repo_path(repo_root, value)
    try:
        return resolved.relative_to(Path(repo_root).expanduser().resolve()).as_posix()
    except ValueError:
        return str(resolved)


def resolve_target(target: dict) -> dict:
    target = dict(target)
    target["workdir"] = str(resolve_repo_path(target["repo_path"], target["workdir"]))
    return target
