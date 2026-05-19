import io, contextlib, json, tempfile, unittest
from pathlib import Path
from ragbaz_frog import main_cli


class ConsolidateSync(unittest.TestCase):
    def _json(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main_cli.main([*argv])
        return rc, buf.getvalue()

    def setUp(self):
        self.db = str(Path(tempfile.mkdtemp()) / "AGENTS.db")
        self._json("--db", self.db, "db", "migrate")

    def test_box_groups_sync(self):
        p = main_cli.build_parser()
        import argparse
        box = None
        for a in p._actions:
            if isinstance(a, argparse._SubParsersAction):
                box = a.choices.get("box")
        self.assertIsNotNone(box)
        subs = []
        for a in box._actions:
            if isinstance(a, argparse._SubParsersAction):
                subs += list(a.choices)
        self.assertEqual(set(subs), {"whoami", "peers", "join", "sync"})

    def test_top_level_sync_alias_still_works(self):
        rc, out = self._json("--db", self.db, "--json", "sync", "list")
        self.assertEqual(rc, 0)
        self.assertTrue(json.loads(out)["ok"])

    def test_box_sync_and_alias_are_equivalent(self):
        _, a = self._json("--db", self.db, "--json", "sync", "list")
        _, b = self._json("--db", self.db, "--json", "box", "sync", "list")
        self.assertEqual(json.loads(a), json.loads(b))


if __name__ == "__main__":
    unittest.main()
