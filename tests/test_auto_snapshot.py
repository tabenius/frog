import os
import tempfile
import unittest
from pathlib import Path

from _util import fresh_db
from ragbaz_frog import store


class AutoSnapshot(unittest.TestCase):
    def setUp(self):
        self.db = fresh_db()
        self.conn = store.connect(self.db)

    def tearDown(self):
        self.conn.close()
        for f in Path(self.db).parent.glob("*.pre-*"):
            f.unlink()

    def _presnap(self, label):
        # auto_snapshot writes to /data/backups when writable, else
        # next to the db -- accept either.
        a = Path("/data/backups") / f"{Path(self.db).name}.pre-{label}"
        b = Path(self.db).parent / f"{Path(self.db).name}.pre-{label}"
        return a if a.exists() else b

    def test_repo_move_takes_presnapshot(self):
        store.register_repo(self.conn, repo_path="/o/r", name="r",
                            kind=None, status="active",
                            third_party=False, notes=None)
        self.conn.commit()
        r = store.repo_move(self.conn, "/o/r", "/n/r")
        self.assertTrue(r["ok"], r)
        self.assertTrue(self._presnap("repo-move").exists())

    def test_db_gc_takes_presnapshot(self):
        store.db_gc(self.conn, keep=10)
        self.assertTrue(self._presnap("db-gc").exists())

    def test_env_disables(self):
        os.environ["FROG_NO_AUTOSNAPSHOT"] = "1"
        try:
            r = store.auto_snapshot(self.conn, label="x")
            self.assertEqual(r.get("skipped"), "disabled")
        finally:
            os.environ.pop("FROG_NO_AUTOSNAPSHOT", None)

    def test_label_sanitized(self):
        r = store.auto_snapshot(self.conn, label="weird/../label")
        self.assertTrue(r["ok"])
        self.assertNotIn("/", Path(r["path"]).name.split(".pre-")[1])

    def test_snapshot_is_a_valid_db(self):
        import sqlite3
        r = store.auto_snapshot(self.conn, label="probe")
        c = sqlite3.connect(r["path"])
        try:
            c.execute("SELECT count(*) FROM schema_migrations").fetchone()
        finally:
            c.close()


if __name__ == "__main__":
    unittest.main()
