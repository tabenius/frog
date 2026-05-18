import subprocess, tempfile, unittest
from pathlib import Path
from _util import fresh_db
from ragbaz_frog import store


def _repo(origin):
    d = tempfile.mkdtemp(prefix="frog-cbl-")
    subprocess.run(["git","-C",d,"init","-q"],check=True)
    subprocess.run(["git","-C",d,"remote","add","origin",origin],check=True)
    (Path(d)/"src").mkdir()
    (Path(d)/"src"/"a.py").write_text("x")
    subprocess.run(["git","-C",d,"add","-A"],check=True)
    subprocess.run(["git","-C",d,"-c","user.email=t@t","-c","user.name=t",
                    "commit","-q","-m","i"],check=True)
    return d


class CrossBoxLocks(unittest.TestCase):
    def setUp(self):
        self.conn = store.connect(fresh_db())

    def tearDown(self):
        self.conn.close()

    def test_lock_stores_repo_key_and_relfiles(self):
        d = _repo("https://example.com/p.git")
        store.register_repo(self.conn, repo_path=d, name="p", kind=None,
                            status="active", third_party=False, notes=None)
        r = store.lock_acquire(self.conn, scope_key="k", repo_ref=d,
                               lock_kind="edit", files=[f"{d}/src/a.py"],
                               agent="claude", pid=None, reason=None,
                               lease_seconds=1800, eta_minutes=None, force=False)
        self.assertTrue(r["ok"])
        row = self.conn.execute(
            "SELECT repo_key, rel_files_json FROM locks WHERE id=?",
            (r["lock"]["id"],)).fetchone()
        self.assertTrue(row["repo_key"].startswith("git:"))
        self.assertIn("src/a.py", row["rel_files_json"])

    def test_conflict_across_boxes_same_repokey_relfile(self):
        # box A
        a = _repo("https://example.com/same.git")
        store.register_repo(self.conn, repo_path=a, name="A", kind=None,
                            status="active", third_party=False, notes=None)
        store.lock_acquire(self.conn, scope_key="A:edit", repo_ref=a,
                           lock_kind="edit", files=[f"{a}/src/a.py"],
                           agent="claude", pid=None, reason=None,
                           lease_seconds=1800, eta_minutes=None, force=False)
        # box B: SAME origin (=> same repo_key), different absolute path,
        # same repo-relative file -> must conflict
        b = _repo("https://example.com/same.git")
        store.register_repo(self.conn, repo_path=b, name="B", kind=None,
                            status="active", third_party=False, notes=None)
        chk = store.lock_check(self.conn, scope_key="B:edit", repo_ref=b,
                               files=[f"{b}/src/a.py"])
        self.assertTrue(chk["conflicts"], "same repo_key+rel file => conflict")

    def test_no_conflict_different_repokey(self):
        a = _repo("https://example.com/one.git")
        store.register_repo(self.conn, repo_path=a, name="A", kind=None,
                            status="active", third_party=False, notes=None)
        store.lock_acquire(self.conn, scope_key="A", repo_ref=a,
                           lock_kind="edit", files=[f"{a}/src/a.py"],
                           agent="x", pid=None, reason=None,
                           lease_seconds=1800, eta_minutes=None, force=False)
        b = _repo("https://example.com/two.git")  # different origin
        store.register_repo(self.conn, repo_path=b, name="B", kind=None,
                            status="active", third_party=False, notes=None)
        chk = store.lock_check(self.conn, scope_key="B", repo_ref=b,
                               files=[f"{b}/src/a.py"])
        self.assertFalse(chk["conflicts"], "different repos must not conflict")


if __name__ == "__main__":
    unittest.main()
