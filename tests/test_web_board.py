from __future__ import annotations

import contextlib
import http.client
import io
import json
import sqlite3
import ssl
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from _util import fresh_db
from ragbaz_frog import store, web_board
from ragbaz_frog.main_cli import main


class WebBoardTests(unittest.TestCase):
    def setUp(self):
        self.db = fresh_db()
        conn = store.connect(self.db)
        self.addCleanup(conn.close)
        self.conn = conn
        now = store.utc_now_iso()
        conn.execute("INSERT INTO repos(repo_path,name,created_at,updated_at) VALUES ('/private/work/frog','frog',?,?)", (now, now))
        for slug, title, priority, status in [('ready','A <script>title</script>','p1','todo'), ('blocked','Blocked task','p0','todo'), ('review','Review is not done','p2','review'), ('closed','Closed task','p3','done')]:
            conn.execute("INSERT INTO tasks(slug,title,priority,workflow_status,repo_path,assigned_agent,created_at,updated_at,status_confidence_at) VALUES (?,?,?,?, '/private/work/frog','agent-test',?,?,?)", (slug,title,priority,status,now,now,now))
        conn.execute("INSERT INTO task_dependencies(task_slug,depends_on_slug,relation,created_at) VALUES ('blocked','ready','depends_on',?)", (now,))
        conn.commit()

    def test_projection_matches_board_lanes_without_private_paths_or_events(self):
        expected = store.board_snapshot(self.conn)
        data = web_board.projection(self.db)
        self.assertEqual(data['schema'], web_board.SCHEMA)
        self.assertTrue(data['read_only'])
        self.assertEqual(data['counts'], expected['counts'])
        for lane in expected['columns']:
            self.assertEqual({c['id'] for c in data['cards'] if c['lane'] == lane}, {c['slug'] for c in expected['columns'][lane]})
        ready = next(c for c in data['cards'] if c['id'] == 'ready')
        self.assertEqual(ready['repository'], 'frog')
        self.assertTrue(ready['ready'])
        blocked = next(c for c in data['cards'] if c['id'] == 'blocked')
        self.assertEqual(blocked['unmet_dependencies'], ['ready'])
        self.assertEqual(blocked['dependencies'], ['ready'])
        self.assertEqual(ready['blocks'], ['blocked'])
        self.assertNotIn('/private', json.dumps(data))
        self.assertNotIn('recent', data)

    def test_snapshot_cli_does_not_log_commands_or_mutate_database(self):
        before = self.conn.execute('SELECT COUNT(*) FROM event_log').fetchone()[0]
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = main(['--db', self.db, 'web', 'snapshot'])
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output.getvalue())['source'], 'frog')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM event_log').fetchone()[0], before)

    def test_missing_database_is_not_created(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'absent.db'
            with self.assertRaises(sqlite3.Error):
                web_board.projection(path)
            self.assertFalse(path.exists())

    def test_read_connections_explicitly_enable_sqlite_uri_mode(self):
        connect = sqlite3.connect
        def checked(path, *args, **kwargs):
            self.assertTrue(kwargs.get('uri'), 'SQLite must interpret mode=ro on every build')
            self.assertIn('?mode=ro', str(path))
            return connect(path, *args, **kwargs)
        with patch.object(web_board.sqlite3, 'connect', side_effect=checked):
            web_board.projection(self.db)
            web_board.repository_story(self.db, 'frog')
            source = web_board.DatabaseSource(self.db)
            try:
                source.version()
            finally:
                source.close()

    def test_repository_story_reads_bounded_description_and_git_without_inferred_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'README.md').write_text('# Example\n\nA frontier workspace for test explorers.\n\nOther text.\n')
            for args in [['init', '-q'], ['add', 'README.md'], ['-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'Document the workspace']]:
                subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True)
            now = store.utc_now_iso()
            self.conn.execute("INSERT INTO repos(repo_path,name,created_at,updated_at) VALUES (?, 'story-test', ?, ?)", (str(root), now, now)); self.conn.commit()
            story = web_board.repository_story(self.db, 'story-test')
            self.assertEqual(story['description'], 'A frontier workspace for test explorers.')
            self.assertEqual(story['history_status'], 'ready')
            self.assertEqual(story['commits'][0]['title'], 'Document the workspace')
            self.assertNotIn(str(root), json.dumps(story))
            self.assertNotIn('done', story)
            with self.assertRaises(KeyError): web_board.repository_story(self.db, '../../missing')

    def test_non_loopback_bind_is_refused(self):
        for host in ['0.0.0.0', '192.0.2.1', '::']:
            with self.assertRaises(ValueError):
                web_board.create_server(self.db, host=host)

    def test_sse_initial_capture_and_external_sqlite_change(self):
        with web_board.create_server(self.db, port=0) as server:
            thread = threading.Thread(target=server.serve_forever); thread.start()
            connection = http.client.HTTPConnection('localhost', server.server_port, timeout=5)
            try:
                connection.request('GET', '/api/v1/events')
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertIn('text/event-stream', response.getheader('Content-Type'))
                def frame():
                    while True:
                        line = response.readline()
                        if line.startswith(b'data: '):
                            return json.loads(line[6:])
                initial = frame()
                self.assertEqual(initial['source'], 'frog')
                self.conn.execute("UPDATE tasks SET title='Changed through another writer' WHERE slug='ready'"); self.conn.commit()
                changed = frame()
                self.assertEqual(next(card['title'] for card in changed['cards'] if card['id']=='ready'), 'Changed through another writer')
                self.assertIsNotNone(server.watcher)
            finally:
                connection.close(); server.shutdown(); thread.join(5)

    def test_localhost_alias_and_opt_in_port_with_collision_fallback(self):
        with web_board.create_server(self.db, host='localhost', port=0) as occupied:
            port = occupied.server_port
            with self.assertRaises(OSError):
                web_board.create_server(self.db, host='localhost', port=port)
            with patch('ragbaz_frog.web_board.range', return_value=[port], create=True):
                with web_board.create_server(self.db, host='localhost') as fallback:
                    self.assertNotEqual(fallback.server_port, port)
        parser = __import__('ragbaz_frog.main_cli', fromlist=['build_parser']).build_parser()
        args = parser.parse_args(['web', 'serve'])
        self.assertEqual(args.host, 'localhost')
        self.assertIsNone(args.port)

    def test_optional_token_gates_data_and_insecure_token_files_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'token'
            path.write_text('test-only-access-credential-with-32-characters')
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                web_board.create_server(self.db, token_file=str(path), port=0)
            path.chmod(0o600)
            with web_board.create_server(self.db, token_file=str(path), port=0) as server:
                thread = threading.Thread(target=server.serve_forever)
                thread.start()
                try:
                    for headers, expected in [({},401), ({'Authorization':'Bearer wrong'},401), ({'Authorization':'Bearer '+path.read_text()},200)]:
                        connection = http.client.HTTPConnection('localhost', server.server_port, timeout=5)
                        connection.request('GET','/api/v1/kanban',headers=headers)
                        response=connection.getresponse()
                        self.assertEqual(response.status, expected)
                        body=response.read()
                        if expected==401:
                            self.assertNotIn(b'<script>title', body)
                        connection.close()
                finally:
                    server.shutdown(); thread.join(5)

    def test_http_is_read_only_and_rebinding_and_cross_site_requests_fail_closed(self):
        server = web_board.create_server(self.db, port=0)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.shutdown)

        def fetch(path, method='GET', headers=None):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
            try:
                connection.request(method, path, headers=headers or {})
                response = connection.getresponse()
                return response.status, dict(response.getheaders()), response.read()
            finally:
                connection.close()

        status, headers, body = fetch('/api/v1/kanban')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['source'], 'frog')
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertNotIn('Access-Control-Allow-Origin', headers)
        self.assertEqual(fetch('/api/v1/kanban', headers={'Host': 'evil.example'})[0], 403)
        self.assertEqual(fetch('/api/v1/kanban', headers={'Origin': 'https://evil.example'})[0], 403)
        self.assertEqual(fetch('/api/v1/kanban', headers={'Sec-Fetch-Site': 'cross-site'})[0], 403)
        for method in ['POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS']:
            self.assertEqual(fetch('/api/v1/kanban', method=method)[0], 405)
        for path in ['/', '/board.js', '/board.css']:
            self.assertEqual(fetch(path)[0], 200)
            self.assertEqual(fetch(path, method='HEAD')[2], b'')
        self.assertEqual(fetch('/../../AGENTS.db')[0], 404)
        self.conn.execute("UPDATE tasks SET title='Live changed title' WHERE slug='ready'")
        self.conn.commit()
        data = json.loads(fetch('/api/v1/kanban')[2])
        self.assertEqual(next(c for c in data['cards'] if c['id'] == 'ready')['title'], 'Live changed title')

    def test_protected_non_loopback_tls_requires_token_and_pinned_origin(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            cert, key, token = (directory / name for name in ('cert.pem', 'key.pem', 'token'))
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                            '-keyout', str(key), '-out', str(cert), '-days', '1',
                            '-subj', '/CN=localhost'], check=True, capture_output=True)
            token.write_text('test-only-non-loopback-token-with-32-characters'); token.chmod(0o600)
            options = dict(host='0.0.0.0', port=0, token_file=str(token), tls_cert=str(cert),
                           tls_key=str(key), public_origin='https://board.example.test')
            for omit in ['token_file', 'tls_cert', 'tls_key', 'public_origin']:
                with self.assertRaises(ValueError):
                    web_board.create_server(self.db, **{k:v for k,v in options.items() if k != omit})
            with web_board.create_server(self.db, **options) as server:
                thread = threading.Thread(target=server.serve_forever); thread.start()
                try:
                    for credential, host, origin, expected in [('', 'board.example.test', None, 401),
                        (token.read_text(), 'evil.example', None, 403),
                        (token.read_text(), 'board.example.test', 'https://evil.example', 403),
                        (token.read_text(), 'board.example.test', 'https://board.example.test', 200)]:
                        connection = http.client.HTTPSConnection('127.0.0.1', server.server_port,
                            context=ssl._create_unverified_context(), timeout=5)
                        headers = {'Host':host, 'Authorization':'Bearer '+credential}
                        if origin: headers['Origin'] = origin
                        connection.request('GET', '/api/v1/kanban', headers=headers)
                        response = connection.getresponse(); self.assertEqual(response.status, expected)
                        response.read(); connection.close()
                finally:
                    server.shutdown(); thread.join(5)


