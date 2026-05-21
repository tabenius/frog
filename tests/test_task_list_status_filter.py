import io
import json
import unittest
from contextlib import redirect_stdout

from _util import fresh_db
from ragbaz_frog import store
from ragbaz_frog.main_cli import main


def mk(conn, slug: str, status: str):
    store.create_task(conn, slug=slug, repo_ref=None, title=slug, why=None,
                      what_text=None, roi_note=None, priority="p2",
                      workflow_status=status, git_status="not_started",
                      assigned_agent=None, delegation_current=None,
                      delegation_other=None, parent_task_slug=None)


def _slugs(body: dict) -> list[str]:
    return sorted(t["slug"] for t in body["tasks"])


class TaskListStatusFilter(unittest.TestCase):
    def setUp(self):
        self.db = fresh_db()
        conn = store.connect(self.db)
        mk(conn, "i", "idea")
        mk(conn, "t", "todo")
        mk(conn, "p", "in_progress")
        mk(conn, "b", "blocked")
        mk(conn, "d", "done")
        conn.close()

    def _run(self, *argv) -> dict:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(["--db", self.db, *argv, "--json"])
        self.assertEqual(rc, 0, buf.getvalue())
        return json.loads(buf.getvalue())

    def test_status_alias_single_value(self):
        body = self._run("task", "list", "--status", "todo")
        self.assertEqual(_slugs(body), ["t"])

    def test_status_comma_separated(self):
        body = self._run("task", "list", "--status", "todo,in_progress")
        self.assertEqual(_slugs(body), ["p", "t"])

    def test_status_repeated_flag(self):
        body = self._run("task", "list",
                          "--status", "todo",
                          "--status", "blocked")
        self.assertEqual(_slugs(body), ["b", "t"])

    def test_not_done_preset(self):
        body = self._run("task", "list", "--not-done")
        # All non-terminal statuses, no done
        self.assertEqual(_slugs(body), ["b", "i", "p", "t"])

    def test_workflow_status_long_form_still_works(self):
        body = self._run("task", "list", "--workflow-status", "done")
        self.assertEqual(_slugs(body), ["d"])

    def test_default_excludes_done_unless_status_given(self):
        # default human listing hides done; JSON path keeps include_done True
        # when --json is set. With --status given we explicitly opt-in.
        body = self._run("task", "list", "--status", "done")
        self.assertEqual(_slugs(body), ["d"])


if __name__ == "__main__":
    unittest.main()
