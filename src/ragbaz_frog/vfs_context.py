"""Compatibility API for the canonical thread-local workspace VFS context."""

from __future__ import annotations

from . import DEFAULT_DB_PATH
from .vfs import (
    VFS,
    current_vfs,
    set_vfs,
    workspace_vfs_context as workspace_vfs,
)


def get_db_path(vfs: VFS | None = None) -> str:
    """Get the database path from the current workspace or VFS."""
    from .config import resolve_workspace

    vfs = vfs or current_vfs()
    workspace = resolve_workspace(None, None)
    return workspace.get("db", DEFAULT_DB_PATH)
