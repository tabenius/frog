import json
import tempfile
import unittest
from pathlib import Path

from _util import fresh_db
from ragbaz_frog import store


class SetupAgent(unittest.TestCase):
    def setUp(self):
        self.conn = store.connect(fresh_db())
        self.d = tempfile.mkdtemp(prefix="frog-setup-")

    def tearDown(self):
        self.conn.close()

    def test_dry_run_writes_nothing(self):
        r = store.setup_agent(self.conn, "claude", target_dir=self.d,
                              dry_run=True)
        self.assertTrue(r["ok"])
        self.assertTrue(r["dry_run"])
        self.assertFalse((Path(self.d) / "CLAUDE.md").exists())
        self.assertTrue(any("would" in a["action"] for a in r["actions"]))

    def test_claude_writes_md_settings_mcp_and_is_idempotent(self):
        store.setup_agent(self.conn, "claude", target_dir=self.d)
        self.assertTrue((Path(self.d) / "CLAUDE.md").exists())
        sj = Path(self.d) / ".claude" / "settings.json"
        mj = Path(self.d) / ".mcp.json"
        self.assertTrue(sj.exists() and mj.exists())
        s = json.loads(sj.read_text())
        pre = s["hooks"]["PreToolUse"]
        self.assertEqual(len(pre), 1)
        self.assertIn("frog", json.loads(mj.read_text())["mcpServers"])
        # idempotent: re-run doesn't duplicate the hook or clobber CLAUDE.md
        r2 = store.setup_agent(self.conn, "claude", target_dir=self.d)
        self.assertTrue(any("skip" in a["action"]
                            for a in r2["actions"] if "CLAUDE.md" in a["path"]))
        self.assertEqual(len(json.loads(sj.read_text())["hooks"]["PreToolUse"]), 1)

    def test_codex_emits_agents_md_and_toml_block(self):
        r = store.setup_agent(self.conn, "codex", target_dir=self.d)
        self.assertTrue(r["ok"])
        self.assertTrue((Path(self.d) / "AGENTS.md").exists())
        self.assertIn("[mcp_servers.frog]", r["codex_config_toml"])
        self.assertIn("FROG_AGENT", r["env_snippet"])

    def test_rejects_unknown_agent(self):
        self.assertFalse(
            store.setup_agent(self.conn, "gemini", target_dir=self.d)["ok"])


if __name__ == "__main__":
    unittest.main()
