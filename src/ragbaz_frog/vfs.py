"""Virtual Filesystem abstraction for Frog.

Provides a unified interface for local, SSH, HTTP(S), WebDAV, S3/R2 backends.
All store.py filesystem operations go through this layer.
"""

from __future__ import annotations

import abc
import contextlib
import fnmatch
import os
import stat
import shutil
import tempfile
import threading
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, Iterator, Optional, TextIO, Union

try:
    import boto3
    from botocore.config import Config as BotoConfig
except ImportError:
    boto3 = None

try:
    import paramiko
except ImportError:
    paramiko = None

try:
    import webdav3.client as webdav_client
except ImportError:
    webdav_client = None


# --local-only mode: abort if any VFS operation would use non-local backend
_local_only_mode: bool = False


def set_local_only_mode(enabled: bool) -> None:
    """Enable/disable --local-only mode globally."""
    global _local_only_mode
    _local_only_mode = enabled


def is_local_only_mode() -> bool:
    """Check if --local-only mode is active."""
    return _local_only_mode


def _assert_local_only(vfs: VFS, operation: str, path: str) -> None:
    """Abort with actionable error if --local-only but path is non-local."""
    if _local_only_mode and not isinstance(vfs, LocalVFS):
        scheme = getattr(vfs, 'scheme', 'unknown')
        raise RuntimeError(
            f"--local-only mode: {operation} requires local filesystem, "
            f"but path '{path}' uses {scheme}:// VFS.\n"
            f"Fix: Run without --local-only, or use a local workspace:\n"
            f"  frog config workspace add <name> --host local --root /local/path --db /local/path/AGENTS.db\n"
            f"  frog --workspace <name> ..."
        )


@dataclass
class VFSStat:
    """Filesystem stat result, analogous to os.stat_result."""
    size: int
    mtime: float
    mode: int
    is_dir: bool
    is_file: bool

    @classmethod
    def from_os_stat(cls, st: os.stat_result) -> VFSStat:
        return cls(
            size=st.st_size,
            mtime=st.st_mtime,
            mode=st.st_mode,
            is_dir=stat.S_ISDIR(st.st_mode),
            is_file=stat.S_ISREG(st.st_mode),
        )


