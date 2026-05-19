import re, unittest
from pathlib import Path
from ragbaz_frog import main_cli

DOCS = Path(__file__).resolve().parents[1] / "docs"


class DocsGuides(unittest.TestCase):
    def test_guides_exist_and_nonempty(self):
        for n in ("FEDERATION.md", "MCP.md", "DEPLOY.md"):
            p = DOCS / n
            self.assertTrue(p.exists(), f"{n} missing")
            self.assertGreater(len(p.read_text()), 200, n)

    def test_no_guide_cites_a_nonexistent_command(self):
        # Every `frog <cmd> [<sub>]` mentioned in a guide must be a real
        # command in the live parser -> guides cannot drift silently.
        ref = main_cli._command_reference(main_cli.build_parser())
        valid = set(re.findall(r"`frog ([a-z][\w-]*(?: [a-z][\w-]*)?)`",
                               ref))
        firsts = {v.split()[0] for v in valid}
        for n in ("FEDERATION.md", "MCP.md", "DEPLOY.md"):
            text = (DOCS / n).read_text()
            for m in re.findall(r"`frog ([a-z][\w-]*)"
                                r"(?: ([a-z][\w-]*))?", text):
                cmd = m[0]
                pair = f"{m[0]} {m[1]}".strip()
                self.assertIn(
                    cmd, firsts,
                    f"{n} cites unknown command 'frog {cmd}'")
                if m[1] and not pair.endswith(("--", "-")):
                    self.assertIn(
                        pair, valid,
                        f"{n} cites unknown subcommand 'frog {pair}'")

    def test_litestream_dr_files_are_documented(self):
        deploy = (DOCS / "DEPLOY.md").read_text()
        self.assertIn("Optional continuous disaster recovery with Litestream", deploy)
        self.assertIn("Litestream is opt-in", deploy)
        self.assertIn("does not require the `litestream` binary", deploy)
        root = DOCS.parents[0]
        for rel in (
            "deploy/litestream/agents-db.litestream.yml.example",
            "deploy/systemd/frog-agents-litestream.service",
        ):
            self.assertTrue((root / rel).exists(), rel)
            self.assertIn(rel, deploy)


if __name__ == "__main__":
    unittest.main()
