import io, contextlib, tempfile, unittest
from pathlib import Path
from ragbaz_frog import main_cli


class DocsCommand(unittest.TestCase):
    def _ref(self):
        return main_cli._command_reference(main_cli.build_parser())

    def test_reference_covers_every_top_level_command(self):
        ref = self._ref()
        parser = main_cli.build_parser()
        import argparse
        names = []
        for a in parser._actions:
            if isinstance(a, argparse._SubParsersAction):
                names += list(a.choices)
        self.assertTrue(names)
        for n in names:
            self.assertIn(f"`frog {n}`", ref,
                          f"{n} missing from generated reference")

    def test_includes_header_and_nested_subcommands(self):
        ref = self._ref()
        self.assertIn("# frog command reference", ref)
        self.assertIn("`frog task next`", ref)   # nested subcommand
        self.assertIn("`frog repo move`", ref)   # added this session

    def test_out_writes_markdown_file(self):
        d = tempfile.mkdtemp(); f = Path(d) / "C.md"
        with contextlib.redirect_stdout(io.StringIO()):
            rc = main_cli.main(["docs", "--out", str(f)])
        self.assertEqual(rc, 0)
        txt = f.read_text()
        self.assertTrue(txt.startswith("# frog command reference"))
        self.assertNotIn('{"ok"', txt)  # file is md, not the emit echo

    def test_stdout_renders_markdown(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main_cli.main(["docs"])
        self.assertEqual(rc, 0)
        self.assertIn("# frog command reference", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