class VFS(abc.ABC):
    """Abstract virtual filesystem interface.

    All paths are absolute, normalized, and use forward slashes.
    """

    @property
    @abc.abstractmethod
    def scheme(self) -> str:
        """URI scheme this VFS handles (e.g., 'file', 'ssh', 's3')."""

    @abc.abstractmethod
    def exists(self, path: str) -> bool:
        """Check if path exists."""

    @abc.abstractmethod
    def is_file(self, path: str) -> bool:
        """Check if path is a regular file."""

    @abc.abstractmethod
    def is_dir(self, path: str) -> bool:
        """Check if path is a directory."""

    @abc.abstractmethod
    def stat(self, path: str) -> VFSStat:
        """Get file/directory metadata."""

    @abc.abstractmethod
    def read_text(self, path: str, encoding: str = "utf-8", errors: str = "strict") -> str:
        """Read file as text."""

    @abc.abstractmethod
    def read_bytes(self, path: str) -> bytes:
        """Read file as bytes."""

    @abc.abstractmethod
    def write_text(self, path: str, content: str, encoding: str = "utf-8") -> None:
        """Write text to file, creating parent directories."""

    @abc.abstractmethod
    def write_bytes(self, path: str, content: bytes) -> None:
        """Write bytes to file, creating parent directories."""

    @abc.abstractmethod
    def mkdir(self, path: str, parents: bool = True, exist_ok: bool = True) -> None:
        """Create directory."""

    @abc.abstractmethod
    def unlink(self, path: str, missing_ok: bool = True) -> None:
        """Delete file."""

    @abc.abstractmethod
    def rename(self, src: str, dst: str) -> None:
        """Rename/move file or directory."""

    @abc.abstractmethod
    def listdir(self, path: str) -> list[str]:
        """List directory entries (names only)."""

    @abc.abstractmethod
    def walk(self, path: str) -> Iterator[tuple[str, list[str], list[str]]]:
        """Walk directory tree: yields (dirpath, dirnames, filenames)."""

    @abc.abstractmethod
    def glob(self, path: str, pattern: str) -> list[str]:
        """Return matching paths under path."""

    @abc.abstractmethod
    def open(self, path: str, mode: str = "r", encoding: Optional[str] = None) -> Union[TextIO, BinaryIO]:
        """Open file for reading/writing."""

    @abc.abstractmethod
    def resolve(self, path: str) -> str:
        """Resolve path to absolute, normalized form."""

    @abc.abstractmethod
    def expanduser(self, path: str) -> str:
        """Expand ~ and ~user."""

    @abc.abstractmethod
    def cwd(self) -> str:
        """Current working directory."""

    @abc.abstractmethod
    def home(self) -> str:
        """User home directory."""

    def parent(self, path: str) -> str:
        """Parent directory of path."""
        p = self.resolve(path)
        parent = "/".join(p.rstrip("/").split("/")[:-1])
        return parent or "/"

    def name(self, path: str) -> str:
        """Final component of path."""
        return self.resolve(path).rstrip("/").split("/")[-1]

    def join(self, *parts: str) -> str:
        """Join path components."""
        if not parts:
            return "/"
        # Preserve leading slash if first part has it
        first = str(parts[0])
        has_leading_slash = first.startswith("/")
        joined = "/".join(str(part).strip("/") for part in parts if part)
        if has_leading_slash and not joined.startswith("/"):
            joined = "/" + joined
        return joined

    def relative_to(self, path: str, base: str) -> str:
        """Relative path from base to path."""
        path = self.resolve(path).rstrip("/")
        base = self.resolve(base).rstrip("/")
        if not path.startswith(base):
            raise ValueError(f"{path} not under {base}")
        rel = path[len(base):].lstrip("/")
        return rel


