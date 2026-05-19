import unittest

from _util import fresh_db
from ragbaz_frog import store


class EventHooks(unittest.TestCase):
    def setUp(self):
        self.conn = store.connect(fresh_db())

    def tearDown(self):
        self.conn.close()

    def test_add_list_remove_hook(self):
        added = store.event_hook_add(self.conn, "https://example.test/frog", kind="slack")
        self.assertTrue(added["ok"], added)
        hooks = store.event_hook_list(self.conn)["hooks"]
        self.assertEqual(len(hooks), 1)
        self.assertEqual(hooks[0]["kind"], "slack")
        removed = store.event_hook_remove(self.conn, hooks[0]["id"])
        self.assertTrue(removed["ok"], removed)
        self.assertEqual(store.event_hook_list(self.conn)["hooks"], [])

    def test_dispatch_posts_new_events_and_advances_cursor(self):
        added = store.event_hook_add(self.conn, "https://example.test/frog")
        hook_id = added["hook"]["id"]
        store.record_event(self.conn, kind="task.claimed", summary="claimed x")
        self.conn.commit()
        calls = []

        def fake(url, body):
            calls.append((url, body))
            return 204, ""

        result = store.event_hook_dispatch(self.conn, request_fn=fake)
        self.assertTrue(result["ok"], result)
        self.assertEqual(calls[0][0], "https://example.test/frog")
        self.assertGreaterEqual(len(calls[0][1]["events"]), 1)
        hook = store.event_hook_list(self.conn)["hooks"][0]
        self.assertGreaterEqual(hook["last_event_id"], calls[0][1]["events"][-1]["id"])
        empty = store.event_hook_dispatch(self.conn, hook_id=hook_id, request_fn=fake)
        self.assertTrue(empty["ok"], empty)
        self.assertEqual(empty["dispatches"][0]["sent"], 0)

    def test_dispatch_failure_keeps_cursor_and_records_error(self):
        added = store.event_hook_add(self.conn, "https://example.test/frog")
        hook_id = added["hook"]["id"]
        store.record_event(self.conn, kind="task.claimed", summary="claimed x")
        self.conn.commit()

        def fail(url, body):
            return 500, "nope"

        result = store.event_hook_dispatch(self.conn, hook_id=hook_id, request_fn=fail)
        self.assertFalse(result["ok"])
        hook = store.event_hook_list(self.conn)["hooks"][0]
        self.assertEqual(hook["last_event_id"], 0)
        self.assertEqual(hook["last_status"], 500)
        self.assertEqual(hook["last_error"], "nope")

    def test_digest_markdown_contains_recent_events(self):
        store.record_event(self.conn, kind="task.finished", summary="finished x", actor="codex")
        self.conn.commit()
        digest = store.event_digest_markdown(self.conn, limit=5)
        self.assertTrue(digest["ok"], digest)
        self.assertIn("# frog event digest", digest["markdown"])
        self.assertIn("task.finished", digest["markdown"])
        self.assertIn("finished x", digest["markdown"])


if __name__ == "__main__":
    unittest.main()
