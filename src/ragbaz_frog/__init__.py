"""ragbaz-frog: Multi-agent workspace coordination toolkit."""

from __future__ import annotations

# VFS abstraction
from .vfs import (
    VFS,
    VFSStat,
    LocalVFS,
    VFSRegistry,
    register_vfs,
    create_vfs,
    get_vfs,
    default_vfs,
    use_vfs,
    parse_vfs_uri,
    vfs_exists,
    vfs_read_text,
    vfs_write_text,
    vfs_mkdir,
    vfs_stat,
    vfs_resolve,
    set_local_only_mode,
    is_local_only_mode,
)

# Register built-in VFS implementations
from .vfs_ssh import register_ssh_vfs
from .vfs_s3 import register_s3_vfs
from .vfs_http import register_http_vfs, register_webdav_vfs

register_ssh_vfs()
register_s3_vfs()
register_http_vfs()
register_webdav_vfs()

DEFAULT_DB_PATH = "/data/src/AGENTS.db"
DEFAULT_CONFIG_PATH = "~/.config/frog/config.yaml"

__all__ = [
    "VFS",
    "VFSStat",
    "LocalVFS",
    "VFSRegistry",
    "register_vfs",
    "create_vfs",
    "get_vfs",
    "default_vfs",
    "use_vfs",
    "parse_vfs_uri",
    "vfs_exists",
    "vfs_read_text",
    "vfs_write_text",
    "vfs_mkdir",
    "vfs_stat",
    "vfs_resolve",
    "set_local_only_mode",
    "is_local_only_mode",
    "vfs_from_local_path",
    "DEFAULT_DB_PATH",
    "DEFAULT_CONFIG_PATH",
]
