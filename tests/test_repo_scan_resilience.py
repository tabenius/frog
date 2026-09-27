"""Regression coverage: repo_scan() must not let one malformed manifest
abort the whole scan.

Bug: `_detect_from_package_json`/`_detect_from_cargo_toml`/
`_detect_from_pyproject` parsed manifest files with no error handling.
Real trigger found in the workspace: tools/headless/hwptoolkit/package.json
is a 0-byte file. `json.loads("")` raises JSONDecodeError, which propagated
all the way up through repo_scan() -> discover_repos()'s scan loop,
crashing `frog repo discover` for the *entire* workspace on the one bad
file, not just the one repo that owns it.
"""
import tempfile
import unittest
from pathlib import Path

from _util import fresh_db
from ragbaz_frog import store


class RepoScanResilienceTests(unittest.TestCase):
    def setUp(self):
        self.conn = store.connect(fresh_db())

    def tearDown(self):
        self.conn.close()

    def _register(self, repo_path: Path) -> None:
        store.register_repo(
            self.conn, repo_path=str(repo_path), name=repo_path.name,
            kind=None, status="active", third_party=False, notes=None,
        )

    def test_empty_package_json_does_not_abort_scan(self):
        d = Path(tempfile.mkdtemp(prefix="frog-scan-"))
        (d / "package.json").write_text("")  # the real trigger: 0 bytes
        (d / "Makefile").write_text("build:\n\techo hi\n")
        self._register(d)
        result = store.repo_scan(self.conn, str(d))
        self.assertTrue(result.get("ok", True), result)
        # the Makefile target next to the broken package.json must still
        # have been detected -- one bad manifest must not blank out the
        # others in the same repo.
        names = [t["target_kind"] for t in result.get("targets", [])]
        self.assertIn("build", names, result)

    def test_malformed_cargo_toml_does_not_abort_scan(self):
        d = Path(tempfile.mkdtemp(prefix="frog-scan-cargo-"))
        (d / "Cargo.toml").write_text("this is not [valid toml")
        (d / "Makefile").write_text("build:\n\techo hi\n")
        self._register(d)
        result = store.repo_scan(self.conn, str(d))
        self.assertTrue(result.get("ok", True), result)

    def test_malformed_pyproject_toml_does_not_abort_scan(self):
        d = Path(tempfile.mkdtemp(prefix="frog-scan-pyproject-"))
        (d / "pyproject.toml").write_text("[project\nname = broken")
        (d / "Makefile").write_text("build:\n\techo hi\n")
        self._register(d)
        result = store.repo_scan(self.conn, str(d))
        self.assertTrue(result.get("ok", True), result)

    def test_skipped_manifest_is_recorded_as_an_event(self):
        d = Path(tempfile.mkdtemp(prefix="frog-scan-event-"))
        (d / "package.json").write_text("")
        self._register(d)
        store.repo_scan(self.conn, str(d))
        rows = self.conn.execute(
            "SELECT kind, payload_json FROM event_log WHERE kind = 'repo.scan_warning' "
            "AND repo_path = ?", (str(d),)
        ).fetchall()
        self.assertEqual(len(rows), 1, rows)
        self.assertIn("package.json", rows[0]["payload_json"])

    def test_discover_with_scan_survives_a_malformed_manifest_anywhere(self):
        """The end-to-end shape of the original bug: a workspace-wide
        `discover --scan` walking two repos, where the *first* one
        (alphabetically) has the broken manifest, must still discover and
        scan the *second* one instead of crashing outright."""
        root = Path(tempfile.mkdtemp(prefix="frog-scan-workspace-"))
        broken = root / "a-broken-repo"
        broken.mkdir()
        (broken / "package.json").write_text("")
        (broken / "Makefile").write_text("build:\n\techo hi\n")
        fine = root / "b-fine-repo"
        fine.mkdir()
        (fine / "Makefile").write_text("build:\n\techo hi\n")
        result = store.discover_repos(self.conn, root=str(root), scan=True)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["counts"]["discovered"], 2, result)
        self.assertEqual(result["counts"]["scanned"], 2, result)


if __name__ == "__main__":
    unittest.main()
