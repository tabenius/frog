import os, sys, json, time, signal, threading, logging, subprocess, socket
from pathlib import Path

logger = logging.getLogger("frog.gateway")

CONFIG_DIR = Path(os.environ.get("FROG_CONFIG_DIR", Path.home() / ".config" / "frog"))
PID_PATH = CONFIG_DIR / "gateway.pid"
LOG_PATH = CONFIG_DIR / "gateway.log"
STATE_PATH = CONFIG_DIR / "gateway.state"

PID_PATH.parent.mkdir(parents=True, exist_ok=True)

DEFAULT_CONFIG = {
    "proxy_enabled": True,
    "proxy_bind": "100.102.135.43",
    "proxy_port": 8080,
    "proxy_target": "100.92.101.49",
    "proxy_target_port": 8976,
    "watch_enabled": True,
    "watch_path": "/data/src",
    "watch_remote_host": "konsonans",
    "watch_remote_path": "/data/src",
    "watch_ssh_port": 22,
    "watch_debounce": 1.0,
    "watch_poll_interval": 5.0,
    "watch_excludes": [
        ".git/", "__pycache__/", ".ruff_cache/", ".pytest_cache/",
        "node_modules/", ".next/", "target/", "build/", "dist/",
        ".venv/", "venv/", ".tox/", "*.pyc", ".DS_Store",
    ],
}

CONFIG_PATH = CONFIG_DIR / "gateway.json"


def load_config():
    if CONFIG_PATH.exists():
        data = json.loads(CONFIG_PATH.read_text())
        merged = dict(DEFAULT_CONFIG)
        merged.update(data)
        return merged
    return dict(DEFAULT_CONFIG)


def save_config(cfg: dict):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n")
    logger.info("Config saved to %s", CONFIG_PATH)


def _setup_logging(log_path=None):
    path = log_path or LOG_PATH
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[
            logging.FileHandler(str(path)),
            logging.StreamHandler(),
        ],
    )


def _write_state(state: dict):
    STATE_PATH.write_text(json.dumps(state))


def _read_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


class PortForwarder:
    def __init__(self, cfg: dict, stop_event: threading.Event):
        self.cfg = cfg
        self.stop_event = stop_event

    def _forward(self, src, dst, label):
        try:
            while True:
                data = src.recv(65536)
                if not data:
                    break
                dst.sendall(data)
        except (ConnectionResetError, BrokenPipeError, OSError):
            pass

    def start(self):
        bind_addr = self.cfg["proxy_bind"]
        bind_port = self.cfg["proxy_port"]
        target_host = self.cfg["proxy_target"]
        target_port = self.cfg["proxy_target_port"]

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((bind_addr, bind_port))
        except OSError as e:
            logger.error("Cannot bind %s:%d: %s", bind_addr, bind_port, e)
            return
        s.listen(10)
        s.settimeout(1.0)
        logger.info("Proxy: %s:%d -> %s:%d", bind_addr, bind_port, target_host, target_port)

        while not self.stop_event.is_set():
            try:
                conn, addr = s.accept()
                logger.debug("Proxy connection from %s", addr)
                t = threading.Thread(target=self._handle, args=(conn, target_host, target_port), daemon=True)
                t.start()
            except socket.timeout:
                continue
            except OSError:
                break
        s.close()
        logger.info("Proxy stopped")

    def _handle(self, conn, target_host, target_port):
        try:
            remote = socket.create_connection((target_host, target_port), timeout=10)
        except (socket.timeout, ConnectionRefusedError, OSError) as e:
            logger.warning("Cannot connect to %s:%d: %s", target_host, target_port, e)
            conn.close()
            return
        t1 = threading.Thread(target=self._forward, args=(conn, remote, "c2r"), daemon=True)
        t2 = threading.Thread(target=self._forward, args=(remote, conn, "r2c"), daemon=True)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        conn.close()
        remote.close()


