"""A workspace relocation must not break run, cache, or affectedness.

These tests move a repo *after* its targets have been recorded, which is the
situation a real relocation creates: the database is full of absolute paths that
described the world before. The claim under test is not "the database was
migrated" -- it never is -- but that resolution happens at the point of use, so a
stale row heals on its own.

The failure this guards against is specific and was observed: `frog task finish`
died with FileNotFoundError on a workdir recorded under the previous root, which
took down verification for every repo on the box at once.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

from _util import fresh_db

from ragbaz_frog import paths, store


def _repo_with_target(cmd: str = "true", subdir: str | None = None) -> str:
    """A git repo carrying a Makefile target frog will discover."""
    root = tempfile.mkdtemp(prefix="frog-reloc-")
    d = Path(root) / subdir if subdir else Path(root)
    d.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", root, "init", "-q"], check=True)
    (d / "src.txt").write_text("v1\n")
    (d / "Makefile").write_text(f"build:\n\t{cmd}\n")
    subprocess.run(["git", "-C", root, "add", "-A"], check=True)
    subprocess.run(["git", "-C", root, "-c", "user.email=t@t", "-c",
                    "user.name=t", "commit", "-q", "-m", "i"], check=True)
    return root


def _relocate(src: str) -> str:
    """Move a tree the way a workspace move does.

    The repo keeps its own name and only its *parent* changes -- that is what a
    workspace relocation is. Renaming the repo instead would test a case that
    never happens, and would let name-based recovery pass for the wrong reason.
    """
    src_path = Path(src)
    new_parent = Path(tempfile.mkdtemp(prefix="frog-moved-"))
    dst = new_parent / src_path.name
    shutil.move(src, dst)
    assert not os.path.exists(src), "relocation must not leave the old path behind"
    return str(dst)


def _repoint_repo(db: str, old: str, new: str) -> None:
    """Repoint the repo at its new location, leaving recorded paths untouched.

    This is the shape the box is actually in after a move: `repos.repo_path`,
    `repo_targets.repo_path` and `locks.repo_path` are current, while every
    recorded `workdir` and every granted lock `file_paths` entry still names the
    previous root. Repointing is what a re-scan does; rewriting the recorded
    paths is what nobody did.
    """
    conn = sqlite3.connect(db)
    for table in ("repos", "repo_targets", "target_runs", "locks"):
        conn.execute(f"UPDATE {table} SET repo_path=? WHERE repo_path=?", (new, old))
    conn.commit()
    conn.close()


def _assert_rows_are_stale(db: str) -> int:
    """Every recorded workdir must be dead, or the fixture proved nothing."""
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT workdir FROM repo_targets WHERE workdir IS NOT NULL").fetchall()
    conn.close()
    dead = [r[0] for r in rows if r[0] and not os.path.isdir(r[0])]
    assert dead, "fixture must leave at least one stale workdir"
    return len(dead)


class PathResolution(unittest.TestCase):
    """The resolver itself, independent of any database."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="frog-pathres-"))
        # Only the *new* tree exists. The old one is referenced but never
        # created, so every "stale" path below is genuinely dead -- otherwise
        # resolution would legitimately take the "it still exists" branch and
        # the recovery path would never be exercised.
        (self.tmp / "new" / "MyRepo" / "src" / "crate").mkdir(parents=True)
        (self.tmp / "new" / "MyRepo" / "file.txt").write_text("x\n")
        self.old_root = str(self.tmp / "old" / "MyRepo")
        self.new_root = str(self.tmp / "new" / "MyRepo")

    def test_relative_joins_to_current_root(self):
        r = paths.resolve(self.new_root, "src/crate")
        self.assertTrue(r.ok)
        self.assertEqual(r.path, str(Path(self.new_root) / "src" / "crate"))
        self.assertFalse(r.relocated)

    def test_live_absolute_is_used_as_is(self):
        r = paths.resolve(self.new_root, str(Path(self.new_root) / "src"))
        self.assertTrue(r.ok)
        self.assertFalse(r.relocated)

    def test_stale_absolute_recovers_from_repo_relative_suffix(self):
        # The old tree is deliberately never created, so the stored path is dead.
        r = paths.resolve(self.new_root, os.path.join(self.old_root, "src", "crate"))
        self.assertTrue(r.ok, r.reason)
        self.assertTrue(r.relocated)
        self.assertEqual(r.path, str(Path(self.new_root) / "src" / "crate"))

    def test_stale_repo_root_recovers_as_root(self):
        r = paths.resolve(self.new_root, self.old_root)
        self.assertTrue(r.ok, r.reason)
        self.assertTrue(r.relocated)
        self.assertEqual(r.path, self.new_root)

    def test_unrecoverable_path_fails_without_raising(self):
        r = paths.resolve(self.new_root, "/nowhere/else/entirely/deeper")
        self.assertFalse(r.ok)
        self.assertIsNone(r.path)
        self.assertIn("could not be recovered", r.reason)

    def test_relative_escape_is_refused(self):
        r = paths.resolve(self.new_root, "../../../etc")
        self.assertFalse(r.ok)
        self.assertIsNone(r.path)

    def test_absolute_escape_is_refused(self):
        r = paths.resolve(self.new_root, os.path.join(self.old_root, "..", "..", "etc"))
        self.assertFalse(r.ok)
        self.assertIsNone(r.path)

    def test_resolve_file_recovers_a_dead_file(self):
        r = paths.resolve_file(self.new_root, os.path.join(self.old_root, "file.txt"))
        self.assertTrue(r.ok, r.reason)
        self.assertTrue(r.relocated)
        self.assertEqual(r.path, str(Path(self.new_root) / "file.txt"))

    def test_repo_relative_is_the_same_before_and_after_a_move(self):
        """The property the cache key depends on."""
        before = paths.repo_relative(self.old_root, os.path.join(self.old_root, "src", "crate"))
        after = paths.repo_relative(self.new_root, os.path.join(self.old_root, "src", "crate"))
        self.assertEqual(before, "src/crate")
        self.assertEqual(after, "src/crate")

    def test_repo_relative_keeps_repo_root_as_dot(self):
        self.assertEqual(
            paths.repo_relative(self.new_root, self.old_root), "."
        )


