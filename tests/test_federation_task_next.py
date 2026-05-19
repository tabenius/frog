import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from _util import fresh_db
from ragbaz_frog import store


def _repo(key: str) -> str:
    d = tempfile.mkdtemp(prefix="frog-ftn-")
    (Path(d) / ".frogid").write_text(key + "\n")
    subprocess.run(["git", "-C", d, "init", "-q"], check=True)
    return d


class FederationTaskNext(unittest.TestCase):
    def setUp(self):
        self._bid = os.environ.get("FROG_BOX_ID")
        os.environ["FROG_BOX_ID"] = "boxA"
        self.conn = store.connect(fresh_db())

    def tearDown(self):
        self.conn.close()
        if self._bid is None:
            os.environ.pop("FROG_BOX_ID", None)
        else:
            os.environ["FROG_BOX_ID"] = self._bid

    def _mk(self, slug, repo):
        store.create_task(self.conn, slug=slug, repo_ref=repo, title=slug,
                          why=None, what_text=None, roi_note=None,
                          priority="p2", workflow_status="idea",
                          git_status="not_started", assigned_agent=None,
                          delegation_current=None, delegation_other=None,
                          parent_task_slug=None)

    def test_no_repo_task_has_no_location(self):
        self._mk("t0", None)
        r = store.task_next(self.conn, agent="claude")
        self.assertIsNone(r["tasks"][0]["location"])

    def test_local_repo_task_is_local_no_elsewhere(self):
        repo = _repo("shared:k1")
        store.register_repo(self.conn, repo_path=repo, name="r", kind=None,
                            status="active", third_party=False, notes=None)
        store.ensure_repo_key(self.conn, repo)
        self._mk("t1", repo)
        loc = store.task_next(self.conn, agent="claude")["tasks"][0]["location"]
        self.assertTrue(loc["is_local"])
        self.assertEqual(loc["elsewhere"], [])
        self.assertEqual(loc["repo_key"], "shared:k1")

    def test_shared_repo_shows_peer_location(self):
        repo = _repo("shared:k2")
        store.register_repo(self.conn, repo_path=repo, name="r", kind=None,
                            status="active", third_party=False, notes=None)
        store.ensure_repo_key(self.conn, repo)
        self._mk("t2", repo)

        def fake_exec(host, rdb, rfrog, argv):
            if argv == ["box", "whoami"]:
                return {"box_id": "boxB", "hostname": "hB"}
            return {"repos": [{"repo_path": "/remote/r",
                               "repo_key": "shared:k2"}]}
        store.federation_join(self.conn, "hB", exec=fake_exec)

        loc = store.task_next(self.conn, agent="claude")["tasks"][0]["location"]
        self.assertTrue(loc["is_local"])
        self.assertEqual([b["box"] for b in loc["elsewhere"]], ["boxB"])
        self.assertEqual(loc["elsewhere"][0]["repo_path"], "/remote/r")

    def test_remote_only_repo_is_not_local(self):
        # repo registered but its path does not exist on this box
        ghost = "/does/not/exist/here"
        store.register_repo(self.conn, repo_path=ghost, name="g", kind=None,
                            status="active", third_party=False, notes=None)
        self.conn.execute(
            "UPDATE repos SET repo_key='remote:only' WHERE repo_path=?",
            (ghost,))
        self.conn.execute(
            "INSERT INTO repo_aliases(repo_key,box,repo_path,created_at) "
            "VALUES('remote:only','boxB','/peer/g',?)",
            (store.utc_now_iso(),))
        self.conn.commit()
        self._mk("t3", ghost)
        loc = store.task_next(self.conn, agent="claude")["tasks"][0]["location"]
        self.assertFalse(loc["is_local"])
        self.assertIsNone(loc["local_path"])
        self.assertEqual([b["box"] for b in loc["elsewhere"]], ["boxB"])

    def test_task_info_also_carries_location(self):
        self._mk("t4", None)
        info = store.task_info(self.conn, "t4")
        self.assertIn("location", info)

    def test_cli_next_renders_remote_hint(self):
        import shutil, sqlite3
        d = tempfile.mkdtemp(); db = Path(d) / "AGENTS.db"
        repo = _repo("ro:1")               # real repo so registration works
        env = dict(os.environ, FROG_BOX_ID="boxA")
        run = lambda *a: subprocess.run(
            ["python3", "bin/frog", "--db", str(db), *a],
            capture_output=True, text=True, env=env)
        run("db", "migrate")
        run("repo", "register", repo)
        run("task", "create", "--slug", "rt", "--title", "RT",
            "--repo", repo)
        # the repo now lives only on a peer + is gone from this box
        c = sqlite3.connect(db)
        c.execute("UPDATE repos SET repo_key='ro:1' WHERE repo_path=?",
                  (repo,))
        c.execute("INSERT INTO repo_aliases(repo_key,box,repo_path,"
                  "created_at) VALUES('ro:1','boxB','/peer/x','now')")
        c.commit(); c.close()
        shutil.rmtree(repo)
        out = run("task", "next").stdout
        self.assertIn("not on this box", out)
        self.assertIn("boxB:/peer/x", out)


if __name__ == "__main__":
    unittest.main()
