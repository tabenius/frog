import subprocess
import tempfile
import unittest
from pathlib import Path

from _util import fresh_db
from ragbaz_frog import store


def _git_repo(origin=None):
    d = tempfile.mkdtemp(prefix="frog-key-")
    subprocess.run(["git", "-C", d, "init", "-q"], check=True)
    if origin:
        subprocess.run(["git", "-C", d, "remote", "add", "origin", origin],
                        check=True)
    (Path(d) / "f.txt").write_text("x")
    subprocess.run(["git", "-C", d, "add", "-A"], check=True)
    subprocess.run(["git", "-C", d, "-c", "user.email=t@t", "-c",
                     "user.name=t", "commit", "-q", "-m", "i"], check=True)
    return d


class RepoKeys(unittest.TestCase):
    def setUp(self):
        self.conn = store.connect(fresh_db())

    def tearDown(self):
        self.conn.close()

    def test_frogid_wins(self):
        d = _git_repo(origin="git@github.com:x/y.git")
        (Path(d) / ".frogid").write_text("ragbaz/my-stable-id\n")
        self.assertEqual(store.compute_repo_key(d), "ragbaz/my-stable-id")

    def test_origin_is_stable_across_paths(self):
        a = _git_repo(origin="https://example.com/proj.git")
        b = _git_repo(origin="https://example.com/proj.git")
        # different absolute paths, same origin -> same key (the whole point)
        self.assertEqual(store.compute_repo_key(a), store.compute_repo_key(b))
        self.assertTrue(store.compute_repo_key(a).startswith("git:"))

    def test_path_fallback(self):
        d = _git_repo()  # no origin
        self.assertTrue(store.compute_repo_key(d).startswith("path:"))

    def test_register_assigns_key_and_alias(self):
        d = _git_repo(origin="https://example.com/r.git")
        store.register_repo(self.conn, repo_path=d, name="r", kind=None,
                            status="active", third_party=False, notes=None)
        key = self.conn.execute(
            "SELECT repo_key FROM repos WHERE repo_path=?", (d,)).fetchone()["repo_key"]
        self.assertTrue(key.startswith("git:"))
        al = self.conn.execute(
            "SELECT box,repo_path FROM repo_aliases WHERE repo_key=?", (key,)
        ).fetchall()
        self.assertEqual(len(al), 1)

    def test_resolve_repo_by_key_and_whereis(self):
        d = _git_repo(origin="https://example.com/z.git")
        store.register_repo(self.conn, repo_path=d, name="z", kind=None,
                            status="active", third_party=False, notes=None)
        key = store.ensure_repo_key(self.conn, d)
        # resolve_repo accepts the key
        r = store.resolve_repo(self.conn, key)
        self.assertIsNotNone(r)
        self.assertEqual(r["repo_path"], d)
        w = store.whereis(self.conn, key)
        self.assertTrue(w["ok"])
        self.assertEqual(w["local_path"], d)

    def test_cross_box_alias_resolution(self):
        d = _git_repo(origin="https://example.com/cb.git")
        store.register_repo(self.conn, repo_path=d, name="cb", kind=None,
                            status="active", third_party=False, notes=None)
        key = store.ensure_repo_key(self.conn, d)
        # simulate the SAME repo on another box at a different path
        self.conn.execute(
            "INSERT INTO repo_aliases(repo_key,box,repo_path,created_at) "
            "VALUES(?,?,?,?)", (key, "otherbox", "/srv/elsewhere/cb",
                                store.utc_now_iso()))
        self.conn.commit()
        w = store.whereis(self.conn, key)
        # this box still resolves to the local path, not the other box's
        self.assertEqual(w["local_path"], d)
        boxes = {a["box"] for a in w["aliases"]}
        self.assertIn("otherbox", boxes)


if __name__ == "__main__":
    unittest.main()
