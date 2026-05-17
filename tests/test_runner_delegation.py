import subprocess
import tempfile
import unittest
from pathlib import Path

from _util import fresh_db
from ragbaz_frog import store


def _repo(files: dict) -> str:
    d = tempfile.mkdtemp(prefix="frog-runner-")
    subprocess.run(["git", "-C", d, "init", "-q"], check=True)
    for name, body in files.items():
        (Path(d) / name).write_text(body)
    subprocess.run(["git", "-C", d, "add", "-A"], check=True)
    subprocess.run(["git", "-C", d, "-c", "user.email=t@t", "-c",
                     "user.name=t", "commit", "-q", "-m", "i"], check=True)
    return d


class RunnerDelegation(unittest.TestCase):
    def _scan(self, repo):
        conn = store.connect(fresh_db())
        store.register_repo(conn, repo_path=repo, name="r", kind=None,
                            status="active", third_party=False, notes=None)
        store.repo_scan(conn, repo)
        rows = conn.execute(
            "SELECT target_kind,name,command,runner,confidence FROM repo_targets "
            "WHERE repo_path=? ORDER BY confidence DESC", (repo,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def test_taskfile_delegation(self):
        repo = _repo({"Taskfile.yml":
                      "version: '3'\ntasks:\n  build:\n    cmds: [go build]\n"
                      "  test:\n    cmds: [go test ./...]\n  deploy:\n    cmds: [./d]\n"})
        ts = self._scan(repo)
        cmds = {t["command"] for t in ts}
        self.assertIn("task build", cmds)
        self.assertIn("task test", cmds)
        self.assertTrue(all(t["runner"] == "task" for t in ts if t["command"].startswith("task ")))

    def test_justfile_delegation(self):
        repo = _repo({"justfile":
                      "set shell := ['bash','-c']\n\nbuild:\n\tcargo build\n\n"
                      "lint:\n\tcargo clippy\n"})
        ts = self._scan(repo)
        cmds = {t["command"] for t in ts}
        self.assertIn("just build", cmds)
        self.assertIn("just lint", cmds)

    def test_runner_outranks_makefile(self):
        # both a Taskfile build and a Makefile build -> runner wins on confidence
        repo = _repo({
            "Taskfile.yml": "version: '3'\ntasks:\n  build:\n    cmds: [echo t]\n",
            "Makefile": "build:\n\techo m\n",
        })
        ts = self._scan(repo)
        builds = [t for t in ts if t["target_kind"] == "build"]
        top = max(builds, key=lambda t: t["confidence"])
        self.assertEqual(top["runner"], "task",
                         "declared runner intent should outrank re-derived make")

    def test_mise_delegation(self):
        repo = _repo({"mise.toml": "[tasks.build]\nrun = 'go build'\n[tasks.ci]\nrun='go test'\n"})
        ts = self._scan(repo)
        cmds = {t["command"] for t in ts}
        self.assertIn("mise run build", cmds)
        self.assertIn("mise run ci", cmds)


if __name__ == "__main__":
    unittest.main()
