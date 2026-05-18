import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from _util import fresh_db
from ragbaz_frog import store

HOOK = Path(__file__).resolve().parents[1] / "hooks/pretooluse-lock-guard.sh"
FROG = Path(__file__).resolve().parents[1] / "bin/frog"


class LockGuardHook(unittest.TestCase):
    def setUp(self):
        self.db = fresh_db()
        self.f = tempfile.mktemp(prefix="frog-guard-file-")
        Path(self.f).write_text("x")
        conn = store.connect(self.db)
        store.lock_acquire(conn, scope_key=f"edit:{self.f}", repo_ref=None,
                           lock_kind="edit", files=[self.f], agent="codex",
                           pid=None, reason=None, lease_seconds=1800,
                           eta_minutes=None, force=False)
        conn.close()

    def _run(self, agent, block=False):
        env = dict(os.environ, FROG_DB=self.db, FROG_BIN=str(FROG),
                   FROG_AGENT=agent)
        if block:
            env["FROG_LOCK_GUARD_BLOCK"] = "1"
        payload = json.dumps({"tool_name": "Edit",
                               "tool_input": {"file_path": self.f}})
        return subprocess.run([str(HOOK)], input=payload, env=env,
                              text=True, capture_output=True).stdout.strip()

    def test_different_agent_warns(self):
        out = self._run("claude")
        self.assertTrue(out)
        self.assertIn("systemMessage", out)
        self.assertIn("codex", out)

    def test_same_agent_is_silent(self):
        self.assertEqual(self._run("codex"), "",
                         "your own lock must not trigger the guard")

    def test_block_mode_denies(self):
        out = self._run("claude", block=True)
        self.assertIn('"continue":false', out)


if __name__ == "__main__":
    unittest.main()
