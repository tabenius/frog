import os
import unittest

from _util import fresh_db
from ragbaz_frog import store


class LocalDbGuard(unittest.TestCase):
    def test_local_tmp_is_allowed(self):
        # fresh_db() lives in /tmp (tmpfs/ext4) -> not remote -> connects.
        db = fresh_db()
        conn = store.connect(db)
        conn.close()

    def test_remote_fs_type_is_rejected(self):
        # _guard_local_db should raise when the fs type looks remote.
        orig = store._fs_type
        store._fs_type = lambda p: "fuse.sshfs"
        try:
            with self.assertRaises(store.RemoteDbError):
                store.connect("/mnt/remote/AGENTS.db")
        finally:
            store._fs_type = orig

    def test_override_env_bypasses_guard(self):
        orig = store._fs_type
        store._fs_type = lambda p: "nfs"
        os.environ["FROG_ALLOW_REMOTE_DB"] = "1"
        try:
            # guard returns early; connect proceeds (path is creatable in /tmp)
            db = fresh_db()
            store.connect(db).close()
        finally:
            store._fs_type = orig
            del os.environ["FROG_ALLOW_REMOTE_DB"]


if __name__ == "__main__":
    unittest.main()
