"""Read-only Frog kanban projection and loopback HTTP surface.

The existing board_snapshot remains the lane/dependency authority. This surface
never migrates a database, creates tasks, acquires locks, or runs commands.
"""
from __future__ import annotations

import datetime as dt
import errno
import hmac
import ipaddress
import json
import os
import socket
import sqlite3
import ssl
import stat
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from ragbaz_frog import store

SCHEMA = "frog.kanban-projection.v0"
LANES = (("idea", "Idea / ready"), ("blocked", "Blocked"),
         ("in_progress", "In progress / review"), ("done", "Done / closed"))
ASSETS = {"/": ("board.html", "text/html; charset=utf-8"),
          "/board.js": ("board.js", "text/javascript; charset=utf-8"),
          "/board.css": ("board.css", "text/css; charset=utf-8")}


def projection(db_path: str | Path) -> dict:
    """Capture one consistent SQLite read transaction, with explicit fields only."""
    path = Path(db_path).expanduser().resolve()
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only = ON")
        conn.execute("BEGIN")
        snapshot = store.board_snapshot(conn, recent_limit=0)
        dependencies = {}
        blocks = {}
        for edge in conn.execute("SELECT task_slug, depends_on_slug FROM task_dependencies WHERE relation='depends_on' ORDER BY task_slug, depends_on_slug"):
            dependencies.setdefault(edge["task_slug"], []).append(edge["depends_on_slug"])
            blocks.setdefault(edge["depends_on_slug"], []).append(edge["task_slug"])
        tags = {}
        for tag in conn.execute("SELECT task_slug, tag FROM task_tags ORDER BY task_slug, tag"):
            tags.setdefault(tag["task_slug"], []).append(tag["tag"])
        ready = set(snapshot["ready"])
        cards = []
        for lane, _ in LANES:
            for task in snapshot["columns"][lane]:
                cards.append({
                    "id": task["slug"], "title": task["title"], "lane": lane,
                    "priority": task["priority"], "status": task["workflow_status"],
                    "assignee": task["assigned_agent"],
                    "repository": Path(task["repo_path"]).name if task["repo_path"] else None,
                    "updated_at": task["updated_at"],
                    "created_at": task["created_at"],
                    "status_observed_at": task["status_confidence_at"],
                    "unmet_dependencies": task["unmet_deps"],
                    "dependencies": dependencies.get(task["slug"], []),
                    "blocks": blocks.get(task["slug"], []),
                    "tags": tags.get(task["slug"], []),
                    "ready": task["slug"] in ready,
                })
        return {"schema": SCHEMA, "source": "frog", "read_only": True,
                "observed_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
                 "revision": snapshot["max_event_id"],
                "repositories": [{"id": row["name"], "title": row["name"], "repository": Path(row["repo_path"]).name}
                    for row in conn.execute("SELECT name, repo_path FROM repos WHERE COALESCE(status,'active') != 'archived' ORDER BY name")],
                "lanes": [{"id": key, "title": title} for key, title in LANES],
                "counts": snapshot["counts"], "cards": cards}
    finally:
        conn.close()


