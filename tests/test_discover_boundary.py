"""Regression coverage for _looks_like_repo_boundary()'s .git detection.

Bug: discover_repos() strips ".git" out of `dirnames` before calling
_looks_like_repo_boundary() (DISCOVERY_EXCLUDED_DIRS controls os.walk
pruning), so the boundary check's `".git" in dirnames or ".git" in
filenames` could never be true -- dead code. A repo with no Makefile /
package.json / Cargo.toml / pyproject.toml / docker-compose file and no
AGENTS.md (real example: ragbaz-surfaces, a skills-only repo) was
therefore invisible to `frog repo discover`, even discovered directly by
its own path, despite being a perfectly real git repository.
"""
import subprocess
import tempfile
import unittest
from pathlib import Path

from _util import fresh_db
from ragbaz_frog import store


def _init_git_repo(tmp_path: Path) -> Path:
    d = tmp_path / "bare-skills-repo"
    d.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=d, check=True)
    (d / "README.md").write_text("# nothing but a readme\n")
    subprocess.run(["git", "add", "-A"], cwd=d, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-q", "-m", "init"],
        cwd=d, check=True,
    )
    return d


class DiscoverBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.conn = store.connect(fresh_db())
        self._tmp = tempfile.TemporaryDirectory(prefix="frog-discover-")
        self.tmp_path = Path(self._tmp.name)

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()

    def test_bare_git_repo_with_no_manifest_is_discovered_by_direct_root(self):
        """The exact regression: `frog repo discover --root <that repo>`
        for a repo with only a README + .git (no build manifest, no
        AGENTS.md) must register it -- this is what silently failed for
        ragbaz-surfaces."""
        repo = _init_git_repo(self.tmp_path)
        result = store.discover_repos(self.conn, root=str(repo), scan=False)
        self.assertEqual(result["counts"]["discovered"], 1, result)
        self.assertEqual(result["repos"][0]["repo_path"], str(repo.resolve()))

    def test_bare_git_repo_is_discovered_as_a_nested_child_too(self):
        """Same repo, but discovered by scanning its *parent* -- the path
        the working code did handle before the fix (current != root_path),
        confirming the fix didn't regress the already-working case."""
        parent = self.tmp_path / "workspace"
        parent.mkdir()
        repo = _init_git_repo(parent)
        result = store.discover_repos(self.conn, root=str(parent), scan=False)
        self.assertEqual(result["counts"]["discovered"], 1, result)
        self.assertEqual(result["repos"][0]["repo_path"], str(repo.resolve()))

    def test_hollow_git_directory_is_not_a_boundary(self):
        """An empty `.git` dir (no HEAD -- e.g. a broken/uninitialized
        `git init` that never got a first commit or was left half-formed)
        must NOT be treated as a repo. Guards against the fix over-firing
        on directories that merely contain a `.git`-named directory."""
        hollow = self.tmp_path / "not-really-a-repo"
        (hollow / ".git").mkdir(parents=True)
        result = store.discover_repos(self.conn, root=str(hollow), scan=False)
        self.assertEqual(result["counts"]["discovered"], 0, result)

    def test_worktree_style_gitdir_file_is_a_boundary(self):
        """A linked worktree's `.git` is a *file* containing a `gitdir:`
        pointer, not a directory -- must still count."""
        worktree = self.tmp_path / "a-worktree"
        worktree.mkdir()
        (worktree / ".git").write_text("gitdir: /somewhere/else/.git/worktrees/a-worktree\n")
        result = store.discover_repos(self.conn, root=str(worktree), scan=False)
        self.assertEqual(result["counts"]["discovered"], 1, result)


if __name__ == "__main__":
    unittest.main()
