import unittest

from _util import fresh_db
from ragbaz_frog import provider_adapters, store


class ProviderAdapters(unittest.TestCase):
    def setUp(self):
        self.conn = store.connect(fresh_db())

    def tearDown(self):
        self.conn.close()

    def test_asana_pull_maps_tasks_and_push_updates_completion_and_status_field(self):
        calls = []

        def fake(method, url, headers, body=None):
            calls.append((method, url, headers, body))
            if method == "GET":
                return 200, {
                    "data": [{
                        "gid": "a1",
                        "name": "Wire board",
                        "notes": "kanban import",
                        "completed": False,
                        "custom_fields": [{
                            "gid": "status-field",
                            "enum_value": {"name": "Doing"},
                        }],
                    }],
                }, ""
            return 200, {"data": {}}, ""

        cfg = {
            "token": "tok",
            "project_gid": "proj",
            "status_custom_field_gid": "status-field",
            "status_values": {"in_progress": "enum-doing"},
        }
        pulled = provider_adapters.asana_pull(cfg, request=fake)
        self.assertTrue(pulled["ok"], pulled)
        self.assertEqual(pulled["items"][0]["external_id"], "a1")
        self.assertEqual(pulled["items"][0]["status"], "in_progress")
        store.provider_sync_in(self.conn, "asana", pulled["items"])
        pushed = provider_adapters.asana_push(
            cfg, store.provider_outbox(self.conn, "asana")["outbox"], request=fake
        )
        self.assertTrue(pushed["ok"], pushed)
        put_call = [call for call in calls if call[0] == "PUT"][0]
        self.assertEqual(put_call[3]["data"]["completed"], False)
        self.assertEqual(
            put_call[3]["data"]["custom_fields"],
            {"status-field": "enum-doing"},
        )

    def test_linear_pull_and_push_use_graphql_state_mapping(self):
        calls = []

        def fake(method, url, headers, body=None):
            calls.append((method, url, headers, body))
            if "query FrogIssues" in body["query"]:
                return 200, {
                    "data": {
                        "issues": {
                            "nodes": [{
                                "id": "lin-id",
                                "identifier": "FROG-7",
                                "title": "Sync providers",
                                "description": "round trip",
                                "priority": 2,
                                "state": {"name": "Started", "type": "started"},
                                "assignee": {"name": "Codex"},
                            }]
                        }
                    }
                }, ""
            return 200, {"data": {"issueUpdate": {"success": True, "issue": {"id": "lin-id"}}}}, ""

        cfg = {"token": "lin", "team_id": "team", "state_ids": {"in_progress": "state-started"}}
        pulled = provider_adapters.linear_pull(cfg, request=fake)
        self.assertTrue(pulled["ok"], pulled)
        self.assertEqual(pulled["items"][0]["priority"], "p1")
        self.assertEqual(pulled["items"][0]["status"], "in_progress")
        store.provider_sync_in(self.conn, "linear", pulled["items"])
        pushed = provider_adapters.linear_push(
            cfg, store.provider_outbox(self.conn, "linear")["outbox"], request=fake
        )
        self.assertTrue(pushed["ok"], pushed)
        self.assertEqual(calls[0][2]["authorization"], "lin")
        mutation = [call for call in calls if "mutation FrogIssueUpdate" in call[3]["query"]][0]
        self.assertEqual(mutation[3]["variables"]["input"]["stateId"], "state-started")
        self.assertEqual(mutation[3]["variables"]["input"]["priority"], 2)

    def test_jira_pull_and_push_use_transition_mapping(self):
        calls = []

        def fake(method, url, headers, body=None):
            calls.append((method, url, headers, body))
            if url.endswith("/search/jql"):
                return 200, {
                    "issues": [{
                        "id": "10001",
                        "key": "FROG-1",
                        "fields": {
                            "summary": "Ship adapter",
                            "status": {"name": "In Progress", "statusCategory": {"key": "indeterminate"}},
                            "priority": {"name": "High"},
                        },
                    }]
                }, ""
            return 204, {}, ""

        cfg = {
            "base_url": "https://example.atlassian.net",
            "email": "u@example.com",
            "api_token": "tok",
            "status_map": {"indeterminate": "in_progress"},
            "transition_ids": {"in_progress": "21"},
        }
        pulled = provider_adapters.jira_pull(cfg, request=fake)
        self.assertTrue(pulled["ok"], pulled)
        self.assertEqual(pulled["items"][0]["external_id"], "FROG-1")
        self.assertEqual(pulled["items"][0]["priority"], "p1")
        store.provider_sync_in(self.conn, "jira", pulled["items"])
        pushed = provider_adapters.jira_push(
            cfg, store.provider_outbox(self.conn, "jira")["outbox"], request=fake
        )
        self.assertTrue(pushed["ok"], pushed)
        transition = [call for call in calls if call[1].endswith("/transitions")][0]
        self.assertEqual(transition[3], {"transition": {"id": "21"}})

    def test_sync_direction_pull_only_does_not_push(self):
        calls = []

        def fake(method, url, headers, body=None):
            calls.append(method)
            return 200, {"data": []}, ""

        out = provider_adapters.sync(
            self.conn,
            "asana",
            {"token": "tok", "project_gid": "proj"},
            direction="pull",
            request=fake,
        )
        self.assertTrue(out["ok"], out)
        self.assertEqual(calls, ["GET"])
        self.assertEqual(out["push"], None)


if __name__ == "__main__":
    unittest.main()
