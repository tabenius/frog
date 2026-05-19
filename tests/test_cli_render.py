import io
import re
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ragbaz_frog import main_cli

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def render(payload):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main_cli._emit(payload, False)
    return rc, buf.getvalue()


def plain(text):
    return ANSI_RE.sub("", text)


class CliRender(unittest.TestCase):
    def tearDown(self):
        main_cli._COLOR_ENABLED = True
        main_cli._PAGE_HUMAN_OUTPUT = False
        main_cli._PAGER_ENABLED = True

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
        self.assertIn("\x1b[", out)
        text = plain(out)
        self.assertIn("Repo: frog", text)
        self.assertIn("Path", text)
        self.assertIn("Active locks", text)
        self.assertNotIn("active_locks=", text)

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
        self.assertIn("\x1b[", out)
        text = plain(out)
        self.assertIn("Status: frog", text)
        self.assertIn("Workflow", text)
        self.assertIn("idea", text)

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
        self.assertIn("\x1b[34m", out, "claim/in-progress output should be blue")
        text = plain(out)
        self.assertIn("Activity: frog", text)
        self.assertIn("Tasks", text)
        self.assertIn("Locks", text)
        self.assertIn("Recent events", text)

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
        self.assertIn("\x1b[31m", out, "missing artifacts should be red")
        text = plain(out)
        self.assertIn("Artifacts: frog", text)
        self.assertIn("build:dist", text)
        self.assertIn("dist [missing stale]", text)
        self.assertNotIn("/data/src/frog/dist", text)

    def test_repo_doctor_advice_renders(self):
        rc, out = render({
            "ok": True,
            "repo": {"name": "frog", "repo_path": "/data/src/frog"},
            "advice": ["no runnable targets detected"],
            "target_counts": {},
            "stale_artifacts": [],
        })
        self.assertEqual(rc, 0)
        text = plain(out)
        self.assertIn("frog", text)
        self.assertIn("no runnable targets detected", text)

    def test_json_payloads_stay_plain(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main_cli._emit({"ok": True, "workflow_status": "done"}, True)
        self.assertEqual(rc, 0)
        self.assertNotIn("\x1b[", buf.getvalue())

    def test_task_next_empty_renders_reason_summary(self):
        rc, out = render({
            "ok": True,
            "agent": "codex",
            "tasks": [],
            "considered": 4,
            "eligible": 0,
            "skipped": [{"slug": "owned", "reason": "owned by claude"}],
            "skipped_summary": {"owner": 1, "deps": 2, "done": 1},
        })
        self.assertEqual(rc, 0)
        text = plain(out)
        self.assertIn("0 eligible of 4 considered", text)
        self.assertIn("blocked by dependencies", text)
        self.assertIn("owned by another agent", text)
        self.assertIn("already done", text)
        self.assertIn("skip owned: owned by claude", text)

    def test_lock_release_event_renders_audit_metadata(self):
        rc, out = render({
            "ok": True,
            "events": [{
                "id": 1,
                "created_at": "2026-05-19T12:00:00+00:00",
                "kind": "lock.released",
                "summary": "released lock 42",
                "actor": "codex",
                "payload": {
                    "forced": True,
                    "lock_agent": "claude",
                    "released_by": "codex",
                    "reason": "stale after adopted commit",
                },
            }],
        })
        self.assertEqual(rc, 0)
        text = plain(out)
        self.assertIn("released lock 42", text)
        self.assertIn("forced", text)
        self.assertIn("released by codex", text)
        self.assertIn("held by claude", text)
        self.assertIn("reason: stale after adopted commit", text)

    def test_lock_release_command_output_renders_audit_metadata(self):
        rc, out = render({
            "ok": True,
            "lock": {
                "id": 42,
                "lock_kind": "edit",
                "scope_key": "task:demo",
                "repo_path": "/data/src/frog",
                "agent_name": "claude",
                "status": "released",
                "started_at": "2026-05-19T11:00:00+00:00",
                "eta_finish_at": None,
                "file_paths": [],
            },
            "release": {
                "forced": True,
                "lock_agent": "claude",
                "released_by": "codex",
                "reason": "operator recovery",
            },
        })
        self.assertEqual(rc, 0)
        text = plain(out)
        self.assertIn("released_by: codex", text)
        self.assertIn("original_holder: claude", text)
        self.assertIn("forced: true", text)
        self.assertIn("release_reason: operator recovery", text)

    def test_lock_release_help_includes_force_and_audit_metadata(self):
        parser = main_cli.build_parser()
        buf = io.StringIO()
        with self.assertRaises(SystemExit) as raised, redirect_stdout(buf):
            parser.parse_args(["lock", "release", "--help"])
        self.assertEqual(raised.exception.code, 0)
        text = plain(buf.getvalue())
        self.assertIn("--force", text)
        self.assertIn("--agent", text)
        self.assertIn("--reason", text)

    def test_task_list_help_exposes_all_history_switch(self):
        parser = main_cli.build_parser()
        buf = io.StringIO()
        with self.assertRaises(SystemExit) as raised, redirect_stdout(buf):
            parser.parse_args(["task", "list", "--help"])
        self.assertEqual(raised.exception.code, 0)
        self.assertIn("--all", plain(buf.getvalue()))

    def test_no_color_disables_ansi(self):
        main_cli._COLOR_ENABLED = False
        rc, out = render({
            "ok": True,
            "task": {
                "slug": "done-task",
                "title": "Done task",
                "repo_path": "/data/src/frog",
                "priority": "p1",
                "workflow_status": "done",
                "git_status": "done",
            },
        })
        self.assertEqual(rc, 0)
        self.assertNotIn("\x1b[", out)
        self.assertIn("workflow_status: done", out)

    def test_pager_threshold_uses_terminal_height(self):
        self.assertFalse(main_cli._should_page_text("one\ntwo\n", rows=10))
        self.assertTrue(main_cli._should_page_text("\n".join(str(i) for i in range(10)), rows=10))

    def test_visible_width_table_aligns_colorized_columns(self):
        main_cli._COLOR_ENABLED = True
        buf = io.StringIO()
        rows = [
            [main_cli._color("short", "claim"), "x"],
            [main_cli._color("much-longer", "claim"), "y"],
        ]
        with redirect_stdout(buf):
            main_cli._render_table(rows)
        lines = plain(buf.getvalue()).splitlines()
        self.assertEqual(lines[0].index("x"), lines[1].index("y"))

    def test_fish_completion_includes_file_info_files(self):
        script = main_cli._completion_script("fish")
        self.assertIn("__frog_complete_registered_files", script)
        self.assertIn("__fish_seen_subcommand_from file; and __fish_seen_subcommand_from info", script)
        self.assertIn("--json file list", script)
        self.assertIn("__fish_seen_subcommand_from info' -F", script)

    def test_completion_prefers_box_join_subcommand(self):
        bash = main_cli._completion_script("bash")
        fish = main_cli._completion_script("fish")
        first_line = bash.splitlines()[3]
        self.assertNotIn(" join ", first_line)
        self.assertIn("box) [[ $COMP_CWORD -eq 2 ]]", bash)
        self.assertIn("whoami peers join", fish)

    def test_box_identity_render_is_human_readable(self):
        rc, out = render({
            "ok": True,
            "box_id": "boxA",
            "hostname": "hostA",
            "pinned_at": "/tmp/frog/box-id",
            "source": "file",
            "known_boxes": [{
                "box_id": "boxA",
                "hostname": "hostA",
                "first_seen": "2026-05-19T08:00:00+00:00",
                "last_seen": "2026-05-19T08:01:00+00:00",
            }],
        })
        self.assertEqual(rc, 0)
        text = plain(out)
        self.assertIn("box_id: boxA", text)
        self.assertIn("known boxes", text)
        self.assertNotIn('"box_id"', text)

    def test_peers_render_as_table(self):
        rc, out = render({
            "ok": True,
            "count": 1,
            "peers": [{
                "box_id": "boxB",
                "hostname": "hostB",
                "ssh_target": "user@hostB",
                "remote_db": "/srv/AGENTS.db",
                "added_at": "2026-05-19T08:00:00+00:00",
                "last_join_at": "2026-05-19T08:05:00+00:00",
            }],
        })
        self.assertEqual(rc, 0)
        text = plain(out)
        self.assertIn("boxB", text)
        self.assertIn("user@hostB", text)
        self.assertIn("/srv/AGENTS.db", text)

    def test_help_output_is_colorized(self):
        main_cli._COLOR_ENABLED = True
        text = main_cli._colorize_help("usage: frog [-h]\noptions:\n")
        self.assertIn("\x1b[", text)
        self.assertIn("frog", text)


if __name__ == "__main__":
    unittest.main()
