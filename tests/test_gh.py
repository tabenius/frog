import json
import unittest

from _util import fresh_db
from ragbaz_frog import gh, store


class GitHubAdapter(unittest.TestCase):
    def setUp(self):
        self.conn = store.connect(fresh_db())

    def tearDown(self):
        self.conn.close()

    def test_pull_maps_issues_to_provider_tasks(self):
        issues = [
            {
                "number": 12,
                "title": "Wire sync",
                "state": "OPEN",
                "labels": [{"name": "p1"}, {"name": "in progress"}],
                "body": "import and export",
                "assignees": [{"login": "codex"}],
            }
        ]

        def fake(args):
            self.assertEqual(args[:2], ["issue", "list"])
            return 0, json.dumps(issues), ""

        res = gh.pull(self.conn, "owner/repo", exec=fake)
        self.assertTrue(res["ok"], res)
        info = store.task_info(self.conn, "github:12")
        self.assertEqual(info["task"]["priority"], "p1")
        self.assertEqual(info["task"]["workflow_status"], "in_progress")
        self.assertEqual(info["task"]["why"], "import and export")

    def test_push_updates_state_labels_and_assignee(self):
        store.provider_sync_in(
            self.conn,
            "github",
            [{"external_id": "7", "title": "Seven", "status": "open",
              "priority": "p2"}],
        )
        self.conn.execute(
            "UPDATE tasks SET workflow_status='in_progress', assigned_agent='codex' "
            "WHERE slug='github:7'"
        )
        self.conn.commit()
        calls = []

        def fake(args):
            calls.append(args)
            return 0, "", ""

        res = gh.push(self.conn, "owner/repo", exec=fake)
        self.assertTrue(res["ok"], res)
        self.assertIn(["issue", "reopen", "7", "--repo", "owner/repo"], calls)
        self.assertIn(
            ["issue", "edit", "7", "--repo", "owner/repo",
             "--add-label", "p2"],
            calls,
        )
        self.assertIn(
            ["issue", "edit", "7", "--repo", "owner/repo",
             "--add-label", "in progress"],
            calls,
        )
        self.assertIn(
            ["issue", "edit", "7", "--repo", "owner/repo",
             "--add-assignee", "codex"],
            calls,
        )

    def test_sync_reports_push_errors(self):
        def fake(args):
            if args[:2] == ["issue", "list"]:
                return 0, json.dumps([{
                    "number": 3,
                    "title": "Closed",
                    "state": "CLOSED",
                    "labels": [{"name": "p3"}],
                    "body": "",
                }]), ""
            return 1, "", "boom"

        res = gh.sync(self.conn, "owner/repo", exec=fake)
        self.assertFalse(res["ok"])
        self.assertTrue(res["push"]["errors"])

    def test_action_uses_global_json_flag(self):
        self.assertIn("frog --json repo affected", gh.action_yaml())


if __name__ == "__main__":
    unittest.main()
