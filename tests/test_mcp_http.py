"""Exercise the public foreground CLI and HTTP wire protocol in a real process."""
import contextlib
import asyncio
import http.client
import json
import os
import select
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from _util import store

ROOT = Path(__file__).resolve().parents[1]


class McpHttp(unittest.TestCase):
    @contextlib.contextmanager
    def server(self, *, clf=False, remote=False):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'config.json'
            config.write_text('{}')
            db = str(Path(directory) / 'tasks.db')
            store.migrate(db)
            env = {**os.environ, 'FROG_CONFIG': str(config)}
            env.pop('FROG_MCP_TOKEN', None)
            if remote:
                env['FROG_MCP_TOKEN'] = 'test-bearer-never-log'
            proc = subprocess.Popen([sys.executable, 'bin/frog', '--db', db,
                'serve', 'mcp', '--http', '--listen', f'{"0.0.0.0" if remote else "127.0.0.1"}:0',
                '--fg', '--clf' if clf else '--json'], cwd=ROOT, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                stream = proc.stderr if clf else proc.stdout
                assert stream is not None
                self.assertTrue(select.select([stream], [], [], 10)[0], 'server did not become ready')
                ready = stream.readline()
                port = int(ready.split(':')[-1].split('/')[0]) if clf else int(json.loads(ready)['listen'].rsplit(':', 1)[1])
                yield proc, port
            finally:
                proc.terminate()
                output, errors = proc.communicate(timeout=10)
                self.output = output
                self.assertEqual(proc.returncode, 0, errors)

    def request(self, port, message=None, *, headers=None, method='POST', raw=None, path='/mcp'):
        body = raw if raw is not None else json.dumps(message)
        conn = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
        try:
            conn.request(method, path, body=body, headers={'Accept': 'application/json, text/event-stream',
                'Content-Type': 'application/json', **(headers or {})})
            response = conn.getresponse()
            data = response.read()
            return response.status, json.loads(data) if data else None
        finally:
            conn.close()

    def test_initialize_write_read_and_redacted_json_logs(self):
        with self.server() as (_, port):
            status, init = self.request(port, {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                'params': {'protocolVersion': 'unsupported'}})
            self.assertEqual(status, 200)
            self.assertEqual(init['result']['protocolVersion'], '2025-03-26')
            for version in ('2025-06-18', '2025-11-25'):
                _, negotiated = self.request(port, {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                    'params': {'protocolVersion': version}})
                self.assertEqual(negotiated['result']['protocolVersion'], version)
            self.assertEqual(self.request(port, {'jsonrpc': '2.0', 'method': 'notifications/initialized'}), (202, None))
            _, listed = self.request(port, {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})
            self.assertIn('frog_task_create', [t['name'] for t in listed['result']['tools']])
            def tool(name, args):
                status, result = self.request(port, {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
                    'params': {'name': name, 'arguments': args}})
                self.assertEqual(status, 200)
                self.assertIn('result', result)
                self.assertFalse(result['result'].get('isError'), result)
                return json.loads(result['result']['content'][0]['text'])
            tool('frog_task_create', {'slug': 'http-roundtrip', 'title': 'private-title-never-log'})
            tool('frog_task_edit', {'slug': 'http-roundtrip', 'title': 'edited-private-title'})
            instance = tool('dash_list_instances', {})['instances'][0]
            result = tool('dash_read_frog', {'instanceId': instance['id']})
            self.assertEqual(result['data']['items'][0]['title'], 'edited-private-title')
            self.request(port, {'jsonrpc': '2.0', 'id': 4, 'method': 'secret-unknown-method'})
            self.assertEqual(self.request(port, {'jsonrpc': '2.0', 'method': 'exit'}), (202, None))
            self.assertEqual(self.request(port, {'jsonrpc': '2.0', 'id': 5, 'method': 'ping'})[0], 200)
        logs = [json.loads(line) for line in self.output.splitlines()]
        self.assertTrue(all(log['datetime'].endswith('Z') for log in logs))
        self.assertTrue(any(log['tool'] == 'frog_task_create' for log in logs))
        for secret in ('private-title', 'secret-unknown-method'):
            self.assertNotIn(secret, self.output)

    def test_http_validation_and_clf_logs(self):
        with self.server(clf=True) as (_, port):
            message = {'jsonrpc': '2.0', 'id': 1, 'method': 'ping'}
            for headers, expected in [({'Host': 'evil.example'}, 403), ({'Origin': 'https://evil.example'}, 403),
                    ({'MCP-Protocol-Version': 'bad'}, 400), ({'Accept': 'application/json'}, 406),
                    ({'Content-Type': 'text/plain'}, 415), ({'Transfer-Encoding': 'chunked'}, 400)]:
                self.assertEqual(self.request(port, message, headers=headers)[0], expected)
            self.assertEqual(self.request(port, raw='x' * 65537)[0], 413)
            self.assertEqual(self.request(port, raw='{bad')[1]['error']['code'], -32700)
            self.assertEqual(self.request(port, {**message, 'params': []})[0], 400)
            self.assertEqual(self.request(port, message, method='GET')[0], 405)
            self.assertEqual(self.request(port, message, path='/missing?secret=never-log')[0], 404)
        self.assertNotIn('never-log', self.output)
        for line in self.output.splitlines():
            self.assertRegex(line, r'^127\.0\.0\.1 - - \[.+ \+0000\] ".+" \d{3} \d+$')

    def test_nonloopback_requires_bearer(self):
        with self.server(remote=True) as (_, port):
            message = {'jsonrpc': '2.0', 'id': 1, 'method': 'ping'}
            self.assertEqual(self.request(port, message)[0], 401)
            self.assertEqual(self.request(port, message, headers={'Authorization': 'Bearer test-bearer-never-log'})[0], 200)
        self.assertNotIn('test-bearer-never-log', self.output)

    def test_official_sdk_streamable_http_client(self):
        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client
        except ImportError:
            self.skipTest('official MCP SDK is not installed in the fallback environment')
        async def exchange(port):
            async with streamable_http_client(f'http://127.0.0.1:{port}/mcp') as (read, write, _):
                async with ClientSession(read, write) as session:
                    initialized = await session.initialize()
                    self.assertEqual(initialized.serverInfo.name, 'ragbaz-frog')
                    tools = await session.list_tools()
                    self.assertIn('frog_task_create', [tool.name for tool in tools.tools])
                    created = await session.call_tool('frog_task_create', {'slug': 'sdk-http', 'title': 'SDK HTTP'})
                    self.assertFalse(created.isError)
                    instances = await session.call_tool('dash_list_instances', {})
                    self.assertFalse(instances.isError)
        with self.server() as (_, port):
            asyncio.run(exchange(port))

    def test_cli_rejects_conflicting_log_flags_and_unauthenticated_remote_binding(self):
        for flags in (['--json', '--clf'], ['--listen', '0.0.0.0:0']):
            env = {**os.environ}
            env.pop('FROG_MCP_TOKEN', None)
            result = subprocess.run([sys.executable, 'bin/frog', 'serve', 'mcp', '--http', *flags],
                cwd=ROOT, env=env, capture_output=True, text=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
