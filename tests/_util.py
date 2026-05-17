"""Shared test helpers: a throwaway migrated DB, zero external deps."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ragbaz_frog import store  # noqa: E402


def fresh_db() -> str:
    d = tempfile.mkdtemp(prefix="frog-test-")
    db = str(Path(d) / "AGENTS.db")
    store.migrate(db)
    return db
