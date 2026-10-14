"""Bounded delivery adapters; registry/cursors remain owned by store.py."""
from __future__ import annotations
import hashlib
import hmac
import json
import os
import stat
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

SCHEMA = 'frog.hook-events.v1'
MAX_BYTES = 256 * 1024


def valid_url(url: str) -> bool:
    try:
        value = urlsplit(url)
        return bool(value.hostname and not value.username and not value.password
                    and not value.query and not value.fragment
                    and (value.scheme == 'https' or value.scheme == 'http'
                         and value.hostname in ('localhost', '127.0.0.1', '::1'))
                    and (value.port is None or 1 <= value.port <= 65535))
    except ValueError:
        return False


def command_target(argv: list[str]) -> str:
    if not isinstance(argv, list) or not argv or len(argv) > 64 or any(not isinstance(arg, str) or len(arg) > 4096 or '\x00' in arg for arg in argv):
        raise ValueError('command hook requires a bounded argument array')
    if not Path(argv[0]).is_absolute():
        raise ValueError('command hook executable must be an absolute path')
    return 'exec:' + json.dumps(argv, separators=(',', ':'))


def envelope(body: dict, schema: str = SCHEMA) -> dict:
    if schema == 'frog.legacy-hook.v0':
        return body
    return {'schema': SCHEMA, 'hook_id': body.get('hook_id'), 'kind': body.get('kind'),
            'events': [{'id': row['id'], 'event_id': f"{row.get('origin_box_id') or 'local'}:{row['id']}",
                        'kind': row['kind'], 'observed_at': row['created_at'], 'task_id': row.get('task_slug'),
                        'repository': Path(row['repo_path']).name if row.get('repo_path') else None}
                       for row in body['events']]}


def read_key(path: str) -> bytes:
    descriptor = os.open(Path(path).expanduser(), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as file:
        metadata = os.fstat(file.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077:
            raise ValueError('signing key must be an owner-only regular file owned by this user')
        key = file.read(4097).strip()
    if not 32 <= len(key) <= 4096:
        raise ValueError('signing key must contain 32–4096 bytes')
    return key


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def deliver(hook: dict, body: dict, *, allow_shell: bool = False) -> tuple[int, str]:
    encoded = json.dumps(envelope(body, hook.get('payload_schema', SCHEMA)), sort_keys=True, separators=(',', ':')).encode()
    if len(encoded) > MAX_BYTES:
        return 413, 'hook batch exceeds byte limit; retry with a smaller --limit'
    if hook['kind'] == 'shell':
        if not allow_shell:
            return 403, 'command hooks require --allow-shell-hooks or trusted local configuration'
        argv = json.loads(hook['url'].removeprefix('exec:')); command_target(argv)
        result = subprocess.run(argv, input=encoded, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                timeout=5, cwd='/', env={'PATH': os.defpath, 'LANG': 'C.UTF-8', 'FROG_NO_HOOKS': '1'}, shell=False)
        return (204, '') if result.returncode == 0 else (502, 'command hook exited unsuccessfully')
    if hook['kind'] not in ('webhook', 'slack', 'discord'):
        return 400, 'unsupported hook adapter'
    if not valid_url(hook['url']):
        return 400, 'webhook requires HTTPS (HTTP only on loopback), no credentials/query/fragment'
    headers = {'Content-Type': 'application/json', 'User-Agent': 'frog/1'}
    if hook.get('signing_key_file'):
        stamp = str(int(time.time()))
        signature = hmac.new(read_key(hook['signing_key_file']), stamp.encode() + b'.' + encoded, hashlib.sha256).hexdigest()
        headers.update({'X-Frog-Timestamp': stamp, 'X-Frog-Signature': 'sha256=' + signature})
    request = urllib.request.Request(hook['url'], data=encoded, headers=headers, method='POST')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=5) as response:
            return int(response.status), ''
    except urllib.error.HTTPError as error:
        error.close()
        return int(error.code), 'webhook returned non-success status'


def safe_error(error: Exception) -> str:
    if isinstance(error, subprocess.TimeoutExpired):
        return 'command hook timed out'
    return 'hook delivery failed (' + type(error).__name__ + ')'


def preflight(args) -> None:
    if args.no_hooks or os.environ.get('FROG_NO_HOOKS') == '1':
        return
    if args.hook_ids and any(hook_id <= 0 for hook_id in args.hook_ids):
        raise ValueError('--hook IDs must be positive')
    if args.webhook_url and any(not valid_url(url) for url in args.webhook_url):
        raise ValueError('--webhook-url requires HTTPS (HTTP only on loopback), no credentials/query/fragment')
    if args.hook_signing_key_file:
        if not args.webhook_url:
            raise ValueError('--hook-signing-key-file requires --webhook-url')
        read_key(args.hook_signing_key_file)


def after_command(conn, args, baseline: int) -> None:
    """Opt-in post-commit delivery; failures never change the CLI operation result."""
    import sys
    from . import config, store
    if args.no_hooks or os.environ.get('FROG_NO_HOOKS') == '1' or conn.in_transaction:
        return
    # Reads only the config already selected by the CLI; never create a new one.
    path = config.config_path(args.config)
    settings = config._load_config_file(path).get('hooks', {}) if path.exists() else {}
    if not isinstance(settings, dict):
        raise ValueError('hooks configuration must be a mapping')
    explicit = bool(args.hook_ids or args.webhook_url)
    if not explicit and settings.get('auto_dispatch') is not True:
        return
    if not conn.execute("SELECT 1 FROM event_log WHERE id>? AND kind!='frog.command' LIMIT 1", (baseline,)).fetchone():
        return
    configured_ids = settings.get('ids')
    if configured_ids is not None and (not isinstance(configured_ids, list) or any(type(value) is not int or value <= 0 for value in configured_ids)):
        raise ValueError('hooks.ids must be a list of positive registry IDs')
    allow_shell = args.allow_shell_hooks
    if settings.get('allow_shell') is True and not path.is_symlink():
        metadata = path.stat()
        allow_shell |= metadata.st_uid == os.geteuid() and not metadata.st_mode & 0o077
    if args.webhook_url:
        events = store._hook_events(conn, baseline, 101)
        if len(events) > 100:
            print('frog: committed operation preserved; one-shot hook batch exceeds 100 events; use a registered hook for cursor-based delivery', file=sys.stderr)
            return
        body = {'hook_id': None, 'kind': 'webhook', 'events': events}
        for url in args.webhook_url:
            status, _ = deliver({'url': url, 'kind': 'webhook', 'payload_schema': SCHEMA,
                                 'signing_key_file': args.hook_signing_key_file}, body)
            if not 200 <= status < 300:
                print(f'frog: committed operation preserved; one-shot hook failed (status {status}); no durable retry cursor', file=sys.stderr)
    ids = args.hook_ids if args.hook_ids else (configured_ids if not explicit else [])
    if ids == []:
        return
    for hook_id in ids if ids is not None else [None]:
        result = store.event_hook_dispatch(conn, hook_id=hook_id, allow_shell=allow_shell)
        if not result['ok']:
            print('frog: committed operation preserved; hook delivery pending; retry with frog hook dispatch', file=sys.stderr)
