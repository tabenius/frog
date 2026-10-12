import subprocess
import tempfile
import unittest
from pathlib import Path

from _util import fresh_db
from ragbaz_frog import store
from ragbaz_frog.paths import resolve_repo_path


class RelocationPaths(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="frog-relocation-")
        self.addCleanup(self.temp.cleanup)
        self.old = Path(self.temp.name) / "old" / "demo"
        self.new = Path(self.temp.name) / "new" / "demo"
        self.old.mkdir(parents=True)
        self.new.parent.mkdir()
        (self.old / "pkg").mkdir()
        (self.old / "pkg" / "source.py").write_text("x = 1\n")
        subprocess.run(["git", "init", "-q", str(self.old)], check=True)
        subprocess.run(["git", "-C", str(self.old), "add", "."], check=True)
        self.conn = store.connect(fresh_db())
        self.addCleanup(self.conn.close)
        store.register_repo(self.conn, repo_path=str(self.old), name="demo",
                            kind=None, status="active", third_party=False, notes=None)
        store._insert_target(self.conn, repo_path=str(self.old), target_kind="test",
                             name="pkg", command="true", workdir=str(self.old / "pkg"),
                             runner="shell", source="test", confidence=1)
        self.conn.commit()

    def move(self):
        self.old.rename(self.new)
        result = store.repo_move(self.conn, str(self.old), str(self.new))
        self.assertTrue(result["ok"], result)

    def test_relative_storage_run_cache_and_affected_survive_move(self):
        self.assertEqual(self.conn.execute("SELECT workdir FROM repo_targets").fetchone()[0], "pkg")
        first = store.repo_run(self.conn, "demo", "test")
        self.assertTrue(first["ok"], first)
        self.move()
        targets = store.repo_targets(self.conn, "demo")["targets"]
        self.assertEqual(targets[-1]["workdir"], str(self.new / "pkg"))
        second = store.repo_run(self.conn, "demo", "test")
        self.assertEqual(second["results"][0]["status"], "cached")
        affected = store._targets_for_files(self.conn, str(self.new), [str(self.new / "pkg" / "source.py")])
        self.assertEqual([t["name"] for t in affected], ["pkg"])
        forced = store.repo_run(self.conn, "demo", "test", use_cache=False)
        self.assertTrue(forced["ok"], forced)

    def test_legacy_absolute_targets_recover_and_bad_target_does_not_abort(self):
        self.conn.execute("UPDATE repo_targets SET workdir=?", (str(self.old / "pkg"),))
        self.conn.commit()
        self.move()
        store._insert_target(self.conn, repo_path=str(self.new), target_kind="test",
                             name="missing", command="true", workdir=str(self.new / "missing"),
                             runner="shell", source="test", confidence=1)
        result = store.repo_run(self.conn, "demo", "test", use_cache=False)
        self.assertFalse(result["ok"], result)
        by_name = {r["name"]: r for r in result["results"]}
        self.assertEqual(by_name["pkg"]["returncode"], 0)
        self.assertEqual(by_name["missing"]["returncode"], 127)
        self.assertTrue(by_name["missing"]["stderr"])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM target_runs").fetchone()[0], 2)

    def test_legacy_lock_still_covers_current_file(self):
        locked = store.lock_acquire(self.conn, scope_key="legacy-file", repo_ref="demo",
                                    lock_kind="edit", files=[str(self.old / "pkg" / "source.py")],
                                    agent="alice", pid=None, reason=None, lease_seconds=3600,
                                    eta_minutes=None, force=False)
        self.assertTrue(locked["ok"], locked)
        # Simulate old rows that lack the newer repo-relative lock column.
        self.conn.execute("UPDATE locks SET rel_files_json='[]'")
        self.conn.commit()
        self.move()
        path = str(self.new / "pkg" / "source.py")
        check = store.lock_check(self.conn, scope_key="different", repo_ref="demo", files=[path])
        self.assertEqual(check["conflicts"][0]["file_paths"], [path])
        collision = store.lock_acquire(self.conn, scope_key="different", repo_ref="demo",
                                       lock_kind="edit", files=[path], agent="bob", pid=None,
                                       reason=None, lease_seconds=3600, eta_minutes=None, force=False)
        self.assertFalse(collision["ok"], collision)

    def test_unrelated_absolute_path_is_not_rebased(self):
        self.assertEqual(resolve_repo_path(str(self.new), "/unrelated/shared"), Path("/unrelated/shared"))
        with self.assertRaises(ValueError):
            resolve_repo_path(str(self.new), "../escape")
