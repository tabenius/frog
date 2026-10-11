import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from _util import fresh_db
from ragbaz_frog import store
from ragbaz_frog.vfs import LocalVFS, current_vfs, set_vfs


class DiscoveryExclusions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="frog-exclusions-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.conn = store.connect(fresh_db())
        self.addCleanup(self.conn.close)
        previous = current_vfs()
        self.addCleanup(set_vfs, previous)
        self.vfs = LocalVFS(str(self.root))
        set_vfs(self.vfs)
        (self.root / "package.json").write_text('{}')
        for excluded in ("node_modules", ".venv", "target"):
            package = self.root / excluded / "dep"
            (package / ".git").mkdir(parents=True)
            (package / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
            (package / "package.json").write_text('{}')
        child = self.root / "real-child"
        (child / ".git").mkdir(parents=True)
        (child / ".git" / "HEAD").write_text("ref: refs/heads/main\n")

    def assert_clean(self, result):
        self.assertTrue(result["ok"], result)
        self.assertEqual({r["repo_path"] for r in result["repos"]},
                         {str(self.root), str(self.root / "real-child")})

    def test_vfs_and_cached_discovery_exclude_dependencies(self):
        self.assert_clean(store.discover_repos(self.conn, root=str(self.root), scan=False))
        self.assert_clean(store.discover_repos(self.conn, root=str(self.root), scan=False))

    def test_local_fallback_excludes_dependencies(self):
        self.assert_clean(store._discover_repos_local_fallback(self.conn, str(self.root), False))

    def test_ssh_manifest_discovery_excludes_dependencies_and_returns_repos(self):
        class Remote:
            def find_manifests_in_repos(remote, manifests, root):
                return {"/project": ["package.json"],
                        "/project/node_modules/dep": ["package.json"]}

            def name(remote, path):
                return Path(path).name

        with patch.object(store, "_vfs_to_local_path", side_effect=lambda p, v: str(self.root / p.lstrip("/"))), \
                patch.object(store, "_discover_repos_git_fallback", return_value=[]), \
                patch.object(store, "_discover_repos_set_cache"):
            result = store._discover_repos_ssh_optimized(self.conn, "/", Remote(), False)
        self.assertTrue(result["ok"], result)
        self.assertEqual([r["repo_path"] for r in result["repos"]],
                         [str(self.root / "project")])
        self.assertEqual(result["counts"]["discovered"], 1)
        self.assertIsNone(self.conn.execute("SELECT 1 FROM repos WHERE repo_path=?",
                                           (str(self.root / "project/node_modules/dep"),)).fetchone())

    def test_polluted_cache_cannot_register_dependency_again(self):
        result = store.discover_repos(self.conn, root=str(self.root), scan=False)
        fake = dict(result["repos"][0], repo_path=str(self.root / "node_modules" / "dep"), name="dep")
        with patch.object(store, "_discover_repos_get_cache", return_value={"repos": [*result["repos"], fake]}):
            self.assert_clean(store.discover_repos(self.conn, root=str(self.root), scan=False))
        self.assertIsNone(self.conn.execute("SELECT 1 FROM repos WHERE repo_path=?",
                                           (fake["repo_path"],)).fetchone())

    def test_remote_git_globs_apply_same_exclusions(self):
        class Remote:
            def parent(self, p):
                return str(Path(p).parent)
            def is_file(self, p):
                return p.endswith(".git")
        remote = Remote()
        with patch.object(store, "vfs_glob", side_effect=[
            ["/good/.git/HEAD", "/node_modules/dep/.git/HEAD"],
            ["/linked/.git", "/.venv/dep/.git"],
        ]), patch.object(store, "_vfs_to_local_path", side_effect=lambda p, v: p):
            self.assertEqual(store._discover_repos_git_fallback("/", remote, set()), ["/good", "/linked"])
