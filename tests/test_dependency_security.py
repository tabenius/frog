import importlib
import re
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FLOORS = {"certifi": (2026, 10, 18), "pyjwt": (2, 15, 0)}


class DependencySecurity(unittest.TestCase):
    def test_core_install_remains_dependency_free(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
        self.assertEqual(project["dependencies"], [])

    def test_optional_extra_and_lock_require_security_fixed_versions(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
        packages = tomllib.loads((ROOT / "uv.lock").read_text())["package"]
        requirements = project["optional-dependencies"]["mcp"]
        for name, floor in FLOORS.items():
            with self.subTest(package=name):
                bounds = [re.fullmatch(rf"{name}>=(\d+(?:\.\d+)*)", requirement.lower())
                          for requirement in requirements]
                bounds = [match for match in bounds if match]
                self.assertEqual(len(bounds), 1, "The MCP extra must enforce the security floor")
                self.assertGreaterEqual(tuple(map(int, bounds[0][1].split("."))), floor)
                versions = [package["version"] for package in packages if package["name"] == name]
                self.assertTrue(versions, "The security-constrained dependency must be locked")
                for version in versions:
                    self.assertGreaterEqual(tuple(map(int, version.split("."))), floor)


class InstalledJwtSecurity(unittest.TestCase):
    def setUp(self):
        try:
            self.jwt = importlib.import_module("jwt")
        except ImportError:
            self.skipTest("PyJWT is only required for the optional MCP environment")
        self.secret = "synthetic-test-key-not-a-credential-" * 2

    def test_unverified_decode_does_not_disable_later_expiry_checks(self):
        token = self.jwt.encode({"sub": "synthetic", "exp": 0}, self.secret, algorithm="HS256")
        options = {"verify_signature": False}
        self.jwt.decode(token, options=options)
        self.assertEqual(options, {"verify_signature": False})
        options["verify_signature"] = True
        with self.assertRaises(self.jwt.ExpiredSignatureError):
            self.jwt.decode(token, self.secret, algorithms=["HS256"], options=options)

    def test_noncanonical_signature_is_refused(self):
        token = self.jwt.encode({"sub": "synthetic"}, self.secret, algorithm="HS256")
        self.assertEqual(self.jwt.decode(token, self.secret, algorithms=["HS256"])["sub"], "synthetic")
        with self.assertRaises(self.jwt.DecodeError):
            self.jwt.decode(token + "!!!!", self.secret, algorithms=["HS256"])


if __name__ == "__main__":
    unittest.main()
