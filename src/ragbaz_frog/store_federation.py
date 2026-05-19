"""Federation / multi-box surface of the store.

Curated re-export view -- the single source of truth is still
``store`` (no behaviour change, every existing ``store.X`` caller is
untouched). This module names the federation seam: box identity,
joining peers, and cross-box repo resolution. New code should import
from here for clarity; a future physical relocation follows exactly
these boundaries.
"""
from __future__ import annotations

from ragbaz_frog.store import (  # noqa: F401
    _box_id,
    _box_id_path,
    ensure_box_identity,
    box_whoami,
    _parse_ssh_target,
    _remote_exec,
    federation_join,
    peers_list,
    whereis,
    compute_repo_key,
    ensure_repo_key,
)

__all__ = [
    "_box_id", "_box_id_path", "ensure_box_identity", "box_whoami",
    "_parse_ssh_target", "_remote_exec", "federation_join",
    "peers_list", "whereis", "compute_repo_key", "ensure_repo_key",
]
