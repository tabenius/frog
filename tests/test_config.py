import tempfile
import unittest
from pathlib import Path
import sys

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ragbaz_frog import config


class ConfigCoordinator(unittest.TestCase):
    def _config_path(self):
        return str(Path(tempfile.mkdtemp(prefix="frog-config-")) / "config.json")

    def test_default_config_names_local_coordinator(self):
        path = self._config_path()
        data = config.ensure_config(path)
        self.assertEqual(data["federation"]["coordinator_workspace"], "local-src")
        info = config.info(path)
        self.assertEqual(info["coordinator_workspace"], "local-src")
        self.assertEqual(info["coordinator"]["name"], "local-src")

    def test_set_coordinator_and_workspace_listing_marks_it(self):
        path = self._config_path()
        config.ensure_config(path)
        config.add_host("box2", ssh_target="box2", path=path)
        config.add_workspace(
            "box2-src",
            host_name="box2",
            root="/data/src",
            path=path,
        )
        out = config.set_coordinator("box2-src", path)
        self.assertTrue(out["ok"])
        self.assertEqual(config.coordinator_name(path), "box2-src")
        listing = config.list_workspaces(path)
        by_name = {item["name"]: item for item in listing["workspaces"]}
        self.assertTrue(by_name["box2-src"]["is_coordinator"])
        self.assertFalse(by_name["local-src"]["is_coordinator"])

    def test_unknown_coordinator_is_rejected(self):
        path = self._config_path()
        config.ensure_config(path)
        out = config.set_coordinator("missing", path)
        self.assertFalse(out["ok"])
        self.assertIn("unknown workspace", out["error"])


if __name__ == "__main__":
    unittest.main()
