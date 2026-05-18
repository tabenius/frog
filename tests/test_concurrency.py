import multiprocessing as mp
import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from _util import fresh_db
from ragbaz_frog import store


def _try_lock(args):
    db, scope, agent = args
    conn = store.connect(db)
    try:
        r = store.lock_acquire(
            conn, scope_key=scope, repo_ref=None, lock_kind="edit",
            files=[], agent=agent, pid=None, reason=None,
            lease_seconds=1800, eta_minutes=None, force=False,
        )
        return bool(r.get("ok"))
    finally:
        conn.close()


def _try_claim(args):
    db, slug, agent = args
    conn = store.connect(db)
    try:
        r = store.task_claim(conn, slug=slug, agent=agent)
        return bool(r.get("ok"))
    finally:
        conn.close()


class Concurrency(unittest.TestCase):
    def test_only_one_acquires_a_contended_scope(self):
        db = fresh_db()
        n = 8
        with mp.Pool(n) as pool:
            wins = pool.map(_try_lock, [(db, "race", f"a{i}") for i in range(n)])
        self.assertEqual(sum(1 for w in wins if w), 1,
                         "exactly one writer may hold a contended scope")
        conn = store.connect(db)
        try:
            active = conn.execute(
                "SELECT COUNT(*) c FROM locks WHERE scope_key='race' "
                "AND status='active'").fetchone()["c"]
        finally:
            conn.close()
        self.assertEqual(active, 1, "no double-grant in the DB")

    def test_only_one_claims_a_task(self):
        db = fresh_db()
        conn = store.connect(db)
        store.create_task(conn, slug="hot", repo_ref=None, title="hot",
                          why=None, what_text=None, roi_note=None,
                          priority="p0", workflow_status="idea",
                          git_status="not_started", assigned_agent=None,
                          delegation_current=None, delegation_other=None,
                          parent_task_slug=None)
        conn.close()
        n = 6
        with mp.Pool(n) as pool:
            wins = pool.map(_try_claim, [(db, "hot", f"c{i}") for i in range(n)])
        self.assertEqual(sum(1 for w in wins if w), 1,
                         "exactly one agent may claim a task")


if __name__ == "__main__":
    unittest.main()
