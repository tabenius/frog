"""VFS-aware store operations for Frog.

This module provides filesystem operations that work with any VFS backend,
allowing Frog to operate on local, SSH, S3, HTTP, WebDAV, etc. filesystems.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import tempfile
from typing import Optional

from pathlib import Path
from .vfs import VFS, VFSStat, LocalVFS, default_vfs, create_vfs, parse_vfs_uri, current_vfs, set_vfs, workspace_vfs_context
from . import DEFAULT_DB_PATH


# Thread-local storage for current VFS
import threading
_vfs_local = threading.local()


def get_workspace_vfs(workspace_name: Optional[str] = None, config_path: Optional[str] = None) -> VFS:
    """Get VFS for a workspace."""
    from .config import resolve_workspace
    workspace = resolve_workspace(workspace_name, config_path)
    if not workspace:
        return default_vfs()
    
    db_uri = workspace.get("db_uri")
    if db_uri:
        return create_vfs(db_uri)
    
    # Fallback: local filesystem with workspace root
    root = workspace.get("root")
    if root:
        scheme, params = parse_vfs_uri(f"file://{root}")
        return create_vfs(f"file://{root}", **params)
    
    return default_vfs()


def vfs_db_path(vfs: Optional[VFS] = None) -> str:
    """Get database path from current workspace."""
    from .config import resolve_workspace
    workspace = resolve_workspace(None, None)
    if workspace:
        return workspace.get("db", DEFAULT_DB_PATH)
    return DEFAULT_DB_PATH


def vfs_frog_home(vfs: Optional[VFS] = None) -> str:
    """Get frog home directory (where this package is installed)."""
    vfs = vfs or current_vfs()
    # This is always local - the frog installation itself
    from pathlib import Path
    return str(Path(__file__).resolve().parents[2])


def vfs_migration_dir(vfs: Optional[VFS] = None) -> str:
    """Get migrations directory."""
    return vfs_frog_home(vfs) + "/src/ragbaz_frog/migrations"


# VFS-aware file operations that mirror pathlib/os API
def vfs_exists(path: str, vfs: Optional[VFS] = None) -> bool:
    vfs = vfs or current_vfs()
    return vfs.exists(path)


def vfs_is_file(path: str, vfs: Optional[VFS] = None) -> bool:
    vfs = vfs or current_vfs()
    return vfs.is_file(path)


def vfs_is_dir(path: str, vfs: Optional[VFS] = None) -> bool:
    vfs = vfs or current_vfs()
    return vfs.is_dir(path)


def vfs_stat(path: str, vfs: Optional[VFS] = None) -> VFSStat:
    vfs = vfs or current_vfs()
    return vfs.stat(path)


def vfs_read_text(path: str, encoding: str = "utf-8", errors: str = "strict", vfs: Optional[VFS] = None) -> str:
    vfs = vfs or current_vfs()
    return vfs.read_text(path, encoding=encoding, errors=errors)


def vfs_read_bytes(path: str, vfs: Optional[VFS] = None) -> bytes:
    vfs = vfs or current_vfs()
    return vfs.read_bytes(path)


def vfs_write_text(path: str, content: str, encoding: str = "utf-8", vfs: Optional[VFS] = None) -> None:
    vfs = vfs or current_vfs()
    vfs.write_text(path, content, encoding=encoding)


def vfs_write_bytes(path: str, content: bytes, vfs: Optional[VFS] = None) -> None:
    vfs = vfs or current_vfs()
    vfs.write_bytes(path, content)


def vfs_mkdir(path: str, parents: bool = True, exist_ok: bool = True, vfs: Optional[VFS] = None) -> None:
    vfs = vfs or current_vfs()
    vfs.mkdir(path, parents=parents, exist_ok=exist_ok)


def vfs_unlink(path: str, missing_ok: bool = True, vfs: Optional[VFS] = None) -> None:
    vfs = vfs or current_vfs()
    vfs.unlink(path, missing_ok=missing_ok)


def vfs_rename(src: str, dst: str, vfs: Optional[VFS] = None) -> None:
    vfs = vfs or current_vfs()
    vfs.rename(src, dst)


def vfs_listdir(path: str, vfs: Optional[VFS] = None) -> list[str]:
    vfs = vfs or current_vfs()
    return vfs.listdir(path)


def vfs_walk(path: str, vfs: Optional[VFS] = None):
    vfs = vfs or current_vfs()
    return vfs.walk(path)


def vfs_glob(path: str, pattern: str, vfs: Optional[VFS] = None) -> list[str]:
    vfs = vfs or current_vfs()
    return vfs.glob(path, pattern)


@contextlib.contextmanager
def vfs_open(path: str, mode: str = "r", encoding: Optional[str] = None, vfs: Optional[VFS] = None):
    vfs = vfs or current_vfs()
    with vfs.open(path, mode, encoding) as f:
        yield f


def vfs_resolve(path: str, vfs: Optional[VFS] = None) -> str:
    vfs = vfs or current_vfs()
    return vfs.resolve(path)


def vfs_expanduser(path: str, vfs: Optional[VFS] = None) -> str:
    vfs = vfs or current_vfs()
    return vfs.expanduser(path)


def vfs_parent(path: str, vfs: Optional[VFS] = None) -> str:
    vfs = vfs or current_vfs()
    return vfs.parent(path)


def vfs_name(path: str, vfs: Optional[VFS] = None) -> str:
    vfs = vfs or current_vfs()
    return vfs.name(path)


def vfs_join(*parts: str, vfs: Optional[VFS] = None) -> str:
    vfs = vfs or current_vfs()
    return vfs.join(*parts)


def vfs_relative_to(path: str, base: str, vfs: Optional[VFS] = None) -> str:
    vfs = vfs or current_vfs()
    return vfs.relative_to(path, base)


def vfs_from_local_path(local_path: str, vfs: Optional[VFS] = None) -> str:
    """Convert a local absolute path to a VFS path relative to VFS root."""
    vfs = vfs or current_vfs()
    if isinstance(vfs, LocalVFS):
        try:
            rel = Path(local_path).resolve().relative_to(vfs._root)
            rel_str = rel.as_posix()
            if rel_str == ".":
                return "/"
            return "/" + rel_str
        except ValueError:
            # Path is outside VFS root - return as VFS path with full path
            return "/" + Path(local_path).resolve().as_posix().lstrip("/")
    return local_path


def _is_path_under_vfs_root(local_path: str, vfs: VFS) -> bool:
    """Check if a local path is under the VFS root."""
    if isinstance(vfs, LocalVFS):
        try:
            Path(local_path).resolve().relative_to(vfs._root)
            return True
        except ValueError:
            return False
    return True  # For non-local VFS, assume it's under root


# VFS-aware path operations
def vfs_workspace_root(vfs: Optional[VFS] = None) -> str:
    """Get workspace root from current VFS context."""
    vfs = vfs or current_vfs()
    
    # If using default VFS (cwd), try to infer from workspace config
    if isinstance(vfs, LocalVFS) and vfs._root == Path.cwd():
        from .config import resolve_workspace
        workspace = resolve_workspace(None, None)
        if workspace and workspace.get("root"):
            # Create a new VFS with the workspace root
            vfs = LocalVFS(workspace["root"])
            set_vfs(vfs)
    
    return vfs.resolve("/")


# SQLite connection with VFS-aware path handling
def vfs_connect(db_path: str, vfs: Optional[VFS] = None) -> sqlite3.Connection:
    """Connect to SQLite database, handling VFS paths.

    For non-local VFS, this copies the database to a temporary local file,
    performs operations, and copies back on commit.
    """
    # Auto-setup VFS context if not already set, based on db_path
    if vfs is None:
        vfs = current_vfs()
        if isinstance(vfs, LocalVFS) and vfs._root == Path.cwd():
            # Default VFS - try to infer workspace root from db_path
            db_dir = str(Path(db_path).parent)
            if db_dir != "." and db_dir != str(Path.cwd()):
                vfs = LocalVFS(db_dir)
                set_vfs(vfs)

    # For local VFS, connect directly - handle absolute paths
    if isinstance(vfs, LocalVFS):
        # If db_path is absolute, use it directly
        if os.path.isabs(db_path):
            local_path = Path(db_path)
        else:
            local_path = vfs._to_path(db_path)  # type: ignore
        local_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(local_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    # For remote VFS, use a temporary local copy
    return _RemoteSQLiteConnection(db_path, vfs)


class _RemoteSQLiteConnection:
    """SQLite connection that works with remote VFS by using a local temporary file."""

    def __init__(self, remote_path: str, vfs: VFS):
        self._remote_path = remote_path
        self._vfs = vfs
        self._temp_dir = tempfile.mkdtemp(prefix="frog-db-")
        self._local_path = os.path.join(self._temp_dir, "AGENTS.db")
        self._conn: Optional[sqlite3.Connection] = None
        self._original_hash: Optional[str] = None
        self._fetch_remote_db()

    def _fetch_remote_db(self) -> None:
        """Download database from remote VFS to local temp file."""
        if self._vfs.exists(self._remote_path):
            data = self._vfs.read_bytes(self._remote_path)
            with open(self._local_path, "wb") as f:
                f.write(data)
            import hashlib
            self._original_hash = hashlib.sha256(data).hexdigest()
        else:
            # New database - create empty
            open(self._local_path, "wb").close()
            self._original_hash = None

        self._conn = sqlite3.connect(self._local_path, timeout=5.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._conn.execute("PRAGMA synchronous = NORMAL")

    def _push_remote_db(self) -> None:
        """Upload local database to remote VFS."""
        if self._conn:
            self._conn.commit()
            # Checkpoint WAL
            try:
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass

        with open(self._local_path, "rb") as f:
            data = f.read()

        import hashlib
        new_hash = hashlib.sha256(data).hexdigest()
        if new_hash != self._original_hash:
            self._vfs.write_bytes(self._remote_path, data)
            self._original_hash = new_hash

    def __getattr__(self, name: str):
        """Delegate to underlying connection."""
        return getattr(self._conn, name)

    def commit(self) -> None:
        if self._conn:
            self._conn.commit()
            self._push_remote_db()

    def rollback(self) -> None:
        if self._conn:
            self._conn.rollback()

    def close(self) -> None:
        if self._conn:
            try:
                self._push_remote_db()
            finally:
                self._conn.close()
                self._conn = None
        # Clean up temp dir
        try:
            import shutil
            shutil.rmtree(self._temp_dir, ignore_errors=True)
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


from .vfs import LocalVFS
import tempfile