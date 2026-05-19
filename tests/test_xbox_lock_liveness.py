import os
import subprocess
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from _util import fresh_db
from ragbaz_frog import store


def _git_repo() -> str:
    d = tempfile.mkdtemp(prefix="frog-xbox-")
    subprocess.run(["git", "-C", d, "init", "-q"], check=True)
    (Path(d) / "a.txt").write_text("x\n")
    return d


class XBoxLockLiveness(unittest.TestCase):
    def setUp(self):
        self._bid = os.environ.get("FROG_BOX_ID")
        os.environ["FROG_BOX_ID"] = "boxA"
        self.conn = store.connect(fresh_db())
        self.repo = _git_repo()
        store.register_repo(self.conn, repo_path=self.repo, name="r",
                            kind=None, status="active",
                            third_party=False, notes=None)
        store.lock_acquire(
            self.conn, scope_key="s1", repo_ref=self.repo,
            lock_kind="edit", files=[str(Path(self.repo) / "a.txt")],
            agent="claude", pid=None, reason=None, lease_seconds=900,
            eta_minutes=None, force=False)
        self.lid = self.conn.execute(
            "SELECT id FROM locks WHERE scope_key='s1'").fetchone()[0]

    def tearDown(self):
        self.conn.close()
        if self._bid is None:
            os.environ.pop("FROG_BOX_ID", None)
        else:
            os.environ["FROG_BOX_ID"] = self._bid

    def _age(self, *, box, lease, hours_ago):
        old = (store.utc_now() - timedelta(hours=hours_ago)).isoformat(
            timespec="seconds")
        self.conn.execute(
            "UPDATE locks SET box_id=?, lease_seconds=?, updated_at=? "
            "WHERE id=?", (box, lease, old, self.lid))
        self.conn.commit()

    def _status(self):
        return self.conn.execute(
            "SELECT status FROM locks WHERE id=?", (self.lid,)).fetchone()[0]

    def test_no_lease_remote_lock_from_unknown_box_is_orphan_reaped(self):
        self._age(box="deadBox", lease=0, hours_ago=2)
        r = store.lock_reap(self.conn)
        self.assertEqual(self._status(), "stale")
        reasons = {x["reason"] for x in r["reaped"]}
        self.assertIn("orphan_box", reasons)
        kinds = [x[0] for x in self.conn.execute(
            "SELECT kind FROM event_log")]
        self.assertIn("lock.orphan_box", kinds)

    def test_no_lease_remote_lock_from_known_peer_is_remote_stale(self):
        self.conn.execute(
            "INSERT INTO peers(box_id,hostname,ssh_target,remote_db,"
            "added_at,last_join_at) VALUES('peerB','hB','hB',NULL,?,?)",
            (store.utc_now_iso(), store.utc_now_iso()))
        self._age(box="peerB", lease=0, hours_ago=2)
        r = store.lock_reap(self.conn)
        self.assertEqual(self._status(), "stale")
        self.assertIn("remote_stale",
                      {x["reason"] for x in r["reaped"]})

    def test_recently_renewed_remote_lock_is_not_reaped(self):
        # heartbeat: box died-detection must not fire while it renews
        self._age(box="peerB", lease=0, hours_ago=0)
        store.lock_reap(self.conn)
        self.assertEqual(self._status(), "active")

    def test_local_no_lease_lock_behavior_unchanged(self):
        # box_id == local box, no lease, no pid -> intentionally sticky
        self._age(box="boxA", lease=0, hours_ago=5)
        store.lock_reap(self.conn)
        self.assertEqual(self._status(), "active")

    def test_legacy_null_box_id_lock_unchanged(self):
        # pre-migration locks have box_id NULL -> treated as local
        self._age(box=None, lease=0, hours_ago=5)
        store.lock_reap(self.conn)
        self.assertEqual(self._status(), "active")

    def test_lease_expiry_still_takes_precedence_crossbox(self):
        self._age(box="deadBox", lease=60, hours_ago=2)
        r = store.lock_reap(self.conn)
        self.assertEqual(self._status(), "stale")
        self.assertIn("lease_expired",
                      {x["reason"] for x in r["reaped"]})


if __name__ == "__main__":
    unittest.main()
