"""First run on a machine whose workspace is not /data/src.

Three bugs met setting Frog up on a fresh host (2026-09-27):
- `config workspace add --db PATH` saved /data/src/AGENTS.db: --db is also a
  global flag, hoisted to the front, and the subparser's default erased it;
- every command opened DEFAULT_DB_PATH before switching to the configured
  workspace's database, so it failed where /data cannot be created;
- `repo discover` missed a Git repository with no build manifest (e.g. a
  Nix-only flake repo): .git was pruned from the walk before the check.
"""
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from _util import fresh_db  # noqa: F401  (sets FROG_HOME, sys.path)
from ragbaz_frog import DEFAULT_DB_PATH, store
from ragbaz_frog.main_cli import main


def run(*argv):
    out = io.StringIO()
    with redirect_stdout(out):
        code = main(list(argv))
    return code, out.getvalue()


class FirstRun(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="frog-first-run-"))
        self.config = str(self.tmp / "frog.json")
        self.root = self.tmp / "src"
        self.root.mkdir()
        self.db = str(self.root / "AGENTS.db")

    def add_workspace(self):
        code, _ = run("--config", self.config, "--json", "config", "workspace", "add", "here",
                      "--host", "local", "--root", str(self.root), "--db", self.db, "--default")
        self.assertEqual(code, 0)

    def test_workspace_add_keeps_its_db(self):
        self.add_workspace()
        saved = json.loads(Path(self.config).read_text())["workspaces"]["here"]
        self.assertEqual(saved["db"], self.db)

    def test_commands_use_the_configured_db_not_the_default(self):
        self.add_workspace()
        code, _ = run("--config", self.config, "--json", "db", "migrate")
        self.assertEqual(code, 0)
        code, out = run("--config", self.config, "--json", "status")
        self.assertEqual(code, 0, out)
        self.assertTrue(Path(self.db).exists())
        if not Path(DEFAULT_DB_PATH).exists():
            self.assertFalse(Path(DEFAULT_DB_PATH).parent.exists(),
                             "the default location was created")

    def test_discover_finds_a_git_repo_without_a_build_manifest(self):
        flake = self.root / "flake-only"
        flake.mkdir()
        subprocess.run(["git", "init", "-q", str(flake)], check=True)
        (flake / "flake.nix").write_text("{ outputs = _: { }; }\n")
        py = self.root / "python"
        py.mkdir()
        subprocess.run(["git", "init", "-q", str(py)], check=True)
        (py / "pyproject.toml").write_text("[project]\nname='p'\n")

        store.migrate(self.db)
        conn = store.connect(self.db)
        try:
            found = store.discover_repos(conn, root=str(self.root), scan=False)
        finally:
            conn.close()
        paths = {Path(r["repo_path"]).name for r in found["repos"]}
        self.assertEqual(paths, {"flake-only", "python"})

    def test_paths_follow_the_workspace_not_data_src(self):
        self.add_workspace()
        old = os.environ.pop("RAGBAZ_SRC_ROOT", None)
        from ragbaz_frog import config as frog_config
        real = frog_config.resolve_workspace
        frog_config.resolve_workspace = lambda name, path=None: real(name, self.config)
        try:
            self.assertEqual(store.workspace_root(), self.root)
            self.assertEqual(store.workspace_db(), self.db)
            text = store.agent_instructions_text()
            self.assertIn(str(self.root / "AGENTS.md"), text)
            self.assertIn(self.db, text)
            self.assertNotIn("/data/src", text + store._agent_md("a"))
            os.environ["RAGBAZ_SRC_ROOT"] = str(self.tmp / "elsewhere")
            self.assertEqual(store.workspace_root(), (self.tmp / "elsewhere").resolve())
        finally:
            frog_config.resolve_workspace = real
            os.environ.pop("RAGBAZ_SRC_ROOT", None)
            if old is not None:
                os.environ["RAGBAZ_SRC_ROOT"] = old

    def test_sync_defaults_follow_workspace_changes(self):
        from ragbaz_frog import sync_watcher
        for root in (self.root, self.tmp):
            with patch.dict(os.environ, {"RAGBAZ_SRC_ROOT": str(root)}):
                cfg = sync_watcher.load_config()
                self.assertEqual(cfg.local_path, str(root))
                self.assertEqual(sync_watcher.sync_config_path(), root / ".frog-sync.json")
                self.assertEqual(cfg.remote_path, "/data/src")
        first = sync_watcher.SyncConfig()
        first.excludes.append("private/")
        self.assertNotIn("private/", sync_watcher.SyncConfig().excludes)

    def test_new_uses_explicit_config_workspace(self):
        self.add_workspace()
        code, _ = run("--config", self.config, "--json", "db", "migrate")
        self.assertEqual(code, 0)
        with patch.dict(os.environ):
            os.environ.pop("RAGBAZ_SRC_ROOT", None)
            code, output = run("--config", self.config, "--json", "new", "portable")
        self.assertEqual(code, 0, output)
        path = self.root / "experiments" / "portable"
        self.assertTrue(path.is_dir())
        self.assertIn(str(self.root), (path / "AGENTS.md").read_text())
        self.assertIsNone(store._workspace_context.get())

    def test_agent_setup_quotes_paths_with_spaces(self):
        import shlex
        import tomllib
        path = '/tmp/a workspace/"quoted"/frog-mcp'
        with patch.object(store, "_frog_mcp", return_value=path):
            conn = store.connect(fresh_db())
            try:
                result = store.setup_agent(conn, "codex", target_dir=str(self.tmp))
            finally:
                conn.close()
        self.assertEqual(tomllib.loads(result["codex_config_toml"])["mcp_servers"]["frog"]["command"], path)
        with patch.object(store, "_frog_bin", return_value="/tmp/a workspace/frog"):
            command = store._claude_settings_fragment()["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        self.assertEqual(shlex.split(command)[0], "/tmp/a workspace/frog")


if __name__ == "__main__":
    unittest.main()
