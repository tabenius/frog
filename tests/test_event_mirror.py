import unittest

from _util import fresh_db
from ragbaz_frog import store


class EventMirror(unittest.TestCase):
    def setUp(self):
        self.conn = store.connect(fresh_db())

    def tearDown(self):
        self.conn.close()

    def _evs(self, *ids):
        return [{"id": i, "created_at": f"2026-01-0{i}T00:00:00+00:00",
                 "kind": "x.test", "summary": f"e{i}", "payload": {"n": i}}
                for i in ids]

    def test_pull_appends_and_advances_cursor(self):
        r = store.event_mirror_pull(self.conn, workspace="ws", events=self._evs(1, 2, 3))
        self.assertEqual(r["pulled"], 3)
        self.assertEqual(store.event_mirror_cursor(self.conn, "ws"), 3)

    def test_pull_is_incremental_and_idempotent(self):
        store.event_mirror_pull(self.conn, workspace="ws", events=self._evs(1, 2))
        # re-pull overlapping + new: only 3,4 are new
        r = store.event_mirror_pull(self.conn, workspace="ws", events=self._evs(1, 2, 3, 4))
        self.assertEqual(r["pulled"], 2)
        self.assertEqual(store.event_mirror_cursor(self.conn, "ws"), 4)
        lst = store.event_mirror_list(self.conn, workspace="ws", limit=10)
        self.assertEqual(len(lst["mirrored_events"]), 4)

    def test_workspaces_are_isolated(self):
        store.event_mirror_pull(self.conn, workspace="a", events=self._evs(1, 2))
        store.event_mirror_pull(self.conn, workspace="b", events=self._evs(1))
        self.assertEqual(store.event_mirror_cursor(self.conn, "a"), 2)
        self.assertEqual(store.event_mirror_cursor(self.conn, "b"), 1)


if __name__ == "__main__":
    unittest.main()