def repository_story(db_path: str | Path, repo_id: str) -> dict:
    """Bounded, on-demand local README/Git history; never infer task completion."""
    conn = sqlite3.connect(Path(db_path).expanduser().resolve().as_uri() + "?mode=ro", timeout=5)
    try:
        row = conn.execute("SELECT repo_path, name FROM repos WHERE name=? AND COALESCE(status,'active') != 'archived'", (repo_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise KeyError(repo_id)
    root = Path(row[0]).resolve()
    description = "No repository description available. Open its tasks to explore the work."
    for filename in ('README.md', 'readme.md', 'README.rst', 'README'):
        path = root / filename
        if path.is_symlink() or not path.is_file():
            continue
        with path.open('r', encoding='utf-8', errors='replace') as file:
            lines = file.read(16384).splitlines()
        paragraphs = []
        for line in lines:
            line = line.strip()
            if line and not line.startswith(('#', '<', '[', '![', '```', '|', '- ', '* ')):
                paragraphs.append(line)
            elif paragraphs:
                break
        if paragraphs:
            description = ' '.join(paragraphs)[:500]
        break
    commits = []
    history_status = 'unavailable'
    try:
        with tempfile.TemporaryFile() as output:
            result = subprocess.run(['git', '--no-pager', '-C', str(root), '-c', 'core.fsmonitor=false',
                'log', '-5', '--format=%h%x1f%cI%x1f%s'], stdout=output, stderr=subprocess.DEVNULL,
                timeout=2, env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0', 'GIT_CONFIG_NOSYSTEM': '1'})
            output.seek(0); raw = output.read(32769)
        if result.returncode == 0 and len(raw) <= 32768:
            for line in raw.decode('utf-8', errors='replace').splitlines():
                fields = line.split('\x1f', 2)
                if len(fields) == 3:
                    commits.append(dict(commit=fields[0][:16], committed_at=fields[1][:40], title=fields[2][:300]))
            history_status = 'ready'
    except (OSError, subprocess.TimeoutExpired):
        pass
    return dict(schema='frog.repository-story.v0', source='frog', read_only=True,
        id=repo_id, description=description, commits=commits, history_status=history_status,
        observed_at=dt.datetime.now(dt.timezone.utc).isoformat())


class SnapshotSource:
    """A projection plus a cheap change token, so one watcher serves any backend."""

    interval = 0.5

    def version(self):
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def close(self):
        """Release anything held open for change detection."""


class DatabaseSource(SnapshotSource):
    """The local SQLite authority: watch PRAGMA data_version, not the payload."""

    def __init__(self, db_path: str | Path):
        self.db_path = db_path
        self._connection = None
        self._identity = None

    def version(self):
        path = Path(self.db_path).expanduser().resolve()
        identity = path.stat().st_dev, path.stat().st_ino
        if self._connection is None or identity != self._identity:
            # data_version is per-connection, so keep one open while the file
            # stays the same; reconnection alone would hide other writers.
            if self._connection is not None:
                self._connection.close()
            self._connection = sqlite3.connect(path.as_uri() + "?mode=ro", timeout=2)
            self._connection.execute("PRAGMA query_only=ON")
            self._identity = identity
        return identity, self._connection.execute("PRAGMA data_version").fetchone()[0]

    def snapshot(self) -> dict:
        return projection(self.db_path)

    def close(self):
        # The watcher owns this connection; closing it from another thread is
        # not legal in sqlite3, so drop the reference and let it be collected.
        self._connection = None
        self._identity = None


class ModelSource(SnapshotSource):
    """A remote model service. D1 has no change feed, so poll a stated interval."""

    def __init__(self, client, workspace: str, *, poll: float = 5.0):
        from ragbaz_frog import model_client
        self.projection = model_client.board_projection
        self.client = client
        self.workspace = workspace
        self.interval = max(1.0, poll)

    def version(self):
        return ()

    def snapshot(self) -> dict:
        return self.projection(self.client, self.workspace)


def create_server(db_path: str | Path | None = None, *, host: str = "localhost", port: int | None = None,
                  token_file: str | None = None, tls_cert: str | None = None,
                  tls_key: str | None = None, public_origin: str | None = None,
                  source: SnapshotSource | None = None, poll: float = 5.0):
    """Prefer 8788, choose another free port only when no explicit port was given."""
    if (db_path is None) == (source is None):
        raise ValueError("serve exactly one backend: a local database or a model source")
    if port is not None and not 0 <= port <= 65535:
        raise ValueError("port must be between 0 and 65535")
    bind_host = "127.0.0.1" if host == "localhost" else host
    address = ipaddress.ip_address(bind_host)
    provider = source if source is not None else DatabaseSource(db_path)
    token = None
    if token_file:
        token_path = Path(token_file).expanduser()
        try:
            descriptor = os.open(token_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError as error:
            raise ValueError("token file must be readable and not a symlink") from error
        with os.fdopen(descriptor, "r", encoding="utf-8") as file:
            metadata = os.fstat(file.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid() or (metadata.st_mode & 0o077):
                raise ValueError("token file must be a regular owner-only file (0600) owned by this user")
            token = file.read(4097).strip()
        if not 32 <= len(token) <= 4096 or any(character.isspace() for character in token):
            raise ValueError("web token must contain 32–4096 non-whitespace characters")
    tls = None
    if tls_cert or tls_key:
        if not (tls_cert and tls_key):
            raise ValueError("TLS requires both --tls-cert and --tls-key")
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.minimum_version = ssl.TLSVersion.TLSv1_2
        tls.load_cert_chain(tls_cert, tls_key)
    public = urlsplit(public_origin) if public_origin else None
    if public and (public.scheme != "https" or not public.hostname or public.username or public.password or public.path not in ("", "/") or public.query or public.fragment):
        raise ValueError("--public-origin must be an HTTPS origin with no credentials or path")
    if not address.is_loopback and not (token and tls and public):
        raise ValueError("non-loopback binds require --token-file, --tls-cert, --tls-key and --public-origin; alternatively use authenticated Rebekah")
    # Fail before binding if the backend is absent, unreachable or incompatible.
    provider.snapshot()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass  # Do not log credentials, request query strings, or task data.

        def respond(self, status, body, content_type="application/json; charset=utf-8"):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=True).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            bound_port = self.server.server_port
            authority = f"[{bind_host}]:{bound_port}" if address.version == 6 else f"{bind_host}:{bound_port}"
            hosts = {authority}
            if bind_host == "127.0.0.1":
                hosts.add(f"localhost:{bound_port}")
            if public:
                hosts.add(public.netloc.lower())
            request_host = self.headers.get("Host", "").lower()
            if request_host not in hosts:
                return self.respond(403, {"error": "bad_host"})
            origin = self.headers.get("Origin")
            if origin and origin != f"{'https' if tls else 'http'}://{request_host}":
                return self.respond(403, {"error": "bad_origin"})
            if self.headers.get("Sec-Fetch-Site") == "cross-site":
                return self.respond(403, {"error": "cross_site_request"})
            path = urlsplit(self.path).path
            if path in ("/api/v1/kanban", "/api/v1/repository", "/api/v1/events"):
                if token:
                    scheme, _, credential = self.headers.get("Authorization", "").partition(" ")
                    if scheme.lower() != "bearer" or not hmac.compare_digest(credential.encode(), token.encode()):
                        return self.respond(401, {"error": "authentication_required"})
                try:
                    if path == "/api/v1/events":
                        if self.command == 'HEAD':
                            return self.respond(200, b'', 'text/event-stream; charset=utf-8')
                        return self.stream_events()
                    if path == "/api/v1/repository":
                        # Repository stories read local Git; only a local backend has them.
                        if not isinstance(provider, DatabaseSource):
                            return self.respond(404, {"error": "not_found"})
                        ids = parse_qs(urlsplit(self.path).query).get('id', [])
                        if len(ids) != 1 or len(ids[0]) > 200:
                            return self.respond(400, {"error": "repository_id_required"})
                        try:
                            return self.respond(200, repository_story(provider.db_path, ids[0]))
                        except KeyError:
                            return self.respond(404, {"error": "repository_not_found"})
                    return self.respond(200, provider.snapshot())
                except (sqlite3.Error, OSError, ValueError):
                    return self.respond(503, {"error": "projection_unavailable"})
            if path in ASSETS:
                name, content_type = ASSETS[path]
                return self.respond(200, (Path(__file__).parent / "web" / name).read_bytes(), content_type)
            return self.respond(404, {"error": "not_found"})

        def stream_events(self):
            server = self.server
            if not server.stream_slots.acquire(blocking=False):
                return self.respond(503, {"error": "too_many_streams"})
            try:
                server.start_watcher()
                self.close_connection = True
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('X-Accel-Buffering', 'no')
                self.end_headers()
                seen = -1
                deadline = time.monotonic() + 300
                while not server.stop_watcher.is_set() and time.monotonic() < deadline:
                    with server.condition:
                        server.condition.wait_for(lambda: server.sequence != seen or server.stop_watcher.is_set(), timeout=15)
                        sequence, payload = server.sequence, server.live_payload
                    if server.stop_watcher.is_set():
                        break
                    if payload is None or sequence == seen:
                        self.wfile.write(b': keepalive\n\n')
                    else:
                        event = 'unavailable' if 'error' in payload else 'projection'
                        body = json.dumps(payload, ensure_ascii=True, separators=(',', ':')).encode()
                        self.wfile.write(f'id: {sequence}\nevent: {event}\ndata: '.encode() + body + b'\n\n')
                    seen = sequence
                    self.wfile.flush()
            except (OSError, TimeoutError):
                pass  # A disconnected/slow reader must not affect other subscribers.
            finally:
                server.stream_slots.release()

        def do_POST(self):
            self.close_connection = True
            self.respond(405, {"error": "read_only"})

        do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_POST

    class Server(ThreadingHTTPServer):
        address_family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
        daemon_threads = True

        def __init__(self, *args, **kwargs):
            self.condition = threading.Condition()
            self.stop_watcher = threading.Event()
            self.stream_slots = threading.BoundedSemaphore(8)
            self.watcher = None
            self.sequence = 0
            self.live_payload = None
            self.previous_digest = [None]
            super().__init__(*args, **kwargs)

        def start_watcher(self):
            with self.condition:
                if self.watcher is None:
                    self.watcher = threading.Thread(target=self.watch_backend, daemon=True)
                    self.watcher.start()

        def publish(self, payload):
            with self.condition:
                self.live_payload = payload
                self.sequence += 1
                self.condition.notify_all()

        def watch_backend(self):
            """One watcher per server: cheap change detection, then a projection.

            A local database reports its own `data_version`; a remote model is
            polled on its stated interval and compared by content. Failures are
            published as unavailable, never as an empty successful board.
            """
            previous = object()
            captured = 0.0
            while not self.stop_watcher.is_set():
                try:
                    version = provider.version()
                    now = time.monotonic()
                    if version != previous or now - captured >= 15:
                        payload = provider.snapshot()
                        digest = json.dumps(payload, sort_keys=True)
                        if digest != self.previous_digest[0]:
                            self.previous_digest[0] = digest
                            self.publish(payload)
                        previous, captured = version, now
                except Exception:
                    previous = object()
                    with self.condition:
                        if not self.live_payload or 'error' not in self.live_payload:
                            self.publish({'error': 'projection_unavailable'})
                self.stop_watcher.wait(provider.interval)

        def server_close(self):
            self.stop_watcher.set()
            with self.condition:
                self.condition.notify_all()
            super().server_close()
            if self.watcher is not None:
                self.watcher.join(6)
            provider.close()

        def get_request(self):
            connection, peer = super().get_request()
            connection.settimeout(5)
            return connection, peer

    ports = [port] if port is not None else list(range(8788, 8808)) + [0]
    for candidate in ports:
        try:
            server = Server((bind_host, candidate), Handler)
        except OSError as error:
            if error.errno == errno.EADDRINUSE and port is None:
                continue
            raise
        if tls:
            server.socket = tls.wrap_socket(server.socket, server_side=True, do_handshake_on_connect=False)
        return server
    raise OSError("no available Frog web port")


def serve(db_path: str | None = None, *, source: SnapshotSource | None = None, **options) -> int:
    host = options.get("host", "localhost")
    with create_server(db_path, source=source, **options) as server:
        authority = f"[{host}]" if ":" in host else host
        scheme = "https" if options.get("tls_cert") else "http"
        backend = "model service" if source is not None else "local database"
        print(f"Frog read-only board ({backend}): {scheme}://{authority}:{server.server_port}/", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0
