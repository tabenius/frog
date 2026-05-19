"""Database lifecycle / admin surface of the store.

Curated re-export view (source of truth remains ``store``). Names the
DB-lifecycle seam: connect/migrate, schema-skew detection, gc, and the
backup paths used before destructive ops.
"""
from __future__ import annotations

from ragbaz_frog.store import (  # noqa: F401
    connect,
    migrate,
    migration_dir,
    schema_status,
    schema_drift,
    db_gc,
    snapshot_workspace,
    auto_snapshot,
    doctor,
)

__all__ = [
    "connect", "migrate", "migration_dir", "schema_status",
    "schema_drift", "db_gc", "snapshot_workspace", "auto_snapshot",
    "doctor",
]
