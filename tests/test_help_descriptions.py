import argparse
import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ragbaz_frog import main_cli


def _walk_subparsers(parser, path=()):
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        for name, child in action.choices.items():
            child_path = (*path, name)
            yield child_path, child
            yield from _walk_subparsers(child, child_path)


class HelpDescriptions(unittest.TestCase):
    def test_every_subcommand_has_a_description(self):
        parser = main_cli.build_parser()
        missing = [
            " ".join(path)
            for path, child in _walk_subparsers(parser)
            if not (child.description and child.description.strip())
        ]
        self.assertEqual(missing, [])

    def test_nested_help_prints_what_command_does(self):
        parser = main_cli.build_parser()
        out = io.StringIO()
        with self.assertRaises(SystemExit) as caught:
            with redirect_stdout(out):
                parser.parse_args(["task", "claim", "--help"])
        self.assertEqual(caught.exception.code, 0)
        text = out.getvalue().replace("\x1b[90m", "").replace("\x1b[36m", "").replace("\x1b[0m", "")
        self.assertIn("Take ownership + lock + mark in_progress", text)
        self.assertIn("usage: frog task claim", text)

    def test_repo_info_help_prints_what_command_does(self):
        parser = main_cli.build_parser()
        out = io.StringIO()
        with self.assertRaises(SystemExit) as caught:
            with redirect_stdout(out):
                parser.parse_args(["repo", "info", "--help"])
        self.assertEqual(caught.exception.code, 0)
        text = out.getvalue().replace("\x1b[90m", "").replace("\x1b[36m", "").replace("\x1b[0m", "")
        self.assertIn("Show repo metadata and counts", text)
        self.assertIn("--repo REPO", text)

    def test_lock_kind_help_says_freeform(self):
        parser = main_cli.build_parser()
        out = io.StringIO()
        with self.assertRaises(SystemExit) as caught:
            with redirect_stdout(out):
                parser.parse_args(["lock", "acquire", "--help"])
        self.assertEqual(caught.exception.code, 0)
        text = out.getvalue().replace("\x1b[90m", "").replace("\x1b[36m", "").replace("\x1b[0m", "")
        self.assertIn("--lock-kind KIND", text)
        self.assertIn("Freeform lock label", text)
        self.assertIn("edit, docs, build", " ".join(text.split()))


if __name__ == "__main__":
    unittest.main()
