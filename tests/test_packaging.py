import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Packaging(unittest.TestCase):
    def test_pyproject_parses_and_declares_entrypoints(self):
        data = tomllib.loads((ROOT / "pyproject.toml").read_text())
        self.assertEqual(data["project"]["name"], "ragbaz-frog")
        scripts = data["project"]["scripts"]
        self.assertEqual(scripts["frog"], "ragbaz_frog.main_cli:main")
        self.assertEqual(scripts["frog-mcp"], "ragbaz_frog.mcp_server:serve")
        self.assertTrue(data["project"]["requires-python"].startswith(">=3."))

    def test_entrypoint_callables_exist(self):
        from ragbaz_frog import main_cli, mcp_server
        self.assertTrue(callable(main_cli.main))
        self.assertTrue(callable(mcp_server.serve))

    def test_migrations_are_package_data(self):
        data = tomllib.loads((ROOT / "pyproject.toml").read_text())
        pkgdata = data["tool"]["setuptools"]["package-data"]["ragbaz_frog"]
        self.assertIn("migrations/*.sql", pkgdata)
        # and they actually exist
        migs = sorted((ROOT / "src/ragbaz_frog/migrations").glob("*.sql"))
        self.assertGreaterEqual(len(migs), 6)


if __name__ == "__main__":
    unittest.main()