class ModelBackedBoard(unittest.TestCase):
    """The same renderer and stream, over the remote model contract."""

    def setUp(self):
        self.service = _Service()
        self.addCleanup(self.service.close)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        token = Path(self.directory.name) / 'token'
        token.write_text('model-board-test-token-never-deployed-12345'); token.chmod(0o600)
        from ragbaz_frog import model_client
        self.client = model_client.ModelClient(self.service.url, token.read_text().strip())
        self.source = web_board.ModelSource(self.client, 'alpha', poll=1.0)

    def _frame(self, response):
        while True:
            line = response.readline()
            if line.startswith(b'data: '):
                return json.loads(line[6:])

    def test_remote_board_streams_model_rows_with_dependency_context(self):
        with web_board.create_server(None, source=self.source, port=0) as server:
            thread = threading.Thread(target=server.serve_forever); thread.start()
            connection = http.client.HTTPConnection('localhost', server.server_port, timeout=5)
            try:
                connection.request('GET', '/api/v1/kanban')
                data = json.loads(connection.getresponse().read())
                self.assertEqual(data['source'], 'frog-model')
                self.assertEqual(data['cards'][0]['priority'], 'p0')
                self.assertEqual(data['cards'][0]['unmet_dependencies'], ['two'])
                self.assertEqual(data['repositories'][0]['id'], 'frog')
                self.assertEqual(data['counts']['blocked'], 1)
                connection.close()
                # An unreachable service is published as unavailable, not empty.
                self.service.status = 503
                stream = http.client.HTTPConnection('localhost', server.server_port, timeout=10)
                stream.request('GET', '/api/v1/events')
                response = stream.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(self._frame(response)['error'], 'projection_unavailable')
                stream.close()
            finally:
                server.shutdown(); thread.join(5)

    def test_a_model_backend_has_no_local_repository_story(self):
        with web_board.create_server(None, source=self.source, port=0) as server:
            thread = threading.Thread(target=server.serve_forever); thread.start()
            try:
                connection = http.client.HTTPConnection('localhost', server.server_port, timeout=5)
                connection.request('GET', '/api/v1/repository?id=frog')
                self.assertEqual(connection.getresponse().status, 404)
                connection.close()
            finally:
                server.shutdown(); thread.join(5)

    def test_exactly_one_backend_is_required(self):
        with self.assertRaises(ValueError):
            web_board.create_server(port=0)
        with self.assertRaises(ValueError):
            web_board.create_server('/dev/null', source=self.source, port=0)


class _Service:
    def __init__(self):
        self.status = 200
        self.rows = [{
            'id': 'one', 'title': 'Remote task', 'repository': 'frog', 'priority': 0,
            'status': 'todo', 'assignee': None, 'revision': 2, 'lane': 'blocked',
            'updated_at': '2026-10-14T09:00:00.000Z', 'status_observed_at': '2026-10-14T09:00:00.000Z',
            'unmet_dependencies': ['two'], 'dependencies': ['two'], 'blocks': [],
        }]
        service = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *_args):
                pass

            def do_GET(self):
                payload = {'schema': 'frog.model-result.v1',
                           'items': service.rows if self.path.startswith('/v1/workspaces/alpha/board')
                           else [{'id': 'frog', 'title': 'Frog'}]}
                body = json.dumps(payload).encode()
                self.send_response(service.status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(5)


if __name__ == '__main__':
    unittest.main()
