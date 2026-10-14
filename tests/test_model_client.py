"""Transport and projection adapter for the Rust model service."""
import contextlib
import io
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import urlsplit

from ragbaz_frog import model_client
from ragbaz_frog.main_cli import main

TOKEN = "model-client-test-token-never-deployed-1234567"


class Service:
    """Records requests and answers with contract-shaped JSON."""

    def __init__(self):
        self.calls = []
        self.responses = {}
        self.status = 200
        self.default = {"schema": "frog.model-result.v1", "kind": "board", "items": []}
        service = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args):
                pass

            def _reply(self):
                body = json.dumps(service.responses.get(len(service.calls), service.default)).encode()
                service.calls.append({
                    "method": self.command, "path": self.path,
                    "auth": self.headers.get("Authorization"),
                    "length": int(self.headers.get("Content-Length") or 0),
                    "body": self.rfile.read(int(self.headers.get("Content-Length") or 0)) if self.command == "POST" else b"",
                })
                self.send_response(service.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = _reply

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)


class ModelClientTests(unittest.TestCase):
    def setUp(self):
        self.service = Service()
        self.addCleanup(self.service.close)
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.token_path = Path(self.directory.name) / "token"
        self.token_path.write_text(TOKEN)
        self.token_path.chmod(0o600)
        self.client = model_client.ModelClient(self.service.url, TOKEN)

    def test_transport_rules_refuse_insecure_and_leaky_urls(self):
        for url in ["http://remote.example", "https://user:secret@example.test",
                    "https://example.test?token=x", "not-a-url", ""]:
            with self.assertRaises(model_client.ModelError):
                model_client.endpoint(url)
        self.assertEqual(model_client.endpoint("http://localhost:8788/"), "http://localhost:8788")

    def test_token_must_come_from_an_owner_only_file_or_environment(self):
        self.assertEqual(model_client.read_token(self.token_path), TOKEN)
        self.token_path.chmod(0o644)
        with self.assertRaises(model_client.ModelError):
            model_client.read_token(self.token_path)
        with patch.dict("os.environ", {"FROG_MODEL_TOKEN": "x" * 40}):
            self.assertEqual(model_client.read_token(None), "x" * 40)

    def test_bearer_is_sent_never_in_the_url_and_responses_are_bounded(self):
        self.client.board("alpha")
        call = self.service.calls[0]
        self.assertEqual(call["auth"], f"Bearer {TOKEN}")
        self.assertEqual(urlsplit(self.service.url + urlsplit(call["path"]).path).query, "")
        self.assertIn("limit=100", call["path"])
        with patch.object(model_client, "MAX_BODY", 4):
            with self.assertRaises(model_client.ModelError):
                self.client.board("alpha")
        self.service.default = {"schema": "wrong"}
        with self.assertRaises(model_client.ModelError) as error:
            self.client.board("alpha")
        self.assertEqual(error.exception.code, "wrong_schema")

    def test_failures_report_the_code_the_service_refused_with(self):
        for status, code in [(401, "rejected_token"), (403, "forbidden"), (404, "not_found"), (503, "unavailable")]:
            self.service.status = status
            self.service.default = {"error": "nope"}
            with self.assertRaises(model_client.ModelError) as error:
                self.client.board("alpha")
            self.assertEqual(error.exception.code, code, status)
        self.service.status = 409
        self.service.default = {"schema": "frog.model-result.v1", "items": [{"status": "conflict"}]}
        with self.assertRaises(model_client.ModelError) as error:
            self.client.claim_task("alpha", "op", task_id="one", expected_revision=0)
        self.assertEqual(error.exception.code, "conflict")

    def test_unreachable_service_is_reported_not_raised_raw(self):
        client = model_client.ModelClient(self.service.url, TOKEN, timeout=1)
        self.service.close()
        with self.assertRaises(model_client.ModelError) as error:
            client.board("alpha")
        self.assertEqual(error.exception.code, "unreachable")

    def test_priority_words_map_to_model_levels_and_invalid_input_never_leaves(self):
        self.client.create_task("alpha", "op", repo_id="frog", task_id="one", title="T", priority="p1")
        self.assertEqual(json.loads(self.service.calls[0]["body"])["command"]["priority"], 1)
        for bad in [{"priority": "urgent"}, {"status": "todo"}]:
            with self.assertRaises(model_client.ModelError):
                if "priority" in bad:
                    self.client.create_task("alpha", "op", repo_id="frog", task_id="one", title="T", **bad)
                else:
                    self.client.transition_task("alpha", "op", task_id="one", expected_revision=0, **bad)
        self.assertEqual(len(self.service.calls), 1)


