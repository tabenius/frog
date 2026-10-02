"""VFS context management for Frog workspaces."""

from __future__ import annotations

import contextlib
import os
import threading
from typing import Optional

from .vfs import VFS, default_vfs, create_vfs, parse_vfs_uri
from .config import resolve_workspace

_vfs_context: contextlib.ContextVar[Optional[VFS]] = contextlib.ContextVar("frog_vfs", default=None)


def current_vfs() -> VFS:
    """Get the current VFS for the active workspace."""
    vfs = _vfs_context.get()
    if vfs is not None:
        return vfs
    return default_vfs()


@contextlib.contextmanager
def workspace_vfs(workspace_name: Optional[str] = None, config_path: Optional[str] = None) -> VFS:
    """Context manager that sets up VFS for a workspace."""
    workspace = resolve_workspace(workspace_name, config_path)
    db_uri = workspace.get("db_uri")

    if db_uri:
        vfs = create_vfs(db_uri)
    else:
        db_path = workspace.get("db", DEFAULT_DB_PATH)
        root = workspace.get("root")
        vfs = create_vfs(f"file://{root}" if root else "file://")

    token = _vfs_context.set(vfs)
    try:
        yield vfs
    finally:
        _vfs_context.reset(token)


def set_vfs(vfs: VFS) -> None:
    """Set the current VFS (for testing or explicit control)."""
    _vfs_context.set(vfs)


def get_db_path(vfs: Optional[VFS] = None) -> str:
    """Get the database path from the current workspace or VFS."""
    vfs = vfs or current_vfs()
    workspace = resolve_workspace(None, None)
    return workspace.get("db", DEFAULT_DB_PATH)


# Re-export for convenience
from . import DEFAULT_DB_PATH, DEFAULT_CONFIG_PATH