class RelocatedRun(unittest.TestCase):
    """Run, cache and affectedness across a relocation."""

    def setUp(self):
        self.db = fresh_db()
        self.conn = store.connect(self.db)
        self.root = _repo_with_target("true")
        store.register_repo(self.conn, repo_path=self.root, name="reloc",
                            kind=None, status="active", third_party=False,
                            notes=None)
        store.repo_run(self.conn, self.root, "build")

    def tearDown(self):
        self.conn.close()

    def _move_repo(self):
        """Move the repo and repoint it, leaving every workdir stale."""
        new_root = _relocate(self.root)
        _repoint_repo(self.db, self.root, new_root)
        self.assertTrue(_assert_rows_are_stale(self.db) > 0,
                        "the fixture must leave stale derived rows")
        self.conn.commit()
        self.root = new_root
        return new_root

    def test_run_survives_relocation(self):
        self._move_repo()
        r = store.repo_run(self.conn, self.root, "build")
        self.assertTrue(r["ok"], r)
        self.assertIn(r["results"][0]["status"], ("ran", "cached"))
        self.assertTrue(Path(r["results"][0]["workdir"]).is_dir(),
                        "workdir must point at the new location")
        self.assertEqual(r["results"][0]["workdir"], self.root)

    def test_derived_workdir_rows_are_left_stale_but_unused(self):
        """Healing happens at use time; the row is deliberately not rewritten."""
        self._move_repo()
        store.repo_run(self.conn, self.root, "build")
        raw = sqlite3.connect(self.db).execute(
            "SELECT workdir FROM repo_targets LIMIT 1").fetchone()[0]
        self.assertNotIn(self.root, raw,
                         "the stored row should still be the pre-move path")

    def test_unresolvable_target_fails_that_target_only(self):
        """The regression: one dead path must not abort the whole run."""
        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE repo_targets SET workdir=? WHERE name='build'",
                     ("/nowhere/entirely/gone",))
        conn.commit()
        conn.close()
        self.conn.commit()
        r = store.repo_run(self.conn, self.root, "build")
        self.assertFalse(r["ok"], "an unresolvable target is not a pass")
        self.assertEqual(r["results"][0]["status"], "unresolved")
        self.assertIn("could not be recovered", r["results"][0]["stderr"])

    def test_cache_survives_relocation(self):
        """A move must not throw away every cached run on the box.

        This is the regression the repo-relative key exists to prevent: the
        absolute path was part of the key, so relocating the workspace silently
        invalidated the whole cache and forced a full rebuild everywhere.
        """
        self._move_repo()
        r = store.repo_run(self.conn, self.root, "build")
        self.assertEqual(r["results"][0]["status"], "cached",
                         "unchanged inputs must still hit the cache after a move")

    def test_cache_still_busts_on_edit_after_relocation(self):
        """The counterweight: a stable key must not become a blind one."""
        self._move_repo()
        self.assertEqual(
            store.repo_run(self.conn, self.root, "build")["results"][0]["status"],
            "cached")
        (Path(self.root) / "src.txt").write_text("v2\n")
        r = store.repo_run(self.conn, self.root, "build")
        self.assertEqual(r["results"][0]["status"], "ran",
                         "a real edit must still bust the cache after a move")

    def test_cache_key_distinguishes_targets_in_different_directories(self):
        """The reason the key is relative rather than dropped entirely."""
        repo = _repo_with_target("true")
        (Path(repo) / "sub").mkdir()
        (Path(repo) / "sub" / "Makefile").write_text("build:\n\ttrue\n")
        store.register_repo(self.conn, repo_path=repo, name="multi", kind=None,
                            status="active", third_party=False, notes=None)
        store.repo_run(self.conn, repo, "build")
        targets = store._targets_for_kind(self.conn, repo, "build")
        relatives = {t["workdir_relative"] for t in targets}
        self.assertGreaterEqual(len(relatives), 2,
                                "same command, different dirs must not collide")


