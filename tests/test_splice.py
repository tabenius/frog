import tempfile, unittest
from pathlib import Path
from ragbaz_frog import store


class Splice(unittest.TestCase):
    def test_appends_when_absent(self):
        out = store.splice_marked_section("# Doc\n\nIntro.\n", "todo",
                                          "- [ ] a")
        self.assertIn("<!-- frog:todo -->", out)
        self.assertIn("<!-- /frog:todo -->", out)
        self.assertTrue(out.startswith("# Doc\n\nIntro.\n"))
        self.assertIn("- [ ] a", out)

    def test_replaces_only_the_block_idempotently(self):
        doc = ("Before.\n<!-- frog:todo -->\nOLD\n<!-- /frog:todo -->\n"
               "After (user prose).\n")
        out = store.splice_marked_section(doc, "todo", "- [x] done")
        self.assertNotIn("OLD", out)
        self.assertIn("- [x] done", out)
        self.assertIn("Before.\n", out)
        self.assertIn("After (user prose).\n", out)
        # idempotent: second splice with same body is a no-op
        out2 = store.splice_marked_section(out, "todo", "- [x] done")
        self.assertEqual(out, out2)

    def test_custom_marker_isolated(self):
        doc = "<!-- frog:todo -->\nT\n<!-- /frog:todo -->\n"
        out = store.splice_marked_section(doc, "roadmap", "- [ ] r")
        self.assertIn("<!-- frog:roadmap -->", out)
        self.assertIn("T", out)  # the todo block untouched

    def test_marker_sanitized(self):
        out = store.splice_marked_section("", "Bad Marker!", "x")
        self.assertIn("<!-- frog:badmarker -->", out)

    def test_empty_doc(self):
        out = store.splice_marked_section("", "todo", "- [ ] z")
        self.assertEqual(
            out, "<!-- frog:todo -->\n- [ ] z\n<!-- /frog:todo -->\n")

    def test_cli_into_roundtrip(self):
        import subprocess, json
        d = tempfile.mkdtemp(); doc = Path(d) / "document_foo.md"
        doc.write_text("# Plan\n\nNotes here.\n")
        db = Path(d) / "AGENTS.db"
        subprocess.run(["python3", "bin/frog", "--db", str(db),
                        "db", "migrate"], capture_output=True)
        subprocess.run(["python3", "bin/frog", "--db", str(db),
                        "task", "create", "--slug", "x", "--title", "X"],
                       capture_output=True)
        r = subprocess.run(["python3", "bin/frog", "--db", str(db),
                             "export", "todo", "--into", str(doc)],
                            capture_output=True, text=True)
        self.assertIn("frog:todo", r.stdout)
        txt = doc.read_text()
        self.assertTrue(txt.startswith("# Plan\n\nNotes here.\n"))
        self.assertIn("<!-- frog:todo -->", txt)
        self.assertIn("- [ ] x", txt)


if __name__ == "__main__":
    unittest.main()