class ProjectionTests(unittest.TestCase):
    def test_model_rows_become_the_existing_kanban_projection(self):
        row = {
            "id": "one", "title": "Task <script>", "repository": "frog", "priority": 0, "status": "review",
            "assignee": "alice", "revision": 3, "lane": "in_progress", "updated_at": "2026-10-14T10:00:00.000Z",
            "status_observed_at": "2026-10-14T10:00:01.000Z",
            "unmet_dependencies": ["two"], "dependencies": ["two"], "blocks": ["three"],
        }
        projection = model_client.board_projection(FakeClient([row], [{"id": "frog", "title": "Frog"}]), "alpha")
        self.assertEqual(projection["schema"], "frog.kanban-projection.v0")
        self.assertEqual(projection["source"], "frog-model")
        self.assertEqual(projection["revision"], 3)
        self.assertEqual(projection["counts"]["in_progress"], 1)
        card = projection["cards"][0]
        self.assertEqual(card["priority"], "p0")
        self.assertEqual(card["unmet_dependencies"], ["two"])
        self.assertEqual(card["blocks"], ["three"])
        self.assertFalse(card["ready"])
        self.assertEqual(projection["repositories"][0]["id"], "frog")

    def test_rogue_link_types_fail_closed_rather_than_becoming_fake_ids(self):
        row = {"id": "one", "title": "T", "repository": "frog", "priority": 99, "status": "todo",
               "revision": 1, "lane": "idea", "dependencies": [1, "two"], "unmet_dependencies": []}
        with self.assertRaises(model_client.ModelError) as error:
            model_client.board_projection(FakeClient([row], []), "alpha")
        self.assertEqual(error.exception.code, "wrong_schema")
        sane = {"id": "one", "title": "T", "repository": "frog", "priority": 99, "status": "todo",
                "revision": 1, "lane": "idea", "dependencies": [], "unmet_dependencies": []}
        card = model_client.board_projection(FakeClient([sane], []), "alpha")["cards"][0]
        self.assertEqual(card["priority"], "p3")
        self.assertTrue(card["ready"])


class FakeClient:
    def __init__(self, rows, repositories):
        self._rows = rows
        self._repositories = repositories

    def board(self, *_args, **_kwargs):
        return self._rows

    def repositories(self, *_args, **_kwargs):
        return self._repositories


class CliTests(unittest.TestCase):
    def setUp(self):
        self.service = Service()
        self.addCleanup(self.service.close)
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.token_path = Path(self.directory.name) / "token"
        self.token_path.write_text(TOKEN)
        self.token_path.chmod(0o600)

    def run_cli(self, *args, token=True):
        out, err = io.StringIO(), io.StringIO()
        command = ["model", "--url", self.service.url, "--workspace-id", "alpha", "--json"]
        if token:
            command = ["model", "--token-file", str(self.token_path), *command[1:]]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), patch.dict("os.environ", {"FROG_MODEL_TOKEN": "", "FROG_MODEL_TOKEN_FILE": ""}):
            code = main([*command, *args])
        return code, out.getvalue(), err.getvalue()

    def test_model_commands_reach_the_service_with_the_workspace_scope(self):
        self.service.default = {"schema": "frog.model-result.v1", "kind": "board",
                                "items": [{"id": "one", "lane": "idea"}]}
        code, out, _ = self.run_cli("board")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["items"][0]["id"], "one")
        self.assertEqual(self.service.calls[0]["auth"], f"Bearer {TOKEN}")
        self.service.default = {"schema": "frog.model-result.v1", "items": [{"status": "applied"}]}
        code, out, _ = self.run_cli("claim", "--task-id", "one", "--operation-id", "stable-id")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(self.service.calls[-1]["body"]),
                         {"operation_id": "stable-id", "command": {"kind": "claim_task", "task_id": "one", "expected_revision": 0}})
        self.service.status = 409
        self.service.default = {"schema": "frog.model-result.v1", "items": [{"status": "conflict"}]}
        code, out, _ = self.run_cli("claim", "--task-id", "one")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["code"], "conflict")

    def test_missing_credentials_fail_before_any_request(self):
        code, out, _ = self.run_cli("board", token=False)
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["code"], "missing_token")
        self.assertEqual(self.service.calls, [])


if __name__ == '__main__':
    unittest.main()