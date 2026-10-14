"""Database lifecycle / admin surface of the store.

Curated re-export view (source of truth remains ``store``). Names the
DB-lifecycle seam: connect/migrate, schema-skew detection, gc, and the
backup paths used before destructive ops.
"""

from __future__ import annotations

from ragbaz_frog.store import (
    auto_snapshot,
    connect,
    db_gc,
    doctor,
    migrate,
    migration_dir,
    schema_drift,
    schema_status,
    snapshot_workspace,
)

__all__ = [
    "auto_snapshot",
    "connect",
    "db_gc",
    "doctor",
    "migrate",
    "migration_dir",
    "schema_drift",
    "schema_status",
    "snapshot_workspace",
]
