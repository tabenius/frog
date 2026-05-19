import unittest
from _util import fresh_db
from ragbaz_frog import mcp_server


class McpWorkflow(unittest.TestCase):
    def setUp(self):
        self.ws = {"db": fresh_db()}

    def _call(self, name, args):
        return mcp_server._call_local(name, args, self.ws)

    def test_specs_list_workflow_verbs(self):
        names = {s["name"] for s in mcp_server._tool_specs()}
        for n in ("frog_task_claim", "frog_task_finish", "frog_task_create",
                  "frog_task_dependency", "frog_lock_acquire",
                  "frog_lock_release"):
            self.assertIn(n, names)

    def test_create_claim_finish_over_mcp(self):
        r = self._call("frog_task_create",
                       {"slug": "m1", "title": "via mcp", "priority": "p1",
                        "files": ["/tmp/frog-mcp-a"]})
        self.assertTrue(r["ok"], r)
        r = self._call("frog_task_claim",
                       {"slug": "m1", "agent": "claude",
                        "files": ["/tmp/frog-mcp-b"]})
        self.assertTrue(r["ok"])
        self.assertEqual(r["task"]["workflow_status"], "in_progress")
        self.assertIn("/tmp/frog-mcp-a", r["lock"]["file_paths"])
        self.assertIn("/tmp/frog-mcp-b", r["lock"]["file_paths"])
        r = self._call("frog_task_finish",
                       {"slug": "m1", "agent": "claude", "verify": False})
        self.assertTrue(r["ok"])
        self.assertEqual(r["task"]["workflow_status"], "done")

    def test_dependency_and_lock_roundtrip(self):
        self._call("frog_task_create", {"slug": "a", "title": "A"})
        self._call("frog_task_create", {"slug": "b", "title": "B"})
        r = self._call("frog_task_dependency",
                       {"slug": "b", "depends_on": "a"})
        self.assertTrue(r["ok"])
        la = self._call("frog_lock_acquire",
                        {"scope_key": "s1", "lock_kind": "edit",
                         "agent": "claude", "files": ["/tmp/x"]})
        self.assertTrue(la["ok"], la)
        lid = la["lock"]["id"]
        # second acquire by other agent conflicts
        c = self._call("frog_lock_acquire",
                       {"scope_key": "s1", "lock_kind": "edit",
                        "agent": "codex", "files": ["/tmp/x"]})
        self.assertFalse(c["ok"])
        rel = self._call(
            "frog_lock_release",
            {"lock_id": lid, "agent": "codex", "reason": "done"},
        )
        self.assertTrue(rel["ok"])


if __name__ == "__main__":
    unittest.main()
