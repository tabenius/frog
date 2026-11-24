"""Foreground, bounded Streamable HTTP transport for Frog's MCP dispatcher.

HTTP responses carry protocol data; stdout is exclusively JSONL/CLF access
logs. Remote listeners require the bearer secret named by --token-env.
"""
from __future__ import annotations

import datetime as dt
import hmac
import ipaddress
import json
import os
import signal
import socket
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlsplit

from . import mcp_server

MAX_BODY = 64 * 1024
VERSIONS = frozenset(mcp_server.SUPPORTED_PROTOCOL_VERSIONS)


def serve_http(*, listen='127.0.0.1:8081', json_log=False, token_env='FROG_MCP_TOKEN',
               allowed_origins=(), config_path=None, db_path=None):
    parsed = urlsplit('//' + listen)
    host, port = parsed.hostname, parsed.port
    if not host or port is None or parsed.path or parsed.query or parsed.fragment or parsed.username:
        raise ValueError('--listen must be IP:PORT (use [::1]:PORT for IPv6)')
    address = ipaddress.ip_address(host)
    token = os.environ.get(token_env, '')
    if not address.is_loopback and not token:
        raise ValueError(f'Non-loopback MCP requires a bearer secret in {token_env}')
    mcp_server._DB_OVERRIDE, mcp_server._CONFIG_PATH = db_path, config_path

    class Server(HTTPServer):
        address_family = socket.AF_INET6 if address.version == 6 else socket.AF_INET

        def get_request(self):
            connection, peer = super().get_request()
            connection.settimeout(10)
            return connection, peer

    class Handler(BaseHTTPRequestHandler):
        server_version = 'FrogMCP/1'

        def log_message(self, format, *args):
            pass  # Never mix BaseHTTPRequestHandler's stderr format with access logs.

        def do_GET(self):
            self.dispatch()

        def do_DELETE(self):
            self.dispatch()

        def do_OPTIONS(self):
            self.dispatch()

        def do_POST(self):
            self.dispatch()

        def dispatch(self):
            started = time.monotonic()
            tool = None
            method = None
            status, result = 500, {'error': 'internal_error'}
            try:
                status, result, method, tool = self.handle_mcp()
            except Exception:
                # No traceback or user payload enters stdout/access logs.
                pass
            body = b'' if result is None else json.dumps(result, separators=(',', ':')).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('X-Content-Type-Options', 'nosniff')
            if status == 401:
                self.send_header('WWW-Authenticate', 'Bearer realm="Frog MCP"')
            if status == 405:
                self.send_header('Allow', 'POST')
            self.end_headers()
            try:
                self.wfile.write(body)
            finally:
                timestamp = dt.datetime.now(dt.UTC)
                if json_log:
                    record = {'datetime': timestamp.isoformat().replace('+00:00', 'Z'),
                              'remote': self.client_address[0], 'method': self.command,
                              'path': urlsplit(self.path).path, 'status': status, 'bytes': len(body),
                              'duration_ms': round((time.monotonic() - started) * 1000, 3),
                              'mcp_method': method, 'tool': tool}
                    print(json.dumps(record), flush=True)
                else:
                    # JSON escaping prevents control characters/quotes from
                    # injecting additional CLF records. Queries are not logged.
                    request = f'{self.command} {urlsplit(self.path).path} {self.request_version}'
                    quoted = json.dumps(request, ensure_ascii=True)
                    stamp = timestamp.strftime('%d/%b/%Y:%H:%M:%S +0000')
                    print(f'{self.client_address[0]} - - [{stamp}] {quoted} {status} {len(body)}', flush=True)

        def handle_mcp(self):
            def refuse(status, error):
                return status, {'error': error}, None, None
            if urlsplit(self.path).path != '/mcp':
                return refuse(404, 'not_found')
            origin = self.headers.get('Origin')
            assert isinstance(self.server, HTTPServer)
            actual_port = self.server.server_port
            authority = f'[{host}]:{actual_port}' if address.version == 6 else f'{host}:{actual_port}'
            allowed = set(allowed_origins) | {f'http://{authority}'}
            if address.is_loopback:
                allowed.add(f'http://localhost:{actual_port}')
                if self.headers.get('Host') not in {authority, f'localhost:{actual_port}'}:
                    return refuse(403, 'bad_host')
            if origin and origin not in allowed:
                return refuse(403, 'bad_origin')
            supplied = self.headers.get('Authorization', '')
            if token and not hmac.compare_digest(supplied.encode(), ('Bearer ' + token).encode()):
                return refuse(401, 'authentication_required')
            if self.command != 'POST':
                return refuse(405, 'method_not_allowed')
            if self.headers.get('MCP-Protocol-Version', '2025-03-26') not in VERSIONS:
                return refuse(400, 'unsupported_protocol_version')
            if 'application/json' not in self.headers.get('Accept', '') or 'text/event-stream' not in self.headers.get('Accept', ''):
                return refuse(406, 'unsupported_accept')
            if self.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json':
                return refuse(415, 'json_required')
            if self.headers.get('Transfer-Encoding'):
                return refuse(400, 'transfer_encoding_not_supported')
            try:
                length = int(self.headers['Content-Length'])
            except (TypeError, ValueError):
                return refuse(411, 'content_length_required')
            if length < 0 or length > MAX_BODY:
                return refuse(413, 'body_too_large')
            self.connection.settimeout(10)
            raw = self.rfile.read(length)
            if len(raw) != length:
                return refuse(400, 'incomplete_body')
            try:
                message = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                return 400, mcp_server._error(None, -32700, 'parse error'), None, None
            if not isinstance(message, dict) or message.get('jsonrpc') != '2.0' or not isinstance(message.get('method'), str):
                return 400, mcp_server._error(None, -32600, 'invalid request'), None, None
            if ('params' in message and not isinstance(message['params'], dict)) or (
                'id' in message and (isinstance(message['id'], bool) or not isinstance(message['id'], (str, int)))):
                return 400, mcp_server._error(None, -32600, 'invalid request'), None, None
            method = message['method']
            tool = ((message.get('params') or {}).get('name') if method == 'tools/call' and isinstance(message.get('params'), dict) else None)
            # Only a known tool name is logged; never arguments, bearer tokens,
            # arbitrary response text, or attacker-controlled pseudo-tool names.
            if tool not in {spec['name'] for spec in mcp_server._tool_specs()}:
                tool = None
            result = mcp_server._handle_message(message, config_path=config_path)
            logged_method = method if method in {'initialize', 'ping', 'tools/list', 'tools/call',
                'resources/list', 'resources/read', 'prompts/list', 'prompts/get',
                'notifications/initialized', 'exit'} else None
            if result == 'exit':
                result = None  # A remote client cannot stop the server process.
            if 'id' not in message:
                return 202, None, logged_method, tool
            return 200, result, logged_method, tool

    server = Server((host, port), Handler)
    if json_log:
        print(json.dumps({'event': 'listening', 'datetime': dt.datetime.now(dt.UTC).isoformat().replace('+00:00', 'Z'),
                          'listen': f'{host}:{server.server_port}', 'path': '/mcp'}), flush=True)
    else:
        print(f'Frog MCP listening on {host}:{server.server_port}/mcp', file=sys.stderr, flush=True)
    previous = signal.getsignal(signal.SIGTERM)
    def stop(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
        signal.signal(signal.SIGTERM, previous)
