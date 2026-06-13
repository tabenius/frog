import os, time, subprocess, threading, hashlib, json, logging, sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("frog.sync")

SYNC_CONF_PATH = Path("/data/src") / ".frog-sync.json"
DEFAULT_EXCLUDES = [
    ".git/", "__pycache__/", ".ruff_cache/", ".pytest_cache/",
    "node_modules/", ".next/", "target/", "build/", "dist/",
    ".venv/", "venv/", ".tox/", "*.pyc", ".DS_Store",
    ".frog-sync-state.json",
]

@dataclass
class SyncConfig:
    enabled: bool = True
    remote_host: str = "konsonans"
    remote_path: str = "/data/src"
    local_path: str = "/data/src"
    excludes: list[str] = field(default_factory=lambda: DEFAULT_EXCLUDES)
    ssh_port: int = 22
    debounce_seconds: float = 1.0
    poll_interval: float = 5.0
    watch_subdirs: list[str] = field(default_factory=lambda: ["."])

def load_config() -> SyncConfig:
    if SYNC_CONF_PATH.exists():
        data = json.loads(SYNC_CONF_PATH.read_text())
        return SyncConfig(**{k: data[k] for k in SyncConfig.__dataclass_fields__ if k in data})
    return SyncConfig()

def save_config(cfg: SyncConfig):
    SYNC_CONF_PATH.write_text(json.dumps({
        k: getattr(cfg, k) for k in SyncConfig.__dataclass_fields__
    }, indent=2))
    logger.info("Config saved to %s", SYNC_CONF_PATH)


class SyncEvent:
    __slots__ = ("path", "kind", "mtime")
    def __init__(self, path: str, kind: str):
        self.path = path
        self.kind = kind
        self.mtime = time.time()


class Debouncer:
    def __init__(self, delay: float, callback):
        self.delay = delay
        self.callback = callback
        self._events: dict[str, SyncEvent] = {}
        self._timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()
        self._seq = 0

    def add(self, path: str, kind: str):
        with self._lock:
            self._events[path] = SyncEvent(path, kind)
            self._seq += 1
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(self.delay, self._flush)
            self._timer.daemon = True
            self._timer.start()

    def _flush(self):
        with self._lock:
            events = list(self._events.values())
            self._events.clear()
            self._timer = None
        if events:
            try:
                self.callback(events)
            except Exception:
                logger.exception("Debounce callback failed")

    def cancel(self):
        with self._lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None
            self._events.clear()


def _rsync_command(cfg: SyncConfig) -> list[str]:
    ssh_cmd = ["ssh"]
    if cfg.ssh_port != 22:
        ssh_cmd += ["-p", str(cfg.ssh_port)]
    cmd = [
        "rsync", "-a", "--delete", "--partial", "--progress",
        "-e", subprocess.list2cmdline(ssh_cmd),
    ]
    for ex in cfg.excludes:
        cmd += ["--exclude", ex]
    return cmd


