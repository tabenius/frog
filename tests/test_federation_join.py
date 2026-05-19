import os
import tempfile
import unittest
from pathlib import Path

from _util import fresh_db
from ragbaz_frog import store


class ParseTarget(unittest.TestCase):
    def test_variants(self):
        self.assertEqual(store._parse_ssh_target("h"), ("h", None))
        self.assertEqual(store._parse_ssh_target("u@h"), ("u@h", None))
        self.assertEqual(store._parse_ssh_target("h:/d/AGENTS.db"),
                         ("h", "/d/AGENTS.db"))
        self.assertEqual(store._parse_ssh_target("u@h:/d/a.db"),
                         ("u@h", "/d/a.db"))


def _local_repo_with_key(key: str) -> str:
    d = tempfile.mkdtemp(prefix="frog-fed-")
    (Path(d) / ".frogid").write_text(key + "\n")
    return d


class FederationJoin(unittest.TestCase):
    def setUp(self):
        self._bid = os.environ.get("FROG_BOX_ID")
        os.environ["FROG_BOX_ID"] = "boxA"
        self.conn = store.connect(fresh_db())
        self.repo = _local_repo_with_key("shared:k1")
        store.register_repo(self.conn, repo_path=self.repo, name="r1",
                            kind=None, status="active",
                            third_party=False, notes=None)
        store.ensure_repo_key(self.conn, self.repo)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        if self._bid is None:
            os.environ.pop("FROG_BOX_ID", None)
        else:
            os.environ["FROG_BOX_ID"] = self._bid

    def _fake_exec(self, box="boxB"):
        def ex(host, rdb, rfrog, argv):
            if argv == ["box", "whoami"]:
                return {"ok": True, "box_id": box, "hostname": "hostB"}
            if argv == ["repo", "list"]:
                return {"ok": True, "repos": [
                    {"repo_path": "/remote/path/r1", "repo_key": "shared:k1"},
                    {"repo_path": "/remote/only", "repo_key": "remote:only"},
                ]}
            raise AssertionError(argv)
        return ex

    def test_join_matches_and_records_alias_and_peer(self):
        r = store.federation_join(self.conn, "hostB:/srv/AGENTS.db",
                                  exec=self._fake_exec())
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["matched"], ["shared:k1"])
        self.assertEqual(r["unmatched_remote"], ["remote:only"])
        self.assertEqual(r["aliases_added"], 1)
        # whereis now resolves the peer location for the shared key
        w = store.whereis(self.conn, "shared:k1")
        boxes = {a["box"]: a["repo_path"] for a in w["aliases"]}
        self.assertEqual(boxes.get("boxB"), "/remote/path/r1")
        # peer registered
        pl = store.peers_list(self.conn)
        self.assertEqual(pl["count"], 1)
        self.assertEqual(pl["peers"][0]["box_id"], "boxB")
        self.assertEqual(pl["peers"][0]["remote_db"], "/srv/AGENTS.db")
        kinds = [x[0] for x in self.conn.execute(
            "SELECT kind FROM event_log")]
        self.assertIn("federation.join", kinds)

    def test_refuses_to_join_self(self):
        r = store.federation_join(self.conn, "hostX",
                                  exec=self._fake_exec(box="boxA"))
        self.assertFalse(r["ok"])
        self.assertIn("self", r["error"])

    def test_remote_errors_are_reported(self):
        def boom(host, rdb, rfrog, argv):
            raise RuntimeError("ssh failed")

        r = store.federation_join(self.conn, "hostB", exec=boom)
        self.assertFalse(r["ok"])
        self.assertEqual(r["host"], "hostB")
        self.assertIn("ssh failed", r["error"])

    def test_empty_target_is_rejected(self):
        r = store.federation_join(self.conn, "   ", exec=self._fake_exec())
        self.assertFalse(r["ok"])
        self.assertIn("empty", r["error"])

    def test_rejoin_is_idempotent(self):
        store.federation_join(self.conn, "hostB", exec=self._fake_exec())
        store.federation_join(self.conn, "hostB", exec=self._fake_exec())
        n_alias = self.conn.execute(
            "SELECT count(*) FROM repo_aliases WHERE box='boxB'"
        ).fetchone()[0]
        self.assertEqual(n_alias, 1)
        self.assertEqual(store.peers_list(self.conn)["count"], 1)

    def test_peers_list_reports_missing_migration(self):
        db = Path(tempfile.mkdtemp(prefix="frog-fed-old-")) / "AGENTS.db"
        conn = store.connect(str(db))
        try:
            r = store.peers_list(conn)
        finally:
            conn.close()
        self.assertFalse(r["ok"])
        self.assertTrue(r["needs_migration"])
        self.assertIn("frog db migrate", r["error"])


if __name__ == "__main__":
    unittest.main()
