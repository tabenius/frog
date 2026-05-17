import subprocess
import tempfile
import unittest
from pathlib import Path

from _util import fresh_db
from ragbaz_frog import store


def _repo() -> str:
    d = tempfile.mkdtemp(prefix="frog-aff-")
    subprocess.run(["git", "-C", d, "init", "-q"], check=True)
    (Path(d) / "Makefile").write_text("build:\n\ttrue\ntest:\n\ttrue\n")
    (Path(d) / "src.txt").write_text("v1\n")
    subprocess.run(["git", "-C", d, "add", "-A"], check=True)
    subprocess.run(["git", "-C", d, "-c", "user.email=t@t", "-c",
                     "user.name=t", "commit", "-q", "-m", "i"], check=True)
    return d


class RepoDiffAffected(unittest.TestCase):
    def setUp(self):
        self.db = fresh_db()
        self.conn = store.connect(self.db)
        self.repo = _repo()
        store.register_repo(self.conn, repo_path=self.repo, name="a",
                            kind=None, status="active",
                            third_party=False, notes=None)

    def tearDown(self):
        self.conn.close()

    def test_repo_diff_returns_patch_and_does_not_crash(self):
        # repo_diff was previously missing entirely (AttributeError).
        (Path(self.repo) / "src.txt").write_text("v2\n")
        r = store.repo_diff(self.conn, self.repo, include_impact=True,
                            include_tasks=True)
        self.assertTrue(r["ok"])
        self.assertIn("src.txt", r["diff"])
        self.assertIn("impacted_targets", r)
        self.assertIn("tasks", r)

    def test_clean_tree_has_no_affected(self):
        r = store.repo_affected(self.conn, self.repo)
        self.assertTrue(r["ok"])
        self.assertEqual(r["changed_files"], [])
        self.assertEqual(r["affected"], [])

    def test_change_makes_targets_affected(self):
        (Path(self.repo) / "src.txt").write_text("v2\n")
        r = store.repo_affected(self.conn, self.repo)
        self.assertTrue(r["ok"])
        self.assertEqual(len(r["changed_files"]), 1)
        self.assertTrue(len(r["affected"]) >= 1)

    def test_build_affected_runs_only_affected(self):
        # nothing changed -> --affected build runs nothing
        empty = store.repo_affected(self.conn, self.repo, target_kind="build")
        names = {t["name"] for t in empty["affected"]}
        r = store.repo_run(self.conn, self.repo, "build", only_targets=names)
        self.assertTrue(r["ok"])
        self.assertEqual(r["results"], [], "no change => no affected build run")


if __name__ == "__main__":
    unittest.main()
