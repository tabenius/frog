"""ragbaz-frog: Multi-agent workspace coordination toolkit."""

from __future__ import annotations

# VFS abstraction
from .vfs import (
    VFS,
    LocalVFS,
    VFSRegistry,
    VFSStat,
    create_vfs,
    default_vfs,
    get_vfs,
    is_local_only_mode,
    parse_vfs_uri,
    register_vfs,
    set_local_only_mode,
    use_vfs,
    vfs_exists,
    vfs_mkdir,
    vfs_read_text,
    vfs_resolve,
    vfs_stat,
    vfs_write_text,
)
from .vfs_http import register_http_vfs, register_webdav_vfs
from .vfs_s3 import register_s3_vfs

# Register built-in VFS implementations
from .vfs_ssh import register_ssh_vfs

register_ssh_vfs()
register_s3_vfs()
register_http_vfs()
register_webdav_vfs()

DEFAULT_DB_PATH = "/data/src/AGENTS.db"
DEFAULT_CONFIG_PATH = "~/.config/frog/config.yaml"

__all__ = [
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_DB_PATH",
    "VFS",
    "LocalVFS",
    "VFSRegistry",
    "VFSStat",
    "create_vfs",
    "default_vfs",
    "get_vfs",
    "is_local_only_mode",
    "parse_vfs_uri",
    "register_vfs",
    "set_local_only_mode",
    "use_vfs",
    "vfs_exists",
    "vfs_from_local_path",
    "vfs_mkdir",
    "vfs_read_text",
    "vfs_resolve",
    "vfs_stat",
    "vfs_write_text",
]
