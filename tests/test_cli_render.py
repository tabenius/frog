import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ragbaz_frog import main_cli


def render(payload):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main_cli._emit(payload, False)
    return rc, buf.getvalue()


class CliRender(unittest.TestCase):
    def test_repo_info_is_labeled(self):
        rc, out = render({
            "ok": True,
            "repo": {
                "name": "frog",
                "repo_path": "/data/src/frog",
                "repo_key": "path:abc",
                "status": "active",
                "kind": "tool",
                "third_party": 0,
            },
            "counts": {"tasks": 2, "active_locks": 1, "targets": 3, "artifacts": 4},
        })
        self.assertEqual(rc, 0)
        self.assertIn("Repo: frog", out)
        self.assertIn("Path", out)
        self.assertIn("Active locks", out)
        self.assertNotIn("active_locks=", out)

    def test_status_summary_is_scoped_and_grouped(self):
        rc, out = render({
            "ok": True,
            "repo_path": "/data/src/frog",
            "counts": {"repos": 1, "files": 2, "tasks": 3, "active_locks": 0},
            "tasks_by_workflow_status": [
                {"workflow_status": "idea", "count": 2},
                {"workflow_status": "done", "count": 1},
            ],
        })
        self.assertEqual(rc, 0)
        self.assertIn("Status: frog", out)
        self.assertIn("Workflow", out)
        self.assertIn("idea", out)

    def test_ps_summary_has_sections(self):
        rc, out = render({
            "ok": True,
            "repo_path": "/data/src/frog",
            "active_tasks": [{
                "slug": "next",
                "priority": "p1",
                "workflow_status": "in_progress",
                "git_status": "not_started",
                "repo_path": "/data/src/frog",
                "title": "Next task",
            }],
            "active_locks": [],
            "recent_events": [{
                "created_at": "2026-05-18T12:00:00+00:00",
                "kind": "task.claimed",
                "summary": "codex claimed next",
            }],
        })
        self.assertEqual(rc, 0)
        self.assertIn("Activity: frog", out)
        self.assertIn("Tasks", out)
        self.assertIn("Locks", out)
        self.assertIn("Recent events", out)

    def test_artifacts_group_by_target_and_show_relative_paths(self):
        rc, out = render({
            "ok": True,
            "repo": {"name": "frog", "repo_path": "/data/src/frog"},
            "artifacts": [{
                "artifact_name": "build:dist",
                "target_kind": "build",
                "target_name": "dist",
                "path_hint": "/data/src/frog/dist",
                "exists": False,
                "stale": True,
            }],
            "source_latest_mtime": 1779100000.0,
        })
        self.assertEqual(rc, 0)
        self.assertIn("Artifacts: frog", out)
        self.assertIn("build:dist", out)
        self.assertIn("dist [missing stale]", out)
        self.assertNotIn("/data/src/frog/dist", out)


if __name__ == "__main__":
    unittest.main()
