import contextlib
import io
import unittest

from ragbaz_frog import main_cli


class EmitWhereisDispatch(unittest.TestCase):
    def _render(self, payload):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main_cli._emit(payload, False)
        return rc, buf.getvalue()

    def test_whereis_with_workspaces_not_shadowed_by_workspace_list(self):
        # Regression: a generic `"workspaces" in payload` branch used to
        # win over the dedicated whereis renderer and KeyError on 'name'.
        payload = {
            "ok": True, "repo_key": "path:abc", "box": "boxA",
            "local_path": "/p/repo", "aliases": [
                {"box": "boxA", "repo_path": "/p/repo"}],
            "workspaces": [
                {"workspace": "local", "result": {"ok": True}},
                {"workspace": "peer",
                 "result": {"ok": True, "local_path": "/q/repo"}}],
        }
        rc, out = self._render(payload)
        self.assertEqual(rc, 0)
        self.assertIn("path:abc", out)
        self.assertIn("/p/repo", out)
        self.assertIn("peer", out)


if __name__ == "__main__":
    unittest.main()