class LocalVFS(VFS):
    """Local filesystem implementation using pathlib/os."""

    scheme = "file"

    def __init__(self, uri_or_root: Optional[str] = None, **kwargs):
        # Accept either a URI (file:///path) or a plain path
        if uri_or_root and uri_or_root.startswith("file://"):
            parsed = urllib.parse.urlparse(uri_or_root)
            root = parsed.path or "/"
        else:
            root = uri_or_root
        self._root = Path(root).resolve() if root else Path.cwd()
        self._cwd = self._root

    def _to_path(self, path: str) -> Path:
        path = self.resolve(path)
        if path == "/":
            return self._root
        # Try VFS-relative path first (join with root)
        vfs_relative = path.lstrip("/")
        candidate = self._root / vfs_relative
        if candidate.exists():
            return candidate
        # If VFS-relative path doesn't exist, check if it's an absolute local path
        # that exists outside the VFS root
        path_obj = Path(path)
        if path_obj.is_absolute() and path_obj.exists():
            try:
                path_obj.relative_to(self._root)
                # Under VFS root but doesn't exist - return VFS-relative anyway
                return candidate
            except ValueError:
                # Outside VFS root, use absolute path directly
                return path_obj
        # Default: VFS-relative path
        return candidate

    def _from_path(self, path: Path) -> str:
        try:
            rel = path.relative_to(self._root)
            return "/" + rel.as_posix()
        except ValueError:
            return path.as_posix()

    def exists(self, path: str) -> bool:
        return self._to_path(path).exists()

    def is_file(self, path: str) -> bool:
        return self._to_path(path).is_file()

    def is_dir(self, path: str) -> bool:
        return self._to_path(path).is_dir()

    def stat(self, path: str) -> VFSStat:
        return VFSStat.from_os_stat(self._to_path(path).stat())

    def read_text(self, path: str, encoding: str = "utf-8", errors: str = "strict") -> str:
        return self._to_path(path).read_text(encoding=encoding, errors=errors)

    def read_bytes(self, path: str) -> bytes:
        return self._to_path(path).read_bytes()

    def write_text(self, path: str, content: str, encoding: str = "utf-8") -> None:
        p = self._to_path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding=encoding)

    def write_bytes(self, path: str, content: bytes) -> None:
        p = self._to_path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)

    def mkdir(self, path: str, parents: bool = True, exist_ok: bool = True) -> None:
        self._to_path(path).mkdir(parents=parents, exist_ok=exist_ok)

    def unlink(self, path: str, missing_ok: bool = True) -> None:
        p = self._to_path(path)
        try:
            p.unlink()
        except FileNotFoundError:
            if not missing_ok:
                raise

    def rename(self, src: str, dst: str) -> None:
        self._to_path(src).rename(self._to_path(dst))

    def listdir(self, path: str) -> list[str]:
        return [p.name for p in self._to_path(path).iterdir()]

    def walk(self, path: str) -> Iterator[tuple[str, list[str], list[str]]]:
        for root, dirs, files in os.walk(self._to_path(path)):
            rel_root = self._from_path(Path(root))
            yield rel_root, dirs, files

    def glob(self, path: str, pattern: str) -> list[str]:
        base = self._to_path(path)
        matches = []
        for p in base.rglob(pattern):
            try:
                matches.append(self._from_path(p))
            except ValueError:
                pass
        return matches

    @contextlib.contextmanager
    def open(self, path: str, mode: str = "r", encoding: Optional[str] = None):
        p = self._to_path(path)
        if "b" in mode:
            f = p.open(mode)
        else:
            f = p.open(mode, encoding=encoding or "utf-8")
        try:
            yield f
        finally:
            f.close()

    def resolve(self, path: str) -> str:
        if not path.startswith("/"):
            path = self.join(self._cwd, path)
        parts = []
        for part in path.split("/"):
            if part == "" or part == ".":
                continue
            elif part == "..":
                if parts:
                    parts.pop()
            else:
                parts.append(part)
        return "/" + "/".join(parts)

    def expanduser(self, path: str) -> str:
        if path.startswith("~"):
            path = path.replace("~", self.home(), 1)
        return path

    def cwd(self) -> str:
        return self._from_path(self._cwd)

    def home(self) -> str:
        return self._from_path(Path.home())

    def chdir(self, path: str) -> None:
        self._cwd = self._to_path(path)


class VFSRegistry:
    """Registry of VFS implementations by scheme."""

    def __init__(self):
        self._schemes: dict[str, type[VFS]] = {}
        self._instances: dict[str, VFS] = {}

    def register(self, scheme: str, cls: type[VFS]) -> None:
        self._schemes[scheme] = cls

    def create(self, uri: str, **kwargs) -> VFS:
        parsed = urllib.parse.urlparse(uri)
        scheme = parsed.scheme or "file"
        cls = self._schemes.get(scheme)
        if cls is None:
            raise ValueError(f"No VFS registered for scheme: {scheme}")
        instance = cls(uri, **kwargs)
        self._instances[uri] = instance
        return instance

    def get(self, uri: str) -> Optional[VFS]:
        return self._instances.get(uri)

    def default(self) -> VFS:
        return self._instances.get("file://") or LocalVFS()


_registry = VFSRegistry()
_registry.register("file", LocalVFS)


def register_vfs(scheme: str, cls: type[VFS]) -> None:
    _registry.register(scheme, cls)


def create_vfs(uri: str, **kwargs) -> VFS:
    return _registry.create(uri, **kwargs)


def get_vfs(uri: str) -> Optional[VFS]:
    return _registry.get(uri)


def default_vfs() -> VFS:
    return _registry.default()


# --- VFS Context Management (thread-local) ---

_vfs_local: threading.local = threading.local()


def current_vfs() -> VFS:
    """Get the current VFS from thread-local context, or default."""
    return getattr(_vfs_local, "vfs", None) or default_vfs()


def set_vfs(vfs: VFS) -> None:
    """Set the current VFS explicitly in thread-local context."""
    _vfs_local.vfs = vfs


