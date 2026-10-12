import io
import json
import unittest
from contextlib import redirect_stdout

from _util import fresh_db
from ragbaz_frog import vfs_store
from ragbaz_frog.main_cli import main


class CliVfsImport(unittest.TestCase):
    def test_task_command_uses_vfs_owner_not_incidental_reexport(self):
        db = fresh_db()
        sentinel = object()
        original = vfs_store.__dict__.pop("workspace_vfs_context", sentinel)
        try:
            output = io.StringIO()
            with redirect_stdout(output):
                result = main(["--db", db, "task", "list", "--json"])
            self.assertEqual(result, 0, output.getvalue())
            self.assertTrue(json.loads(output.getvalue())["ok"])
        finally:
            if original is not sentinel:
                vfs_store.workspace_vfs_context = original


if __name__ == "__main__":
    unittest.main()
