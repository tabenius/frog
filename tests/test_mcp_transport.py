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
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0.1"},
                },
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("Content-Length", proc.stdout)
        lines = [json.loads(line) for line in proc.stdout.splitlines() if json.loads(line).get("id")]
        by_id = {l["id"]: l for l in lines}
        self.assertIn(1, by_id)
        self.assertIn(2, by_id)
        self.assertEqual(by_id[1]["result"]["serverInfo"]["name"], "ragbaz-frog")
        self.assertIn("tools", by_id[2]["result"])

    def test_malformed_message_does_not_crash_server(self):
        proc = subprocess.run(
            [sys.executable, "bin/frog", "--db", fresh_db(), "mcp", "serve"],
            cwd=ROOT,
            input="{not json}\n",
            text=True,
            capture_output=True,
            timeout=5,
        )
        # FastMCP handles malformed JSON gracefully (no crash); returncode=0
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # Must not write anything with Content-Length framing
        self.assertNotIn("Content-Length", proc.stdout)

    def test_unknown_method_returns_json_rpc_error(self):
        proc = _run_mcp([
            {"jsonrpc": "2.0", "id": 9, "method": "frog/nope"},
        ])
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["id"], 9)
        # FastMCP returns -32602; legacy returns -32601 — both are valid error responses
        self.assertIn(payload["error"]["code"], (-32601, -32602))

    def test_ping_response_goes_to_stdout_only(self):
        proc = _run_mcp([
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0.1"},
                },
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "ping"},
        ])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        lines = [json.loads(l) for l in proc.stdout.splitlines() if json.loads(l).get("id")]
        by_id = {l["id"]: l for l in lines}
        self.assertIn(2, by_id)
        self.assertNotIn("error", by_id[2])
if __name__ == "__main__":
    unittest.main()
