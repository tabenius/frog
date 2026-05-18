import subprocess
import tempfile
import unittest
from pathlib import Path

from _util import fresh_db
from ragbaz_frog import store


def _git_repo() -> str:
    d = tempfile.mkdtemp(prefix="frog-lockrepo-")
    subprocess.run(["git", "-C", d, "init", "-q"], check=True)
    (Path(d) / "a.txt").write_text("one\n")
    subprocess.run(["git", "-C", d, "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", d, "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-q", "-m", "init"], check=True,
    )
    return d


class LockAudit(unittest.TestCase):
    def setUp(self):
        self.db = fresh_db()
        self.conn = store.connect(self.db)
        self.repo = _git_repo()
        store.register_repo(
            self.conn, repo_path=self.repo, name="r", kind=None,
            status="active", third_party=False, notes=None,
        )

    def tearDown(self):
        self.conn.close()

    def test_clean_tree_has_no_findings(self):
        res = store.lock_audit(self.conn, repo_ref=self.repo, agent="claude")
        self.assertTrue(res["ok"])
        self.assertEqual(res["findings"], [])

    def test_uncovered_dirty_file_is_a_finding(self):
        (Path(self.repo) / "a.txt").write_text("changed\n")
        res = store.lock_audit(self.conn, repo_ref=self.repo, agent="claude")
        self.assertFalse(res["ok"])
        kinds = {f["kind"] for f in res["findings"]}
        self.assertIn("uncovered", kinds)

    def test_file_covered_by_own_lock_is_clean(self):
        f = str(Path(self.repo) / "a.txt")
        (Path(self.repo) / "a.txt").write_text("changed\n")
        store.lock_acquire(
            self.conn, scope_key="edit-a", repo_ref=self.repo,
            lock_kind="edit", files=[f], agent="claude", pid=None,
            reason=None, lease_seconds=1800, eta_minutes=None, force=False,
        )
        res = store.lock_audit(self.conn, repo_ref=self.repo, agent="claude")
        self.assertTrue(res["ok"], res)

    def test_file_locked_by_other_agent_is_conflict(self):
        f = str(Path(self.repo) / "a.txt")
        (Path(self.repo) / "a.txt").write_text("changed\n")
        store.lock_acquire(
            self.conn, scope_key="edit-a", repo_ref=self.repo,
            lock_kind="edit", files=[f], agent="codex", pid=None,
            reason=None, lease_seconds=1800, eta_minutes=None, force=False,
        )
        res = store.lock_audit(self.conn, repo_ref=self.repo, agent="claude")
        self.assertFalse(res["ok"])
        self.assertEqual(res["findings"][0]["kind"], "conflict")
        self.assertIn("codex", res["findings"][0]["holders"])

    def test_task_scope_only_lock_does_not_cover_dirty_files(self):
        (Path(self.repo) / "a.txt").write_text("changed\n")
        store.lock_acquire(
            self.conn, scope_key="task:gh-sync", repo_ref=self.repo,
            lock_kind="edit", files=[], agent="codex", pid=None,
            reason=None, lease_seconds=1800, eta_minutes=None, force=False,
        )
        res = store.lock_audit(self.conn, repo_ref=self.repo, agent="claude")
        self.assertFalse(res["ok"])
        self.assertEqual(res["findings"][0]["kind"], "uncovered")

    def test_explicit_repo_lock_still_conflicts_with_file_work(self):
        f = str(Path(self.repo) / "a.txt")
        store.lock_acquire(
            self.conn, scope_key="repo:freeze", repo_ref=self.repo,
            lock_kind="edit", files=[], agent="codex", pid=None,
            reason=None, lease_seconds=1800, eta_minutes=None, force=False,
        )
        chk = store.lock_check(
            self.conn, scope_key="edit-a", repo_ref=self.repo, files=[f])
        self.assertTrue(chk["conflicts"])


class LockReap(unittest.TestCase):
    def test_reap_reports_expired_lease(self):
        db = fresh_db()
        conn = store.connect(db)
        try:
            store.lock_acquire(
                conn, scope_key="s", repo_ref=None, lock_kind="x",
                files=[], agent="a", pid=None, reason=None,
                lease_seconds=1, eta_minutes=None, force=False,
            )
            # backdate updated_at so the 1s lease is elapsed
            conn.execute(
                "UPDATE locks SET updated_at = '2000-01-01T00:00:00+00:00'"
            )
            conn.commit()
            res = store.lock_reap(conn)
            self.assertTrue(res["ok"])
            self.assertEqual(len(res["reaped"]), 1)
            row = conn.execute("SELECT status FROM locks").fetchone()
            self.assertEqual(row["status"], "stale")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
