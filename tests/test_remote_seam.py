import json
import unittest

import sys
from pathlib import Path
SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ragbaz_frog import main_cli


class RemoteSeam(unittest.TestCase):
    def setUp(self):
        self._orig = main_cli._remote_exec

    def tearDown(self):
        main_cli._remote_exec = self._orig

    def _ws(self):
        return {
            "name": "box2", "root": "/data/src", "db": "/data/src/AGENTS.db",
            "frog_bin": "/data/src/ragbaz-frog/bin/frog",
            "host": {"transport": "ssh", "ssh_target": "user@box2"},
        }

    def test_local_workspace_is_noop(self):
        ws = self._ws(); ws["host"]["transport"] = "local"
        self.assertEqual(main_cli._dispatch_workspace(ws, ["status"]), {"ok": True})

    def test_remote_json_is_parsed_via_seam(self):
        captured = {}

        def fake(target, cmd):
            captured["target"] = target
            captured["cmd"] = cmd
            return 0, json.dumps({"ok": True, "events": [{"id": 1}]}), ""

        main_cli._remote_exec = fake
        out = main_cli._dispatch_workspace(self._ws(), ["log", "--limit", "5"])
        self.assertTrue(out["ok"])
        self.assertEqual(out["events"], [{"id": 1}])
        self.assertEqual(captured["target"], "user@box2")
        self.assertIn("--json", captured["cmd"])
        self.assertIn("log", captured["cmd"])

    def test_discover_gets_root_injected(self):
        seen = {}

        def fake(target, cmd):
            seen["cmd"] = cmd
            return 0, "{}", ""

        main_cli._remote_exec = fake
        main_cli._dispatch_workspace(self._ws(), ["repo", "discover"])
        self.assertIn("--root", seen["cmd"])
        self.assertIn("/data/src", seen["cmd"])

    def test_nonzero_rc_surfaces_error(self):
        main_cli._remote_exec = lambda t, c: (255, "", "ssh: connect refused")
        out = main_cli._dispatch_workspace(self._ws(), ["status"])
        self.assertFalse(out["ok"])
        self.assertEqual(out["returncode"], 255)


if __name__ == "__main__":
    unittest.main()
