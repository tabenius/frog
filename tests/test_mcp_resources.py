import json, unittest
from _util import fresh_db
from ragbaz_frog import mcp_server, store


class McpResources(unittest.TestCase):
    def test_resource_and_prompt_specs(self):
        uris = {r["uri"] for r in mcp_server._resource_specs()}
        self.assertIn("frog://board", uris)
        self.assertIn("frog://events", uris)
        names = {p["name"] for p in mcp_server._prompt_specs()}
        self.assertIn("coordinate-before-edit", names)

    def test_read_board_resource(self):
        db = fresh_db()
        c = store.connect(db)
        store.create_task(c, slug="x", repo_ref=None, title="X", why=None,
                          what_text=None, roi_note=None, priority="p2",
                          workflow_status="idea", git_status="not_started",
                          assigned_agent=None, delegation_current=None,
                          delegation_other=None, parent_task_slug=None)
        c.close()
        r = mcp_server._read_resource("frog://board", db_path=db)
        self.assertEqual(r["mimeType"], "application/json")
        snap = json.loads(r["text"])
        self.assertIn("x", [t["slug"] for t in snap["columns"]["idea"]])

    def test_unknown_resource_raises(self):
        with self.assertRaises(KeyError):
            mcp_server._read_resource("frog://nope", db_path=fresh_db())

    def test_prompt_get_renders_agent(self):
        p = mcp_server._get_prompt("coordinate-before-edit", {"agent": "claude"})
        txt = p["messages"][0]["content"]["text"]
        self.assertIn("claude", txt)
        self.assertIn("frog task claim", txt)
        with self.assertRaises(KeyError):
            mcp_server._get_prompt("bogus", {})


if __name__ == "__main__":
    unittest.main()
