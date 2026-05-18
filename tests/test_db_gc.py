import unittest
from _util import fresh_db
from ragbaz_frog import store


class DbGc(unittest.TestCase):
    def test_gc_trims_old_events_keeps_newest(self):
        conn = store.connect(fresh_db())
        try:
            for i in range(50):
                store.record_event(conn, kind="x.test", summary=f"e{i}")
            conn.commit()
            before = conn.execute("SELECT COUNT(*) c FROM event_log").fetchone()["c"]
            self.assertGreaterEqual(before, 50)
            # backdate everything so the cutoff bites
            conn.execute("UPDATE event_log SET created_at='2000-01-01T00:00:00+00:00'")
            conn.commit()
            r = store.db_gc(conn, older_than_days=1, keep=10)
            self.assertTrue(r["ok"])
            after = conn.execute("SELECT COUNT(*) c FROM event_log").fetchone()["c"]
            # 10 retained + 1 db.gc audit event recorded post-prune
            self.assertEqual(after, 11, "newest --keep retained + the gc audit event")
            self.assertEqual(r["removed"]["event_log"], before - 10)
        finally:
            conn.close()

    def test_gc_no_cutoff_is_safe_noop_for_events(self):
        conn = store.connect(fresh_db())
        try:
            for i in range(5):
                store.record_event(conn, kind="x", summary=str(i))
            conn.commit()
            r = store.db_gc(conn, older_than_days=None, keep=2)
            # no cutoff -> events not dropped (only target_runs trims by keep)
            self.assertEqual(r["removed"]["event_log"], 0)
        finally:
            conn.close()

    def test_gc_trims_target_runs_per_target(self):
        conn = store.connect(fresh_db())
        try:
            for i in range(12):
                conn.execute(
                    "INSERT INTO target_runs(repo_path,target_kind,target_name,"
                    "workdir,command,input_hash,returncode,status,duration_ms,ran_at)"
                    " VALUES('/r','build','b','/r','make',?,0,'ran',1,?)",
                    (f"h{i}", f"2026-01-01T00:00:{i:02d}+00:00"),
                )
            conn.commit()
            store.db_gc(conn, keep=5)
            n = conn.execute("SELECT COUNT(*) c FROM target_runs").fetchone()["c"]
            self.assertEqual(n, 5)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
