import io
import unittest
from contextlib import redirect_stdout

from _util import fresh_db
from ragbaz_frog import store
from ragbaz_frog.main_cli import _hoist_global_flags, main


class GlobalFlagHoisting(unittest.TestCase):
    """`frog task list --json` should produce JSON, not a usage error.

    argparse requires global flags before the subcommand, but users
    naturally suffix them. _hoist_global_flags moves recognised top-
    level flags to the front so either position works."""

    def test_json_after_subcommand_is_hoisted(self):
        self.assertEqual(
            _hoist_global_flags(["task", "list", "--json"]),
            ["--json", "task", "list"],
        )

    def test_already_hoisted_is_unchanged(self):
        self.assertEqual(
            _hoist_global_flags(["--json", "task", "list"]),
            ["--json", "task", "list"],
        )

    def test_no_color_and_no_pager_hoist(self):
        self.assertEqual(
            _hoist_global_flags(["task", "list", "--no-color", "--no-pager"]),
            ["--no-color", "--no-pager", "task", "list"],
        )

    def test_db_value_flag_hoisted_with_its_value(self):
        self.assertEqual(
            _hoist_global_flags(["task", "list", "--db", "/tmp/x.db"]),
            ["--db", "/tmp/x.db", "task", "list"],
        )

    def test_equals_form_db_flag_hoisted(self):
        self.assertEqual(
            _hoist_global_flags(["task", "list", "--db=/tmp/x.db"]),
            ["--db=/tmp/x.db", "task", "list"],
        )

    def test_non_global_flags_left_in_place(self):
        # --workflow-status is a task-list-local flag and should not be
        # hoisted (would land in the wrong parser).
        self.assertEqual(
            _hoist_global_flags(
                ["task", "list", "--workflow-status", "idea", "--json"]),
            ["--json", "task", "list", "--workflow-status", "idea"],
        )

    def test_end_to_end_task_list_json_emits_payload(self):
        db = fresh_db()
        conn = store.connect(db)
        store.create_task(conn, slug="seen", repo_ref=None, title="seen",
                          why=None, what_text=None, roi_note=None,
                          priority="p2", workflow_status="idea",
                          git_status="not_started", assigned_agent=None,
                          delegation_current=None, delegation_other=None,
                          parent_task_slug=None)
        conn.close()
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(["--db", db, "task", "list", "--json"])
        import json as _json
        self.assertEqual(rc, 0, buf.getvalue())
        body = _json.loads(buf.getvalue())
        self.assertTrue(body["ok"])
        self.assertEqual([t["slug"] for t in body["tasks"]], ["seen"])


if __name__ == "__main__":
    unittest.main()
