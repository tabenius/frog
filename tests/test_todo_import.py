import tempfile, unittest
from pathlib import Path
from _util import fresh_db
from ragbaz_frog import store


class TodoImport(unittest.TestCase):
    def setUp(self):
        self.conn = store.connect(fresh_db())

    def tearDown(self):
        self.conn.close()

    def test_parse_checkboxes_priority_tags(self):
        md = ("# notes\n"
              "- [ ] Build the thing (p1) #infra #urgent\n"
              "* [x] Old done item P0\n"
              "- not a checkbox\n"
              "  - [ ] indented task\n")
        items = store.parse_todo_markdown(md)
        self.assertEqual(len(items), 3)
        first = items[0]
        self.assertEqual(first["status"], "open")
        self.assertEqual(first["priority"], "p1")
        self.assertNotIn("#infra", first["title"])
        self.assertNotIn("(p1)", first["title"])
        self.assertEqual(items[1]["status"], "done")
        self.assertEqual(items[1]["priority"], "p0")

    def test_import_is_idempotent(self):
        d = tempfile.mkdtemp()
        f = Path(d) / "TODO.md"
        f.write_text("- [ ] alpha\n- [ ] beta\n")
        r1 = store.import_todo(self.conn, str(f))
        self.assertEqual(len(r1["created"]), 2)
        r2 = store.import_todo(self.conn, str(f))
        self.assertEqual(r2["created"], [])
        self.assertEqual(len(r2["updated"]), 2)
        n = self.conn.execute(
            "SELECT COUNT(*) c FROM tasks WHERE source='todo'").fetchone()["c"]
        self.assertEqual(n, 2)

    def test_check_flips_status_on_reimport(self):
        d = tempfile.mkdtemp(); f = Path(d) / "T.md"
        f.write_text("- [ ] ship it\n")
        store.import_todo(self.conn, str(f))
        f.write_text("- [x] ship it\n")
        store.import_todo(self.conn, str(f))
        row = self.conn.execute(
            "SELECT workflow_status FROM tasks WHERE source='todo'").fetchone()
        self.assertEqual(row["workflow_status"], "done")

    def test_missing_file_and_empty(self):
        self.assertFalse(store.import_todo(self.conn, "/no/such")["ok"])
        d = tempfile.mkdtemp(); f = Path(d) / "e.md"; f.write_text("nothing\n")
        r = store.import_todo(self.conn, str(f))
        self.assertTrue(r["ok"])
        self.assertEqual(r["created"], [])


if __name__ == "__main__":
    unittest.main()
