import subprocess
import tempfile
import unittest
from pathlib import Path

from _util import fresh_db
from ragbaz_frog import store


def _repo_with_target(cmd: str) -> str:
    d = tempfile.mkdtemp(prefix="frog-cache-")
    subprocess.run(["git", "-C", d, "init", "-q"], check=True)
    (Path(d) / "src.txt").write_text("v1\n")
    (Path(d) / "Makefile").write_text(f"build:\n\t{cmd}\n")
    subprocess.run(["git", "-C", d, "add", "-A"], check=True)
    subprocess.run(["git", "-C", d, "-c", "user.email=t@t", "-c",
                     "user.name=t", "commit", "-q", "-m", "i"], check=True)
    return d


class TargetCache(unittest.TestCase):
    def setUp(self):
        self.db = fresh_db()
        self.conn = store.connect(self.db)
        self.repo = _repo_with_target("true")
        store.register_repo(self.conn, repo_path=self.repo, name="c",
                            kind=None, status="active",
                            third_party=False, notes=None)

    def tearDown(self):
        self.conn.close()

    def test_second_run_is_cached_then_busted_by_edit(self):
        r1 = store.repo_run(self.conn, self.repo, "build")
        self.assertTrue(r1["ok"], r1)
        self.assertEqual(r1["results"][0]["status"], "ran")

        r2 = store.repo_run(self.conn, self.repo, "build")
        self.assertTrue(r2["ok"])
        self.assertEqual(r2["results"][0]["status"], "cached",
                         "unchanged inputs must hit the cache")

        # edit a tracked source file -> fingerprint changes -> re-run
        (Path(self.repo) / "src.txt").write_text("v2\n")
        r3 = store.repo_run(self.conn, self.repo, "build")
        self.assertEqual(r3["results"][0]["status"], "ran",
                         "a working-tree edit must bust the cache")

    def test_no_cache_forces_run(self):
        store.repo_run(self.conn, self.repo, "build")
        r = store.repo_run(self.conn, self.repo, "build", use_cache=False)
        self.assertEqual(r["results"][0]["status"], "ran")

    def test_failed_target_not_cached(self):
        bad = _repo_with_target("false")
        store.register_repo(self.conn, repo_path=bad, name="bad", kind=None,
                            status="active", third_party=False, notes=None)
        r1 = store.repo_run(self.conn, bad, "build")
        self.assertFalse(r1["ok"])
        r2 = store.repo_run(self.conn, bad, "build")
        self.assertEqual(r2["results"][0]["status"], "ran",
                         "a failed run must not be cached as success")


if __name__ == "__main__":
    unittest.main()
