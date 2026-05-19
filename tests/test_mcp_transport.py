import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from _util import fresh_db


ROOT = Path(__file__).resolve().parents[1]


def _run_mcp(messages, *, env=None):
    body = "".join(json.dumps(message, separators=(",", ":")) + "\n"
                   for message in messages)
    return subprocess.run(
        [sys.executable, "bin/frog", "--db", fresh_db(), "mcp", "serve"],
        cwd=ROOT,
        input=body,
        text=True,
        capture_output=True,
        env=env,
        timeout=5,
    )


class McpTransport(unittest.TestCase):
    def test_stdio_uses_newline_delimited_json(self):
        proc = _run_mcp([
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-03-26"},
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("Content-Length", proc.stdout)
        lines = [json.loads(line) for line in proc.stdout.splitlines()]
        self.assertEqual([line["id"] for line in lines], [1, 2])
        self.assertEqual(lines[0]["result"]["serverInfo"]["name"], "ragbaz-frog")
        self.assertIn("tools", lines[1]["result"])

    def test_malformed_message_returns_parse_error_and_keeps_stdout_protocol_clean(self):
        proc = subprocess.run(
            [sys.executable, "bin/frog", "--db", fresh_db(), "mcp", "serve"],
            cwd=ROOT,
            input="{not json}\n",
            text=True,
            capture_output=True,
            timeout=5,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["error"]["code"], -32700)

    def test_unknown_method_returns_json_rpc_error(self):
        proc = _run_mcp([
            {"jsonrpc": "2.0", "id": 9, "method": "frog/nope"},
        ])
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["id"], 9)
        self.assertEqual(payload["error"]["code"], -32601)

    def test_debug_logging_goes_to_stderr_only(self):
        env = dict(**os.environ, FROG_MCP_LOG="debug")
        proc = _run_mcp([
            {"jsonrpc": "2.0", "id": 1, "method": "ping"},
        ], env=env)
        self.assertEqual(json.loads(proc.stdout)["id"], 1)
        self.assertIn('"level": "debug"', proc.stderr)


if __name__ == "__main__":
    unittest.main()
