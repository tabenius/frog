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


class RepoTreeBoxes(unittest.TestCase):
    def test_snapshot_has_local_and_peer_boxes(self):
        import os, subprocess
        d = tempfile.mkdtemp()
        c = store.connect(fresh_db())
        r = _mk_repo(d, "repo")
        store.register_repo(c, repo_path=r, name="rr", kind=None,
                            status="active", third_party=False, notes=None)
        store.ensure_repo_key(c, r)
        c.execute("INSERT INTO peers(box_id,hostname,ssh_target,"
                  "remote_db,added_at,last_join_at) VALUES"
                  "('boxB','hB','hB',NULL,'t','t')")
        key = c.execute("SELECT repo_key FROM repos WHERE repo_path=?",
                        (r,)).fetchone()[0]
        c.execute("INSERT INTO repo_aliases(repo_key,box,repo_path,"
                  "created_at) VALUES(?, 'boxB','/srv/rr','now')", (key,))
        c.commit()
        s = store.repo_tree_snapshot(c)
        ids = [(b["box_id"], b["is_local"]) for b in s["boxes"]]
        self.assertIn((store._box_id(), True), ids)
        peer = [b for b in s["boxes"] if b["box_id"] == "boxB"][0]
        self.assertFalse(peer["is_local"])
        self.assertEqual(peer["repos"][0]["on_path"], "/srv/rr")
        self.assertTrue(peer["repos"][0]["remote"])
        c.close()


class RepoViewBoxMode(unittest.TestCase):
    def _snap(self):
        local = {"repo_path": "/p/a", "name": "a", "repo_key": "k",
                 "status": "active", "third_party": False,
                 "units": ["u1"], "actions": ["build"]}
        remote = {"repo_path": "/srv/a", "on_path": "/srv/a",
                  "name": "a", "repo_key": "k", "status": "active",
                  "third_party": False, "units": [],
                  "actions": ["build"], "remote": True}
        return {
            "repos": [local],
            "tree": {"name": "/p", "path": "/p", "dirs": [],
                     "repos": [local]},
            "boxes": [
                {"box_id": "boxA", "hostname": "hA", "is_local": True,
                 "ssh_target": None,
                 "repos": [dict(local, on_path="/p/a")]},
                {"box_id": "boxB", "hostname": "hB", "is_local": False,
                 "ssh_target": "hB", "repos": [remote]},
            ],
            "local_box": "boxA",
        }

    def test_box_mode_lists_boxes_folded(self):
        rv = RepoView(self._snap())
        rv.toggle_boxes()
        rows = rv.visible_rows()
        self.assertEqual([r["kind"] for r in rows], ["box", "box"])
        self.assertFalse(rows[0]["expanded"])

    def test_expand_box_reveals_its_repos(self):
        rv = RepoView(self._snap())
        rv.toggle_boxes()
        rv.sel = 1                       # boxB (peer)
        rv.toggle()
        rows = rv.visible_rows()
        self.assertEqual([r["kind"] for r in rows],
                         ["box", "box", "repo"])
        self.assertIn("(remote)", rows[2]["text"])
        # expand the remote repo -> @path info + actions, no units
        rv.sel = 2
        rv.toggle()
        kinds = [r["kind"] for r in rv.visible_rows()]
        self.assertEqual(kinds, ["box", "box", "repo", "unit",
                                 "actions"])
        info = rv.visible_rows()[3]["text"]
        self.assertIn("/srv/a", info)

    def _expand_named(self, rv, kind, needle):
        for i, r in enumerate(rv.visible_rows()):
            if r["kind"] == kind and needle in r["key"]:
                rv.sel = i
                rv.toggle()
                return
        raise AssertionError(f"no {kind} row matching {needle}")

    def test_same_repo_under_two_boxes_folds_independently(self):
        rv = RepoView(self._snap())
        rv.toggle_boxes()
        self._expand_named(rv, "box", "boxA")   # expand boxA
        self._expand_named(rv, "box", "boxB")   # expand boxB
        # box-prefixed fold keys: expand only boxA's copy of the repo
        self._expand_named(rv, "repo", "boxA::/p/a")
        rows = rv.visible_rows()
        self.assertTrue(any(r["kind"] == "unit" and "u1" in r["text"]
                            for r in rows))     # boxA repo expanded
        b_repo = next(r for r in rows if r["kind"] == "repo"
                      and "boxB::" in r["key"])
        self.assertFalse(b_repo["expanded"])    # boxB copy still folded

    def test_box_mode_independent_of_tree_flag(self):
        rv = RepoView(self._snap())
        rv.tree = True
        rv.toggle_boxes()                 # box_mode wins
        self.assertEqual(rv.visible_rows()[0]["kind"], "box")

if __name__ == "__main__":
    unittest.main()
