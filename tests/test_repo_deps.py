import subprocess
import tempfile
import unittest
from pathlib import Path

from _util import fresh_db
from ragbaz_frog import store


def _repo(name_file="src.txt"):
    d = tempfile.mkdtemp(prefix="frog-dep-")
    subprocess.run(["git", "-C", d, "init", "-q"], check=True)
    (Path(d) / "Makefile").write_text("build:\n\ttrue\n")
    (Path(d) / name_file).write_text("v1\n")
    subprocess.run(["git", "-C", d, "add", "-A"], check=True)
    subprocess.run(["git", "-C", d, "-c", "user.email=t@t", "-c",
                     "user.name=t", "commit", "-q", "-m", "i"], check=True)
    return d


class RepoDeps(unittest.TestCase):
    def setUp(self):
        self.db = fresh_db()
        self.conn = store.connect(self.db)
        self.up = _repo()
        self.down = _repo()
        for path, nm in ((self.up, "up"), (self.down, "down")):
            store.register_repo(self.conn, repo_path=path, name=nm,
                                kind=None, status="active",
                                third_party=False, notes=None)

    def tearDown(self):
        self.conn.close()

    def test_add_and_list_and_self_dep_rejected(self):
        r = store.repo_dep_add(self.conn, "down", "up", note="links")
        self.assertTrue(r["ok"])
        lst = store.repo_dep_list(self.conn)
        self.assertEqual(len(lst["deps"]), 1)
        self.assertFalse(store.repo_dep_add(self.conn, "up", "up")["ok"])
        self.assertFalse(store.repo_dep_add(self.conn, "up", "nope")["ok"])

    def test_upstream_change_fans_out_to_dependent(self):
        store.repo_dep_add(self.conn, "down", "up")
        # change upstream
        (Path(self.up) / "src.txt").write_text("v2\n")
        aff = store.repo_affected(self.conn, self.up)
        self.assertTrue(aff["ok"])
        self.assertIn("downstream", aff)
        names = {d["repo"]["name"] for d in aff["downstream"]}
        self.assertIn("down", names)

    def test_no_change_no_fanout(self):
        store.repo_dep_add(self.conn, "down", "up")
        aff = store.repo_affected(self.conn, self.up)
        self.assertNotIn("downstream", aff)


if __name__ == "__main__":
    unittest.main()
