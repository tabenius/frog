import tempfile
import unittest
from pathlib import Path

import _util
from ragbaz_frog.vfs import LocalVFS


class LocalVfsRoot(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="frog-vfs-root-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "workspace"
        self.root.mkdir()
        (self.root / "a.txt").write_text("Synthetic")
        (self.root / "nested").mkdir()
        (self.root / "nested" / "b.txt").write_text("Synthetic nested")
        self.vfs = LocalVFS(str(self.root))

    def test_initial_cwd_is_the_virtual_root(self):
        self.assertEqual(self.vfs.cwd(), "/")

    def test_chdir_root_and_nested_return_normalized_cwd(self):
        self.vfs.chdir("nested")
        self.assertEqual(self.vfs.cwd(), "/nested")
        self.vfs.chdir("/")
        self.assertEqual(self.vfs.cwd(), "/")

    def test_root_walk_reports_slash_not_dot(self):
        roots = [item[0] for item in self.vfs.walk("/")]
        self.assertEqual(roots[0], "/")
        self.assertNotIn("/.", roots)

    def test_nested_walk_reports_normalized_directory(self):
        roots = [item[0] for item in self.vfs.walk("/nested")]
        self.assertEqual(roots, ["/nested"])

    def test_relative_to_exact_base_is_empty(self):
        self.assertEqual(self.vfs.relative_to("/nested", "/nested"), "")

    def test_relative_to_root_and_nested_children(self):
        self.assertEqual(self.vfs.relative_to("/a.txt", "/"), "a.txt")
        self.assertEqual(
            self.vfs.relative_to("/nested/b.txt", "/"), "nested/b.txt"
        )
        self.assertEqual(
            self.vfs.relative_to("/nested/b.txt", "/nested"), "b.txt"
        )

    def test_relative_to_rejects_sibling_prefix_collision(self):
        with self.assertRaises(ValueError):
            self.vfs.relative_to("/foo/bar", "/foo/b")
        with self.assertRaises(ValueError):
            self.vfs.relative_to("/foobar", "/foo")

    def test_relative_to_accepts_component_matching_prefix(self):
        self.assertEqual(self.vfs.relative_to("/foo/b/ar", "/foo/b"), "ar")


if __name__ == "__main__":
    unittest.main()
