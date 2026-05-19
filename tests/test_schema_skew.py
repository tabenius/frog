import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _util import fresh_db
from ragbaz_frog import store


def _behind_db() -> tuple[str, str]:
    """A DB genuinely migrated by an *older* code generation: built with
    every migration except the newest, so the newest is authentically
    unapplied (its DDL never ran) -- exactly real code-ahead drift."""
    real_dir = store.migration_dir()
    files = sorted(real_dir.glob("*.sql"))
    last = files[-1].name
    tmp = Path(tempfile.mkdtemp()) / "migrations"
    tmp.mkdir(parents=True)
    for f in files[:-1]:
        shutil.copy(f, tmp / f.name)
    d = tempfile.mkdtemp()
    db = str(Path(d) / "AGENTS.db")
    with mock.patch.object(store, "migration_dir", lambda: tmp):
        store.migrate(db)
    return db, last


class SchemaDrift(unittest.TestCase):
    def test_fresh_db_is_current(self):
        c = store.connect(fresh_db())
        try:
            d = store.schema_drift(c)
            self.assertTrue(d["current"])
            self.assertEqual(d["behind"], [])
            self.assertEqual(d["applied"], d["available"])
        finally:
            c.close()

    def test_code_ahead_db_is_behind(self):
        db, last = _behind_db()
        c = store.connect(db)
        try:
            d = store.schema_drift(c)
            self.assertFalse(d["current"])
            self.assertEqual(d["behind"], [last])
        finally:
            c.close()


class CliGuard(unittest.TestCase):
    def setUp(self):
        self.db, self.last = _behind_db()

    def _run(self, *a, env=None):
        return subprocess.run(
            ["python3", "bin/frog", "--db", self.db, *a],
            capture_output=True, text=True,
            env={**os.environ, **(env or {})})

    def test_normal_command_refused_with_remediation(self):
        r = self._run("task", "list")
        self.assertNotEqual(r.returncode, 0)
        out = r.stdout + r.stderr
        self.assertIn("schema is behind", out)
        self.assertIn("db migrate", out)

    def test_db_migrate_still_allowed_and_heals(self):
        r = self._run("db", "migrate")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r2 = self._run("task", "list")
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)

    def test_override_env_bypasses(self):
        r = self._run("task", "list", env={"FROG_ALLOW_SCHEMA_SKEW": "1"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
