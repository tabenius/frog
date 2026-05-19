import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from _util import fresh_db
from ragbaz_frog import store
from ragbaz_frog.tui import RepoView


def _mk_repo(base, rel):
    r = os.path.join(base, rel)
    os.makedirs(r, exist_ok=True)
    subprocess.run(["git", "-C", r, "init", "-q"], check=True)
    return r


class RepoTreeSnapshot(unittest.TestCase):
    def test_groups_repos_and_builds_tree(self):
        d = tempfile.mkdtemp()
        c = store.connect(fresh_db())
        for rel in ("proj/alpha", "proj/beta", "proj/sub/gamma"):
            r = _mk_repo(d, rel)
            store.register_repo(c, repo_path=r, name=rel.split("/")[-1],
                                kind=None, status="active",
                                third_party=False, notes=None)
        c.commit()
        s = store.repo_tree_snapshot(c)
        self.assertEqual([x["name"] for x in s["repos"]],
                         ["alpha", "beta", "gamma"])
        for it in s["repos"]:
            self.assertIn("units", it)
            self.assertIn("actions", it)
        # tree has a 'sub' directory holding gamma
        names = {dd["name"] for dd in s["tree"]["dirs"]}
        self.assertIn("sub", names)
        c.close()


class RepoViewFlat(unittest.TestCase):
    def _snap(self):
        return {
            "repos": [
                {"repo_path": "/p/a", "name": "a", "repo_key": "k1",
                 "status": "active", "third_party": False,
                 "units": ["u1", "u2"], "actions": ["build", "test"]},
                {"repo_path": "/p/b", "name": "b", "repo_key": "k2",
                 "status": "active", "third_party": False,
                 "units": [], "actions": ["build"]},
            ],
            "tree": {"name": "/p", "path": "/p", "repos": [], "dirs": [
                {"name": "a", "path": "/p/a", "dirs": [], "repos": []}]},
        }

    def test_folded_by_default(self):
        rv = RepoView(self._snap())
        rows = rv.visible_rows()
        self.assertEqual([r["kind"] for r in rows], ["repo", "repo"])
        self.assertTrue(all(not r["expanded"] for r in rows))

    def test_expand_shows_units_then_actions(self):
        rv = RepoView(self._snap())
        rv.sel = 0
        rv.toggle()
        rows = rv.visible_rows()
        kinds = [r["kind"] for r in rows]
        self.assertEqual(kinds, ["repo", "unit", "unit", "actions",
                                 "repo"])
        self.assertIn("build test", rows[3]["text"])
        rv.toggle()  # collapse
        self.assertEqual([r["kind"] for r in rv.visible_rows()],
                         ["repo", "repo"])

    def test_toggle_only_on_foldable_and_nav_wraps(self):
        rv = RepoView(self._snap())
        rv.sel = 0
        rv.toggle()                       # expand a -> unit rows appear
        rv.move(1)                        # onto a unit (not foldable)
        self.assertEqual(rv.selected()["kind"], "unit")
        before = len(rv.visible_rows())
        rv.toggle()                       # no-op on a unit
        self.assertEqual(len(rv.visible_rows()), before)
        n = len(rv.visible_rows())
        rv.sel = n - 1
        rv.move(1)
        self.assertEqual(rv.sel, 0)       # wraps

    def test_empty_snapshot_is_safe(self):
        rv = RepoView({"repos": [],
                       "tree": {"dirs": [], "repos": [], "path": "/"}})
        self.assertEqual(rv.visible_rows(), [])
        self.assertIsNone(rv.selected())
        rv.move(1); rv.toggle(); rv.to_edge(True)  # no crash


class RepoViewTree(unittest.TestCase):
    def _snap(self):
        repo = {"repo_path": "/p/sub/g", "name": "g", "repo_key": "k",
                "status": "active", "third_party": False,
                "units": ["x"], "actions": ["build"]}
        return {
            "repos": [repo],
            "tree": {"name": "/p", "path": "/p", "repos": [], "dirs": [
                {"name": "sub", "path": "/p/sub", "repos": [repo],
                 "dirs": []}]},
        }

    def test_tree_dirs_collapsed_then_expand_reveals_repo(self):
        rv = RepoView(self._snap())
        rv.toggle_tree()
        rows = rv.visible_rows()
        self.assertEqual([r["kind"] for r in rows], ["dir"])
        self.assertFalse(rows[0]["expanded"])
        rv.sel = 0
        rv.toggle()                       # expand the dir
        rows = rv.visible_rows()
        self.assertEqual([r["kind"] for r in rows], ["dir", "repo"])
        # and the repo under it still folds to units/actions
        rv.sel = 1
        rv.toggle()
        self.assertEqual([r["kind"] for r in rv.visible_rows()],
                         ["dir", "repo", "unit", "actions"])

    def test_toggle_tree_preserves_expanded_keys(self):
        rv = RepoView(self._snap())
        rv.sel = 0
        rv.toggle()                       # expand repo in flat mode
        self.assertIn("/p/sub/g", rv.expanded)
        rv.toggle_tree()                  # switch to tree
        self.assertIn("/p/sub/g", rv.expanded)  # key survives


if __name__ == "__main__":
    unittest.main()
