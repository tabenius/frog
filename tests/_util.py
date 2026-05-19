"""Shared test helpers: a throwaway migrated DB, zero external deps."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Keep the test suite hermetic: _box_id() persists a pinned id under
# FROG_HOME (default ~/.config/frog). Point it at a throwaway dir so
# running tests never writes to the developer's real home and box
# identity stays deterministic across the run.
os.environ.setdefault("FROG_HOME", tempfile.mkdtemp(prefix="frog-home-"))

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ragbaz_frog import store  # noqa: E402


def fresh_db() -> str:
    d = tempfile.mkdtemp(prefix="frog-test-")
    db = str(Path(d) / "AGENTS.db")
    store.migrate(db)
    return db
