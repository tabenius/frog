import unittest

from _util import fresh_db
from ragbaz_frog import store


class ConnectPragmas(unittest.TestCase):
    def test_wal_and_timeouts_set(self):
        db = fresh_db()
        conn = store.connect(db)
        try:
            self.assertEqual(
                conn.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal"
            )
            self.assertEqual(
                conn.execute("PRAGMA busy_timeout").fetchone()[0], 5000
            )
            # synchronous NORMAL == 1
            self.assertEqual(conn.execute("PRAGMA synchronous").fetchone()[0], 1)
            self.assertEqual(
                conn.execute("PRAGMA foreign_keys").fetchone()[0], 1
            )
        finally:
            conn.close()

    def test_migrate_is_idempotent(self):
        db = fresh_db()
        again = store.migrate(db)
        self.assertTrue(again["ok"])
        self.assertEqual(again["applied"], [])


if __name__ == "__main__":
    unittest.main()
