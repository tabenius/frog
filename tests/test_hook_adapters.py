import contextlib
import hashlib
import hmac
import io
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from _util import fresh_db
from ragbaz_frog import hooks, store
from ragbaz_frog.main_cli import main, _hoist_global_flags


class HookAdapters(unittest.TestCase):
    def setUp(self):
        self.db = fresh_db(); self.conn = store.connect(self.db)
        self.addCleanup(self.conn.close)
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.config = self.directory / 'config.json'
        self.config.write_text(json.dumps({'hosts': {'local': {'transport': 'local'}},
            'workspaces': {'test': {'host': 'local', 'root': str(self.directory), 'db': self.db}},
            'current_workspace': 'test', 'hooks': {'auto_dispatch': True}}))

    def cli(self, *args):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return main(['--config', str(self.config), '--db', self.db, '--json', *args])

    def test_new_envelope_excludes_paths_arguments_and_event_prose(self):
        now = store.utc_now_iso()
        self.conn.execute("INSERT INTO repos(repo_path,name,created_at,updated_at) VALUES ('/private/frog','frog',?,?)", (now, now))
        self.conn.execute("INSERT INTO tasks(slug,title,created_at,updated_at,status_confidence_at) VALUES ('one','One task',?,?,?)", (now, now, now))
        store.record_event(self.conn, kind='task.claimed', task_slug='one', repo_path='/private/frog',
            summary='private command', payload={'secret': 'hidden'})
        self.conn.commit()
        body = hooks.envelope({'events': store._hook_events(self.conn, 0, 100), 'kind':'webhook', 'hook_id': 1})
        self.assertEqual(body['schema'], hooks.SCHEMA)
        claimed = next(event for event in body['events'] if event['kind'] == 'task.claimed')
        self.assertEqual(claimed['repository'], 'frog')
        self.assertNotIn('private', json.dumps(body)); self.assertNotIn('hidden', json.dumps(body))
        self.assertIn(':', body['events'][0]['event_id'])

    def test_command_requires_trust_receives_json_stdin_and_not_a_shell(self):
        target = hooks.command_target([sys.executable, '-c', 'import sys; sys.exit(0)'])
        hook = store.event_hook_add(self.conn, target, kind='shell')['hook']
        refused = store.event_hook_dispatch(self.conn, hook_id=hook['id'])
        self.assertFalse(refused['ok'])
        self.assertEqual(store.event_hook_list(self.conn)['hooks'][0]['last_event_id'], 0)
        with patch('ragbaz_frog.hooks.subprocess.run', return_value=subprocess.CompletedProcess([], 0)) as run:
            sent = store.event_hook_dispatch(self.conn, hook_id=hook['id'], allow_shell=True)
            self.assertTrue(sent['ok'])
            self.assertEqual(json.loads(run.call_args.kwargs['input'])['schema'], hooks.SCHEMA)
            self.assertFalse(run.call_args.kwargs['shell'])
            self.assertEqual(run.call_args.kwargs['env']['FROG_NO_HOOKS'], '1')
            self.assertEqual(run.call_args.kwargs['timeout'], 5)

    def test_command_timeout_and_uncommitted_events_do_not_advance_cursor(self):
        target = hooks.command_target(['/bin/true'])
        hook = store.event_hook_add(self.conn, target, kind='shell')['hook']
        store.record_event(self.conn, kind='task.created', summary='test')
        self.assertFalse(store.event_hook_dispatch(self.conn, allow_shell=True)['ok'])
        self.conn.commit()
        with patch('ragbaz_frog.hooks.subprocess.run', side_effect=subprocess.TimeoutExpired('/private/secret', 5)):
            result = store.event_hook_dispatch(self.conn, allow_shell=True)
        self.assertFalse(result['ok'])
        self.assertNotIn('/private', json.dumps(result))
        self.assertEqual(store.event_hook_list(self.conn)['hooks'][0]['last_event_id'], 0)

    def test_invalid_targets_signing_files_and_argument_separator(self):
        for url in ['http://remote.example/hook', 'https://user:secret@example.test', 'https://example.test?token=secret', 'file:///tmp/test']:
            self.assertFalse(store.event_hook_add(self.conn, url)['ok'])
        with self.assertRaises(ValueError): hooks.command_target(['relative-program'])
        key = self.directory / 'key'; key.write_bytes(b'x' * 32); key.chmod(0o644)
        self.assertFalse(store.event_hook_add(self.conn, 'https://example.test', signing_key_file=str(key))['ok'])
        self.assertEqual(_hoist_global_flags(['hook','add-shell','--','/bin/echo','--json']), ['hook','add-shell','--','/bin/echo','--json'])

    def test_auto_cli_dispatch_overrides_suppression_and_failure_isolation(self):
        hook = store.event_hook_add(self.conn, 'https://example.test/hook')['hook']
        with patch('ragbaz_frog.hooks.deliver', return_value=(503, 'down')) as deliver:
            self.assertEqual(self.cli('task','create','--slug','created','--title','Created task'), 0)
            self.assertEqual(deliver.call_count, 1)
            self.assertIsNotNone(self.conn.execute("SELECT slug FROM tasks WHERE slug='created'").fetchone())
            self.assertEqual(store.event_hook_list(self.conn)['hooks'][0]['last_event_id'], 0)
            deliver.reset_mock()
            self.assertEqual(self.cli('task','list'), 0); deliver.assert_not_called()
            self.assertEqual(self.cli('--no-hooks','task','create','--slug','suppressed','--title','Suppressed hook'), 0); deliver.assert_not_called()
            self.assertEqual(self.cli('--webhook-url','https://override.example/hook','task','create','--slug','override','--title','One shot'), 0)
            self.assertEqual(deliver.call_args.args[0]['url'], 'https://override.example/hook')
            self.assertEqual(store.event_hook_list(self.conn)['hooks'][0]['id'], hook['id'])

    def test_loopback_http_signature_and_redirect_refusal(self):
        received = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args): pass
            def do_POST(self):
                body = self.rfile.read(int(self.headers['Content-Length']))
                received.append((dict(self.headers), body))
                self.send_response(302 if self.path == '/redirect' else 204)
                self.send_header('Location', '/must-not-follow'); self.end_headers()
        with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
            thread=threading.Thread(target=server.serve_forever); thread.start()
            try:
                key=self.directory/'key';key.write_bytes(b'x'*32);key.chmod(0o600)
                body={'kind':'webhook','hook_id':1,'events':[]}
                hook={'kind':'webhook','url':f'http://127.0.0.1:{server.server_port}/hook','signing_key_file':str(key)}
                self.assertEqual(hooks.deliver(hook,body)[0],204)
                headers,raw=received[0];stamp=headers['X-Frog-Timestamp']
                self.assertEqual(headers['X-Frog-Signature'],'sha256='+hmac.new(b'x'*32,stamp.encode()+b'.'+raw,hashlib.sha256).hexdigest())
                hook['url']=f'http://127.0.0.1:{server.server_port}/redirect'
                self.assertEqual(hooks.deliver(hook,body)[0],302)
                self.assertEqual(len(received),2)
            finally: server.shutdown();thread.join(5)
