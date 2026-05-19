import multiprocessing as mp
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ragbaz_frog import store


def _migrate_worker(db):
    r = store.migrate(db)
    return (r.get("ok"), tuple(r.get("applied", [])))


class SplitSql(unittest.TestCase):
    def test_splits_statements_and_ignores_comments(self):
        sql = ("-- a comment\n"
               "CREATE TABLE x(id INTEGER);\n"
               "-- another; with a semicolon in the comment\n"
               "ALTER TABLE x ADD COLUMN tags TEXT "
               "NOT NULL DEFAULT '[]';\n"
               "CREATE INDEX ix ON x(id);")
        stmts = store._split_sql(sql)
        self.assertEqual(len(stmts), 3)
        self.assertTrue(stmts[0].startswith("CREATE TABLE x"))
        # the ';' inside DEFAULT '[]' must NOT split
        self.assertIn("DEFAULT '[]'", stmts[1])
        self.assertTrue(stmts[2].startswith("CREATE INDEX"))

    def test_quoted_semicolon_not_a_boundary(self):
        stmts = store._split_sql(
            "INSERT INTO t(v) VALUES ('a;b'); CREATE TABLE q(id);")
        self.assertEqual(len(stmts), 2)
        self.assertIn("'a;b'", stmts[0])


class Atomicity(unittest.TestCase):
    def _mig_dir(self, files):
        d = Path(tempfile.mkdtemp()) / "m"
        d.mkdir()
        for name, body in files.items():
            (d / name).write_text(body)
        return d

    def test_failed_migration_rolls_back_fully_and_is_recoverable(self):
        import shutil
        # real migrations + one appended broken migration -> realistic
        d = Path(tempfile.mkdtemp()) / "m"
        d.mkdir()
        for f in sorted(store.migration_dir().glob("*.sql")):
            shutil.copy(f, d / f.name)
        (d / "999_bad.sql").write_text(
            "CREATE TABLE half_baked(id INTEGER);\nTHIS IS NOT SQL;")
        db = str(Path(tempfile.mkdtemp()) / "AGENTS.db")
        with mock.patch.object(store, "migration_dir", lambda: d):
            r = store.migrate(db)
        self.assertFalse(r["ok"])
        self.assertIn("rolled back", r["error"])
        self.assertIn("999_bad.sql", r["error"])
        c = sqlite3.connect(db)
        try:
            names = {x[0] for x in c.execute(
                "SELECT name FROM schema_migrations")}
            self.assertIn("001_initial.sql", names)   # real ones committed
            self.assertNotIn("999_bad.sql", names)     # failed one absent
            tbls = {x[0] for x in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertNotIn("half_baked", tbls)       # partial DDL rolled back
        finally:
            c.close()
        # recoverable: fix the broken migration, re-run, it applies
        (d / "999_bad.sql").write_text(
            "CREATE TABLE half_baked(id INTEGER);")
        with mock.patch.object(store, "migration_dir", lambda: d):
            r2 = store.migrate(db)
        self.assertTrue(r2["ok"], r2)
        self.assertEqual(r2["applied"], ["999_bad.sql"])

class Concurrency(unittest.TestCase):
    def test_parallel_migrate_applies_each_exactly_once(self):
        db = str(Path(tempfile.mkdtemp()) / "AGENTS.db")
        total = len(list(store.migration_dir().glob("*.sql")))
        with mp.Pool(6) as pool:
            results = pool.map(_migrate_worker, [db] * 6)
        self.assertTrue(all(ok for ok, _ in results),
                        f"a migrator failed: {results}")
        # union of every process's applied == the full set, no dupes
        applied = [m for _, lst in results for m in lst]
        self.assertEqual(sorted(applied),
                         sorted(set(applied)))   # no migration applied twice
        self.assertEqual(len(set(applied)), total)
        c = sqlite3.connect(db)
        try:
            n = c.execute(
                "SELECT count(*) FROM schema_migrations").fetchone()[0]
            self.assertEqual(n, total)
        finally:
            c.close()


if __name__ == "__main__":
    unittest.main()
