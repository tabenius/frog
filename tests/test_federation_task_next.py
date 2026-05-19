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

    def test_renderer_shows_remote_and_also_hints(self):
        # Exercise the real _emit rendering path in-process (deterministic;
        # the data paths are covered by the store-level tests above).
        import io, contextlib
        from ragbaz_frog import main_cli

        def render(loc):
            payload = {"ok": True, "agent": "claude", "considered": 1,
                       "eligible": 1, "skipped": [],
                       "tasks": [{"slug": "rt", "priority": "p2",
                                  "title": "RT", "location": loc}]}
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                main_cli._emit(payload, False)
            return buf.getvalue()

        remote = render({"repo_key": "ro:1", "is_local": False,
                         "local_path": None, "boxes": [],
                         "elsewhere": [{"box": "boxB",
                                        "repo_path": "/peer/x"}]})
        self.assertIn("not on this box", remote)
        self.assertIn("boxB:/peer/x", remote)

        also = render({"repo_key": "k", "is_local": True,
                       "local_path": "/here", "boxes": [],
                       "elsewhere": [{"box": "boxB",
                                      "repo_path": "/peer/x"}]})
        self.assertIn("also on boxB:/peer/x", also)
        self.assertNotIn("not on this box", also)

if __name__ == "__main__":
    unittest.main()
