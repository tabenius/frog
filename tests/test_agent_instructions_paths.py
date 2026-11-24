import tempfile
import unittest
from pathlib import Path

import _util
from ragbaz_frog import store


class AgentInstructionsPaths(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="frog-agent-paths-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "workspace"
        self.root.mkdir()
        self.outside = self.base / "outside"
        self.outside.mkdir()
        self.db = str(self.base / "AGENTS.db")
        store.migrate(self.db)
        store.set_vfs(store.LocalVFS(str(self.root)))
        self.addCleanup(store.set_vfs, None)
        self.conn = store.connect(self.db)
        self.addCleanup(self.conn.close)

    def assert_not_nested_inside_root(self, target: Path):
        nested = self.root / target.as_posix().lstrip("/")
        self.assertFalse(nested.exists(), f"target was misrouted under {nested}")

    def test_absolute_outside_directory_receives_the_requested_file(self):
        result = store.write_agent_instructions(self.conn, str(self.outside))
        expected = self.outside / "AGENTS.md"
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["agents_path"], str(expected))
        self.assertTrue(expected.is_file())
        self.assertIn("AGENTS.md", expected.read_text())
        self.assert_not_nested_inside_root(expected)

    def test_absolute_outside_markdown_path_is_written_at_that_path(self):
        expected = self.outside / "custom-name.md"
        result = store.write_agent_instructions(self.conn, str(expected))
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["agents_path"], str(expected))
        self.assertTrue(expected.is_file())
        self.assert_not_nested_inside_root(expected)

    def test_outside_overwrite_is_refused_then_allowed_with_force(self):
        expected = self.outside / "AGENTS.md"
        expected.write_text("Synthetic existing instructions")
        refused = store.write_agent_instructions(self.conn, str(self.outside))
        self.assertFalse(refused["ok"])
        self.assertIn("refusing to overwrite", refused["error"])
        self.assertEqual(expected.read_text(), "Synthetic existing instructions")

        forced = store.write_agent_instructions(
            self.conn, str(self.outside), force=True
        )
        self.assertTrue(forced["ok"], forced)
        self.assertNotEqual(
            expected.read_text(), "Synthetic existing instructions"
        )
        self.assert_not_nested_inside_root(expected)

    def test_in_root_target_remains_vfs_backed(self):
        repo = self.root / "nested" / "repo"
        repo.mkdir(parents=True)
        result = store.write_agent_instructions(self.conn, str(repo))
        expected = repo / "AGENTS.md"
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["agents_path"], str(expected))
        self.assertEqual(expected, repo / "AGENTS.md")
        self.assertTrue(expected.is_file())

    def test_omitted_path_targets_the_workspace_root(self):
        result = store.write_agent_instructions(self.conn, None)
        expected = self.root / "AGENTS.md"
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["agents_path"], str(expected))
        self.assertTrue(expected.is_file())


if __name__ == "__main__":
    unittest.main()
