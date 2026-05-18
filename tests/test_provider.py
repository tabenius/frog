import unittest
from _util import fresh_db
from ragbaz_frog import store


class Provider(unittest.TestCase):
    def setUp(self):
        self.conn = store.connect(fresh_db())

    def tearDown(self):
        self.conn.close()

    def test_sync_in_creates_then_updates_idempotently(self):
        items = [{"external_id": "101", "title": "Fix auth",
                  "status": "open", "priority": "p1"},
                 {"external_id": "102", "title": "Docs", "status": "todo"}]
        r = store.provider_sync_in(self.conn, "github", items)
        self.assertEqual(len(r["created"]), 2)
        self.assertEqual(r["updated"], [])
        # re-sync with a status change -> update, not duplicate
        items[0]["status"] = "in_progress"
        r2 = store.provider_sync_in(self.conn, "github", items)
        self.assertEqual(r2["created"], [])
        self.assertEqual(len(r2["updated"]), 2)
        row = self.conn.execute(
            "SELECT workflow_status FROM tasks WHERE source='github' "
            "AND external_id='101'").fetchone()
        self.assertEqual(row["workflow_status"], "in_progress")
        n = self.conn.execute(
            "SELECT COUNT(*) c FROM tasks WHERE source='github'").fetchone()["c"]
        self.assertEqual(n, 2, "idempotent: no duplicates")

    def test_status_mapping(self):
        store.provider_sync_in(self.conn, "asana", [
            {"external_id": "a", "title": "A", "status": "closed"},
            {"external_id": "b", "title": "B", "status": "blocked"},
            {"external_id": "c", "title": "C", "status": "weird"}])
        got = dict(self.conn.execute(
            "SELECT external_id, workflow_status FROM tasks "
            "WHERE source='asana'").fetchall() and
            [(r["external_id"], r["workflow_status"]) for r in
             self.conn.execute("SELECT external_id,workflow_status FROM tasks WHERE source='asana'")])
        self.assertEqual(got["a"], "done")
        self.assertEqual(got["b"], "blocked")
        self.assertEqual(got["c"], "idea")  # unknown -> idea

    def test_outbox_lists_source_tasks(self):
        store.provider_sync_in(self.conn, "github",
                               [{"external_id": "9", "title": "T",
                                 "status": "open"}])
        ob = store.provider_outbox(self.conn, "github")
        self.assertEqual(len(ob["outbox"]), 1)
        self.assertEqual(ob["outbox"][0]["external_id"], "9")
        self.assertEqual(store.provider_outbox(self.conn, "none")["outbox"], [])


if __name__ == "__main__":
    unittest.main()
