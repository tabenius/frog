import os, tempfile, unittest
from pathlib import Path
from _util import fresh_db
from ragbaz_frog import store


def _repo(key):
    d = tempfile.mkdtemp(prefix="frog-recip-")
    (Path(d) / ".frogid").write_text(key + "\n")
    return d


class ReciprocalJoin(unittest.TestCase):
    def setUp(self):
        self._b = os.environ.get("FROG_BOX_ID")
        os.environ["FROG_BOX_ID"] = "boxA"
        self.conn = store.connect(fresh_db())
        r = _repo("shared:k")
        store.register_repo(self.conn, repo_path=r, name="r", kind=None,
                            status="active", third_party=False, notes=None)
        store.ensure_repo_key(self.conn, r)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        if self._b is None:
            os.environ.pop("FROG_BOX_ID", None)
        else:
            os.environ["FROG_BOX_ID"] = self._b

    def _exec(self, calls):
        def ex(host, rdb, rfrog, argv):
            calls.append(argv)
            if argv == ["box", "whoami"]:
                return {"box_id": "boxB", "hostname": "hB"}
            if argv == ["repo", "list"]:
                return {"repos": [{"repo_path": "/r",
                                   "repo_key": "shared:k"}]}
            if argv[:2] == ["box", "join"]:
                return {"ok": True, "message": "peer joined back"}
            raise AssertionError(argv)
        return ex

    def test_no_reciprocal_by_default(self):
        calls = []
        r = store.federation_join(self.conn, "hB", exec=self._exec(calls))
        self.assertTrue(r["ok"])
        self.assertIsNone(r["reciprocal"])
        self.assertNotIn(["box", "join", "me@here"], calls)

    def test_reciprocal_invokes_peer_join_back(self):
        calls = []
        r = store.federation_join(
            self.conn, "hB", exec=self._exec(calls),
            reciprocal_self="me@here:/data/src/AGENTS.db")
        self.assertTrue(r["ok"])
        self.assertTrue(r["reciprocal"]["ok"])
        self.assertIn(["box", "join", "me@here:/data/src/AGENTS.db"],
                      calls)
        self.assertIn("+reciprocal", r["message"])

    def test_reciprocal_failure_is_reported_not_fatal(self):
        def ex(host, rdb, rfrog, argv):
            if argv == ["box", "whoami"]:
                return {"box_id": "boxB", "hostname": "hB"}
            if argv == ["repo", "list"]:
                return {"repos": []}
            raise RuntimeError("ssh down")
        r = store.federation_join(self.conn, "hB", exec=ex,
                                  reciprocal_self="me@here")
        self.assertTrue(r["ok"])               # local half still ok
        self.assertFalse(r["reciprocal"]["ok"])
        self.assertIn("ssh down", r["reciprocal"]["error"])

    def test_cli_reciprocal_requires_self(self):
        import subprocess
        d = tempfile.mkdtemp(); db = str(Path(d) / "AGENTS.db")
        env = {**os.environ, "FROG_BOX_ID": "boxA"}
        subprocess.run(["python3", "bin/frog", "--db", db, "db",
                        "migrate"], capture_output=True, env=env)
        r = subprocess.run(
            ["python3", "bin/frog", "--db", db, "box", "join",
             "peer", "--reciprocal"], capture_output=True, text=True,
            env=env)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--reciprocal requires --self",
                      r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
