import json
import tempfile
import unittest

import sys
from pathlib import Path
from types import SimpleNamespace
SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from _util import fresh_db
from ragbaz_frog import config
from ragbaz_frog import main_cli
from ragbaz_frog import store


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

    def test_task_claim_routes_to_configured_remote_coordinator(self):
        cfg = str(Path(tempfile.mkdtemp(prefix="frog-remote-cfg-")) / "config.json")
        config.ensure_config(cfg)
        config.add_host("box2", ssh_target="user@box2", path=cfg)
        config.add_workspace("box2-src", host_name="box2", root="/data/src", path=cfg)
        config.set_coordinator("box2-src", cfg)
        active = config.resolve_workspace("local-src", cfg)
        args = SimpleNamespace(
            command="task",
            task_command="claim",
            config=cfg,
            _workspace_explicit=False,
        )
        coord = main_cli._coordinator_workspace_for_write(args, active)
        self.assertIsNotNone(coord)
        self.assertEqual(coord["name"], "box2-src")

    def test_explicit_workspace_does_not_route_to_coordinator(self):
        cfg = str(Path(tempfile.mkdtemp(prefix="frog-remote-cfg-")) / "config.json")
        config.ensure_config(cfg)
        config.add_host("box2", ssh_target="user@box2", path=cfg)
        config.add_workspace("box2-src", host_name="box2", root="/data/src", path=cfg)
        config.set_coordinator("box2-src", cfg)
        active = config.resolve_workspace("local-src", cfg)
        args = SimpleNamespace(
            command="lock",
            lock_command="acquire",
            config=cfg,
            _workspace_explicit=True,
        )
        self.assertIsNone(main_cli._coordinator_workspace_for_write(args, active))

    def test_explicit_db_does_not_route_to_coordinator(self):
        cfg = str(Path(tempfile.mkdtemp(prefix="frog-remote-cfg-")) / "config.json")
        config.ensure_config(cfg)
        config.add_host("box2", ssh_target="user@box2", path=cfg)
        config.add_workspace("box2-src", host_name="box2", root="/data/src", path=cfg)
        config.set_coordinator("box2-src", cfg)
        active = config.resolve_workspace("local-src", cfg)
        args = SimpleNamespace(
            command="lock",
            lock_command="acquire",
            config=cfg,
            _db_explicit=True,
            _workspace_explicit=False,
        )
        self.assertIsNone(main_cli._coordinator_workspace_for_write(args, active))

    def test_whereis_fanout_uses_local_only_remote_call(self):
        cfg = str(Path(tempfile.mkdtemp(prefix="frog-remote-cfg-")) / "config.json")
        config.ensure_config(cfg)
        config.add_host("box2", ssh_target="user@box2", path=cfg)
        config.add_workspace("box2-src", host_name="box2", root="/srv/src", path=cfg)
        conn = store.connect(fresh_db())
        seen = {}

        def fake(target, cmd):
            seen["target"] = target
            seen["cmd"] = cmd
            return 0, json.dumps(
                {
                    "ok": True,
                    "repo_key": "git:example",
                    "box": "box2",
                    "local_path": "/srv/src/project",
                    "aliases": [],
                }
            ), ""

        main_cli._remote_exec = fake
        try:
            out = main_cli._whereis_federated(conn, "git:example", config_path=cfg)
        finally:
            conn.close()
        self.assertTrue(out["ok"])
        self.assertIn("--local-only", seen["cmd"])
        self.assertEqual(seen["target"], "user@box2")
        remote = [item for item in out["workspaces"] if item["workspace"] == "box2-src"][0]
        self.assertEqual(remote["result"]["local_path"], "/srv/src/project")


if __name__ == "__main__":
    unittest.main()
