from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from ragbaz_frog import DEFAULT_CONFIG_PATH
from ragbaz_frog import store


def config_path(path: str | None = None) -> Path:
    return Path(path or DEFAULT_CONFIG_PATH).expanduser().resolve()


def infer_local_root() -> str:
    override = os.environ.get("RAGBAZ_SRC_ROOT")
    if override:
        return str(Path(override).expanduser().resolve())
    repo_root = Path(__file__).resolve().parents[3]
    if (repo_root / "ragbaz-frog").exists():
        return str(repo_root)
    return str(Path.cwd().resolve())


def default_config() -> dict:
    root = infer_local_root()
    return {
        "hosts": {
            "local": {
                "name": "local",
                "transport": "local",
            }
        },
        "workspaces": {
            "local-src": {
                "name": "local-src",
                "host": "local",
                "root": root,
                "db": f"{root.rstrip('/')}/AGENTS.db",
                "notes": "auto-created on first frog run",
            }
        },
        "current_workspace": "local-src",
        "federation": {
            "coordinator_workspace": "local-src",
        },
    }


def save_config(payload: dict, path: str | None = None) -> str:
    target = config_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return str(target)


def ensure_config(path: str | None = None) -> dict:
    target = config_path(path)
    changed = False
    if target.exists():
        payload = json.loads(target.read_text(encoding="utf-8"))
    else:
        payload = default_config()
        save_config(payload, path)
    payload.setdefault("hosts", {})
    payload.setdefault("workspaces", {})
    payload.setdefault("current_workspace", None)
    payload["hosts"].setdefault("local", {"name": "local", "transport": "local"})
    if not payload["workspaces"]:
        payload.update(default_config())
        save_config(payload, path)
    if not payload.get("current_workspace"):
        payload["current_workspace"] = sorted(payload["workspaces"].keys())[0]
        changed = True
    federation = payload.setdefault("federation", {})
    if not federation.get("coordinator_workspace"):
        federation["coordinator_workspace"] = payload.get("current_workspace")
        changed = True
    if federation.get("coordinator_workspace") not in payload["workspaces"]:
        federation["coordinator_workspace"] = payload.get("current_workspace")
        changed = True
    if changed:
        save_config(payload, path)
    return payload


def load_config(path: str | None = None) -> dict:
    return ensure_config(path)


def workspace_frog_bin(root: str) -> str:
    return str((Path(root).expanduser().resolve() / "ragbaz-frog" / "bin" / "frog"))


def resolve_workspace(name: str | None, path: str | None = None) -> dict | None:
    data = load_config(path)
    workspace_name = name or data.get("current_workspace")
    if not workspace_name:
        return None
    workspace = data["workspaces"].get(workspace_name)
    if not workspace:
        return None
    host = data["hosts"].get(workspace["host"], {"name": workspace["host"], "transport": "unknown"})
    return {
        **workspace,
        "name": workspace_name,
        "host_name": workspace["host"],
        "host": host,
        "frog_bin": workspace.get("frog_bin") or workspace_frog_bin(workspace["root"]),
    }


def coordinator_name(path: str | None = None) -> str | None:
    data = load_config(path)
    return data.get("federation", {}).get("coordinator_workspace")


def coordinator(path: str | None = None) -> dict | None:
    return resolve_workspace(coordinator_name(path), path)


def set_coordinator(name: str, path: str | None = None) -> dict:
    data = load_config(path)
    if name not in data["workspaces"]:
        return {"ok": False, "error": f"unknown workspace: {name}"}
    data.setdefault("federation", {})["coordinator_workspace"] = name
    saved_to = save_config(data, path)
    return {
        "ok": True,
        "message": f"selected coordinator workspace {name}",
        "coordinator_workspace": name,
        "coordinator": resolve_workspace(name, path),
        "config_path": saved_to,
    }


def workspace_names(path: str | None = None) -> list[str]:
    return sorted(load_config(path)["workspaces"].keys())


def host_names(path: str | None = None) -> list[str]:
    return sorted(load_config(path)["hosts"].keys())


def add_host(
    name: str,
    *,
    ssh_target: str | None = None,
    transport: str = "ssh",
    notes: str | None = None,
    path: str | None = None,
) -> dict:
    data = load_config(path)
    if transport == "local":
        ssh_target = None
    elif not ssh_target:
        return {"ok": False, "error": "ssh_target is required for non-local hosts"}
    data["hosts"][name] = {
        "name": name,
        "transport": transport,
        "ssh_target": ssh_target,
        "notes": notes,
    }
    saved_to = save_config(data, path)
    return {"ok": True, "message": f"configured host {name}", "host": data["hosts"][name], "config_path": saved_to}


