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


    # ---- splice_heading_section (--section) ----
    def test_heading_replaces_under_existing_section(self):
        doc = ("# Project\n\n## TODO\n\nold item\n\n"
               "## Notes\n\nkeep me\n")
        out = store.splice_heading_section(doc, "TODO", "- [ ] new")
        self.assertIn("## TODO", out)
        self.assertNotIn("old item", out)
        self.assertIn("- [ ] new", out)
        self.assertIn("## Notes", out)
        self.assertIn("keep me", out)
        # stops at the next same-level heading
        self.assertLess(out.index("- [ ] new"), out.index("## Notes"))

    def test_heading_idempotent(self):
        doc = "## TODO\n\nx\n\n## After\n\ny\n"
        a = store.splice_heading_section(doc, "TODO", "- [x] done")
        b = store.splice_heading_section(a, "TODO", "- [x] done")
        self.assertEqual(a, b)
        self.assertIn("## After", b)
        self.assertIn("y", b)

    def test_heading_any_level_and_case(self):
        doc = "### todo\n\nz\n"
        out = store.splice_heading_section(doc, "TODO", "- [ ] q")
        self.assertIn("### todo", out)   # original heading line untouched
        self.assertIn("- [ ] q", out)
        self.assertNotIn("\nz\n", out)

    def test_heading_nested_subsection_not_a_boundary(self):
        doc = "## TODO\n\nold\n\n### sub\n\ndeep\n\n## Next\n\nn\n"
        out = store.splice_heading_section(doc, "TODO", "- [ ] r")
        # deeper heading is inside the section -> replaced
        self.assertNotIn("### sub", out)
        self.assertNotIn("deep", out)
        # shallower/equal heading is the boundary -> preserved
        self.assertIn("## Next", out)
        self.assertIn("n", out)

    def test_heading_appends_when_absent(self):
        out = store.splice_heading_section("# Doc\n\nbody\n",
                                           "TODO", "- [ ] a")
        self.assertTrue(out.startswith("# Doc\n\nbody\n"))
        self.assertIn("## TODO", out)
        self.assertIn("- [ ] a", out)

    def test_cli_section_roundtrip(self):
        import subprocess, tempfile
        d = tempfile.mkdtemp(); doc = Path(d) / "doc.md"
        doc.write_text("# Plan\n\n## TODO\n\nplaceholder\n\n"
                       "## Done\n\nshipped\n")
        db = Path(d) / "AGENTS.db"
        subprocess.run(["python3", "bin/frog", "--db", str(db),
                        "db", "migrate"], capture_output=True)
        subprocess.run(["python3", "bin/frog", "--db", str(db), "task",
                        "create", "--slug", "s1", "--title", "S1"],
                       capture_output=True)
        r = subprocess.run(["python3", "bin/frog", "--db", str(db),
                             "export", "todo", "--into", str(doc),
                             "--section", "TODO"],
                            capture_output=True, text=True)
        self.assertIn('"TODO" section', r.stdout)
        txt = doc.read_text()
        self.assertNotIn("placeholder", txt)
        self.assertIn("- [ ] s1", txt)
        self.assertIn("## Done", txt)
        self.assertIn("shipped", txt)

    def test_cli_section_requires_into(self):
        import subprocess, tempfile
        d = tempfile.mkdtemp(); db = Path(d) / "AGENTS.db"
        subprocess.run(["python3", "bin/frog", "--db", str(db),
                        "db", "migrate"], capture_output=True)
        r = subprocess.run(["python3", "bin/frog", "--db", str(db),
                             "export", "todo", "--section", "TODO"],
                            capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--section requires --into", r.stdout + r.stderr)

if __name__ == "__main__":
    unittest.main()