class FileWatcher:
    def __init__(self, cfg: dict, stop_event: threading.Event):
        self.cfg = cfg
        self.stop_event = stop_event
        self._debounce_events: dict[str, float] = {}
        self._debounce_lock = threading.Lock()
        self._debounce_timer: threading.Timer | None = None

    def _debounce_add(self, path: str):
        with self._debounce_lock:
            self._debounce_events[path] = time.time()
            if self._debounce_timer:
                self._debounce_timer.cancel()
            self._debounce_timer = threading.Timer(self.cfg["watch_debounce"], self._debounce_flush)
            self._debounce_timer.daemon = True
            self._debounce_timer.start()

    def _debounce_flush(self):
        with self._debounce_lock:
            events = dict(self._debounce_events)
            self._debounce_events.clear()
            self._debounce_timer = None
        if events:
            self._run_rsync()

    def _rsync_cmd(self) -> list[str]:
        ssh_port = self.cfg.get("watch_ssh_port", 22)
        ssh_cmd = ["ssh"]
        if ssh_port != 22:
            ssh_cmd += ["-p", str(ssh_port)]
        cmd = ["rsync", "-a", "--delete", "--partial", "-q",
               "-e", subprocess.list2cmdline(ssh_cmd)]
        for ex in self.cfg.get("watch_excludes", []):
            cmd += ["--exclude", ex]
        return cmd

    def _run_rsync(self):
        host = self.cfg["watch_remote_host"]
        rpath = self.cfg["watch_remote_path"]
        local = self.cfg["watch_path"].rstrip("/") + "/"
        remote = f"{host}:{rpath}"
        cmd = self._rsync_cmd() + [local, remote]
        logger.info("rsync %s -> %s", local, remote)
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if proc.returncode != 0:
                logger.warning("rsync exit %d: %s", proc.returncode, proc.stderr[:500])
            else:
                logger.debug("rsync OK")
        except subprocess.TimeoutExpired:
            logger.warning("rsync timed out")
        except Exception as e:
            logger.warning("rsync failed: %s", e)

    def start_inotify(self):
        import pyinotify
        wm = pyinotify.WatchManager()
        mask = (
            pyinotify.IN_CLOSE_WRITE | pyinotify.IN_CREATE |
            pyinotify.IN_DELETE | pyinotify.IN_MODIFY |
            pyinotify.IN_MOVED_FROM | pyinotify.IN_MOVED_TO
        )
        try:
            wm.add_watch(self.cfg["watch_path"], mask, rec=True, auto_add=True)
            logger.info("Watch (inotify): %s", self.cfg["watch_path"])
        except Exception as e:
            logger.warning("Cannot watch %s: %s", self.cfg["watch_path"], e)
            return

        class Handler(pyinotify.ProcessEvent):
            def __init__(self, owner):
                self.owner = owner

            def process_default(self, event):
                if not event.pathname:
                    return
                p = event.pathname
                if any(ex.rstrip("/") in p or p.endswith(ex.lstrip("*"))
                       for ex in self.owner.cfg.get("watch_excludes", [])):
                    return
                if ".git" in p or "__pycache__" in p:
                    return
                self.owner._debounce_add(p)

        notifier = pyinotify.Notifier(wm, Handler(self))
        logger.info("Inotify watcher running")
        while not self.stop_event.is_set():
            if notifier.check_events(200):
                notifier.read_events()
                notifier.process_events()

    def start_poll(self):
        interval = self.cfg.get("watch_poll_interval", 5.0)
        logger.info("Watch (poll, %ss): %s", interval, self.cfg["watch_path"])
        snap: dict[str, tuple[float, int]] = {}
        try:
            while not self.stop_event.is_set():
                new_snap = self._build_snapshot()
                for path, (mtime, size) in new_snap.items():
                    prev = snap.get(path)
                    if prev is None:
                        self._debounce_add(path)
                    elif prev[0] != mtime or prev[1] != size:
                        self._debounce_add(path)
                for path in snap:
                    if path not in new_snap:
                        self._debounce_add(path)
                snap = new_snap
                self.stop_event.wait(interval)
        finally:
            logger.info("Poll watcher stopped")

    def _build_snapshot(self) -> dict[str, tuple[float, int]]:
        snap: dict[str, tuple[float, int]] = {}
        root = self.cfg["watch_path"]
        if not os.path.isdir(root):
            return snap
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames
                           if d not in (".git", "__pycache__", "node_modules")]
            for fn in filenames:
                if fn.endswith((".pyc", ".swp", ".swo")):
                    continue
                fpath = os.path.join(dirpath, fn)
                try:
                    st = os.stat(fpath)
                    snap[fpath] = (st.st_mtime, st.st_size)
                except OSError:
                    pass
        return snap

    def start(self):
        has_inotify = False
        try:
            import pyinotify
            has_inotify = True
        except ImportError:
            pass

        if has_inotify:
            self.start_inotify()
        else:
            self.start_poll()


