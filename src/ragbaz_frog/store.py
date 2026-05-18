from __future__ import annotations

import hashlib
import json
import time
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path

WORKSPACE_ROOT = Path("/data/src")
DISCOVERY_MANIFESTS = (
    "Makefile",
    "package.json",
    "Cargo.toml",
    "pyproject.toml",
    "compose.yml",
    "compose.yaml",
    "docker-compose.yml",
    "docker-compose.yaml",
)
DISCOVERY_EXCLUDED_DIRS = {
    ".claude",
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".turbo",
    ".next",
    "build",
    "dist",
    "out",
    "site",
    "target",
}
DISCOVERY_CATEGORY_ROOTS = (
    "products",
    "experiments",
    "infra",
    "private",
    "vendor",
    "archive",
    "headless",
    "doc",
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat(timespec="seconds")


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


class RemoteDbError(RuntimeError):
    """Raised when the AGENTS.db path is on a non-local filesystem."""


def _fs_type(path: Path) -> str | None:
    """Best-effort filesystem type for the nearest existing ancestor of
    `path`, parsed from /proc/mounts. Returns None if undeterminable."""
    try:
        mounts = Path("/proc/mounts").read_text().splitlines()
    except OSError:
        return None
    target = path
    while not target.exists() and target != target.parent:
        target = target.parent
    try:
        target = target.resolve()
    except OSError:
        pass
    best = None
    best_len = -1
    for line in mounts:
        parts = line.split()
        if len(parts) < 3:
            continue
        mnt, fstype = parts[1], parts[2]
        try:
            mp = Path(mnt)
        except ValueError:
            continue
        try:
            target.relative_to(mp)
        except ValueError:
            continue
        if len(str(mp)) > best_len:
            best, best_len = fstype, len(str(mp))
    return best


_REMOTE_FS = {"nfs", "nfs4", "fuse.sshfs", "cifs", "smbfs", "fuse.rclone"}


def _guard_local_db(path: Path) -> None:
    if os.environ.get("FROG_ALLOW_REMOTE_DB") == "1":
        return
    fstype = _fs_type(path)
    if fstype in _REMOTE_FS:
        raise RemoteDbError(
            f"AGENTS.db at {path} is on a {fstype} (non-local) filesystem. "
            "SQLite coordination over a network FS is unsafe. Use a named "
            "workspace instead -- `frog --workspace NAME ...` RPCs over SSH "
            "to the box that owns that DB. (Override: FROG_ALLOW_REMOTE_DB=1.)"
        )


def connect(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    _guard_local_db(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # C1: robust local multi-writer (many agents, one box -- the common case).
    # WAL lets readers run concurrently with a writer; busy_timeout makes
    # contending writers wait instead of erroring; synchronous=NORMAL is the
    # safe/fast pairing with WAL.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def migration_dir() -> Path:
    return Path(__file__).resolve().parent / "migrations"


def dicts(rows) -> list[dict]:
    return [dict(row) for row in rows]


def current_agent() -> str:
    """Resolved acting-agent identity. FROG_AGENT lets a Claude/Codex
    session declare itself distinctly even when many run as the same OS
    user; falls back to $USER."""
    return (os.environ.get("FROG_AGENT")
            or os.environ.get("USER")
            or "unknown").strip() or "unknown"


def current_session() -> str:
    """Stable-per-process session id so two sessions of the same agent are
    distinguishable (FROG_SESSION, else host:pid)."""
    return os.environ.get("FROG_SESSION") or f"{socket.gethostname()}:{os.getpid()}"


def agent_whoami(conn) -> dict:
    name = current_agent()
    row = conn.execute("SELECT * FROM agents WHERE name = ?", (name,)).fetchone()
    return {
        "ok": True,
        "agent": name,
        "session": current_session(),
        "registered": bool(row),
        "kind": row["kind"] if row else None,
    }


def agent_register(conn, name: str | None = None, *, kind: str | None = None,
                   notes: str | None = None) -> dict:
    name = (name or current_agent()).strip()
    if not name:
        return {"ok": False, "error": "empty agent name"}
    now = utc_now_iso()
    conn.execute(
        """INSERT INTO agents(name, kind, notes, created_at, updated_at)
           VALUES(?,?,?,?,?)
           ON CONFLICT(name) DO UPDATE SET
             kind=COALESCE(excluded.kind, agents.kind),
             notes=COALESCE(excluded.notes, agents.notes),
             updated_at=excluded.updated_at""",
        (name, kind, notes, now, now),
    )
    record_event(conn, kind="agent.registered",
                 summary=f"registered agent {name}", actor=name,
                 payload={"kind": kind, "session": current_session()})
    conn.commit()
    return {"ok": True, "message": f"registered agent {name}",
            "agent": name, "kind": kind}


def ensure_agent(conn, name: str) -> None:
    now = utc_now_iso()
    conn.execute(
        """
        INSERT INTO agents(name, created_at, updated_at)
        VALUES(?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET updated_at = excluded.updated_at
        """,
        (name, now, now),
    )


def record_event(
    conn,
    *,
    kind: str,
    summary: str,
    repo_path: str | None = None,
    task_slug: str | None = None,
    actor: str | None = None,
    payload: dict | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO event_log(created_at, kind, repo_path, task_slug, actor, summary, payload_json)
        VALUES(?, ?, ?, ?, ?, ?, ?)
        """,
        (
            utc_now_iso(),
            kind,
            repo_path,
            task_slug,
            actor,
            summary,
            json.dumps(payload or {}, sort_keys=True),
        ),
    )


def migrate(db_path: str) -> dict:
    conn = connect(db_path)
    try:
        applied = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'")
        }
        if applied:
            rows = conn.execute("SELECT name FROM schema_migrations").fetchall()
            applied = {row["name"] for row in rows}
        else:
            applied = set()
        newly_applied = []
        for sql_file in sorted(migration_dir().glob("*.sql")):
            if sql_file.name in applied:
                continue
            conn.executescript(sql_file.read_text())
            conn.execute(
                "INSERT OR REPLACE INTO schema_migrations(name, applied_at) VALUES(?, ?)",
                (sql_file.name, utc_now_iso()),
            )
            record_event(
                conn,
                kind="migration.applied",
                summary=f"applied {sql_file.name}",
                payload={"migration": sql_file.name},
            )
            conn.commit()
            newly_applied.append(sql_file.name)
        return {
            "ok": True,
            "message": f"applied {len(newly_applied)} migration(s)",
            "db_path": db_path,
            "applied": newly_applied,
        }
    finally:
        conn.close()


def init_repo(conn, path_or_name: str, *, kind: str | None = None, notes: str | None = None) -> dict:
    raw = Path(path_or_name).expanduser()
    if any(sep in path_or_name for sep in ("/",)) or path_or_name.startswith(("~", ".")):
        repo_path = raw.resolve()
    else:
        repo_path = (WORKSPACE_ROOT / "experiments" / path_or_name).resolve()
    repo_path.mkdir(parents=True, exist_ok=True)
    agents_result = write_agent_instructions(conn, str(repo_path))
    if not agents_result.get("ok", True):
        return agents_result
    agents_path = Path(agents_result["agents_path"])
    repo = register_repo(
        conn,
        repo_path=str(repo_path),
        name=repo_path.name,
        kind=kind or "embryo",
        status="active",
        third_party=False,
        notes=notes or "initialized by frog",
    )
    record_event(
        conn,
        kind="repo.initialized",
        summary=f"initialized repo {repo_path.name}",
        repo_path=str(repo_path),
        payload={"agents_path": str(agents_path)},
    )
    conn.commit()
    return {"ok": True, "message": f"initialized {repo_path}", "repo": repo["repo"], "agents_path": str(agents_path)}


def agent_instructions_text() -> str:
    return """# Local AGENTS.md

Read `/data/src/AGENTS.md` before starting work in this repo.

Use the shared coordination system:
- CLI: `/data/src/ragbaz-frog/bin/frog`
- DB: `/data/src/AGENTS.db`
"""


_FROG_BIN = "/data/src/ragbaz-frog/bin/frog"
_FROG_MCP = "/data/src/ragbaz-frog/bin/frog-mcp"


def _agent_md(agent: str) -> str:
    return f"""# Agent instructions ({agent})

This tree is coordinated by **frog** (`{_FROG_BIN}`, DB `/data/src/AGENTS.db`).

Before editing:
- `frog board` -- see lifecycle + what's blocked on what
- `frog task next --agent {agent}` -- highest-ROI unblocked slice
- `frog task claim <slug> --agent {agent}` -- take it (assigns + locks)
- `frog lock check --file <path>` -- is someone else on this file?

When done:
- `frog task finish <slug> --agent {agent}` -- verify + release + unblock

Set `FROG_AGENT` so your edits/locks are attributed distinctly.
"""


def _claude_settings_fragment() -> dict:
    return {
        "hooks": {
            "PreToolUse": [{
                "matcher": "Edit|Write",
                "hooks": [{"type": "command",
                           "command": "/data/src/ragbaz-frog/hooks/pretooluse-lock-guard.sh"}],
            }],
            "SessionStart": [{
                "hooks": [{"type": "command",
                           "command": f"{_FROG_BIN} board --once; {_FROG_BIN} task next --agent ${{FROG_AGENT:-$USER}}"}],
            }],
        }
    }


def _mcp_json_fragment() -> dict:
    return {"mcpServers": {"frog": {"command": _FROG_MCP, "args": []}}}


def _merge_hooks(existing: dict, frag: dict) -> dict:
    out = json.loads(json.dumps(existing)) if existing else {}
    hooks = out.setdefault("hooks", {})
    for event, entries in frag["hooks"].items():
        bucket = hooks.setdefault(event, [])
        for e in entries:
            cmds = {h["command"] for grp in bucket for h in grp.get("hooks", [])}
            new_cmds = {h["command"] for h in e["hooks"]}
            if not (new_cmds & cmds):
                bucket.append(e)
    return out


def setup_agent(conn, agent: str, *, target_dir: str | None,
                dry_run: bool = False, force: bool = False) -> dict:
    """Generate the config a Claude/Codex user otherwise hand-rolls.
    Idempotent; --dry-run plans without writing."""
    agent = agent.lower()
    if agent not in {"claude", "codex"}:
        return {"ok": False, "error": "agent must be 'claude' or 'codex'"}
    base = Path(target_dir).expanduser().resolve() if target_dir else Path.cwd()
    if not base.is_dir():
        return {"ok": False, "error": f"not a directory: {base}"}
    actions = []

    def plan(path: Path, kind: str, write):
        rel = str(path)
        if path.exists() and not force and kind == "create":
            actions.append({"path": rel, "action": "skip (exists)"})
            return
        actions.append({"path": rel,
                        "action": ("would " if dry_run else "") +
                        ("merge" if kind == "merge" else "write")})
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            write()

    md_name = "CLAUDE.md" if agent == "claude" else "AGENTS.md"
    md = base / md_name
    plan(md, "create", lambda: md.write_text(_agent_md(agent)))

    snippet = {"FROG_AGENT": f"{agent}-$(hostname -s)"}
    if agent == "claude":
        sj = base / ".claude" / "settings.json"
        def _w_sj():
            cur = {}
            if sj.exists():
                try:
                    cur = json.loads(sj.read_text())
                except json.JSONDecodeError:
                    cur = {}
            sj.write_text(json.dumps(_merge_hooks(cur, _claude_settings_fragment()),
                                     indent=2) + "\n")
        plan(sj, "merge", _w_sj)
        mj = base / ".mcp.json"
        def _w_mj():
            cur = {}
            if mj.exists():
                try:
                    cur = json.loads(mj.read_text())
                except json.JSONDecodeError:
                    cur = {}
            cur.setdefault("mcpServers", {})["frog"] = _mcp_json_fragment()["mcpServers"]["frog"]
            mj.write_text(json.dumps(cur, indent=2) + "\n")
        plan(mj, "merge", _w_mj)
        extra = {"mcp": _mcp_json_fragment(),
                 "env_snippet": f'export FROG_AGENT="claude-$(hostname -s)"'}
    else:
        # Codex reads AGENTS.md (written above). Emit the config.toml block
        # to add by hand (we never edit a user's global ~/.codex/config.toml).
        toml_block = (
            "[mcp_servers.frog]\n"
            f'command = "{_FROG_MCP}"\n'
            "args = []\n"
        )
        extra = {"codex_config_toml": toml_block,
                 "env_snippet": f'export FROG_AGENT="codex-$(hostname -s)"'}

    return {"ok": True, "agent": agent, "dir": str(base),
            "dry_run": dry_run, "actions": actions, **extra}


def write_agent_instructions(conn, path_or_dir: str | None, *, force: bool = False) -> dict:
    if path_or_dir:
        raw = Path(path_or_dir).expanduser()
    else:
        raw = Path.cwd()
    if raw.suffix.lower() == ".md":
        target = raw.resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
    else:
        target = raw.resolve() / "AGENTS.md"
        target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not force:
        return {"ok": False, "error": f"refusing to overwrite existing file: {target}"}
    target.write_text(agent_instructions_text(), encoding="utf-8")
    repo = resolve_repo(conn, str(target.parent))
    record_event(
        conn,
        kind="agents.instructions.written",
        summary=f"wrote agent instructions to {target}",
        repo_path=repo["repo_path"] if repo else None,
        payload={"agents_path": str(target)},
    )
    conn.commit()
    return {"ok": True, "message": f"wrote {target}", "agents_path": str(target)}


def snapshot_workspace(conn) -> dict:
    source_root = Path("/data/src")
    backup_root = Path("/data/backups")
    src_last = backup_root / "src.last"
    src_prev = backup_root / "src.prev"

    backup_root.mkdir(parents=True, exist_ok=True)

    if src_prev.exists():
        if src_prev.is_dir():
            shutil.rmtree(src_prev)
        else:
            src_prev.unlink()

    if src_last.exists():
        src_last.rename(src_prev)

    src_last.mkdir(parents=True, exist_ok=True)

    proc = subprocess.run(
        ["rsync", "-a", f"{source_root}/", f"{src_last}/"],
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        return {
            "ok": False,
            "error": "rsync snapshot failed",
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }

    record_event(
        conn,
        kind="workspace.snapshot",
        summary="snapshotted /data/src to /data/backups/src.last",
        payload={
            "source_root": str(source_root),
            "backup_root": str(backup_root),
            "src_last": str(src_last),
            "src_prev": str(src_prev),
        },
    )
    conn.commit()
    return {
        "ok": True,
        "message": f"snapshotted {source_root} to {src_last}",
        "source_root": str(source_root),
        "src_last": str(src_last),
        "src_prev": str(src_prev),
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def doctor(conn, db_path: str | None = None) -> dict:
    """Self-diagnostic: turn the coordination data back on the operator."""
    findings = []

    def add(level, code, detail):
        findings.append({"level": level, "code": code, "detail": detail})

    # DB size
    if db_path:
        try:
            sz = Path(db_path).stat().st_size
            mb = sz / (1024 * 1024)
            if mb >= 250:
                add("warn", "db_large", f"AGENTS.db is {mb:.0f} MB -- consider `frog db gc`")
        except OSError:
            pass

    # stale/expired locks
    _mark_stale_locks(conn)
    conn.commit()
    stale = conn.execute(
        "SELECT COUNT(*) c FROM locks WHERE status = 'stale'"
    ).fetchone()["c"]
    if stale:
        add("warn", "stale_locks", f"{stale} stale lock(s); `frog lock reap`/`list --status stale`")

    # tasks whose deps are ALL done but task is still blocked-ish (drift)
    drift = []
    for r in conn.execute(
        "SELECT slug, workflow_status FROM tasks "
        "WHERE LOWER(workflow_status) IN ('blocked','idea')"
    ):
        deps = conn.execute(
            "SELECT depends_on_slug FROM task_dependencies "
            "WHERE task_slug=? AND relation='depends_on'", (r["slug"],)
        ).fetchall()
        if not deps:
            continue
        all_done = True
        for d in deps:
            dd = conn.execute(
                "SELECT workflow_status FROM tasks WHERE slug=?",
                (d["depends_on_slug"],)
            ).fetchone()
            if not dd or (dd["workflow_status"] or "").lower() not in _WF_DONE:
                all_done = False
                break
        if all_done:
            drift.append(r["slug"])
    if drift:
        add("info", "ready_tasks",
            f"{len(drift)} task(s) have all deps done and are takeable: "
            + ", ".join(drift[:10]))

    # mirror lag
    for r in conn.execute(
        "SELECT workspace, MAX(mirrored_at) m FROM event_mirror GROUP BY workspace"
    ):
        try:
            age_h = (utc_now() - parse_iso(r["m"])).total_seconds() / 3600
            if age_h > 48:
                add("info", "mirror_lag",
                    f"workspace '{r['workspace']}' mirror is {age_h:.0f}h behind")
        except Exception:
            pass

    return {
        "ok": not any(f["level"] == "warn" for f in findings),
        "findings": findings,
        "summary": {
            "warn": sum(1 for f in findings if f["level"] == "warn"),
            "info": sum(1 for f in findings if f["level"] == "info"),
        },
    }


def db_gc(conn, *, older_than_days: int | None = None, keep: int = 200) -> dict:
    """Prune unbounded coordination tables, then checkpoint + VACUUM.
      - event_log / event_mirror: drop rows older than `older_than_days`
        (if given) but always retain the newest `keep`.
      - target_runs: keep only the newest `keep` rows per
        (repo_path,target_kind,target_name); older cache rows are noise.
    """
    removed = {}
    cutoff = None
    if older_than_days is not None:
        cutoff = (utc_now() - timedelta(days=older_than_days)).isoformat(timespec="seconds")

    def _trim(table: str, order_col: str) -> int:
        keep_ids = [r["id"] for r in conn.execute(
            f"SELECT id FROM {table} ORDER BY {order_col} DESC LIMIT ?", (keep,)
        ).fetchall()]
        if cutoff is None:
            return 0
        ph = ",".join("?" * len(keep_ids)) or "0"
        cur = conn.execute(
            f"DELETE FROM {table} WHERE created_at < ? AND id NOT IN ({ph})",
            (cutoff, *keep_ids),
        )
        return cur.rowcount

    removed["event_log"] = _trim("event_log", "id")
    # event_mirror keyed by (workspace, remote_id), no autoinc id
    if cutoff is not None:
        keep_rows = conn.execute(
            "SELECT workspace, remote_id FROM event_mirror "
            "ORDER BY mirrored_at DESC LIMIT ?", (keep,)
        ).fetchall()
        keep_set = {(r["workspace"], r["remote_id"]) for r in keep_rows}
        em = conn.execute(
            "SELECT rowid, workspace, remote_id, created_at FROM event_mirror"
        ).fetchall()
        gone = 0
        for r in em:
            if r["created_at"] < cutoff and (r["workspace"], r["remote_id"]) not in keep_set:
                conn.execute("DELETE FROM event_mirror WHERE rowid = ?", (r["rowid"],))
                gone += 1
        removed["event_mirror"] = gone
    else:
        removed["event_mirror"] = 0
    # target_runs: keep newest `keep` per logical target
    tr = 0
    groups = conn.execute(
        "SELECT DISTINCT repo_path, target_kind, target_name FROM target_runs"
    ).fetchall()
    for g in groups:
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM target_runs WHERE repo_path=? AND target_kind=? "
            "AND target_name=? ORDER BY id DESC LIMIT -1 OFFSET ?",
            (g["repo_path"], g["target_kind"], g["target_name"], keep),
        ).fetchall()]
        for i in ids:
            conn.execute("DELETE FROM target_runs WHERE id = ?", (i,))
            tr += 1
    removed["target_runs"] = tr
    conn.commit()
    # checkpoint + reclaim space (VACUUM cannot run in a transaction)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("VACUUM")
    except sqlite3.Error:
        pass
    record_event(conn, kind="db.gc",
                 summary=f"gc removed {sum(removed.values())} row(s)",
                 payload={"removed": removed, "older_than_days": older_than_days,
                          "keep": keep})
    conn.commit()
    return {"ok": True, "removed": removed,
            "message": f"gc removed {sum(removed.values())} row(s)"}


def schema_status(db_path: str) -> dict:
    conn = connect(db_path)
    try:
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
        ).fetchone()
        if not exists:
            return {"ok": True, "db_path": db_path, "migrations": []}
        rows = conn.execute(
            "SELECT name, applied_at FROM schema_migrations ORDER BY name"
        ).fetchall()
        return {"ok": True, "db_path": db_path, "migrations": dicts(rows)}
    finally:
        conn.close()


def register_repo(
    conn,
    *,
    repo_path: str,
    name: str | None,
    kind: str | None,
    status: str,
    third_party: bool,
    notes: str | None,
) -> dict:
    now = utc_now_iso()
    repo_path = str(Path(repo_path).expanduser().resolve())
    preferred_name = name or Path(repo_path).name
    name = _unique_repo_name(conn, repo_path=repo_path, preferred_name=preferred_name)
    conn.execute(
        """
        INSERT INTO repos(repo_path, name, kind, status, third_party, notes, created_at, updated_at)
        VALUES(?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(repo_path) DO UPDATE SET
            name = excluded.name,
            kind = excluded.kind,
            status = excluded.status,
            third_party = excluded.third_party,
            notes = excluded.notes,
            updated_at = excluded.updated_at
        """,
        (repo_path, name, kind, status, int(third_party), notes, now, now),
    )
    record_event(
        conn,
        kind="repo.upserted",
        summary=f"registered repo {name}",
        repo_path=repo_path,
        payload={"name": name, "kind": kind, "status": status},
    )
    conn.commit()
    try:
        ensure_repo_key(conn, repo_path)
    except sqlite3.Error:
        pass
    return repo_info(conn, repo_path)


def _unique_repo_name(conn, *, repo_path: str, preferred_name: str) -> str:
    row = conn.execute("SELECT repo_path FROM repos WHERE name = ?", (preferred_name,)).fetchone()
    if not row or row["repo_path"] == repo_path:
        return preferred_name
    repo = Path(repo_path)
    try:
        relative = repo.relative_to(WORKSPACE_ROOT)
        candidate = relative.as_posix().replace("/", ":")
    except ValueError:
        candidate = repo.as_posix().replace("/", ":").lstrip(":")
    existing = conn.execute("SELECT repo_path FROM repos WHERE name = ?", (candidate,)).fetchone()
    if not existing or existing["repo_path"] == repo_path:
        return candidate
    return f"{candidate}:{repo.name}"


def repo_list(conn, *, include_third_party: bool) -> dict:
    query = "SELECT * FROM repos"
    if not include_third_party:
        query += " WHERE third_party = 0"
    query += " ORDER BY name, repo_path"
    rows = conn.execute(query).fetchall()
    return {"ok": True, "repos": dicts(rows)}


def repo_list_with_activity(conn, *, include_third_party: bool) -> dict:
    payload = repo_list(conn, include_third_party=include_third_party)
    if not payload.get("ok", True):
        return payload
    for repo in payload["repos"]:
        repo.update(path_metadata(repo["repo_path"]))
        repo.update(path_activity(repo["repo_path"]))
    return payload


def _ts_iso(timestamp: float | None) -> str | None:
    if not timestamp:
        return None
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="seconds")


def path_activity(path: str) -> dict:
    repo_path = Path(path)
    latest_source = _source_latest_mtime(repo_path)
    fs_iso = _ts_iso(latest_source)
    git_iso = None
    dirty = None
    try:
        git_head = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "--is-inside-work-tree"],
            text=True,
            capture_output=True,
        )
        if git_head.returncode == 0 and git_head.stdout.strip() == "true":
            git_log = subprocess.run(
                ["git", "-C", str(repo_path), "log", "-1", "--format=%cI", "--", "."],
                text=True,
                capture_output=True,
            )
            if git_log.returncode == 0:
                git_iso = git_log.stdout.strip() or None
            git_status = subprocess.run(
                ["git", "-C", str(repo_path), "status", "--porcelain", "--", "."],
                text=True,
                capture_output=True,
            )
            if git_status.returncode == 0:
                dirty = bool(git_status.stdout.strip())
    except OSError:
        pass
    return {
        "dirty": dirty,
        "last_git_change_at": git_iso,
        "last_fs_change_at": fs_iso,
    }


def path_metadata(path: str) -> dict:
    resolved = Path(path).resolve()
    try:
        relative = resolved.relative_to(WORKSPACE_ROOT)
        parts = relative.parts
    except ValueError:
        parts = resolved.parts
        relative = resolved
    category = parts[0] if parts else None
    suite = parts[1] if len(parts) > 1 else None
    subgroup = "/".join(parts[2:-1]) if len(parts) > 3 else (parts[2] if len(parts) == 4 else None)
    return {
        "relative_path": relative.as_posix(),
        "category": category,
        "suite": suite,
        "subgroup": subgroup,
    }


def _box_id() -> str:
    return socket.gethostname()


def compute_repo_key(repo_path: str) -> str:
    """Stable identity that is identical on every box:
      1. a committed `.frogid` file at the repo root (authoritative), else
      2. the git `origin` remote URL, else
      3. the absolute path (box-local fallback -- not portable, flagged
         by the `path:` prefix)."""
    root = Path(repo_path)
    fid = root / ".frogid"
    try:
        if fid.is_file():
            v = fid.read_text().strip()
            if v:
                return v
    except OSError:
        pass
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "remote", "get-url", "origin"],
            text=True, capture_output=True,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            url = proc.stdout.strip()
            return "git:" + hashlib.sha256(url.encode()).hexdigest()[:16]
    except OSError:
        pass
    return "path:" + hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:16]


def _record_repo_alias(conn, repo_key: str, repo_path: str) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO repo_aliases(repo_key, box, repo_path, created_at)
           VALUES(?,?,?,?)""",
        (repo_key, _box_id(), repo_path, utc_now_iso()),
    )


def ensure_repo_key(conn, repo_path: str) -> str:
    """Idempotently assign a repo_key to a registered repo + record the
    (box -> local path) alias for this machine."""
    row = conn.execute(
        "SELECT repo_key FROM repos WHERE repo_path = ?", (repo_path,)
    ).fetchone()
    key = row["repo_key"] if row else None
    if not key:
        key = compute_repo_key(repo_path)
        conn.execute(
            "UPDATE repos SET repo_key = ?, updated_at = ? WHERE repo_path = ?",
            (key, utc_now_iso(), repo_path),
        )
    _record_repo_alias(conn, key, repo_path)
    conn.commit()
    return key


def repo_key_backfill(conn) -> dict:
    done = []
    for r in conn.execute("SELECT repo_path FROM repos").fetchall():
        done.append({"repo_path": r["repo_path"],
                     "repo_key": ensure_repo_key(conn, r["repo_path"])})
    return {"ok": True, "message": f"keyed {len(done)} repo(s)", "repos": done}


def resolve_local_path(conn, repo_key: str) -> str | None:
    """Where does `repo_key` live on THIS box?"""
    box = _box_id()
    row = conn.execute(
        "SELECT repo_path FROM repo_aliases WHERE repo_key = ? AND box = ? "
        "ORDER BY created_at LIMIT 1", (repo_key, box)
    ).fetchone()
    if row:
        return row["repo_path"]
    row = conn.execute(
        "SELECT repo_path FROM repos WHERE repo_key = ? LIMIT 1", (repo_key,)
    ).fetchone()
    return row["repo_path"] if row else None


def whereis(conn, repo_key: str) -> dict:
    box = _box_id()
    here = resolve_local_path(conn, repo_key)
    aliases = dicts(conn.execute(
        "SELECT box, repo_path, created_at FROM repo_aliases "
        "WHERE repo_key = ? ORDER BY box, repo_path", (repo_key,)
    ).fetchall())
    return {"ok": here is not None, "repo_key": repo_key, "box": box,
            "local_path": here, "aliases": aliases,
            "error": None if here else f"no local path for {repo_key} on {box}"}


def repo_key_info(conn, repo_ref: str, *, set_key: str | None = None,
                  write_frogid: bool = False) -> dict:
    repo = resolve_repo(conn, repo_ref)
    if not repo or not repo.get("repo_path"):
        return {"ok": False, "error": f"repo not found: {repo_ref}"}
    rp = repo["repo_path"]
    if set_key:
        conn.execute("UPDATE repos SET repo_key=?, updated_at=? WHERE repo_path=?",
                     (set_key, utc_now_iso(), rp))
        _record_repo_alias(conn, set_key, rp)
        conn.commit()
        key = set_key
    else:
        key = ensure_repo_key(conn, rp)
    if write_frogid:
        try:
            (Path(rp) / ".frogid").write_text(key + "\n")
        except OSError as e:
            return {"ok": False, "error": f"could not write .frogid: {e}",
                    "repo_key": key}
    return {"ok": True, "repo": repo["name"], "repo_path": rp,
            "repo_key": key,
            "aliases": dicts(conn.execute(
                "SELECT box, repo_path FROM repo_aliases WHERE repo_key=? "
                "ORDER BY box", (key,)).fetchall())}


def resolve_repo(conn, repo_ref: str) -> dict | None:
    repo_ref = repo_ref.strip()
    exact = conn.execute(
        "SELECT * FROM repos WHERE repo_path = ? OR name = ?",
        (repo_ref, repo_ref),
    ).fetchone()
    if exact:
        return dict(exact)
    rows = conn.execute("SELECT * FROM repos ORDER BY repo_path").fetchall()
    for row in rows:
        payload = dict(row)
        if Path(payload["repo_path"]).name == repo_ref:
            return payload
    keyed = conn.execute(
        "SELECT * FROM repos WHERE repo_key = ?", (repo_ref,)
    ).fetchone()
    if keyed:
        return dict(keyed)
    al = conn.execute(
        "SELECT repo_path FROM repo_aliases WHERE repo_key = ? AND box = ? LIMIT 1",
        (repo_ref, _box_id()),
    ).fetchone()
    if al:
        r = conn.execute("SELECT * FROM repos WHERE repo_path = ?",
                         (al["repo_path"],)).fetchone()
        if r:
            return dict(r)
    as_path = Path(repo_ref).expanduser()
    if as_path.exists() and as_path.is_dir():
        resolved = str(as_path.resolve())
        return {
            "repo_path": resolved,
            "name": Path(resolved).name,
            "kind": None,
            "status": "unregistered",
            "third_party": 0,
            "notes": None,
            "created_at": None,
            "updated_at": None,
        }
    workspace = WORKSPACE_ROOT
    matches = []
    for candidate in workspace.rglob(repo_ref):
        if candidate.is_dir():
            try:
                candidate.relative_to(workspace)
            except ValueError:
                continue
            if any(
                part in {
                    "vendor",
                    "archive",
                    ".git",
                    "node_modules",
                    "__pycache__",
                    "build",
                    "site",
                    ".next",
                    "dist",
                    "out",
                    "target",
                }
                for part in candidate.parts
            ):
                continue
            matches.append(candidate.resolve())
    if len(matches) == 1:
        resolved = str(matches[0])
        return {
            "repo_path": resolved,
            "name": Path(resolved).name,
            "kind": None,
            "status": "discovered",
            "third_party": 0,
            "notes": None,
            "created_at": None,
            "updated_at": None,
        }
    return None


def infer_repo_from_cwd(conn, cwd: str | None = None) -> dict | None:
    cwd_path = Path(cwd or os.getcwd()).expanduser().resolve()
    rows = conn.execute("SELECT * FROM repos ORDER BY LENGTH(repo_path) DESC").fetchall()
    for row in rows:
        payload = dict(row)
        repo_path = Path(payload["repo_path"])
        try:
            cwd_path.relative_to(repo_path)
            return payload
        except ValueError:
            continue

    workspace = WORKSPACE_ROOT.resolve()
    try:
        cwd_path.relative_to(workspace)
    except ValueError:
        return None

    manifests = ("Makefile", "package.json", "Cargo.toml", "pyproject.toml", "compose.yml", "docker-compose.yml")
    current = cwd_path
    while True:
        if any((current / name).exists() for name in manifests):
            return {
                "repo_path": str(current),
                "name": current.name,
                "kind": None,
                "status": "discovered",
                "third_party": 0,
                "notes": None,
                "created_at": None,
                "updated_at": None,
            }
        if current == workspace:
            break
        if current.parent == current:
            break
        current = current.parent
    return None


def repo_names(conn) -> list[str]:
    rows = conn.execute("SELECT name FROM repos ORDER BY name").fetchall()
    return [row["name"] for row in rows if row["name"]]


def _infer_repo_kind(repo_path: Path) -> tuple[str, bool]:
    parts = set(repo_path.parts)
    if {"vendor", "archive"} & parts:
        return "third_party", True
    if "products" in parts:
        return "product", False
    if "experiments" in parts:
        return "embryo", False
    if "infra" in parts:
        return "infra", False
    if "private" in parts:
        return "private", False
    if "doc" in parts or "docs" in parts:
        return "documentation", False
    return "repo", False


def _candidate_category_root(root_path: Path, current: Path) -> tuple[str | None, Path]:
    try:
        relative = current.relative_to(root_path)
    except ValueError:
        return None, root_path
    if not relative.parts:
        return None, root_path
    if relative.parts[0] in DISCOVERY_CATEGORY_ROOTS:
        return relative.parts[0], root_path / relative.parts[0]
    return None, root_path


def _looks_like_repo_boundary(root_path: Path, current: Path, dirnames: list[str], filenames: list[str]) -> bool:
    if ".git" in dirnames or ".git" in filenames:
        return True
    if any(part in {".claude", "worktrees"} for part in current.parts):
        return False
    category, anchor = _candidate_category_root(root_path, current)
    if "AGENTS.md" in filenames and (category is not None or current != root_path):
        return True
    if not any(name in filenames for name in DISCOVERY_MANIFESTS):
        return False
    try:
        relative_to_anchor = current.relative_to(anchor)
    except ValueError:
        return False
    max_depth = 2 if category is None else 3
    if category in {"products", "experiments", "private"}:
        max_depth = 4
    return len(relative_to_anchor.parts) <= max_depth


def discover_repos(conn, *, root: str = str(WORKSPACE_ROOT), scan: bool = True) -> dict:
    root_path = Path(root).expanduser().resolve()
    discovered: list[dict] = []
    seen: set[str] = set()
    scanned = 0
    repo_roots: list[Path] = []
    for current_root, dirnames, filenames in os.walk(root_path):
        dirnames[:] = [name for name in dirnames if name not in DISCOVERY_EXCLUDED_DIRS]
        current = Path(current_root)
        if any(_is_within(current, existing) for existing in repo_roots):
            dirnames[:] = [name for name in dirnames if (current / name / ".git").exists()]
            continue
        if not _looks_like_repo_boundary(root_path, current, dirnames, filenames):
            continue
        repo_path = str(current.resolve())
        if repo_path in seen:
            continue
        seen.add(repo_path)
        kind, third_party = _infer_repo_kind(current)
        info = register_repo(
            conn,
            repo_path=repo_path,
            name=current.name,
            kind=kind,
            status="active",
            third_party=third_party,
            notes=f"discovered by frog under {root_path}",
        )
        repo_roots.append(current.resolve())
        if scan:
            scan_result = repo_scan(conn, repo_path)
            if scan_result.get("ok", True):
                scanned += 1
        discovered.append(info["repo"])
    record_event(
        conn,
        kind="repo.discovered",
        summary=f"discovered {len(discovered)} repos under {root_path}",
        payload={"root": str(root_path), "repo_count": len(discovered), "scanned": scanned},
    )
    conn.commit()
    return {
        "ok": True,
        "root": str(root_path),
        "repos": sorted(discovered, key=lambda item: (item["name"], item["repo_path"])),
        "counts": {"discovered": len(discovered), "scanned": scanned},
    }


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return path != root
    except ValueError:
        return False


def repo_info(conn, repo_ref: str) -> dict:
    repo = resolve_repo(conn, repo_ref)
    if not repo:
        return {"ok": False, "error": f"repo not found: {repo_ref}"}
    task_count = conn.execute(
        "SELECT COUNT(*) AS count FROM tasks WHERE repo_path = ?",
        (repo["repo_path"],),
    ).fetchone()["count"]
    active_lock_count = conn.execute(
        "SELECT COUNT(*) AS count FROM locks WHERE repo_path = ? AND status = 'active'",
        (repo["repo_path"],),
    ).fetchone()["count"]
    target_count = 0
    artifact_count = 0
    if conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'repo_targets'"
    ).fetchone():
        target_count = conn.execute(
            "SELECT COUNT(*) AS count FROM repo_targets WHERE repo_path = ?",
            (repo["repo_path"],),
        ).fetchone()["count"]
        artifact_count = conn.execute(
            "SELECT COUNT(*) AS count FROM repo_artifacts WHERE repo_path = ?",
            (repo["repo_path"],),
        ).fetchone()["count"]
    return {
        "ok": True,
        "repo": repo,
        "counts": {
            "tasks": task_count,
            "active_locks": active_lock_count,
            "targets": target_count,
            "artifacts": artifact_count,
        },
    }


def upsert_file(
    conn,
    *,
    file_path: str,
    repo_path: str | None,
    file_type: str | None,
    source_of_truth: str | None,
    notes: str | None,
) -> dict:
    now = utc_now_iso()
    file_path = str(Path(file_path).expanduser().resolve())
    if repo_path:
        repo_path = str(Path(repo_path).expanduser().resolve())
    conn.execute(
        """
        INSERT INTO files(file_path, repo_path, file_type, source_of_truth, notes, created_at, updated_at)
        VALUES(?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(file_path) DO UPDATE SET
            repo_path = excluded.repo_path,
            file_type = excluded.file_type,
            source_of_truth = excluded.source_of_truth,
            notes = excluded.notes,
            updated_at = excluded.updated_at
        """,
        (file_path, repo_path, file_type, source_of_truth, notes, now, now),
    )
    record_event(
        conn,
        kind="file.upserted",
        summary=f"classified file {Path(file_path).name}",
        repo_path=repo_path,
        payload={"file_path": file_path, "file_type": file_type},
    )
    conn.commit()
    return file_info(conn, file_path)


def file_list(conn, *, repo_ref: str | None, file_type: str | None) -> dict:
    clauses = []
    params = []
    repo_path = None
    if repo_ref:
        repo = resolve_repo(conn, repo_ref)
        if not repo:
            return {"ok": False, "error": f"repo not found: {repo_ref}"}
        repo_path = repo["repo_path"]
        clauses.append("repo_path = ?")
        params.append(repo_path)
    if file_type:
        clauses.append("file_type = ?")
        params.append(file_type)
    query = "SELECT * FROM files"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY file_path"
    rows = conn.execute(query, tuple(params)).fetchall()
    return {"ok": True, "repo_path": repo_path, "files": dicts(rows)}


def file_info(conn, file_path: str) -> dict:
    file_path = str(Path(file_path).expanduser().resolve())
    row = conn.execute("SELECT * FROM files WHERE file_path = ?", (file_path,)).fetchone()
    if not row:
        return {"ok": False, "error": f"file not found: {file_path}"}
    return {"ok": True, "file": dict(row)}


def create_task(
    conn,
    *,
    slug: str,
    repo_ref: str | None,
    title: str,
    why: str | None,
    what_text: str | None,
    roi_note: str | None,
    priority: str,
    workflow_status: str,
    git_status: str,
    assigned_agent: str | None,
    delegation_current: str | None,
    delegation_other: str | None,
    parent_task_slug: str | None,
) -> dict:
    repo_path = None
    if repo_ref:
        repo = resolve_repo(conn, repo_ref)
        if not repo:
            return {"ok": False, "error": f"repo not found: {repo_ref}"}
        repo_path = repo["repo_path"]
    now = utc_now_iso()
    if assigned_agent:
        ensure_agent(conn, assigned_agent)
    conn.execute(
        """
        INSERT INTO tasks(
            slug, repo_path, title, why, what_text, roi_note, priority,
            workflow_status, git_status, assigned_agent,
            delegation_current, delegation_other, parent_task_slug,
            created_at, updated_at, status_confidence_at
        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            slug,
            repo_path,
            title,
            why,
            what_text,
            roi_note,
            priority,
            workflow_status,
            git_status,
            assigned_agent,
            delegation_current,
            delegation_other,
            parent_task_slug,
            now,
            now,
            now,
        ),
    )
    conn.execute(
        """
        INSERT INTO task_status_history(task_slug, workflow_status, git_status, note, changed_at)
        VALUES(?, ?, ?, ?, ?)
        """,
        (slug, workflow_status, git_status, "task created", now),
    )
    if assigned_agent:
        conn.execute(
            """
            INSERT INTO task_assignments(task_slug, agent_name, assigned_at, active, notes)
            VALUES(?, ?, ?, 1, ?)
            """,
            (slug, assigned_agent, now, "initial assignment"),
        )
    record_event(
        conn,
        kind="task.created",
        summary=f"created task {slug}",
        repo_path=repo_path,
        task_slug=slug,
        actor=assigned_agent,
        payload={"priority": priority, "workflow_status": workflow_status},
    )
    conn.commit()
    return task_info(conn, slug)


def task_list(
    conn,
    *,
    repo_ref: str | None,
    workflow_status: str | None,
    assigned_agent: str | None,
) -> dict:
    clauses = []
    params = []
    repo_path = None
    if repo_ref:
        repo = resolve_repo(conn, repo_ref)
        if not repo:
            return {"ok": False, "error": f"repo not found: {repo_ref}"}
        repo_path = repo["repo_path"]
        clauses.append("repo_path = ?")
        params.append(repo_path)
    if workflow_status:
        clauses.append("workflow_status = ?")
        params.append(workflow_status)
    if assigned_agent:
        clauses.append("assigned_agent = ?")
        params.append(assigned_agent)
    query = "SELECT * FROM tasks"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY priority, updated_at DESC, slug"
    rows = conn.execute(query, tuple(params)).fetchall()
    return {"ok": True, "repo_path": repo_path, "tasks": dicts(rows)}


_WF_DONE = {"done", "cancelled", "abandoned", "archived"}
_WF_BLOCKED = {"blocked"}
_WF_INPROG = {"in_progress", "in-progress", "doing", "wip", "active",
              "review", "started"}


def _priority_rank(p: str | None) -> int:
    if p and len(p) >= 2 and p[0] in "pP" and p[1:].isdigit():
        return int(p[1:])
    return 9


def _task_is_inprogress(conn, slug: str) -> bool:
    row = conn.execute(
        "SELECT workflow_status FROM tasks WHERE slug = ?", (slug,)
    ).fetchone()
    if row and (row["workflow_status"] or "").lower() in _WF_INPROG:
        return True
    a = conn.execute(
        "SELECT 1 FROM task_assignments WHERE task_slug = ? AND active = 1 LIMIT 1",
        (slug,),
    ).fetchone()
    return bool(a)


def task_next(conn, *, agent: str, repo_ref: str | None = None,
              limit: int = 1) -> dict:
    """Highest-ROI unblocked slice `agent` can safely take now:
      - not done/blocked, git not done
      - every depends_on dependency is done
      - no conflicting task is currently in progress
      - the task's repo is not locked by another agent
      - not already owned by a different agent
    Ordered by priority then age."""
    repo_path = None
    if repo_ref:
        repo = resolve_repo(conn, repo_ref)
        if not repo:
            return {"ok": False, "error": f"repo not found: {repo_ref}"}
        repo_path = repo["repo_path"]

    rows = conn.execute("SELECT * FROM tasks").fetchall()
    active_locks = _lock_rows(conn, include_inactive=False)
    locked_repos_by_other = {
        lk["repo_path"] for lk in active_locks
        if lk["repo_path"] and lk["agent_name"] != agent
    }

    eligible, skipped = [], []
    for r in rows:
        task = dict(r)
        wf = (task["workflow_status"] or "").lower()
        if repo_path and task["repo_path"] != repo_path:
            continue
        if wf in _WF_DONE:
            continue
        reason = None
        if wf in _WF_BLOCKED or (task["git_status"] or "") == "done":
            reason = f"status {wf or task['git_status']}"
        if reason is None:
            unmet = []
            for d in conn.execute(
                "SELECT depends_on_slug FROM task_dependencies "
                "WHERE task_slug = ? AND relation = 'depends_on'",
                (task["slug"],),
            ):
                dep = conn.execute(
                    "SELECT workflow_status FROM tasks WHERE slug = ?",
                    (d["depends_on_slug"],),
                ).fetchone()
                if not dep or (dep["workflow_status"] or "").lower() not in _WF_DONE:
                    unmet.append(d["depends_on_slug"])
            if unmet:
                reason = "deps not done: " + ", ".join(unmet)
        if reason is None:
            conflicting = []
            for c in conn.execute(
                "SELECT conflicts_with_slug AS o FROM task_conflicts WHERE task_slug = ? "
                "UNION SELECT task_slug AS o FROM task_conflicts WHERE conflicts_with_slug = ?",
                (task["slug"], task["slug"]),
            ):
                if _task_is_inprogress(conn, c["o"]):
                    conflicting.append(c["o"])
            if conflicting:
                reason = "conflicts in progress: " + ", ".join(conflicting)
        if reason is None and task["repo_path"] in locked_repos_by_other:
            reason = "repo locked by another agent"
        if reason is None:
            owner = task["assigned_agent"]
            if owner and owner != agent:
                reason = f"owned by {owner}"
        if reason is None:
            other = conn.execute(
                "SELECT agent_name FROM task_assignments WHERE task_slug = ? "
                "AND active = 1 AND agent_name != ? LIMIT 1",
                (task["slug"], agent),
            ).fetchone()
            if other:
                reason = f"owned by {other['agent_name']}"
        if reason is None:
            eligible.append(task)
        else:
            skipped.append({"slug": task["slug"], "reason": reason})

    eligible.sort(key=lambda x: (_priority_rank(x["priority"]),
                                 x["created_at"], x["slug"]))
    chosen = eligible[: max(1, limit)]
    return {"ok": True, "agent": agent, "tasks": chosen,
            "considered": len(rows), "eligible": len(eligible),
            "skipped": skipped[:20]}


def _task_file_paths(conn, slug: str) -> list[str]:
    return [r["file_path"] for r in conn.execute(
        "SELECT file_path FROM task_files WHERE task_slug = ?", (slug,)
    ).fetchall()]


def _newly_unblocked(conn, finished_slug: str) -> list[str]:
    """Tasks that depend_on `finished_slug` and now have ALL their
    depends_on dependencies done (and are not themselves done)."""
    out = []
    deps = conn.execute(
        "SELECT task_slug FROM task_dependencies "
        "WHERE depends_on_slug = ? AND relation = 'depends_on'",
        (finished_slug,),
    ).fetchall()
    for d in deps:
        ts = d["task_slug"]
        trow = conn.execute(
            "SELECT workflow_status FROM tasks WHERE slug = ?", (ts,)
        ).fetchone()
        if not trow or (trow["workflow_status"] or "").lower() in _WF_DONE:
            continue
        unmet = False
        for e in conn.execute(
            "SELECT depends_on_slug FROM task_dependencies "
            "WHERE task_slug = ? AND relation = 'depends_on'", (ts,)
        ):
            dep = conn.execute(
                "SELECT workflow_status FROM tasks WHERE slug = ?",
                (e["depends_on_slug"],),
            ).fetchone()
            if not dep or (dep["workflow_status"] or "").lower() not in _WF_DONE:
                unmet = True
                break
        if not unmet:
            out.append(ts)
    return sorted(set(out))


def task_claim(conn, *, slug: str, agent: str, lock_kind: str = "edit",
               scope_key: str | None = None, force: bool = False) -> dict:
    """Atomic-ish: take ownership + the scoped lock + mark in_progress."""
    row = conn.execute("SELECT * FROM tasks WHERE slug = ?", (slug,)).fetchone()
    if not row:
        return {"ok": False, "error": f"task not found: {slug}"}
    task = dict(row)
    if (task["workflow_status"] or "").lower() in _WF_DONE:
        return {"ok": False, "error": f"task {slug} is already {task['workflow_status']}"}
    owner = task["assigned_agent"]
    if owner and owner != agent and not force:
        return {"ok": False, "error": f"task {slug} is owned by {owner}"}
    scope = scope_key or f"task:{slug}"
    files = _task_file_paths(conn, slug)
    lk = lock_acquire(
        conn, scope_key=scope, repo_ref=task["repo_path"],
        lock_kind=lock_kind, files=files, agent=agent, pid=None,
        reason=f"claim {slug}", lease_seconds=1800, eta_minutes=None,
        force=force,
    )
    if not lk.get("ok", True):
        return {"ok": False, "error": "could not lock task scope",
                "lock": lk}
    conn.execute("UPDATE tasks SET assigned_agent = ?, updated_at = ? WHERE slug = ?",
                 (agent, utc_now_iso(), slug))
    task_assign(conn, slug, agent, f"claimed by {agent}")
    task_set_status(conn, slug=slug, workflow_status="in_progress",
                    git_status=None, note=f"claimed by {agent}")
    record_event(conn, kind="task.claimed",
                 summary=f"{agent} claimed {slug}", repo_path=task["repo_path"],
                 task_slug=slug, actor=agent,
                 payload={"lock": lk.get("lock", {}).get("id")})
    conn.commit()
    return {"ok": True, "task": dict(conn.execute(
        "SELECT * FROM tasks WHERE slug = ?", (slug,)).fetchone()),
        "lock": lk.get("lock")}


def task_finish(conn, *, slug: str, agent: str, verify: bool = True) -> dict:
    """Verify (affected build+test) -> done + release locks + report which
    dependents this unblocks."""
    row = conn.execute("SELECT * FROM tasks WHERE slug = ?", (slug,)).fetchone()
    if not row:
        return {"ok": False, "error": f"task not found: {slug}"}
    task = dict(row)
    verification = None
    if verify and task["repo_path"]:
        aff = repo_affected(conn, task["repo_path"])
        results = []
        failed = False
        if aff.get("ok"):
            for kind in ("build", "test"):
                names = {x["name"] for x in aff["affected"]
                         if x["target_kind"] == kind}
                if not names:
                    continue
                rr = repo_run(conn, task["repo_path"], kind,
                              only_targets=names)
                results.append({"kind": kind, "ok": rr.get("ok", True),
                                 "results": rr.get("results", [])})
                if not rr.get("ok", True):
                    failed = True
        verification = {"ran": bool(results), "failed": failed,
                        "detail": results}
        if failed:
            return {"ok": False, "error": "verification failed; status not flipped",
                    "task": task, "verification": verification}
    task_set_status(conn, slug=slug, workflow_status="done",
                    git_status="done", note=f"finished by {agent}")
    released = []
    for lkr in _lock_rows(conn, include_inactive=False,
                          repo_path=task["repo_path"]):
        if lkr["agent_name"] == agent and (
            lkr["scope_key"] == f"task:{slug}"
        ):
            lock_release(conn, lkr["id"], force=True)
            released.append(lkr["id"])
    unblocked = _newly_unblocked(conn, slug)
    record_event(conn, kind="task.finished",
                 summary=f"{agent} finished {slug}",
                 repo_path=task["repo_path"], task_slug=slug, actor=agent,
                 payload={"unblocked": unblocked, "released_locks": released})
    for ub in unblocked:
        record_event(conn, kind="task.unblocked",
                     summary=f"{ub} unblocked by {slug}",
                     task_slug=ub, actor=agent,
                     payload={"by": slug})
    conn.commit()
    return {"ok": True,
            "task": dict(conn.execute("SELECT * FROM tasks WHERE slug = ?",
                                       (slug,)).fetchone()),
            "verification": verification, "released_locks": released,
            "unblocked": unblocked}


def task_info(conn, slug: str) -> dict:
    task = conn.execute("SELECT * FROM tasks WHERE slug = ?", (slug,)).fetchone()
    if not task:
        return {"ok": False, "error": f"task not found: {slug}"}
    return {
        "ok": True,
        "task": dict(task),
        "dependencies": dicts(
            conn.execute(
                "SELECT * FROM task_dependencies WHERE task_slug = ? ORDER BY depends_on_slug",
                (slug,),
            ).fetchall()
        ),
        "conflicts": dicts(
            conn.execute(
                "SELECT * FROM task_conflicts WHERE task_slug = ? ORDER BY conflicts_with_slug",
                (slug,),
            ).fetchall()
        ),
        "tags": [
            row["tag"]
            for row in conn.execute(
                "SELECT tag FROM task_tags WHERE task_slug = ? ORDER BY tag",
                (slug,),
            ).fetchall()
        ],
        "history": dicts(
            conn.execute(
                "SELECT * FROM task_status_history WHERE task_slug = ? ORDER BY changed_at",
                (slug,),
            ).fetchall()
        ),
        "assignments": dicts(
            conn.execute(
                "SELECT * FROM task_assignments WHERE task_slug = ? ORDER BY assigned_at",
                (slug,),
            ).fetchall()
        ),
    }


def task_set_status(
    conn,
    *,
    slug: str,
    workflow_status: str | None,
    git_status: str | None,
    note: str | None,
) -> dict:
    row = conn.execute(
        "SELECT workflow_status, git_status, repo_path, assigned_agent FROM tasks WHERE slug = ?",
        (slug,),
    ).fetchone()
    if not row:
        return {"ok": False, "error": f"task not found: {slug}"}
    now = utc_now_iso()
    workflow_status = workflow_status or row["workflow_status"]
    git_status = git_status or row["git_status"]
    conn.execute(
        """
        UPDATE tasks
        SET workflow_status = ?, git_status = ?, updated_at = ?, status_confidence_at = ?
        WHERE slug = ?
        """,
        (workflow_status, git_status, now, now, slug),
    )
    conn.execute(
        """
        INSERT INTO task_status_history(task_slug, workflow_status, git_status, note, changed_at)
        VALUES(?, ?, ?, ?, ?)
        """,
        (slug, workflow_status, git_status, note, now),
    )
    record_event(
        conn,
        kind="task.status",
        summary=f"updated task {slug} status",
        repo_path=row["repo_path"],
        task_slug=slug,
        actor=row["assigned_agent"],
        payload={"workflow_status": workflow_status, "git_status": git_status, "note": note},
    )
    conn.commit()
    return task_info(conn, slug)


def task_add_dependency(conn, slug: str, depends_on_slug: str, relation: str) -> dict:
    now = utc_now_iso()
    conn.execute(
        """
        INSERT OR REPLACE INTO task_dependencies(task_slug, depends_on_slug, relation, created_at)
        VALUES(?, ?, ?, ?)
        """,
        (slug, depends_on_slug, relation, now),
    )
    record_event(
        conn,
        kind="task.dependency",
        summary=f"{slug} now depends on {depends_on_slug}",
        task_slug=slug,
        payload={"depends_on_slug": depends_on_slug, "relation": relation},
    )
    conn.commit()
    return task_info(conn, slug)


def task_add_conflict(conn, slug: str, conflicts_with_slug: str, reason: str | None) -> dict:
    now = utc_now_iso()
    conn.execute(
        """
        INSERT OR REPLACE INTO task_conflicts(task_slug, conflicts_with_slug, reason, created_at)
        VALUES(?, ?, ?, ?)
        """,
        (slug, conflicts_with_slug, reason, now),
    )
    record_event(
        conn,
        kind="task.conflict",
        summary=f"{slug} conflicts with {conflicts_with_slug}",
        task_slug=slug,
        payload={"conflicts_with_slug": conflicts_with_slug, "reason": reason},
    )
    conn.commit()
    return task_info(conn, slug)


def task_add_tags(conn, slug: str, tags: list[str]) -> dict:
    now = utc_now_iso()
    for tag in sorted({tag for tag in tags if tag}):
        conn.execute(
            "INSERT OR IGNORE INTO task_tags(task_slug, tag, created_at) VALUES(?, ?, ?)",
            (slug, tag, now),
        )
    record_event(
        conn,
        kind="task.tag",
        summary=f"tagged task {slug}",
        task_slug=slug,
        payload={"tags": sorted({tag for tag in tags if tag})},
    )
    conn.commit()
    return task_info(conn, slug)


def task_assign(conn, slug: str, agent_name: str, notes: str | None) -> dict:
    row = conn.execute("SELECT repo_path FROM tasks WHERE slug = ?", (slug,)).fetchone()
    if not row:
        return {"ok": False, "error": f"task not found: {slug}"}
    ensure_agent(conn, agent_name)
    now = utc_now_iso()
    conn.execute("UPDATE task_assignments SET active = 0 WHERE task_slug = ?", (slug,))
    conn.execute(
        "UPDATE tasks SET assigned_agent = ?, updated_at = ? WHERE slug = ?",
        (agent_name, now, slug),
    )
    conn.execute(
        """
        INSERT INTO task_assignments(task_slug, agent_name, assigned_at, active, notes)
        VALUES(?, ?, ?, 1, ?)
        """,
        (slug, agent_name, now, notes),
    )
    record_event(
        conn,
        kind="task.assigned",
        summary=f"assigned task {slug} to {agent_name}",
        repo_path=row["repo_path"],
        task_slug=slug,
        actor=agent_name,
        payload={"notes": notes},
    )
    conn.commit()
    return task_info(conn, slug)


def _normalise_files(file_paths: list[str] | None) -> list[str]:
    paths = []
    for path in file_paths or []:
        if not path:
            continue
        paths.append(str(Path(path).expanduser().resolve()))
    return sorted(set(paths))


def _mark_stale_locks(conn) -> None:
    rows = conn.execute(
        "SELECT id, updated_at, lease_seconds FROM locks WHERE status = 'active'"
    ).fetchall()
    now = utc_now()
    stale = []
    for row in rows:
        updated = parse_iso(row["updated_at"])
        if row["lease_seconds"] and (now - updated).total_seconds() > row["lease_seconds"]:
            stale.append(row["id"])
    for lock_id in stale:
        conn.execute("UPDATE locks SET status = 'stale' WHERE id = ?", (lock_id,))
        record_event(
            conn,
            kind="lock.stale",
            summary=f"marked lock {lock_id} stale",
            payload={"lock_id": lock_id},
        )
    if stale:
        conn.commit()


def _lock_rows(
    conn,
    include_inactive: bool,
    repo_path: str | None = None,
    status: str | None = None,
) -> list[dict]:
    _mark_stale_locks(conn)
    clauses = []
    params = []
    query = "SELECT * FROM locks"
    if status is not None:
        if status != "all":
            clauses.append("status = ?")
            params.append(status)
    elif not include_inactive:
        clauses.append("status = 'active'")
    if repo_path:
        clauses.append("repo_path = ?")
        params.append(repo_path)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY started_at"
    rows = []
    for row in conn.execute(query, tuple(params)).fetchall():
        payload = dict(row)
        payload["file_paths"] = json.loads(payload.pop("file_paths_json"))
        payload["rel_files"] = json.loads(payload.pop("rel_files_json", "[]") or "[]")
        rows.append(payload)
    return rows


def _rel_files(repo_path: str | None, files: list[str]) -> list[str]:
    if not repo_path:
        return sorted(set(files or []))
    root = Path(repo_path)
    out = []
    for f in files or []:
        try:
            out.append(Path(f).resolve().relative_to(root.resolve()).as_posix())
        except ValueError:
            out.append(str(f))            # outside the repo -> keep absolute
    return sorted(set(out))


def _locks_conflict(candidate: dict, lock: dict) -> bool:
    if candidate["scope_key"] == lock["scope_key"]:
        return True
    # Cross-box clause FIRST: same logical repo (repo_key) makes a lock
    # meaningful even when the absolute path / box differs. This must be
    # evaluated before the repo_path-mismatch shortcut below, which would
    # otherwise wrongly clear a same-repo-different-box conflict.
    ck, lk = candidate.get("repo_key"), lock.get("repo_key")
    if ck and lk and ck == lk:
        cr = set(candidate.get("rel_files") or [])
        lr = set(lock.get("rel_files") or [])
        if not cr or not lr:
            return True   # a whole-repo lock on the same logical repo
        return bool(cr & lr)
    # Different logical repos (or repo_key unknown): a differing absolute
    # repo_path means no conflict.
    if candidate["repo_path"] and lock["repo_path"] and candidate["repo_path"] != lock["repo_path"]:
        return False
    current_files = set(candidate["file_paths"])
    other_files = set(lock["file_paths"])
    if not current_files or not other_files:
        return bool(candidate["repo_path"]) and candidate["repo_path"] == lock["repo_path"]
    return bool(current_files & other_files)


def lock_check(conn, *, scope_key: str, repo_ref: str | None, files: list[str] | None) -> dict:
    repo_path = None
    if repo_ref:
        repo = resolve_repo(conn, repo_ref)
        repo_path = repo["repo_path"] if repo else repo_ref
        if repo and repo["repo_path"]:
            repo_path = repo["repo_path"]
        elif repo_ref.startswith("/"):
            repo_path = str(Path(repo_ref).expanduser().resolve())
    nf = _normalise_files(files)
    rk = None
    if repo_path:
        rr = conn.execute("SELECT repo_key FROM repos WHERE repo_path = ?",
                          (repo_path,)).fetchone()
        rk = rr["repo_key"] if rr else None
    candidate = {"scope_key": scope_key, "repo_path": repo_path,
                 "file_paths": nf, "repo_key": rk,
                 "rel_files": _rel_files(repo_path, nf)}
    conflicts = [lock for lock in _lock_rows(conn, include_inactive=False) if _locks_conflict(candidate, lock)]
    return {"ok": True, "scope_key": scope_key, "repo_path": repo_path, "conflicts": conflicts}


def lock_acquire(
    conn,
    *,
    scope_key: str,
    repo_ref: str | None,
    lock_kind: str,
    files: list[str] | None,
    agent: str,
    pid: int | None,
    reason: str | None,
    lease_seconds: int,
    eta_minutes: int | None,
    force: bool,
) -> dict:
    repo_path = None
    if repo_ref:
        repo = resolve_repo(conn, repo_ref)
        repo_path = repo["repo_path"] if repo else str(Path(repo_ref).expanduser().resolve())
    files = _normalise_files(files)
    lock_repo_key = None
    if repo_path:
        try:
            lock_repo_key = ensure_repo_key(conn, repo_path)
        except sqlite3.Error:
            rr = conn.execute("SELECT repo_key FROM repos WHERE repo_path=?",
                              (repo_path,)).fetchone()
            lock_repo_key = rr["repo_key"] if rr else None
    rel_files = _rel_files(repo_path, files)
    # Reap expired leases in their own short transaction first.
    _mark_stale_locks(conn)
    try:
        conn.commit()
    except sqlite3.Error:
        pass
    candidate = {"scope_key": scope_key, "repo_path": repo_path,
                 "file_paths": files, "repo_key": lock_repo_key,
                 "rel_files": rel_files}
    # The conflict check + insert MUST be atomic. A plain check-then-insert
    # let concurrent acquirers double-grant the same contended scope
    # (TOCTOU). BEGIN IMMEDIATE takes SQLite's write lock up front, so
    # contenders serialize: the loser re-reads the now-committed winner.
    conn.execute("BEGIN IMMEDIATE")
    try:
        rows = []
        for row in conn.execute("SELECT * FROM locks WHERE status = 'active'").fetchall():
            r = dict(row)
            r["file_paths"] = json.loads(r.pop("file_paths_json"))
            r["rel_files"] = json.loads(r.pop("rel_files_json", "[]") or "[]")
            rows.append(r)
        conflicts = [r for r in rows if _locks_conflict(candidate, r)]
        if conflicts and not force:
            conn.rollback()
            return {"ok": False, "error": "conflicting active lock exists",
                    "conflicts": conflicts}
        now = utc_now_iso()
        eta_finish_at = None
        if eta_minutes is not None:
            eta_finish_at = (utc_now() + timedelta(minutes=eta_minutes)).isoformat(timespec="seconds")
        conn.execute(
            """
            INSERT INTO locks(
                scope_key, repo_path, repo_key, lock_kind, file_paths_json,
                rel_files_json, agent_name, pid, host, reason,
                status, lease_seconds, started_at, updated_at, eta_finish_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)
            """,
            (
                scope_key,
                repo_path,
                lock_repo_key,
                lock_kind,
                json.dumps(files),
                json.dumps(rel_files),
                agent,
                pid if pid is not None else os.getpid(),
                socket.gethostname(),
                reason,
                lease_seconds,
                now,
                now,
                eta_finish_at,
            ),
        )
        lock_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        record_event(
            conn,
            kind="lock.acquired",
            summary=f"acquired {lock_kind} lock {scope_key}",
            repo_path=repo_path,
            actor=agent,
            payload={"lock_id": lock_id, "files": files},
        )
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise
    return lock_info(conn, lock_id)


def lock_renew(conn, lock_id: int, eta_minutes: int | None) -> dict:
    row = conn.execute("SELECT * FROM locks WHERE id = ?", (lock_id,)).fetchone()
    if not row:
        return {"ok": False, "error": f"lock not found: {lock_id}"}
    eta_finish_at = row["eta_finish_at"]
    if eta_minutes is not None:
        eta_finish_at = (utc_now() + timedelta(minutes=eta_minutes)).isoformat(timespec="seconds")
    conn.execute(
        "UPDATE locks SET updated_at = ?, eta_finish_at = ? WHERE id = ?",
        (utc_now_iso(), eta_finish_at, lock_id),
    )
    record_event(
        conn,
        kind="lock.renewed",
        summary=f"renewed lock {lock_id}",
        repo_path=row["repo_path"],
        actor=row["agent_name"],
        payload={"lock_id": lock_id, "eta_finish_at": eta_finish_at},
    )
    conn.commit()
    return lock_info(conn, lock_id)


def lock_release(conn, lock_id: int, *, force: bool) -> dict:
    row = conn.execute("SELECT * FROM locks WHERE id = ?", (lock_id,)).fetchone()
    if not row:
        return {"ok": False, "error": f"lock not found: {lock_id}"}
    if row["status"] != "active" and not force:
        return {"ok": False, "error": f"lock is not active: {lock_id}"}
    now = utc_now_iso()
    conn.execute(
        "UPDATE locks SET status = 'released', released_at = ?, updated_at = ? WHERE id = ?",
        (now, now, lock_id),
    )
    record_event(
        conn,
        kind="lock.released",
        summary=f"released lock {lock_id}",
        repo_path=row["repo_path"],
        actor=row["agent_name"],
        payload={"lock_id": lock_id, "forced": force},
    )
    conn.commit()
    return lock_info(conn, lock_id)


def lock_list(
    conn, *, include_inactive: bool, repo_ref: str | None, status: str | None = None
) -> dict:
    repo_path = None
    if repo_ref:
        repo = resolve_repo(conn, repo_ref)
        repo_path = repo["repo_path"] if repo else str(Path(repo_ref).expanduser().resolve())
    return {
        "ok": True,
        "status_filter": status or ("all" if include_inactive else "active"),
        "locks": _lock_rows(
            conn, include_inactive=include_inactive, repo_path=repo_path, status=status
        ),
    }


def lock_reap(conn) -> dict:
    """A2: explicit lease reaping. Marks active locks whose lease elapsed as
    'stale' (via the same path _lock_rows uses lazily) and reports them, so
    'reap' is an operation an operator/cron can run and observe, not just a
    silent side effect of listing."""
    before = {
        row["id"]
        for row in conn.execute("SELECT id FROM locks WHERE status = 'stale'")
    }
    _mark_stale_locks(conn)
    after_rows = conn.execute(
        "SELECT id, scope_key, agent_name, repo_path FROM locks WHERE status = 'stale'"
    ).fetchall()
    newly = [dict(r) for r in after_rows if r["id"] not in before]
    return {
        "ok": True,
        "message": f"reaped {len(newly)} expired lock(s)",
        "reaped": newly,
    }


def _git_dirty_files(repo_path: str) -> list[str]:
    """Absolute paths of working-tree-dirty files in a repo (porcelain)."""
    try:
        proc = subprocess.run(
            ["git", "-C", repo_path, "status", "--porcelain", "--", "."],
            text=True,
            capture_output=True,
        )
    except OSError:
        return []
    if proc.returncode != 0:
        return []
    out = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        # porcelain: 'XY <path>' or 'XY <old> -> <new>'
        rel = line[3:].strip()
        if " -> " in rel:
            rel = rel.split(" -> ", 1)[1]
        out.append(str((Path(repo_path) / rel).resolve()))
    return sorted(set(out))


def lock_audit(conn, *, repo_ref: str | None, agent: str) -> dict:
    """A1: honest reading of AGENTS.md containment rule 6 -- any working-tree
    change to a file that is NOT covered by an active lock held by the
    current agent is a finding.

      - uncovered : dirty file under no active lock at all (unlocked write)
      - conflict  : dirty file covered only by another agent's active lock

    Emits a lock.audit.* event per finding and returns ok=False when any
    finding exists so callers/CI exit nonzero."""
    repo = _repo_required(conn, repo_ref)
    if not repo:
        return {"ok": False, "error": f"repo not found: {repo_ref}"}
    repo_path = repo["repo_path"]
    dirty = _git_dirty_files(repo_path)
    active = [
        lk
        for lk in _lock_rows(conn, include_inactive=False, repo_path=repo_path)
    ]
    # whole-repo coverage: an active lock on this repo with no explicit files
    repo_holders = {
        lk["agent_name"] for lk in active if not lk["file_paths"]
    }
    file_holders: dict[str, set[str]] = {}
    for lk in active:
        for fp in lk["file_paths"]:
            file_holders.setdefault(fp, set()).add(lk["agent_name"])

    findings = []
    for fp in dirty:
        holders = set(file_holders.get(fp, set())) | repo_holders
        if not holders:
            findings.append({"file": fp, "kind": "uncovered", "holders": []})
        elif agent not in holders:
            findings.append(
                {"file": fp, "kind": "conflict", "holders": sorted(holders)}
            )
        # covered by this agent -> expected, not a finding
    for f in findings:
        record_event(
            conn,
            kind=f"lock.audit.{f['kind']}",
            summary=f"audit {f['kind']}: {f['file']}",
            repo_path=repo_path,
            actor=agent,
            payload=f,
        )
    if findings:
        conn.commit()
    return {
        "ok": not findings,
        "repo": repo,
        "agent": agent,
        "dirty_count": len(dirty),
        "findings": findings,
        "error": None if not findings else f"{len(findings)} lock-audit finding(s)",
    }


def lock_info(conn, lock_id: int) -> dict:
    _mark_stale_locks(conn)


def lock_info(conn, lock_id: int) -> dict:
    _mark_stale_locks(conn)
    row = conn.execute("SELECT * FROM locks WHERE id = ?", (lock_id,)).fetchone()
    if not row:
        return {"ok": False, "error": f"lock not found: {lock_id}"}
    payload = dict(row)
    payload["file_paths"] = json.loads(payload.pop("file_paths_json"))
    return {"ok": True, "lock": payload}


def status_summary(conn, *, repo_ref: str | None) -> dict:
    repo_path = None
    if repo_ref:
        repo = resolve_repo(conn, repo_ref)
        if not repo:
            return {"ok": False, "error": f"repo not found: {repo_ref}"}
        repo_path = repo["repo_path"]
    params = []
    repo_clause = ""
    if repo_path:
        repo_clause = " WHERE repo_path = ?"
        params.append(repo_path)
    tasks = conn.execute(
        f"""
        SELECT workflow_status, COUNT(*) AS count
        FROM tasks
        {repo_clause}
        GROUP BY workflow_status
        ORDER BY workflow_status
        """,
        tuple(params),
    ).fetchall()
    active_locks = conn.execute(
        "SELECT COUNT(*) AS count FROM locks WHERE status = 'active'"
        + (" AND repo_path = ?" if repo_path else ""),
        tuple([repo_path] if repo_path else []),
    ).fetchone()["count"]
    return {
        "ok": True,
        "repo_path": repo_path,
        "counts": {
            "repos": conn.execute("SELECT COUNT(*) AS count FROM repos WHERE third_party = 0").fetchone()["count"],
            "files": conn.execute("SELECT COUNT(*) AS count FROM files").fetchone()["count"],
            "tasks": sum(row["count"] for row in tasks),
            "active_locks": active_locks,
        },
        "tasks_by_workflow_status": dicts(tasks),
    }


def ps_summary(conn, *, repo_ref: str | None) -> dict:
    repo_path = None
    if repo_ref:
        repo = resolve_repo(conn, repo_ref)
        if not repo:
            return {"ok": False, "error": f"repo not found: {repo_ref}"}
        repo_path = repo["repo_path"]
    task_query = """
        SELECT *
        FROM tasks
        WHERE workflow_status NOT IN ('done')
    """
    task_params = []
    lock_query = "SELECT * FROM locks WHERE status = 'active'"
    lock_params = []
    if repo_path:
        task_query += " AND repo_path = ?"
        task_params.append(repo_path)
        lock_query += " AND repo_path = ?"
        lock_params.append(repo_path)
    task_query += " ORDER BY priority, updated_at DESC, slug"
    lock_query += " ORDER BY started_at"
    tasks = dicts(conn.execute(task_query, tuple(task_params)).fetchall())
    locks = []
    for row in conn.execute(lock_query, tuple(lock_params)).fetchall():
        payload = dict(row)
        payload["file_paths"] = json.loads(payload.pop("file_paths_json"))
        locks.append(payload)
    recent = log_tail(conn, limit=8, repo_ref=repo_ref)
    return {
        "ok": True,
        "repo_path": repo_path,
        "active_tasks": tasks,
        "active_locks": locks,
        "recent_events": recent["events"],
    }


def event_mirror_cursor(conn, workspace: str) -> int:
    row = conn.execute(
        "SELECT MAX(remote_id) AS m FROM event_mirror WHERE workspace = ?",
        (workspace,),
    ).fetchone()
    return int(row["m"]) if row and row["m"] is not None else 0


def event_mirror_pull(conn, *, workspace: str, events: list[dict]) -> dict:
    """Append remote events (those with id > our cursor) into the read-only
    mirror. `events` is a list of event_log rows fetched from the remote
    (the CLI supplies them via the SSH dispatch; tests inject directly)."""
    cursor = event_mirror_cursor(conn, workspace)
    now = utc_now_iso()
    added = 0
    max_id = cursor
    for ev in sorted(events, key=lambda e: e.get("id", 0)):
        rid = int(ev.get("id", 0))
        if rid <= cursor:
            continue
        conn.execute(
            """INSERT OR IGNORE INTO event_mirror(workspace, remote_id,
               created_at, kind, repo_path, task_slug, actor, summary,
               payload_json, mirrored_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                workspace, rid, ev.get("created_at") or now,
                ev.get("kind") or "?", ev.get("repo_path"),
                ev.get("task_slug"), ev.get("actor"),
                ev.get("summary") or "", json.dumps(ev.get("payload") or {}),
                now,
            ),
        )
        added += 1
        max_id = max(max_id, rid)
    if added:
        conn.commit()
    return {"ok": True, "workspace": workspace, "pulled": added,
            "cursor": max_id, "message": f"mirrored {added} event(s) from {workspace}"}


def event_mirror_list(conn, *, workspace: str | None, limit: int = 20) -> dict:
    if workspace:
        rows = conn.execute(
            """SELECT * FROM event_mirror WHERE workspace = ?
               ORDER BY remote_id DESC LIMIT ?""",
            (workspace, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM event_mirror ORDER BY mirrored_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return {"ok": True, "mirrored_events": [dict(r) for r in rows]}


def log_tail(conn, *, limit: int, repo_ref: str | None) -> dict:
    repo_path = None
    params = []
    query = "SELECT * FROM event_log"
    if repo_ref:
        repo = resolve_repo(conn, repo_ref)
        if not repo:
            return {"ok": False, "error": f"repo not found: {repo_ref}"}
        repo_path = repo["repo_path"]
        query += " WHERE repo_path = ?"
        params.append(repo_path)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, tuple(params)).fetchall()
    events = []
    for row in rows:
        payload = dict(row)
        payload["payload"] = json.loads(payload.pop("payload_json"))
        events.append(payload)
    events.reverse()
    return {"ok": True, "repo_path": repo_path, "events": events}


def log_since(conn, *, after_id: int, repo_ref: str | None) -> dict:
    repo_path = None
    params = [after_id]
    query = "SELECT * FROM event_log WHERE id > ?"
    if repo_ref:
        repo = resolve_repo(conn, repo_ref)
        if not repo:
            return {"ok": False, "error": f"repo not found: {repo_ref}"}
        repo_path = repo["repo_path"]
        query += " AND repo_path = ?"
        params.append(repo_path)
    query += " ORDER BY id"
    rows = conn.execute(query, tuple(params)).fetchall()
    events = []
    for row in rows:
        payload = dict(row)
        payload["payload"] = json.loads(payload.pop("payload_json"))
        events.append(payload)
    return {"ok": True, "repo_path": repo_path, "events": events}


KNOWN_ARTIFACT_DIRS = ("dist", "build", ".next", "out", "target")
SOURCE_EXTENSIONS = {
    ".py",
    ".rs",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".json",
    ".toml",
    ".yml",
    ".yaml",
    ".md",
    ".html",
    ".css",
    ".scss",
    ".sh",
}

UNIT_KIND_BY_SOURCE = {
    "makefile": "make",
    "package_json": "node",
    "cargo_toml": "rust",
    "pyproject": "python",
    "compose": "compose",
}


def board_snapshot(conn, *, recent_limit: int = 14) -> dict:
    """Pure snapshot for the live board: tasks bucketed by lifecycle
    column, the ready set (all deps done, not started), and the recent
    event tail (incl. task.unblocked transitions)."""
    cols = {"idea": [], "blocked": [], "in_progress": [], "done": []}
    ready = []
    for r in conn.execute(
        "SELECT slug,title,priority,workflow_status,assigned_agent,repo_path "
        "FROM tasks ORDER BY priority, slug"
    ):
        task = dict(r)
        wf = (task["workflow_status"] or "").lower()
        unmet = []
        for d in conn.execute(
            "SELECT depends_on_slug FROM task_dependencies "
            "WHERE task_slug=? AND relation='depends_on'", (task["slug"],)
        ):
            dd = conn.execute(
                "SELECT workflow_status FROM tasks WHERE slug=?",
                (d["depends_on_slug"],)
            ).fetchone()
            if not dd or (dd["workflow_status"] or "").lower() not in _WF_DONE:
                unmet.append(d["depends_on_slug"])
        task["unmet_deps"] = unmet
        if wf in _WF_DONE:
            col = "done"
        elif wf in _WF_INPROG:
            col = "in_progress"
        elif wf in _WF_BLOCKED or unmet:
            col = "blocked"
        else:
            col = "idea"
        cols[col].append(task)
        if col == "idea" and not unmet:
            ready.append(task["slug"])
    recent = dicts(conn.execute(
        "SELECT id,created_at,kind,summary,task_slug,actor FROM event_log "
        "WHERE kind LIKE 'task.%' OR kind LIKE 'lock.%' "
        "ORDER BY id DESC LIMIT ?", (recent_limit,)
    ).fetchall())
    recent.reverse()
    return {"ok": True, "columns": cols, "ready": ready,
            "recent": recent,
            "counts": {k: len(v) for k, v in cols.items()},
            "max_event_id": conn.execute(
                "SELECT COALESCE(MAX(id),0) m FROM event_log"
            ).fetchone()["m"]}


def log_why(conn, slug: str) -> dict:
    """Causality for a task: its event timeline + status history +
    assignments. Reconstructs 'what happened to this slice and when'."""
    task = conn.execute("SELECT * FROM tasks WHERE slug = ?", (slug,)).fetchone()
    if not task:
        return {"ok": False, "error": f"task not found: {slug}"}
    events = dicts(conn.execute(
        "SELECT * FROM event_log WHERE task_slug = ? ORDER BY id", (slug,)
    ).fetchall())
    history = dicts(conn.execute(
        "SELECT * FROM task_status_history WHERE task_slug = ? ORDER BY changed_at",
        (slug,),
    ).fetchall())
    assignments = dicts(conn.execute(
        "SELECT * FROM task_assignments WHERE task_slug = ? ORDER BY assigned_at",
        (slug,),
    ).fetchall())
    return {"ok": True, "task": dict(task), "events": events,
            "history": history, "assignments": assignments}


def log_blame(conn, file_path: str) -> dict:
    """Causality for a file: which tasks declared it, which locks covered
    it, and the repo events around it -- 'who touched this, under which
    task, holding which lock, when'."""
    fp = str(Path(file_path).expanduser().resolve())
    file_row = conn.execute(
        "SELECT * FROM files WHERE file_path = ?", (fp,)
    ).fetchone()
    tasks = dicts(conn.execute(
        """SELECT t.slug, t.title, t.workflow_status, tf.role, tf.created_at
           FROM task_files tf JOIN tasks t ON t.slug = tf.task_slug
           WHERE tf.file_path = ? ORDER BY tf.created_at""",
        (fp,),
    ).fetchall())
    locks = []
    for lk in _lock_rows(conn, include_inactive=True):
        if fp in lk["file_paths"]:
            locks.append({
                "id": lk["id"], "agent_name": lk["agent_name"],
                "lock_kind": lk["lock_kind"], "status": lk["status"],
                "started_at": lk["started_at"], "scope_key": lk["scope_key"],
            })
    repo_path = file_row["repo_path"] if file_row else None
    events = []
    if repo_path:
        events = dicts(conn.execute(
            "SELECT * FROM event_log WHERE repo_path = ? ORDER BY id DESC LIMIT 50",
            (repo_path,),
        ).fetchall())
    return {"ok": True, "file_path": fp, "repo_path": repo_path,
            "tasks": tasks, "locks": locks, "events": events}


def _repo_required(conn, repo_ref: str) -> dict | None:
    repo = resolve_repo(conn, repo_ref)
    if not repo:
        return None
    if not conn.execute("SELECT 1 FROM repos WHERE repo_path = ?", (repo["repo_path"],)).fetchone():
        register_repo(
            conn,
            repo_path=repo["repo_path"],
            name=repo.get("name"),
            kind=repo.get("kind"),
            status="active",
            third_party=bool(repo.get("third_party", 0)),
            notes=repo.get("notes"),
        )
        repo = resolve_repo(conn, repo["repo_path"])
    return repo


def _manifest_candidates(repo_path: Path) -> list[tuple[str, Path]]:
    candidates: list[tuple[str, Path]] = []
    names = [
        ("taskfile", "Taskfile.yml"),
        ("taskfile", "Taskfile.yaml"),
        ("justfile", "justfile"),
        ("justfile", "Justfile"),
        ("mise", "mise.toml"),
        ("mise", ".mise.toml"),
        ("makefile", "Makefile"),
        ("package_json", "package.json"),
        ("cargo_toml", "Cargo.toml"),
        ("pyproject", "pyproject.toml"),
        ("compose", "docker-compose.yml"),
        ("compose", "compose.yml"),
        ("compose", "docker-compose.yaml"),
        ("compose", "compose.yaml"),
    ]
    for kind, name in names:
        for path in [repo_path / name, *repo_path.glob(f"*/{name}")]:
            if path.exists():
                candidates.append((kind, path))
    return sorted({(kind, path.resolve()) for kind, path in candidates}, key=lambda item: str(item[1]))


def _upsert_unit(
    conn,
    *,
    repo_path: str,
    unit_path: str,
    rel_path: str,
    kind: str,
    discovery_source: str,
) -> None:
    now = utc_now_iso()
    name = Path(unit_path).name if rel_path != "." else "."
    conn.execute(
        """
        INSERT INTO units(unit_path, repo_path, name, rel_path, kind, status, discovery_source, created_at, updated_at)
        VALUES(?, ?, ?, ?, ?, 'active', ?, ?, ?)
        ON CONFLICT(unit_path) DO UPDATE SET
            repo_path = excluded.repo_path,
            name = excluded.name,
            rel_path = excluded.rel_path,
            kind = excluded.kind,
            discovery_source = excluded.discovery_source,
            updated_at = excluded.updated_at
        """,
        (unit_path, repo_path, name, rel_path, kind, discovery_source, now, now),
    )


def unit_discover(conn, *, repo_ref: str | None = None, all_repos: bool = False) -> dict:
    repos: list[dict]
    if all_repos:
        repos = repo_list(conn, include_third_party=True)["repos"]
    else:
        if not repo_ref:
            inferred = infer_repo_from_cwd(conn)
            if not inferred:
                return {"ok": False, "error": "no repo specified and cwd is not inside a known repo"}
            repo_ref = inferred["repo_path"]
        repo = _repo_required(conn, repo_ref)
        if not repo:
            return {"ok": False, "error": f"repo not found: {repo_ref}"}
        repos = [repo]

    discovered: list[dict] = []
    for repo in repos:
        repo_root = Path(repo["repo_path"])
        conn.execute("DELETE FROM units WHERE repo_path = ?", (repo["repo_path"],))
        by_dir: dict[Path, set[str]] = {}
        for source_kind, path in _manifest_candidates(repo_root):
            by_dir.setdefault(path.parent.resolve(), set()).add(source_kind)
        for workdir, source_kinds in sorted(by_dir.items(), key=lambda item: str(item[0])):
            rel_path = "."
            if workdir != repo_root:
                rel_path = workdir.relative_to(repo_root).as_posix()
            if any(part in DISCOVERY_EXCLUDED_DIRS or part == ".claude" for part in workdir.parts):
                continue
            if workdir != repo_root and ".git" in workdir.parts:
                continue
            kind = "+".join(sorted(UNIT_KIND_BY_SOURCE.get(kind, kind) for kind in source_kinds))
            _upsert_unit(
                conn,
                repo_path=repo["repo_path"],
                unit_path=str(workdir),
                rel_path=rel_path,
                kind=kind,
                discovery_source="manifest",
            )
        repo_units = unit_list(conn, repo_ref=repo["repo_path"])["units"]
        discovered.extend(repo_units)
        record_event(
            conn,
            kind="unit.discovered",
            summary=f"discovered {len(repo_units)} units in {repo['name']}",
            repo_path=repo["repo_path"],
            payload={"unit_count": len(repo_units)},
        )
    conn.commit()
    return {"ok": True, "units": discovered}


def unit_list(conn, *, repo_ref: str | None) -> dict:
    clauses = []
    params: list[str] = []
    repo_path = None
    if repo_ref:
        repo = resolve_repo(conn, repo_ref)
        if not repo:
            return {"ok": False, "error": f"repo not found: {repo_ref}"}
        repo_path = repo["repo_path"]
        clauses.append("repo_path = ?")
        params.append(repo_path)
    query = "SELECT * FROM units"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY repo_path, rel_path"
    rows = dicts(conn.execute(query, tuple(params)).fetchall())
    for row in rows:
        row.update(path_metadata(row["unit_path"]))
        row.update(path_activity(row["unit_path"]))
    return {"ok": True, "repo_path": repo_path, "units": rows}


def _insert_target(
    conn,
    *,
    repo_path: str,
    target_kind: str,
    name: str,
    command: str,
    workdir: str,
    runner: str,
    source: str,
    confidence: float,
    artifact_paths: list[str] | None = None,
    notes: str | None = None,
    needs_lock: bool = False,
    lock_kind: str | None = None,
    destructive: bool = False,
    network_required: bool = False,
) -> None:
    now = utc_now_iso()
    conn.execute(
        """
        INSERT OR REPLACE INTO repo_targets(
            repo_path, target_kind, name, command, workdir, runner, source,
            confidence, aggregate, needs_lock, lock_kind, destructive,
            network_required, artifact_paths_json, notes, created_at, updated_at
        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            repo_path,
            target_kind,
            name,
            command,
            workdir,
            runner,
            source,
            confidence,
            int(needs_lock),
            lock_kind,
            int(destructive),
            int(network_required),
            json.dumps(sorted(set(artifact_paths or []))),
            notes,
            now,
            now,
        ),
    )
    for artifact_path in sorted(set(artifact_paths or [])):
        conn.execute(
            """
            INSERT OR REPLACE INTO repo_artifacts(
                repo_path, artifact_name, path_hint, source, target_kind, target_name, created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                repo_path,
                f"{target_kind}:{name}",
                artifact_path,
                source,
                target_kind,
                name,
                now,
                now,
            ),
        )


def _record_detection_source(conn, *, repo_path: str, source_kind: str, source_path: Path, payload: dict) -> None:
    conn.execute(
        """
        INSERT INTO repo_detection_sources(repo_path, source_kind, source_path, scanned_at, payload_json)
        VALUES(?, ?, ?, ?, ?)
        """,
        (repo_path, source_kind, str(source_path), utc_now_iso(), json.dumps(payload, sort_keys=True)),
    )


def _make_target_name(repo_root: Path, workdir: Path, label: str) -> str:
    if workdir == repo_root:
        return label
    rel = workdir.relative_to(repo_root).as_posix().replace("/", ":")
    return f"{rel}:{label}"


_RUNNER_KIND_MAP = {"build":"build","clean":"clean","test":"test","lint":"lint","check":"check","verify":"verify","deploy":"deploy","dev":"dev","start":"start","ci":"verify","fmt":"lint","format":"lint","typecheck":"check"}


def _runner_target_kind(name: str) -> tuple[str, float]:
    key = name.strip().lower()
    if key in _RUNNER_KIND_MAP:
        # Above make (0.98) / npm (0.95): a declared runner is explicit
        # operator intent, so it must outrank re-derived manifest targets.
        return _RUNNER_KIND_MAP[key], 0.99
    return "task", 0.85


def _emit_runner_targets(conn, repo_root: Path, path: Path, *, runner: str,
                         invoke: str, names: list[str]) -> None:
    _record_detection_source(
        conn, repo_path=str(repo_root), source_kind=runner,
        source_path=path, payload={"recipes": names},
    )
    for nm in names:
        kind, conf = _runner_target_kind(nm)
        _insert_target(
            conn,
            repo_path=str(repo_root),
            target_kind=kind,
            name=_make_target_name(repo_root, path.parent, f"{runner}:{nm}"),
            command=f"{invoke} {nm}",
            workdir=str(path.parent),
            runner=runner,
            source=str(path),
            confidence=conf,
            notes=f"delegated to {runner}",
        )


def _detect_from_taskfile(conn, repo_root: Path, path: Path) -> None:
    # stdlib-only: collect keys under a top-level `tasks:` mapping.
    text = path.read_text(errors="ignore").splitlines()
    names, in_tasks = [], False
    for line in text:
        if re.match(r"^tasks:\s*$", line):
            in_tasks = True
            continue
        if in_tasks:
            if re.match(r"^\S", line):  # dedent to col 0 ends the block
                if not re.match(r"^\s", line):
                    in_tasks = False
                    continue
            m = re.match(r"^  ([A-Za-z0-9_:.-]+):\s*", line)
            if m:
                names.append(m.group(1))
    _emit_runner_targets(conn, repo_root, path, runner="task",
                         invoke="task", names=sorted(set(names)))


def _detect_from_justfile(conn, repo_root: Path, path: Path) -> None:
    names = []
    for line in path.read_text(errors="ignore").splitlines():
        if not line or line[0] in " \t#":
            continue
        if line.startswith(("set ", "export ", "alias ")):
            continue
        m = re.match(r"^([A-Za-z0-9_-]+)\s*(\([^)]*\))?\s*:", line)
        if m and "=" not in line.split(":", 1)[0]:
            names.append(m.group(1))
    _emit_runner_targets(conn, repo_root, path, runner="just",
                         invoke="just", names=sorted(set(names)))


def _detect_from_mise(conn, repo_root: Path, path: Path) -> None:
    names = []
    for line in path.read_text(errors="ignore").splitlines():
        m = re.match(r"^\[tasks\.([A-Za-z0-9_:.-]+)\]\s*$", line)
        if m:
            names.append(m.group(1))
    _emit_runner_targets(conn, repo_root, path, runner="mise",
                         invoke="mise run", names=sorted(set(names)))


def _detect_from_makefile(conn, repo_root: Path, path: Path) -> None:
    text = path.read_text(errors="ignore")
    targets = sorted(
        {
            match.group(1)
            for match in re.finditer(r"^([A-Za-z0-9_.-]+)\s*:", text, flags=re.MULTILINE)
            if not match.group(1).startswith(".")
        }
    )
    _record_detection_source(
        conn,
        repo_path=str(repo_root),
        source_kind="makefile",
        source_path=path,
        payload={"targets": targets},
    )
    interesting = {
        "build": "build",
        "clean": "clean",
        "test": "test",
        "lint": "lint",
        "check": "check",
        "verify": "verify",
        "deploy": "deploy",
    }
    for target, kind in interesting.items():
        if target in targets:
            artifact_paths = []
            if target == "build":
                artifact_paths = [str(path.parent / entry) for entry in KNOWN_ARTIFACT_DIRS]
            _insert_target(
                conn,
                repo_path=str(repo_root),
                target_kind=kind,
                name=_make_target_name(repo_root, path.parent, target),
                command=f"make {target}",
                workdir=str(path.parent),
                runner="make",
                source=str(path),
                confidence=0.98,
                artifact_paths=artifact_paths,
            )


def _detect_from_package_json(conn, repo_root: Path, path: Path) -> None:
    payload = json.loads(path.read_text())
    scripts = payload.get("scripts", {})
    _record_detection_source(
        conn,
        repo_path=str(repo_root),
        source_kind="package_json",
        source_path=path,
        payload={"scripts": scripts},
    )
    script_map = {
        "build": "build",
        "clean": "clean",
        "test": "test",
        "lint": "lint",
        "check": "check",
        "verify": "verify",
        "deploy": "deploy",
        "dev": "dev",
        "start": "start",
        "types": "build",
        "typecheck": "check",
    }
    for script_name, command_kind in script_map.items():
        if script_name not in scripts:
            continue
        artifact_paths = []
        if command_kind == "build":
            artifact_paths = [str(path.parent / entry) for entry in ("dist", "build", ".next", "out")]
        _insert_target(
            conn,
            repo_path=str(repo_root),
            target_kind=command_kind,
            name=_make_target_name(repo_root, path.parent, script_name),
            command=f"npm run {script_name}",
            workdir=str(path.parent),
            runner="npm",
            source=str(path),
            confidence=0.95 if script_name in {"build", "clean", "test", "lint", "deploy"} else 0.72,
            artifact_paths=artifact_paths,
            network_required=script_name in {"deploy", "dev"},
        )


def _detect_from_cargo_toml(conn, repo_root: Path, path: Path) -> None:
    payload = tomllib.loads(path.read_text())
    _record_detection_source(
        conn,
        repo_path=str(repo_root),
        source_kind="cargo_toml",
        source_path=path,
        payload={"keys": sorted(payload.keys())},
    )
    workdir = path.parent
    label = "cargo" if workdir == repo_root else workdir.name
    artifact_paths = [str(workdir / "target")]
    specs = [
        ("build", "cargo build", 0.96, artifact_paths, False),
        ("clean", "cargo clean", 0.96, artifact_paths, True),
        ("test", "cargo test", 0.96, [], False),
        ("lint", "cargo fmt --all --check", 0.85, [], False),
        ("check", "cargo check", 0.93, [], False),
        ("verify", "cargo test", 0.7, [], False),
    ]
    for kind, command, confidence, artifacts, destructive in specs:
        _insert_target(
            conn,
            repo_path=str(repo_root),
            target_kind=kind,
            name=_make_target_name(repo_root, workdir, label),
            command=command,
            workdir=str(workdir),
            runner="cargo",
            source=str(path),
            confidence=confidence,
            artifact_paths=artifacts,
            destructive=destructive,
        )


def _detect_from_pyproject(conn, repo_root: Path, path: Path) -> None:
    payload = tomllib.loads(path.read_text())
    _record_detection_source(
        conn,
        repo_path=str(repo_root),
        source_kind="pyproject",
        source_path=path,
        payload={"keys": sorted(payload.keys())},
    )
    tool = payload.get("tool", {})
    deps = payload.get("dependency-groups", {})
    if "pytest" in json.dumps(deps) or "pytest" in json.dumps(tool):
        _insert_target(
            conn,
            repo_path=str(repo_root),
            target_kind="test",
            name=_make_target_name(repo_root, path.parent, "pytest"),
            command="uv run pytest",
            workdir=str(path.parent),
            runner="uv",
            source=str(path),
            confidence=0.78,
        )
    _insert_target(
        conn,
        repo_path=str(repo_root),
        target_kind="check",
        name=_make_target_name(repo_root, path.parent, "compileall"),
        command="python3 -m compileall .",
        workdir=str(path.parent),
        runner="python",
        source=str(path),
        confidence=0.65,
    )


def _detect_from_compose(conn, repo_root: Path, path: Path) -> None:
    _record_detection_source(
        conn,
        repo_path=str(repo_root),
        source_kind="compose",
        source_path=path,
        payload={"file": path.name},
    )
    _insert_target(
        conn,
        repo_path=str(repo_root),
        target_kind="build",
        name=_make_target_name(repo_root, path.parent, path.stem),
        command=f"docker compose -f {path.name} build",
        workdir=str(path.parent),
        runner="docker-compose",
        source=str(path),
        confidence=0.88,
        needs_lock=True,
        lock_kind="build",
    )


def repo_scan(conn, repo_ref: str) -> dict:
    repo = _repo_required(conn, repo_ref)
    if not repo:
        return {"ok": False, "error": f"repo not found: {repo_ref}"}
    repo_root = Path(repo["repo_path"])
    conn.execute("DELETE FROM repo_targets WHERE repo_path = ?", (repo["repo_path"],))
    conn.execute("DELETE FROM repo_artifacts WHERE repo_path = ?", (repo["repo_path"],))
    conn.execute("DELETE FROM repo_detection_sources WHERE repo_path = ?", (repo["repo_path"],))
    for kind, path in _manifest_candidates(repo_root):
        if kind == "taskfile":
            _detect_from_taskfile(conn, repo_root, path)
        elif kind == "justfile":
            _detect_from_justfile(conn, repo_root, path)
        elif kind == "mise":
            _detect_from_mise(conn, repo_root, path)
        elif kind == "makefile":
            _detect_from_makefile(conn, repo_root, path)
        elif kind == "package_json":
            _detect_from_package_json(conn, repo_root, path)
        elif kind == "cargo_toml":
            _detect_from_cargo_toml(conn, repo_root, path)
        elif kind == "pyproject":
            _detect_from_pyproject(conn, repo_root, path)
        elif kind == "compose":
            _detect_from_compose(conn, repo_root, path)
    record_event(
        conn,
        kind="repo.scanned",
        summary=f"scanned repo {repo['name']}",
        repo_path=repo["repo_path"],
        payload={"sources": len(_manifest_candidates(repo_root))},
    )
    conn.commit()
    return repo_targets(conn, repo_ref)


def repo_targets(conn, repo_ref: str) -> dict:
    repo = resolve_repo(conn, repo_ref)
    if not repo:
        return {"ok": False, "error": f"repo not found: {repo_ref}"}
    rows = conn.execute(
        "SELECT * FROM repo_targets WHERE repo_path = ? ORDER BY target_kind, confidence DESC, name",
        (repo["repo_path"],),
    ).fetchall()
    targets = []
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        payload = dict(row)
        payload["artifact_paths"] = json.loads(payload.pop("artifact_paths_json"))
        targets.append(payload)
        grouped.setdefault(payload["target_kind"], []).append(payload)
    aggregates = []
    for kind, items in sorted(grouped.items()):
        aggregates.append(
            {
                "repo_path": repo["repo_path"],
                "target_kind": kind,
                "name": kind,
                "command": None,
                "workdir": repo["repo_path"],
                "runner": "aggregate",
                "source": "frog-scan",
                "confidence": max(item["confidence"] for item in items),
                "aggregate": 1,
                "artifact_paths": sorted(
                    {artifact for item in items for artifact in item["artifact_paths"]}
                ),
                "children": [item["name"] for item in items],
            }
        )
    return {"ok": True, "repo": repo, "targets": [*aggregates, *targets]}


def _source_latest_mtime(workdir: Path) -> float:
    latest = 0.0
    for path in workdir.rglob("*"):
        if not path.is_file():
            continue
        if any(part in {"node_modules", ".git", "__pycache__", "dist", "build", ".next", "out", "target"} for part in path.parts):
            continue
        if path.suffix and path.suffix not in SOURCE_EXTENSIONS:
            continue
        try:
            latest = max(latest, path.stat().st_mtime)
        except OSError:
            pass
    return latest


def repo_artifacts(conn, repo_ref: str) -> dict:
    repo = resolve_repo(conn, repo_ref)
    if not repo:
        return {"ok": False, "error": f"repo not found: {repo_ref}"}
    rows = conn.execute(
        "SELECT * FROM repo_artifacts WHERE repo_path = ? ORDER BY artifact_name, path_hint",
        (repo["repo_path"],),
    ).fetchall()
    artifacts = []
    for row in rows:
        payload = dict(row)
        artifact_path = Path(payload["path_hint"])
        payload["exists"] = artifact_path.exists()
        if artifact_path.exists():
            try:
                payload["mtime"] = artifact_path.stat().st_mtime
            except OSError:
                payload["mtime"] = None
        artifacts.append(payload)
    return {"ok": True, "repo": repo, "artifacts": artifacts}


def repo_artifacts_stale(conn, repo_ref: str) -> dict:
    repo = resolve_repo(conn, repo_ref)
    if not repo:
        return {"ok": False, "error": f"repo not found: {repo_ref}"}
    all_artifacts = repo_artifacts(conn, repo_ref)
    if not all_artifacts["ok"]:
        return all_artifacts
    latest_source = _source_latest_mtime(Path(repo["repo_path"]))
    stale = []
    for artifact in all_artifacts["artifacts"]:
        artifact_path = Path(artifact["path_hint"])
        exists = artifact_path.exists()
        artifact["stale"] = (not exists) or (
            exists and artifact.get("mtime") is not None and artifact["mtime"] < latest_source
        )
        if artifact["stale"]:
            stale.append(artifact)
    return {"ok": True, "repo": repo, "artifacts": stale, "source_latest_mtime": latest_source}


def _targets_for_kind(conn, repo_path: str, target_kind: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT * FROM repo_targets
        WHERE repo_path = ? AND target_kind = ?
        ORDER BY confidence DESC, workdir, name
        """,
        (repo_path, target_kind),
    ).fetchall()
    targets = []
    for row in rows:
        payload = dict(row)
        payload["artifact_paths"] = json.loads(payload.pop("artifact_paths_json"))
        targets.append(payload)
    return targets


def _target_input_hash(target: dict) -> str:
    """Deterministic fingerprint of a target's inputs: the command plus the
    state of its workdir. Prefers git object ids (cheap, exact); falls back
    to source mtimes for non-git trees."""
    h = hashlib.sha256()
    h.update(("cmd:" + (target.get("command") or "")).encode())
    h.update(("wd:" + (target.get("workdir") or "")).encode())
    wd = target.get("workdir") or "."
    used_git = False
    try:
        inside = subprocess.run(
            ["git", "-C", wd, "rev-parse", "--is-inside-work-tree"],
            text=True, capture_output=True,
        )
        if inside.returncode == 0 and inside.stdout.strip() == "true":
            used_git = True
            tree = subprocess.run(
                ["git", "-C", wd, "ls-files", "-s", "--", "."],
                text=True, capture_output=True,
            )
            h.update(b"tree:")
            h.update(tree.stdout.encode())
            # include unstaged/dirty content so a working-tree edit busts cache
            dirty = subprocess.run(
                ["git", "-C", wd, "diff", "--", "."],
                text=True, capture_output=True,
            )
            h.update(b"dirty:")
            h.update(dirty.stdout.encode())
            untr = subprocess.run(
                ["git", "-C", wd, "ls-files", "-o", "--exclude-standard", "--", "."],
                text=True, capture_output=True,
            )
            h.update(b"untracked:")
            h.update(untr.stdout.encode())
    except OSError:
        used_git = False
    if not used_git:
        h.update(("mtime:%r" % _source_latest_mtime(Path(wd))).encode())
    return h.hexdigest()


def _cached_success(conn, repo_path: str, target: dict, input_hash: str) -> bool:
    row = conn.execute(
        """
        SELECT returncode FROM target_runs
        WHERE repo_path = ? AND target_kind = ? AND target_name = ?
          AND workdir = ? AND command = ? AND input_hash = ?
        ORDER BY id DESC LIMIT 1
        """,
        (
            repo_path,
            target["target_kind"] if "target_kind" in target else target.get("_kind", ""),
            target["name"],
            target["workdir"],
            target["command"],
            input_hash,
        ),
    ).fetchone()
    return bool(row) and row["returncode"] == 0


def _changed_files(repo_path: str, since: str | None) -> list[str]:
    """Absolute paths changed in the repo. With `since`: diff since that ref.
    Without: working-tree changes vs HEAD + untracked files."""
    out: set[str] = set()
    try:
        if since:
            d = subprocess.run(
                ["git", "-C", repo_path, "diff", "--name-only", since, "--", "."],
                text=True, capture_output=True,
            )
            for rel in d.stdout.splitlines():
                if rel.strip():
                    out.add(str((Path(repo_path) / rel.strip()).resolve()))
        else:
            for fp in _git_dirty_files(repo_path):
                out.add(fp)
    except OSError:
        pass
    return sorted(out)


def _targets_for_files(conn, repo_path: str, files: list[str]) -> list[dict]:
    """Targets whose workdir is an ancestor of any changed file. If a change
    is outside every target workdir (e.g. repo-root config), all targets are
    considered affected -- the safe over-approximation."""
    rows = conn.execute(
        "SELECT * FROM repo_targets WHERE repo_path = ? ORDER BY target_kind, name",
        (repo_path,),
    ).fetchall()
    targets = []
    for row in rows:
        payload = dict(row)
        payload["artifact_paths"] = json.loads(payload.pop("artifact_paths_json"))
        targets.append(payload)
    if not targets:
        return []
    if not files:
        return []  # nothing changed -> nothing affected
    affected = []
    unmatched = list(files)
    for tgt in targets:
        wd = str(Path(tgt["workdir"]).resolve())
        hit = False
        for f in files:
            try:
                Path(f).resolve().relative_to(wd)
                hit = True
                if f in unmatched:
                    unmatched.remove(f)
            except ValueError:
                continue
        if hit:
            affected.append(tgt)
    if unmatched:
        # changes not under any target workdir -> can't prove unaffected
        return targets
    return affected


def repo_dep_add(conn, dependent_ref: str, dependency_ref: str,
                  note: str | None = None) -> dict:
    dep = _repo_required(conn, dependent_ref)
    on = _repo_required(conn, dependency_ref)
    if not dep:
        return {"ok": False, "error": f"repo not found: {dependent_ref}"}
    if not on:
        return {"ok": False, "error": f"repo not found: {dependency_ref}"}
    if dep["repo_path"] == on["repo_path"]:
        return {"ok": False, "error": "a repo cannot depend on itself"}
    conn.execute(
        """INSERT OR REPLACE INTO repo_deps(dependent_repo_path,
           dependency_repo_path, note, created_at) VALUES(?,?,?,?)""",
        (dep["repo_path"], on["repo_path"], note, utc_now_iso()),
    )
    record_event(
        conn, kind="repo.dep.added",
        summary=f"{dep['name']} depends on {on['name']}",
        repo_path=dep["repo_path"],
        payload={"dependency": on["repo_path"], "note": note},
    )
    conn.commit()
    return {"ok": True, "message": f"{dep['name']} -> {on['name']}",
            "dependent": dep["repo_path"], "dependency": on["repo_path"]}


def repo_dep_list(conn, repo_ref: str | None = None) -> dict:
    if repo_ref:
        repo = _repo_required(conn, repo_ref)
        if not repo:
            return {"ok": False, "error": f"repo not found: {repo_ref}"}
        rows = conn.execute(
            """SELECT * FROM repo_deps
               WHERE dependent_repo_path = ? OR dependency_repo_path = ?
               ORDER BY dependent_repo_path""",
            (repo["repo_path"], repo["repo_path"]),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM repo_deps ORDER BY dependent_repo_path"
        ).fetchall()
    return {"ok": True, "deps": [dict(r) for r in rows]}


def _dependents_of(conn, dependency_repo_path: str) -> list[str]:
    rows = conn.execute(
        "SELECT dependent_repo_path FROM repo_deps WHERE dependency_repo_path = ?",
        (dependency_repo_path,),
    ).fetchall()
    return [r["dependent_repo_path"] for r in rows]


def repo_diff(conn, repo_ref: str, *, stat_only: bool = False,
              include_tasks: bool = False, include_impact: bool = False) -> dict:
    repo = _repo_required(conn, repo_ref)
    if not repo:
        return {"ok": False, "error": f"repo not found: {repo_ref}"}
    rp = repo["repo_path"]
    args = ["git", "-C", rp, "diff", "--stat" if stat_only else "--patch", "HEAD", "--", "."]
    try:
        proc = subprocess.run(args, text=True, capture_output=True)
        diff = proc.stdout if proc.returncode == 0 else ""
    except OSError:
        diff = ""
    out = {"ok": True, "repo": repo, "diff": diff}
    if include_tasks:
        out["tasks"] = task_list(
            conn, repo_ref=rp, workflow_status=None, assigned_agent=None
        ).get("tasks", [])
    if include_impact:
        out["impacted_targets"] = _targets_for_files(
            conn, rp, _changed_files(rp, None)
        )
    return out


def repo_affected(conn, repo_ref: str, *, since: str | None = None,
                  target_kind: str | None = None) -> dict:
    repo = _repo_required(conn, repo_ref)
    if not repo:
        return {"ok": False, "error": f"repo not found: {repo_ref}"}
    rp = repo["repo_path"]
    if not conn.execute(
        "SELECT 1 FROM repo_targets WHERE repo_path = ? LIMIT 1", (rp,)
    ).fetchone():
        repo_scan(conn, repo_ref)
    changed = _changed_files(rp, since)
    affected = _targets_for_files(conn, rp, changed)
    if target_kind:
        affected = [t for t in affected if t["target_kind"] == target_kind]
    result = {
        "ok": True,
        "repo": repo,
        "since": since,
        "changed_files": changed,
        "affected": affected,
    }
    if changed:
        downstream = []
        for dep_path in _dependents_of(conn, rp):
            drepo = resolve_repo(conn, dep_path)
            if not drepo:
                continue
            dtargets = _targets_for_files(conn, dep_path, ["__upstream_changed__"])
            if target_kind:
                dtargets = [t for t in dtargets if t["target_kind"] == target_kind]
            downstream.append({"repo": drepo, "targets": dtargets})
        if downstream:
            result["downstream"] = downstream
    return result


def repo_run(conn, repo_ref: str, target_kind: str, *, use_cache: bool = True, only_targets: set | None = None) -> dict:
    repo = _repo_required(conn, repo_ref)
    if not repo:
        return {"ok": False, "error": f"repo not found: {repo_ref}"}
    if not conn.execute(
        "SELECT 1 FROM repo_targets WHERE repo_path = ? LIMIT 1", (repo["repo_path"],)
    ).fetchone():
        repo_scan(conn, repo_ref)
    targets = _targets_for_kind(conn, repo["repo_path"], target_kind)
    if not targets:
        return {
            "ok": False,
            "error": f"no {target_kind} targets detected for {repo['name']}",
            "repo": repo,
        }
    results = []
    ok = True
    if only_targets is not None:
        targets = [t for t in targets if t["name"] in only_targets]
        if not targets:
            return {"ok": True, "repo": repo, "results": [],
                    "message": "no affected targets for this kind"}
    for target in targets:
        target = dict(target)
        target["target_kind"] = target_kind
        input_hash = _target_input_hash(target)
        if use_cache and _cached_success(conn, repo["repo_path"], target, input_hash):
            results.append({
                "name": target["name"],
                "target_kind": target_kind,
                "command": target["command"],
                "workdir": target["workdir"],
                "returncode": 0,
                "status": "cached",
                "stdout": "",
                "stderr": "",
            })
            conn.execute(
                """INSERT INTO target_runs(repo_path,target_kind,target_name,
                   workdir,command,input_hash,returncode,status,duration_ms,ran_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (repo["repo_path"], target_kind, target["name"], target["workdir"],
                 target["command"], input_hash, 0, "cached", 0, utc_now_iso()),
            )
            record_event(
                conn,
                kind=f"repo.run.{target_kind}",
                summary=f"cached {target_kind} target {target['name']} (unchanged)",
                repo_path=repo["repo_path"],
                payload={"command": target["command"], "cached": True},
            )
            continue
        started = time.monotonic()
        proc = subprocess.run(
            target["command"], cwd=target["workdir"], shell=True,
            text=True, capture_output=True,
        )
        duration_ms = int((time.monotonic() - started) * 1000)
        if proc.returncode != 0:
            ok = False
        results.append({
            "name": target["name"],
            "target_kind": target_kind,
            "command": target["command"],
            "workdir": target["workdir"],
            "returncode": proc.returncode,
            "status": "ran",
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        })
        conn.execute(
            """INSERT INTO target_runs(repo_path,target_kind,target_name,
               workdir,command,input_hash,returncode,status,duration_ms,ran_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (repo["repo_path"], target_kind, target["name"], target["workdir"],
             target["command"], input_hash, proc.returncode, "ran",
             duration_ms, utc_now_iso()),
        )
        record_event(
            conn,
            kind=f"repo.run.{target_kind}",
            summary=f"ran {target_kind} target {target['name']}",
            repo_path=repo["repo_path"],
            payload={
                "command": target["command"],
                "workdir": target["workdir"],
                "returncode": proc.returncode,
                "duration_ms": duration_ms,
            },
        )
    conn.commit()
    return {"ok": ok, "repo": repo, "results": results}
