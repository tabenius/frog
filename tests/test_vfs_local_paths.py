import tempfile
import unittest
from pathlib import Path

import _util
from ragbaz_frog.vfs import LocalVFS


class LocalVfsPaths(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="frog-local-paths-")
        self.addCleanup(temporary.cleanup)
        self.parent = Path(temporary.name)
        self.root = self.parent / "workspace"
        self.root.mkdir()
        (self.root / "existing.txt").write_text("Synthetic original")
        self.vfs = LocalVFS(str(self.root))

    def assert_no_duplicated_root(self):
        self.assertFalse((self.root / self.root.as_posix().lstrip("/")).exists())

    def test_absolute_existing_workspace_file_is_not_prefixed_again(self):
        filename = self.root / "existing.txt"
        self.assertTrue(self.vfs.exists(str(filename)))
        self.assertEqual(self.vfs.read_text(str(filename)), "Synthetic original")
        self.assertEqual(self.vfs.stat(str(filename)).size, len("Synthetic original"))
        self.assert_no_duplicated_root()

    def test_absolute_new_workspace_file_is_written_at_selected_path(self):
        filename = self.root / "new" / "draft.txt"
        self.vfs.write_text(str(filename), "Synthetic new")
        self.assertEqual(filename.read_text(), "Synthetic new")
        self.assertEqual(self.vfs.read_text(str(filename)), "Synthetic new")
        self.assert_no_duplicated_root()

    def test_relative_existing_and_new_files_use_current_workspace_directory(self):
        self.assertEqual(self.vfs.read_text("existing.txt"), "Synthetic original")
        self.vfs.write_bytes("new.bin", b"synthetic bytes")
        self.assertEqual((self.root / "new.bin").read_bytes(), b"synthetic bytes")
        self.assert_no_duplicated_root()

    def test_virtual_absolute_paths_keep_their_workspace_root(self):
        self.assertEqual(self.vfs.read_text("/existing.txt"), "Synthetic original")
        self.vfs.write_text("/virtual/new.txt", "Synthetic virtual")
        self.assertEqual((self.root / "virtual" / "new.txt").read_text(), "Synthetic virtual")
        self.assertEqual(self.vfs.listdir("/virtual"), ["new.txt"])

    def test_chdir_then_relative_open_and_parent_navigation(self):
        (self.root / "nested").mkdir()
        self.vfs.chdir("/nested")
        self.assertEqual(self.vfs.cwd(), "/nested")
        with self.vfs.open("draft.txt", "w") as file:
            file.write("Synthetic nested")
        self.assertEqual((self.root / "nested" / "draft.txt").read_text(), "Synthetic nested")
        self.assertEqual(self.vfs.read_text("../existing.txt"), "Synthetic original")
        self.assert_no_duplicated_root()

    def test_absolute_rename_to_new_destination_and_unlink_use_actual_files(self):
        source = self.root / "existing.txt"
        destination = self.root / "renamed.txt"
        self.vfs.rename(str(source), str(destination))
        self.assertFalse(source.exists())
        self.assertEqual(destination.read_text(), "Synthetic original")
        self.vfs.unlink(str(destination))
        self.assertFalse(destination.exists())
        self.assert_no_duplicated_root()

    def test_virtual_relative_names_that_resemble_root_path_are_not_shortened(self):
        relative = self.root.as_posix().lstrip("/") + "/synthetic.txt"
        self.vfs.write_text(relative, "Synthetic virtual relative")
        self.assertEqual((self.root / relative).read_text(), "Synthetic virtual relative")
        self.assertFalse((self.root / "synthetic.txt").exists())

    def test_existing_outside_root_path_compatibility_remains_explicit(self):
        sibling = self.parent / "workspace-sibling"
        sibling.mkdir()
        outside = sibling / "outside.txt"
        outside.write_text("Synthetic outside")
        self.assertEqual(self.vfs.read_text(str(outside)), "Synthetic outside")
        self.assertEqual(outside.read_text(), "Synthetic outside")


if __name__ == "__main__":
    unittest.main()