def _run_rsync(cfg: SyncConfig, source: str, dest: str):
    cmd = _rsync_command(cfg) + [source, dest]
    logger.debug("Running: %s", " ".join(cmd))
    proc = subprocess.run(
        cmd,
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        logger.warning("rsync exit %d:\n%s", proc.returncode, proc.stderr[:1000])
    else:
        logger.info("rsync OK: %s -> %s (%d files)", source, dest,
                     len([l for l in proc.stdout.split("\n") if l and not l.startswith(".")]))


def _changed_paths(events: list[SyncEvent], cfg: SyncConfig) -> list[str]:
    dirs = set()
    for ev in events:
        p = Path(ev.path)
        if p.is_dir():
            dirs.add(str(p))
        else:
            dirs.add(str(p.parent))
    return sorted(dirs)


def sync_changes(events: list[SyncEvent], cfg: SyncConfig):
    dirs = _changed_paths(events, cfg)
    if not dirs:
        return
    remote = f"{cfg.remote_host}:{cfg.remote_path}"
    source = cfg.local_path.rstrip("/") + "/"
    logger.info("Changes in %d dirs, syncing %s -> %s", len(dirs), source, remote)
    _run_rsync(cfg, source, remote)


class InotifyWatcher:
    def __init__(self, cfg: SyncConfig, debouncer: Debouncer):
        self.cfg = cfg
        self.debouncer = debouncer
        self._wm = None
        self._notifier = None

    def start(self):
        import pyinotify
        self._wm = pyinotify.WatchManager()
        mask = (
            pyinotify.IN_CLOSE_WRITE | pyinotify.IN_CREATE |
            pyinotify.IN_DELETE | pyinotify.IN_MODIFY |
            pyinotify.IN_MOVED_FROM | pyinotify.IN_MOVED_TO
        )
        for sub in self.cfg.watch_subdirs:
            path = str(Path(self.cfg.local_path) / sub)
            if os.path.isdir(path):
                try:
                    self._wm.add_watch(path, mask, rec=True, auto_add=True)
                    logger.info("Watching (inotify): %s", path)
                except Exception as e:
                    logger.warning("Cannot watch %s: %s", path, e)

        class Handler(pyinotify.ProcessEvent):
            def __init__(self, owner):
                self.owner = owner
            def process_default(self, event):
                if event.pathname and "__pycache__" not in event.pathname and ".git" not in event.pathname:
                    kind = event.maskname if event.maskname else "UNKNOWN"
                    self.owner.debouncer.add(event.pathname, kind)

        self._notifier = pyinotify.Notifier(self._wm, Handler(self))
        logger.info("Inotify watcher started")
        self._notifier.loop()

    def stop(self):
        if self._notifier:
            self._notifier.stop()


class PollWatcher:
    def __init__(self, cfg: SyncConfig, debouncer: Debouncer):
        self.cfg = cfg
        self.debouncer = debouncer
        self._snapshots: dict[str, tuple[float, int, str]] = {}
        self._running = False

    def _build_snapshot(self) -> dict[str, tuple[float, int, str]]:
        snap: dict[str, tuple[float, int, str]] = {}
        for sub in self.cfg.watch_subdirs:
            root = str(Path(self.cfg.local_path) / sub)
            if not os.path.isdir(root):
                continue
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames
                               if d not in (".git", "__pycache__", "node_modules")]
                for fn in filenames:
                    if fn.endswith((".pyc", ".swp", ".swo")):
                        continue
                    fpath = os.path.join(dirpath, fn)
                    try:
                        st = os.stat(fpath)
                        snap[fpath] = (st.st_mtime, st.st_size, "")
                    except OSError:
                        pass
        return snap

    def _run_check(self):
        if not self._running:
            return
        snap = self._build_snapshot()
        for path, (mtime, size, _) in snap.items():
            prev = self._snapshots.get(path)
            if prev is None:
                self.debouncer.add(path, "CREATED")
            elif prev[0] != mtime or prev[1] != size:
                self.debouncer.add(path, "MODIFIED")
        for path in self._snapshots:
            if path not in snap:
                self.debouncer.add(path, "DELETED")
        self._snapshots = snap

    def start(self):
        logger.info("Poll watcher starting (interval=%ss)", self.cfg.poll_interval)
        self._running = True
        self._snapshots = self._build_snapshot()
        try:
            while self._running:
                self._run_check()
                time.sleep(self.cfg.poll_interval)
        finally:
            self._running = False

    def stop(self):
        self._running = False


def watch(cfg: SyncConfig = None):
    cfg = cfg or load_config()
    if not cfg.enabled:
        logger.info("Sync watcher disabled in config")
        return

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    debouncer = Debouncer(cfg.debounce_seconds,
                          lambda evs: sync_changes(evs, cfg))

    use_inotify = False
    try:
        import pyinotify
        use_inotify = True
        watcher = InotifyWatcher(cfg, debouncer)
    except ImportError:
        logger.info("pyinotify unavailable, falling back to poll")
        watcher = PollWatcher(cfg, debouncer)

    try:
        watcher.start()
    except KeyboardInterrupt:
        logger.info("Watcher stopped")
    finally:
        debouncer.cancel()
        watcher.stop()


if __name__ == "__main__":
    cfg = load_config()
    if "--init" in sys.argv:
        save_config(cfg)
        print(f"Config written to {SYNC_CONF_PATH}")
    else:
        watch(cfg)