def run_gateway(cfg: dict = None):
    cfg = cfg or load_config()
    _setup_logging()
    logger.info("Gateway starting with config: proxy=%s, watch=%s",
                cfg.get("proxy_enabled"), cfg.get("watch_enabled"))

    stop = threading.Event()
    threads = []

    if cfg.get("proxy_enabled"):
        fwd = PortForwarder(cfg, stop)
        t = threading.Thread(target=fwd.start, daemon=True)
        t.start()
        threads.append(t)

    if cfg.get("watch_enabled"):
        watcher = FileWatcher(cfg, stop)
        t = threading.Thread(target=watcher.start, daemon=True)
        t.start()
        threads.append(t)

    _write_state({
        "pid": os.getpid(),
        "status": "running",
        "proxy": cfg.get("proxy_enabled", False),
        "watch": cfg.get("watch_enabled", False),
    })

    def _shutdown(signum, frame):
        logger.info("Shutdown signal %d received", signum)
        stop.set()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    try:
        stop.wait()
    except KeyboardInterrupt:
        stop.set()

    for t in threads:
        t.join(timeout=5)

    logger.info("Gateway stopped")
    _write_state({"status": "stopped"})


def cmd_start(args):
    cfg = load_config()
    if args.bind:
        cfg["proxy_bind"] = args.bind
    if args.port:
        cfg["proxy_port"] = args.port
    if args.target:
        cfg["proxy_target"] = args.target
    if args.target_port:
        cfg["proxy_target_port"] = args.target_port
    if args.watch_only:
        cfg["proxy_enabled"] = False
    if args.proxy_only:
        cfg["watch_enabled"] = False
    if args.no_watch:
        cfg["watch_enabled"] = False

    run_gateway(cfg)


def cmd_stop(args):
    state = _read_state()
    pid = state.get("pid")
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
            print(f"Sent SIGTERM to gateway pid {pid}")
        except ProcessLookupError:
            print("Gateway not running")
        _write_state({"status": "stopped"})
    else:
        print("No gateway pid file found")


def cmd_status(args):
    state = _read_state()
    pid = state.get("pid")
    running = False
    if pid:
        try:
            os.kill(pid, 0)
            running = True
        except OSError:
            pass

    print(f"Status: {'running' if running else 'stopped'}")
    if running:
        print(f"Pid:    {pid}")
        print(f"Proxy:  {'enabled' if state.get('proxy') else 'disabled'}")
        print(f"Watch:  {'enabled' if state.get('watch') else 'disabled'}")

    cfg = load_config()
    print(f"Config: {CONFIG_PATH}")
    if args.verbose:
        print(json.dumps(cfg, indent=2))


def cmd_config(args):
    cfg = load_config()
    if args.show:
        print(json.dumps(cfg, indent=2))
    elif args.key and args.value:
        cfg[args.key] = json.loads(args.value) if args.value.startswith(("[", "{")) else args.value
        save_config(cfg)
        print(f"Set {args.key} = {cfg[args.key]}")
    else:
        print(json.dumps(cfg, indent=2))


def add_subcommands(subparsers):
    gw = subparsers.add_parser("gateway", help="Manage the inter-box gateway daemon (proxy + file watcher)")
    gw_sub = gw.add_subparsers(dest="gateway_command", required=True)

    start_p = gw_sub.add_parser("start", help="Start the gateway daemon")
    start_p.add_argument("--bind", help="Proxy bind address")
    start_p.add_argument("--port", type=int, help="Proxy bind port")
    start_p.add_argument("--target", help="Proxy target host")
    start_p.add_argument("--target-port", type=int, help="Proxy target port")
    start_p.add_argument("--proxy-only", action="store_true", help="Run only the proxy (no file watcher)")
    start_p.add_argument("--watch-only", action="store_true", help="Run only the file watcher (no proxy)")
    start_p.add_argument("--no-watch", action="store_true", help="Disable file watcher")

    gw_sub.add_parser("stop", help="Stop the gateway daemon")

    status_p = gw_sub.add_parser("status", help="Show gateway daemon status")
    status_p.add_argument("-v", "--verbose", action="store_true", help="Show full config")

    config_p = gw_sub.add_parser("config", help="View or set gateway configuration")
    config_p.add_argument("key", nargs="?", help="Config key to set")
    config_p.add_argument("value", nargs="?", help="Config value (JSON for objects/arrays)")
    config_p.add_argument("--show", action="store_true", help="Show full config")


def handle_command(args):
    if args.gateway_command == "start":
        cmd_start(args)
    elif args.gateway_command == "stop":
        cmd_stop(args)
    elif args.gateway_command == "status":
        cmd_status(args)
    elif args.gateway_command == "config":
        cmd_config(args)
