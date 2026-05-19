import os
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path

from _util import fresh_db
from ragbaz_frog import store


class BoxId(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.get(k) for k in ("FROG_BOX_ID", "FROG_HOME")}

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_env_override_wins(self):
        os.environ["FROG_BOX_ID"] = "box-from-env"
        self.assertEqual(store._box_id(), "box-from-env")

    def test_pins_to_file_and_survives_hostname_change(self):
        os.environ.pop("FROG_BOX_ID", None)
        home = tempfile.mkdtemp()
        os.environ["FROG_HOME"] = home
        first = store._box_id()                       # writes the file
        self.assertEqual(first, socket.gethostname())
        self.assertTrue((Path(home) / "box-id").exists())
        # simulate a later hostname change: file content is authoritative
        (Path(home) / "box-id").write_text("renamed-box\n")
        self.assertEqual(store._box_id(), "renamed-box")

    def test_migrate_records_box_identity(self):
        db = fresh_db()
        conn = store.connect(db)
        try:
            rows = conn.execute(
                "SELECT box_id, hostname FROM box_identity").fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["box_id"], store._box_id())
        finally:
            conn.close()

    def test_ensure_box_identity_is_idempotent_and_refreshes(self):
        conn = store.connect(fresh_db())
        try:
            store.ensure_box_identity(conn)
            store.ensure_box_identity(conn)
            n = conn.execute(
                "SELECT count(*) FROM box_identity").fetchone()[0]
            self.assertEqual(n, 1)
        finally:
            conn.close()

    def test_lock_is_stamped_with_box_id(self):
        os.environ["FROG_BOX_ID"] = "stamp-test-box"
        d = tempfile.mkdtemp(prefix="frog-bi-repo-")
        subprocess.run(["git", "-C", d, "init", "-q"], check=True)
        (Path(d) / "a.txt").write_text("x\n")
        conn = store.connect(fresh_db())
        try:
            store.register_repo(conn, repo_path=d, name="r", kind=None,
                                status="active", third_party=False,
                                notes=None)
            store.lock_acquire(
                conn, scope_key="s1", repo_ref=d, lock_kind="edit",
                files=[str(Path(d) / "a.txt")], agent="claude", pid=None,
                reason=None, lease_seconds=900, eta_minutes=None,
                force=False)
            box = conn.execute(
                "SELECT box_id FROM locks WHERE scope_key='s1'"
            ).fetchone()[0]
            self.assertEqual(box, "stamp-test-box")
        finally:
            conn.close()

    def test_cli_box_whoami(self):
        d = tempfile.mkdtemp()
        db = Path(d) / "AGENTS.db"
        env = dict(os.environ, FROG_BOX_ID="cli-box")
        subprocess.run(["python3", "bin/frog", "--db", str(db),
                        "db", "migrate"], capture_output=True, env=env)
        r = subprocess.run(
            ["python3", "bin/frog", "--db", str(db), "--json",
             "box", "whoami"], capture_output=True, text=True, env=env)
        self.assertIn('"box_id": "cli-box"', r.stdout)
        self.assertIn('"known_boxes"', r.stdout)


if __name__ == "__main__":
    unittest.main()
