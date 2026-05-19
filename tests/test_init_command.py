import io, contextlib, json, shutil, tempfile, unittest
from pathlib import Path
from unittest import mock
from ragbaz_frog import main_cli, store


def _cli(*argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = main_cli.main([*argv])
    return rc, buf.getvalue()


class InitCommand(unittest.TestCase):
    def test_fresh_bootstrap(self):
        db = str(Path(tempfile.mkdtemp()) / "AGENTS.db")
        rc, out = _cli("--db", db, "--json", "init")
        self.assertEqual(rc, 0)
        d = json.loads(out)
        self.assertTrue(d["ok"])
        self.assertTrue(d["current"])
        self.assertTrue(d["box_id"])
        a, b = d["migrations"].split("/")
        self.assertEqual(a, b)
        self.assertTrue(Path(db).exists())

    def test_idempotent(self):
        db = str(Path(tempfile.mkdtemp()) / "AGENTS.db")
        _cli("--db", db, "init")
        rc, out = _cli("--db", db, "--json", "init")
        self.assertEqual(rc, 0)
        self.assertTrue(json.loads(out)["ok"])

    def test_heals_a_behind_db_and_is_not_skew_guarded(self):
        # build a DB missing the newest migration, then `init` it
        real = store.migration_dir()
        files = sorted(real.glob("*.sql"))
        tmpm = Path(tempfile.mkdtemp()) / "m"; tmpm.mkdir()
        for f in files[:-1]:
            shutil.copy(f, tmpm / f.name)
        db = str(Path(tempfile.mkdtemp()) / "AGENTS.db")
        with mock.patch.object(store, "migration_dir", lambda: tmpm):
            store.migrate(db)
        rc, out = _cli("--db", db, "--json", "init")
        self.assertEqual(rc, 0, out)
        self.assertTrue(json.loads(out)["current"])

    def test_human_output(self):
        db = str(Path(tempfile.mkdtemp()) / "AGENTS.db")
        rc, out = _cli("--db", db, "init")
        self.assertEqual(rc, 0)
        self.assertIn("initialized", out)
        self.assertIn("box", out)


if __name__ == "__main__":
    unittest.main()
