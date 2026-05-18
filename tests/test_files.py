import os, tempfile, unittest
from pathlib import Path
from _util import fresh_db
from ragbaz_frog import store


class FileInfoMany(unittest.TestCase):
    def setUp(self):
        self.conn = store.connect(fresh_db())
        self.d = tempfile.mkdtemp(prefix="frog-files-")
        self.a = os.path.join(self.d, "a.py"); open(self.a, "w").write("a")
        self.b = os.path.join(self.d, "b.py"); open(self.b, "w").write("b")
        store.upsert_file(self.conn, file_path=self.a, repo_path=None,
                          file_type="py", source_of_truth=None, notes=None)

    def tearDown(self):
        self.conn.close()

    def test_single_registered_passthrough(self):
        r = store.file_info_many(self.conn, [self.a])
        self.assertTrue(r["ok"])
        self.assertEqual(r["file"]["file_path"], self.a)  # back-compat shape

    def test_directory_reported_precisely(self):
        r = store.file_info_many(self.conn, [self.d])
        self.assertFalse(r["ok"])
        self.assertEqual(r["file_errors"][0]["kind"], "directory")

    def test_missing_vs_unregistered(self):
        r = store.file_info_many(self.conn, [self.b, "/nope/x.py"])
        kinds = {e["kind"] for e in r["file_errors"]}
        self.assertIn("unregistered", kinds)  # b exists, not in db
        self.assertIn("missing", kinds)        # /nope/x.py absent
        self.assertFalse(r["ok"])

    def test_glob_expands_and_dedups(self):
        r = store.file_info_many(self.conn, [os.path.join(self.d, "*.py"),
                                             self.a])
        # a.py registered -> in files; b.py exists unregistered -> error
        self.assertEqual(len(r["files"]), 1)
        self.assertTrue(any(e["kind"] == "unregistered"
                            for e in r["file_errors"]))

    def test_glob_no_match_errors(self):
        r = store.file_info_many(self.conn, [os.path.join(self.d, "*.zzz")])
        self.assertFalse(r["ok"])
        self.assertEqual(r["file_errors"][0]["kind"], "glob_empty")

    def test_multi_registered_ok(self):
        store.upsert_file(self.conn, file_path=self.b, repo_path=None,
                          file_type="py", source_of_truth=None, notes=None)
        r = store.file_info_many(self.conn, [self.a, self.b])
        self.assertTrue(r["ok"])
        self.assertEqual(len(r["files"]), 2)
        self.assertEqual(r["file_errors"], [])


if __name__ == "__main__":
    unittest.main()
