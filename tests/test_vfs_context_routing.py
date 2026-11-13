import importlib
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import _util  # Select the checkout and isolate test configuration.
from ragbaz_frog import store, vfs, vfs_store


class WorkspaceVfsRouting(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="frog-vfs-routing-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.roots = {name: root / name for name in ("default", "first", "second")}
        for directory in self.roots.values():
            directory.mkdir()
            (directory / "marker.txt").write_text(directory.name)
        self.baseline = vfs.LocalVFS(str(self.roots["default"]))
        state = patch.object(vfs._vfs_local, "vfs", self.baseline, create=True)
        state.start()
        self.addCleanup(state.stop)
        resolver = patch(
            "ragbaz_frog.config.resolve_workspace",
            side_effect=lambda name, config: {"root": str(self.roots[name])},
        )
        self.resolve = resolver.start()
        self.addCleanup(resolver.stop)

    def assert_routed(self, selected, name):
        self.assertIs(vfs.current_vfs(), selected)
        self.assertIs(vfs_store.current_vfs(), selected)
        self.assertIs(store.current_vfs(), selected)
        self.assertEqual(vfs_store.vfs_read_text("/marker.txt"), name)
        vfs_store.vfs_write_text("/new.txt", "Synthetic " + name)
        self.assertEqual((self.roots[name] / "new.txt").read_text(), "Synthetic " + name)
        self.assertFalse((self.roots["default"] / "new.txt").exists())

    def test_store_compatibility_context_routes_real_filesystem_helpers(self):
        with vfs_store.workspace_vfs_context("first", "synthetic-config") as selected:
            self.assert_routed(selected, "first")
        self.assertIs(vfs.current_vfs(), self.baseline)
        self.resolve.assert_called_once_with("first", "synthetic-config")

    def test_context_module_imports_and_shares_explicit_vfs_selection(self):
        compatibility = importlib.import_module("ragbaz_frog.vfs_context")
        with compatibility.workspace_vfs("first", "synthetic-config") as selected:
            self.assert_routed(selected, "first")
            self.assertIs(compatibility.current_vfs(), selected)
            other = vfs.LocalVFS(str(self.roots["second"]))
            compatibility.set_vfs(other)
            self.assertIs(vfs.current_vfs(), other)
        self.assertIs(compatibility.current_vfs(), self.baseline)

    def test_database_uri_takes_precedence_over_fallback_root(self):
        self.resolve.side_effect = None
        self.resolve.return_value = {
            "db_uri": self.roots["first"].as_uri(),
            "root": str(self.roots["second"]),
        }
        with vfs_store.workspace_vfs_context("selected") as selected:
            self.assert_routed(selected, "first")
        self.assertIs(vfs.current_vfs(), self.baseline)

    def test_unsupported_backend_does_not_fall_back_or_change_the_context(self):
        self.resolve.side_effect = None
        self.resolve.return_value = {
            "db_uri": "unregistered-test-backend:///synthetic",
            "root": str(self.roots["first"]),
        }
        with self.assertRaisesRegex(ValueError, "No VFS registered"):
            with vfs_store.workspace_vfs_context("selected"):
                self.fail("unsupported backends must fail before entering")
        self.assertIs(vfs.current_vfs(), self.baseline)
        self.assertFalse((self.roots["first"] / "new.txt").exists())

    def test_mixed_nested_contexts_restore_the_outer_selection_after_errors(self):
        compatibility = importlib.import_module("ragbaz_frog.vfs_context")
        with vfs.workspace_vfs_context("first") as outer:
            with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
                with vfs_store.workspace_vfs_context("second") as inner:
                    self.assert_routed(inner, "second")
                    raise RuntimeError("synthetic failure")
            self.assertIs(vfs.current_vfs(), outer)
            with compatibility.workspace_vfs("second") as inner:
                self.assert_routed(inner, "second")
            self.assertIs(vfs.current_vfs(), outer)
        self.assertIs(vfs.current_vfs(), self.baseline)

    def test_store_context_does_not_leave_an_explicit_selection_behind(self):
        with patch.object(vfs, "_vfs_local", threading.local()):
            with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
                with vfs_store.workspace_vfs_context("first") as selected:
                    self.assertIs(vfs.current_vfs(), selected)
                    raise RuntimeError("synthetic failure")
            self.assertFalse(hasattr(vfs._vfs_local, "vfs"))

    def test_concurrent_threads_keep_their_own_workspace_and_restore_it(self):
        barrier = threading.Barrier(2)

        def worker(name):
            baseline = vfs.LocalVFS(str(self.roots["default"]))
            vfs.set_vfs(baseline)
            with vfs_store.workspace_vfs_context(name) as selected:
                barrier.wait(timeout=5)
                self.assert_routed(selected, name)
                barrier.wait(timeout=5)
            self.assertIs(vfs.current_vfs(), baseline)

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(worker, name) for name in ("first", "second")]
            for future in futures:
                future.result(timeout=10)
        self.assertIs(vfs.current_vfs(), self.baseline)


if __name__ == "__main__":
    unittest.main()