def list_hosts(path: str | None = None) -> dict:
    data = load_config(path)
    return {"ok": True, "hosts": [data["hosts"][name] for name in sorted(data["hosts"].keys())]}


def add_workspace(
    name: str,
    *,
    host_name: str,
    root: str,
    db: str | None = None,
    notes: str | None = None,
    use_default: bool = False,
    path: str | None = None,
) -> dict:
    data = load_config(path)
    if host_name not in data["hosts"]:
        return {"ok": False, "error": f"unknown host: {host_name}"}
    data["workspaces"][name] = {
        "name": name,
        "host": host_name,
        "root": root,
        "db": db or f"{root.rstrip('/')}/AGENTS.db",
        "notes": notes,
    }
    if use_default or not data.get("current_workspace"):
        data["current_workspace"] = name
    saved_to = save_config(data, path)
    return {
        "ok": True,
        "message": f"configured workspace {name}",
        "workspace": data["workspaces"][name],
        "current_workspace": data.get("current_workspace"),
        "config_path": saved_to,
    }


def list_workspaces(path: str | None = None) -> dict:
    data = load_config(path)
    coordinator_workspace = data.get("federation", {}).get("coordinator_workspace")
    items = []
    for name in sorted(data["workspaces"].keys()):
        workspace = dict(data["workspaces"][name])
        workspace["is_current"] = data.get("current_workspace") == name
        workspace["is_coordinator"] = coordinator_workspace == name
        workspace.update(_workspace_activity(workspace, data["hosts"].get(workspace["host"], {})))
        items.append(workspace)
    return {
        "ok": True,
        "current_workspace": data.get("current_workspace"),
        "coordinator_workspace": coordinator_workspace,
        "workspaces": items,
    }


def use_workspace(name: str, path: str | None = None) -> dict:
    data = load_config(path)
    if name not in data["workspaces"]:
        return {"ok": False, "error": f"unknown workspace: {name}"}
    data["current_workspace"] = name
    saved_to = save_config(data, path)
    return {"ok": True, "message": f"selected workspace {name}", "current_workspace": name, "config_path": saved_to}


def info(path: str | None = None) -> dict:
    data = load_config(path)
    current = resolve_workspace(data.get("current_workspace"), path)
    coord_name = data.get("federation", {}).get("coordinator_workspace")
    return {
        "ok": True,
        "config_path": str(config_path(path)),
        "current_workspace": data.get("current_workspace"),
        "coordinator_workspace": coord_name,
        "coordinator": resolve_workspace(coord_name, path) if coord_name else None,
        "local_root": current["root"] if current else None,
        "host_count": len(data["hosts"]),
        "workspace_count": len(data["workspaces"]),
    }


def path_setup(shell: str, path: str | None = None) -> dict:
    workspace = resolve_workspace(None, path)
    frog_bin = workspace_frog_bin(workspace["root"] if workspace else infer_local_root())
    bin_dir = str(Path(frog_bin).parent)
    if shell == "fish":
        lines = [
            f"mkdir -p ~/.config/fish/conf.d",
            f'printf \'set -gx PATH "{bin_dir}" $PATH\\n\' > ~/.config/fish/conf.d/ragbaz-frog-path.fish',
            f"source ~/.config/fish/conf.d/ragbaz-frog-path.fish",
        ]
    elif shell == "bash":
        lines = [
            f"mkdir -p ~/.config/ragbaz-frog",
            f"grep -qxF 'export PATH=\"{bin_dir}:$PATH\"' ~/.bashrc || echo 'export PATH=\"{bin_dir}:$PATH\"' >> ~/.bashrc",
            f"source ~/.bashrc",
        ]
    else:
        return {"ok": False, "error": f"unsupported shell: {shell}"}
    return {
        "ok": True,
        "shell": shell,
        "bin_dir": bin_dir,
        "snippet": "\n".join(lines),
    }


def _workspace_activity(workspace: dict, host: dict) -> dict:
    if host.get("transport") != "local":
        return {"last_db_change_at": None}
    db_path = Path(workspace["db"]).expanduser().resolve()
    if not db_path.exists():
        return {"last_db_change_at": None}
    last_db_change_at = store._ts_iso(db_path.stat().st_mtime)  # type: ignore[attr-defined]
    repo_count = 0
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        repo_count = conn.execute("SELECT COUNT(*) AS count FROM repos").fetchone()["count"]
        conn.close()
    except sqlite3.Error:
        pass
    return {"last_db_change_at": last_db_change_at, "repo_count": repo_count}