@contextlib.contextmanager
def workspace_vfs_context(workspace_name: Optional[str] = None, config_path: Optional[str] = None) -> Iterator[VFS]:
    """Context manager that sets up VFS for a workspace."""
    from .config import resolve_workspace
    from .vfs import create_vfs
    
    workspace = resolve_workspace(workspace_name, config_path)
    if not workspace:
        vfs = default_vfs()
    else:
        db_uri = workspace.get("db_uri")
        if db_uri:
            vfs = create_vfs(db_uri)
        else:
            root = workspace.get("root")
            if root:
                vfs = create_vfs(f"file://{root}")
            else:
                vfs = default_vfs()
    
    old_vfs = getattr(_vfs_local, "vfs", None)
    _vfs_local.vfs = vfs
    try:
        yield vfs
    finally:
        if old_vfs is not None:
            _vfs_local.vfs = old_vfs
        else:
            delattr(_vfs_local, "vfs")


@contextlib.contextmanager
def use_vfs(uri: str, **kwargs) -> Iterator[VFS]:
    """Context manager to set default VFS."""
    vfs = create_vfs(uri, **kwargs)
    old_default = _registry._instances.get("file://")
    _registry._instances["file://"] = vfs
    try:
        yield vfs
    finally:
        if old_default:
            _registry._instances["file://"] = old_default
        else:
            _registry._instances.pop("file://", None)


def parse_vfs_uri(uri: str) -> tuple[str, dict]:
    """Parse VFS URI into scheme and connection parameters.

    Supported formats:
    - file:///absolute/path
    - file://relative/path
    - ssh://user@host/path?port=22
    - s3://bucket/prefix?region=us-east-1
    - https://host/path
    - webdav://user:pass@host/path
    """
    parsed = urllib.parse.urlparse(uri)
    scheme = parsed.scheme or "file"
    params = dict(urllib.parse.parse_qsl(parsed.query))

    if scheme == "file":
        path = parsed.path
        if not path.startswith("/") and parsed.netloc:
            path = "/" + parsed.netloc + path
        return scheme, {"root": path or "/"}

    elif scheme == "ssh":
        params.setdefault("host", parsed.hostname)
        params.setdefault("username", parsed.username)
        params.setdefault("port", int(parsed.port) if parsed.port else 22)
        params.setdefault("root", parsed.path or "/")
        if parsed.password:
            params.setdefault("password", parsed.password)
        return scheme, params

    elif scheme == "s3":
        params.setdefault("bucket", parsed.netloc)
        params.setdefault("prefix", parsed.path.lstrip("/"))
        return scheme, params

    elif scheme in ("http", "https"):
        params.setdefault("base_url", f"{scheme}://{parsed.netloc}{parsed.path}")
        return scheme, params

    elif scheme == "webdav":
        params.setdefault("host", f"{parsed.scheme}://{parsed.netloc}")
        params.setdefault("username", parsed.username)
        params.setdefault("password", parsed.password)
        params.setdefault("root", parsed.path or "/")
        return scheme, params

    return scheme, params


# Convenience functions matching pathlib/os API for gradual migration
def vfs_exists(uri: str, path: str) -> bool:
    return get_vfs(uri).exists(path)


def vfs_read_text(uri: str, path: str, encoding: str = "utf-8") -> str:
    return get_vfs(uri).read_text(path, encoding=encoding)


def vfs_write_text(uri: str, path: str, content: str, encoding: str = "utf-8") -> None:
    get_vfs(uri).write_text(path, content, encoding=encoding)


def vfs_mkdir(uri: str, path: str, parents: bool = True, exist_ok: bool = True) -> None:
    get_vfs(uri).mkdir(path, parents=parents, exist_ok=exist_ok)


def vfs_stat(uri: str, path: str) -> VFSStat:
    return get_vfs(uri).stat(path)


def vfs_resolve(uri: str, path: str) -> str:
    return get_vfs(uri).resolve(path)