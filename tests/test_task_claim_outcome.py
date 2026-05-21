import io
import unittest
from contextlib import redirect_stderr, redirect_stdout

from _util import fresh_db
from ragbaz_frog import store
from ragbaz_frog.main_cli import _emit


def mk(conn, slug):
    store.create_task(conn, slug=slug, repo_ref=None, title=slug, why=None,
                      what_text=None, roi_note=None, priority="p2",
                      workflow_status="idea", git_status="not_started",
                      assigned_agent=None, delegation_current=None,
                      delegation_other=None, parent_task_slug=None)


class ClaimOutcome(unittest.TestCase):
    def setUp(self):
        self.conn = store.connect(fresh_db())

    def tearDown(self):
        self.conn.close()

    def test_success_outcome_has_claimed_true_and_lock_id(self):
        mk(self.conn, "free")
        r = store.task_claim(self.conn, slug="free", agent="claude")
        self.assertTrue(r["ok"])
        out = r["claim_outcome"]
        self.assertTrue(out["claimed"])
        self.assertEqual(out["reason"], "acquired")
        self.assertEqual(out["slug"], "free")
        self.assertEqual(out["agent"], "claude")
        self.assertEqual(out["holder_agent"], "claude")
        self.assertIsNotNone(out["lock_id"])

    def test_not_found_surfaces_reason(self):
        r = store.task_claim(self.conn, slug="nope", agent="claude")
        self.assertFalse(r["ok"])
        out = r["claim_outcome"]
        self.assertFalse(out["claimed"])
        self.assertEqual(out["reason"], "not_found")

    def test_owned_by_other_surfaces_holder_identity(self):
        mk(self.conn, "t")
        store.task_claim(self.conn, slug="t", agent="codex")
        r = store.task_claim(self.conn, slug="t", agent="claude")
        self.assertFalse(r["ok"])
        out = r["claim_outcome"]
        self.assertFalse(out["claimed"])
        self.assertEqual(out["reason"], "owned_by_other")
        self.assertEqual(out["holder_agent"], "codex")

    def test_renderer_banner_on_success(self):
        mk(self.conn, "shown")
        r = store.task_claim(self.conn, slug="shown", agent="claude")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = _emit(r, as_json=False)
        self.assertEqual(rc, 0)
        text = buf.getvalue()
        self.assertIn("CLAIMED", text)
        self.assertIn("shown", text)

    def test_renderer_banner_on_owned_by_other_to_stderr(self):
        mk(self.conn, "owned")
        store.task_claim(self.conn, slug="owned", agent="codex")
        r = store.task_claim(self.conn, slug="owned", agent="claude")
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            rc = _emit(r, as_json=False)
        self.assertEqual(rc, 1)
        err = err_buf.getvalue()
        self.assertIn("NOT CLAIMED", err)
        self.assertIn("owned", err)
        self.assertIn("codex", err)
        # The blocker identity must NOT have been printed to stdout
        # (avoid mistaking a held-elsewhere outcome for a successful claim).
        self.assertNotIn("CLAIMED ", out_buf.getvalue().split("NOT CLAIMED")[0])

    def test_renderer_json_passthrough(self):
        mk(self.conn, "jsn")
        store.task_claim(self.conn, slug="jsn", agent="codex")
        r = store.task_claim(self.conn, slug="jsn", agent="claude")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = _emit(r, as_json=True)
        self.assertEqual(rc, 1)
        import json as _json
        body = _json.loads(buf.getvalue())
        self.assertFalse(body["ok"])
        self.assertEqual(body["claim_outcome"]["reason"], "owned_by_other")
        self.assertEqual(body["claim_outcome"]["holder_agent"], "codex")


if __name__ == "__main__":
    unittest.main()