class Affectedness(unittest.TestCase):
    """`_targets_for_files` must still attribute a changed file after a move."""

    def test_changed_file_is_matched_after_relocation(self):
        db = fresh_db()
        conn = store.connect(db)
        root = _repo_with_target("true")
        store.register_repo(conn, repo_path=root, name="aff", kind=None,
                            status="active", third_party=False, notes=None)
        store.repo_run(conn, root, "build")

        new_root = _relocate(root)
        _repoint_repo(db, root, new_root)
        conn.commit()

        affected = store._targets_for_files(conn, new_root,
                                            [str(Path(new_root) / "src.txt")])
        self.assertTrue(affected,
                        "a changed file under a moved repo must still be attributed")
        conn.close()


class StaleLockPaths(unittest.TestCase):
    """A lock taken before a move must still cover the file it names.

    This is the silent-collision case: the stored path is dead, the queried path
    is live, and a naive string comparison finds no overlap -- so a lock held by
    another agent reads as "uncovered" and the edit proceeds.
    """

    def _repo_and_lock(self):
        db = fresh_db()
        conn = store.connect(db)
        root = _repo_with_target("true")
        store.register_repo(conn, repo_path=root, name="lockbox", kind=None,
                            status="active", third_party=False, notes=None)
        acquired = store.lock_acquire(
            conn, scope_key="reloc-test", repo_ref=root, lock_kind="edit",
            files=["src.txt"], agent="someone-else", pid=None, reason="test",
            lease_seconds=0, eta_minutes=None, force=False)
        self.assertTrue(acquired.get("ok"), acquired)
        return db, conn, root

    def test_resolve_file_handles_the_real_box_shape(self):
        tmp = Path(tempfile.mkdtemp(prefix="frog-lockres-"))
        (tmp / "new" / "BAZ.AI-Governance" / "src").mkdir(parents=True)
        (tmp / "new" / "BAZ.AI-Governance" / "src" / "lib.rs").write_text("x")
        root = tmp / "new" / "BAZ.AI-Governance"
        stored = "/home/xyzzy/src/ragbaz/BAZ.AI-Governance/src/lib.rs"
        r = paths.resolve_file(root, stored)
        self.assertTrue(r.ok, r.reason)
        self.assertEqual(r.path, str(root / "src" / "lib.rs"))
        self.assertTrue(r.relocated)

    def test_lock_still_covers_its_file_after_a_move(self):
        db, conn, root = self._repo_and_lock()
        new_root = _relocate(root)
        _repoint_repo(db, root, new_root)
        conn.commit()

        check = store.lock_check_file(conn, repo_ref=new_root, agent="me",
                                      file_path="src.txt")
        self.assertEqual(check["status"], "conflict",
                         "another agent's lock must still be seen after a move")
        self.assertIn("someone-else", check["holders"])

    def test_rel_files_are_repo_relative_after_a_move(self):
        db, conn, root = self._repo_and_lock()
        new_root = _relocate(root)
        _repoint_repo(db, root, new_root)
        conn.commit()
        rows = store._lock_rows(conn, include_inactive=True, repo_path=new_root)
        self.assertTrue(rows)
        for row in rows:
            for rel in row["rel_files"]:
                self.assertFalse(
                    rel.startswith("/"),
                    f"relative form must stay relative, got {rel!r}")

    def test_stored_paths_are_preserved_for_audit(self):
        _db, conn, root = self._repo_and_lock()
        rows = store._lock_rows(conn, include_inactive=True, repo_path=root)
        self.assertTrue(rows)
        for row in rows:
            self.assertTrue(row["stored_file_paths"],
                            "the granted paths must remain visible")


if __name__ == "__main__":
    unittest.main()
