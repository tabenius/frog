from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

from ragbaz_frog import DEFAULT_DB_PATH
from ragbaz_frog import config as frog_config
from ragbaz_frog import store


DEFAULT_PROTOCOL_VERSION = "2025-03-26"
SUPPORTED_PROTOCOL_VERSIONS = (
    "2025-03-26",
    "2024-11-05",
)
SERVER_INFO = {"name": "ragbaz-frog", "version": "0.1.0"}


def _tool_specs() -> list[dict]:
    return [
        {
            "name": "frog_status",
            "description": "Show workspace or repo-scoped task and lock summary.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "workspace": {"type": "string"},
                    "repo_ref": {"type": "string"},
                },
            },
        },
        {
            "name": "frog_repo_list",
            "description": "List discovered repos for a workspace.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "workspace": {"type": "string"},
                    "include_third_party": {"type": "boolean"},
                },
            },
        },
        {
            "name": "frog_repo_info",
            "description": "Show metadata and counts for one repo.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "workspace": {"type": "string"},
                    "repo_ref": {"type": "string"},
                },
                "required": ["repo_ref"],
            },
        },
        {
            "name": "frog_repo_discover",
            "description": "Discover repos under a workspace or root path and update AGENTS.db.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "workspace": {"type": "string"},
                    "root": {"type": "string"},
                    "scan": {"type": "boolean"},
                },
            },
        },
        {
            "name": "frog_repo_targets",
            "description": "List detected targets for one repo.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "workspace": {"type": "string"},
                    "repo_ref": {"type": "string"},
                },
                "required": ["repo_ref"],
            },
        },
        {
            "name": "frog_unit_discover",
            "description": "Discover nested units inside one repo or across all repos.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "workspace": {"type": "string"},
                    "repo_ref": {"type": "string"},
                    "all_repos": {"type": "boolean"},
                },
            },
        },
        {
            "name": "frog_unit_list",
            "description": "List units inside one repo or across the workspace.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "workspace": {"type": "string"},
                    "repo_ref": {"type": "string"},
                },
            },
        },
        {
            "name": "frog_task_list",
            "description": "List tasks, optionally scoped by repo, workflow status, or assigned agent.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "workspace": {"type": "string"},
                    "repo_ref": {"type": "string"},
                    "workflow_status": {"type": "string"},
                    "assigned_agent": {"type": "string"},
                },
            },
        },
        {
            "name": "frog_lock_list",
            "description": "List active or historical locks.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "workspace": {"type": "string"},
                    "repo_ref": {"type": "string"},
                    "include_inactive": {"type": "boolean"},
                },
            },
        },
        {
            "name": "frog_log_tail",
            "description": "Show recent event log entries.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "workspace": {"type": "string"},
                    "repo_ref": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1},
                },
            },
        },
        {
            "name": "frog_task_claim",
            "description": "Atomically take ownership + the scoped lock + mark a task in_progress.",
            "inputSchema": {"type": "object", "properties": {
                "workspace": {"type": "string"}, "slug": {"type": "string"},
                "agent": {"type": "string"}, "lock_kind": {"type": "string"},
                "files": {"type": "array", "items": {"type": "string"}},
                "force": {"type": "boolean"}}, "required": ["slug", "agent"]},
        },
        {
            "name": "frog_task_finish",
            "description": "Verify (affected build/test) then mark done, release locks, report unblocked dependents.",
            "inputSchema": {"type": "object", "properties": {
                "workspace": {"type": "string"}, "slug": {"type": "string"},
                "agent": {"type": "string"}, "verify": {"type": "boolean"}},
                "required": ["slug", "agent"]},
        },
        {
            "name": "frog_task_create",
            "description": "Create a task slice.",
            "inputSchema": {"type": "object", "properties": {
                "workspace": {"type": "string"}, "slug": {"type": "string"},
                "title": {"type": "string"}, "repo_ref": {"type": "string"},
                "why": {"type": "string"}, "what": {"type": "string"},
                "files": {"type": "array", "items": {"type": "string"}},
                "priority": {"type": "string"}},
                "required": ["slug", "title"]},
        },
        {
            "name": "frog_task_dependency",
            "description": "Declare task A depends_on task B.",
            "inputSchema": {"type": "object", "properties": {
                "workspace": {"type": "string"}, "slug": {"type": "string"},
                "depends_on": {"type": "string"}, "relation": {"type": "string"}},
                "required": ["slug", "depends_on"]},
        },
        {
            "name": "frog_lock_acquire",
            "description": "Acquire a coordination lock (atomic; conflicts return ok=false).",
            "inputSchema": {"type": "object", "properties": {
                "workspace": {"type": "string"}, "scope_key": {"type": "string"},
                "repo_ref": {"type": "string"}, "lock_kind": {"type": "string"},
                "files": {"type": "array", "items": {"type": "string"}},
                "agent": {"type": "string"}, "reason": {"type": "string"},
                "force": {"type": "boolean"}},
                "required": ["scope_key", "lock_kind", "agent"]},
        },
        {
            "name": "frog_lock_release",
            "description": "Release a coordination lock by id.",
            "inputSchema": {"type": "object", "properties": {
                "workspace": {"type": "string"}, "lock_id": {"type": "integer"},
                "force": {"type": "boolean"}, "agent": {"type": "string"},
                "reason": {"type": "string"}}, "required": ["lock_id"]},
        },
        {
            "name": "frog_task_next",
            "description": "Highest-ROI unblocked task slice an agent can safely take now (deps/conflicts/locks/ownership aware).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "workspace": {"type": "string"},
                    "agent": {"type": "string"},
                    "repo_ref": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["agent"],
            },
        },
        {
            "name": "frog_lock_audit",
            "description": "Flag working-tree changes not covered by an active lock held by the acting agent.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "workspace": {"type": "string"},
                    "repo_ref": {"type": "string"},
                    "agent": {"type": "string"},
                },
                "required": ["agent"],
            },
        },
        {
            "name": "frog_repo_affected",
            "description": "Targets affected by working-tree / since-REF changes (+ declared downstream repos).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "workspace": {"type": "string"},
                    "repo_ref": {"type": "string"},
                    "since": {"type": "string"},
                },
                "required": ["repo_ref"],
            },
        },
        {
            "name": "frog_workspace_list",
            "description": "List configured frog workspaces.",
            "inputSchema": {"type": "object", "properties": {}},
        },
    ]


def _remote_dispatch(workspace: dict, argv: list[str]) -> dict:
    remote_argv = [workspace["frog_bin"], "--db", workspace["db"], "--json", *argv]
    proc = subprocess.run(
        ["ssh", workspace["host"]["ssh_target"], shlex.join(remote_argv)],
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        return {
            "ok": False,
            "error": f"remote frog failed on workspace {workspace['name']}",
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }
    try:
        return json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return {
            "ok": False,
            "error": "remote frog returned non-JSON output",
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }


def _workspace(name: str | None, config_path: str | None) -> dict | None:
    return frog_config.resolve_workspace(name, config_path)


def _with_conn(db_path: str, fn):
    conn = store.connect(db_path)
    try:
        return fn(conn)
    finally:
        conn.close()


def _call_local(tool_name: str, arguments: dict, workspace: dict | None):
    db_path = workspace["db"] if workspace else DEFAULT_DB_PATH

    def run(conn):
        if tool_name == "frog_status":
            return store.status_summary(conn, repo_ref=arguments.get("repo_ref"))
        if tool_name == "frog_repo_list":
            return store.repo_list_with_activity(
                conn,
                include_third_party=bool(arguments.get("include_third_party", False)),
            )
        if tool_name == "frog_repo_info":
            return store.repo_info(conn, arguments["repo_ref"])
        if tool_name == "frog_repo_discover":
            root = arguments.get("root") or (workspace["root"] if workspace else "/data/src")
            return store.discover_repos(conn, root=root, scan=bool(arguments.get("scan", True)))
        if tool_name == "frog_repo_targets":
            return store.repo_targets(conn, arguments["repo_ref"])
        if tool_name == "frog_unit_discover":
            return store.unit_discover(
                conn,
                repo_ref=arguments.get("repo_ref"),
                all_repos=bool(arguments.get("all_repos", False)),
            )
        if tool_name == "frog_unit_list":
            return store.unit_list(conn, repo_ref=arguments.get("repo_ref"))
        if tool_name == "frog_task_list":
            return store.task_list(
                conn,
                repo_ref=arguments.get("repo_ref"),
                workflow_status=arguments.get("workflow_status"),
                assigned_agent=arguments.get("assigned_agent"),
            )
        if tool_name == "frog_lock_list":
            return store.lock_list(
                conn,
                repo_ref=arguments.get("repo_ref"),
                include_inactive=bool(arguments.get("include_inactive", False)),
            )
        if tool_name == "frog_log_tail":
            return store.log_tail(
                conn,
                limit=int(arguments.get("limit", 20)),
                repo_ref=arguments.get("repo_ref"),
            )
        if tool_name == "frog_task_claim":
            return store.task_claim(
                conn, slug=arguments["slug"],
                agent=arguments.get("agent", "unknown"),
                lock_kind=arguments.get("lock_kind", "edit"),
                files=arguments.get("files", []),
                force=bool(arguments.get("force", False)))
        if tool_name == "frog_task_finish":
            return store.task_finish(
                conn, slug=arguments["slug"],
                agent=arguments.get("agent", "unknown"),
                verify=bool(arguments.get("verify", True)))
        if tool_name == "frog_task_create":
            return store.create_task(
                conn, slug=arguments["slug"],
                repo_ref=arguments.get("repo_ref"),
                title=arguments["title"], why=arguments.get("why"),
                what_text=arguments.get("what"),
                roi_note=arguments.get("roi_note"),
                priority=arguments.get("priority", "p3"),
                workflow_status=arguments.get("workflow_status", "idea"),
                git_status=arguments.get("git_status", "not_started"),
                assigned_agent=arguments.get("assigned_agent"),
                delegation_current=None, delegation_other=None,
                parent_task_slug=arguments.get("parent_task_slug"),
                files=arguments.get("files", []))
        if tool_name == "frog_task_dependency":
            return store.task_add_dependency(
                conn, arguments["slug"], arguments["depends_on"],
                arguments.get("relation", "depends_on"))
        if tool_name == "frog_lock_acquire":
            return store.lock_acquire(
                conn, scope_key=arguments["scope_key"],
                repo_ref=arguments.get("repo_ref"),
                lock_kind=arguments["lock_kind"],
                files=arguments.get("files", []),
                agent=arguments.get("agent", "unknown"),
                pid=None, reason=arguments.get("reason"),
                lease_seconds=int(arguments.get("lease_seconds", 1800)),
                eta_minutes=arguments.get("eta_minutes"),
                force=bool(arguments.get("force", False)))
        if tool_name == "frog_lock_release":
            return store.lock_release(
                conn, int(arguments["lock_id"]),
                force=bool(arguments.get("force", False)),
                agent=arguments.get("agent"),
                reason=arguments.get("reason"))
        if tool_name == "frog_task_next":
            return store.task_next(
                conn,
                agent=arguments.get("agent", "unknown"),
                repo_ref=arguments.get("repo_ref"),
                limit=int(arguments.get("limit", 1)),
            )
        if tool_name == "frog_lock_audit":
            return store.lock_audit(
                conn,
                repo_ref=arguments.get("repo_ref"),
                agent=arguments.get("agent", "unknown"),
            )
        if tool_name == "frog_repo_affected":
            return store.repo_affected(
                conn,
                arguments["repo_ref"],
                since=arguments.get("since"),
            )
        if tool_name == "frog_workspace_list":
            return frog_config.list_workspaces()
        return {"ok": False, "error": f"unsupported tool: {tool_name}"}

    return _with_conn(db_path, run)


def call_tool(tool_name: str, arguments: dict | None, *, config_path: str | None) -> dict:
    args = arguments or {}
    workspace = _workspace(args.get("workspace"), config_path)
    if tool_name == "frog_workspace_list":
        return frog_config.list_workspaces(config_path)
    if workspace and workspace["host"].get("transport") != "local":
        if tool_name == "frog_repo_list":
            argv = ["repo", "list"]
            if args.get("include_third_party"):
                argv.append("--include-third-party")
            return _remote_dispatch(workspace, argv)
        if tool_name == "frog_repo_info":
            return _remote_dispatch(workspace, ["repo", "info", args["repo_ref"]])
        if tool_name == "frog_repo_discover":
            argv = ["repo", "discover"]
            if args.get("root"):
                argv.extend(["--root", args["root"]])
            if not args.get("scan", True):
                argv.append("--no-scan")
            return _remote_dispatch(workspace, argv)
        if tool_name == "frog_repo_targets":
            return _remote_dispatch(workspace, ["repo", "targets", args["repo_ref"]])
        if tool_name == "frog_unit_discover":
            argv = ["unit", "discover"]
            if args.get("repo_ref"):
                argv.extend(["--repo-ref", args["repo_ref"]])
            if args.get("all_repos"):
                argv.append("--all-repos")
            return _remote_dispatch(workspace, argv)
        if tool_name == "frog_unit_list":
            argv = ["unit", "list"]
            if args.get("repo_ref"):
                argv.extend(["--repo-ref", args["repo_ref"]])
            return _remote_dispatch(workspace, argv)
        if tool_name == "frog_task_list":
            argv = ["task", "list"]
            if args.get("repo_ref"):
                argv.extend(["--repo-ref", args["repo_ref"]])
            if args.get("workflow_status"):
                argv.extend(["--workflow-status", args["workflow_status"]])
            if args.get("assigned_agent"):
                argv.extend(["--assigned-agent", args["assigned_agent"]])
            return _remote_dispatch(workspace, argv)
        if tool_name == "frog_lock_list":
            argv = ["lock", "list"]
            if args.get("repo_ref"):
                argv.extend(["--repo-ref", args["repo_ref"]])
            if args.get("include_inactive"):
                argv.append("--include-inactive")
            return _remote_dispatch(workspace, argv)
        if tool_name == "frog_log_tail":
            argv = ["log", "--limit", str(int(args.get("limit", 20)))]
            if args.get("repo_ref"):
                argv.extend(["--repo-ref", args["repo_ref"]])
            return _remote_dispatch(workspace, argv)
        if tool_name == "frog_status":
            argv = ["status"]
            if args.get("repo_ref"):
                argv.extend(["--repo-ref", args["repo_ref"]])
            return _remote_dispatch(workspace, argv)
    return _call_local(tool_name, args, workspace)


def _read_message() -> dict | None:
    headers: dict[str, str] = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        key, _, value = line.decode("utf-8").partition(":")
        headers[key.strip().lower()] = value.strip()
    length = int(headers.get("content-length", "0"))
    if length <= 0:
        return None
    payload = sys.stdin.buffer.read(length)
    return json.loads(payload.decode("utf-8"))


def _write_message(payload: dict) -> None:
    blob = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(blob)}\r\n\r\n".encode("ascii"))
    sys.stdout.buffer.write(blob)
    sys.stdout.buffer.flush()


def _response(message_id, result=None, error=None) -> dict:
    payload = {"jsonrpc": "2.0", "id": message_id}
    if error is not None:
        payload["error"] = error
    else:
        payload["result"] = result or {}
    return payload


def _negotiate_protocol_version(message: dict) -> str:
    params = message.get("params") or {}
    requested = params.get("protocolVersion")
    if isinstance(requested, str) and requested in SUPPORTED_PROTOCOL_VERSIONS:
        return requested
    if isinstance(requested, str) and requested:
        # Core tools/list and tools/call semantics are stable enough here that
        # matching the client preference is safer than forcing an older version.
        return requested
    return DEFAULT_PROTOCOL_VERSION


def _tool_result(payload: dict) -> dict:
    text = json.dumps(payload, indent=2, sort_keys=True)
    return {
        "content": [{"type": "text", "text": text}],
        "isError": not payload.get("ok", True),
        "structuredContent": payload,
    }


_SRC_ROOT = "/data/src"


def _resource_specs() -> list[dict]:
    return [
        {"uri": "frog://board", "name": "frog board snapshot",
         "description": "Live task lifecycle columns + ready set + recent events.",
         "mimeType": "application/json"},
        {"uri": "frog://events", "name": "frog event tail",
         "description": "Recent task/lock coordination events.",
         "mimeType": "application/json"},
        {"uri": "frog://agents-md", "name": "AGENTS.md",
         "description": "Workspace agent instructions.",
         "mimeType": "text/markdown"},
        {"uri": "frog://agents-coop", "name": "agents-coop.md",
         "description": "Shared agent worklog.",
         "mimeType": "text/markdown"},
    ]


def _read_resource(uri: str, *, db_path: str | None = None) -> dict:
    db_path = db_path or DEFAULT_DB_PATH
    if uri == "frog://board":
        snap = _with_conn(db_path, lambda c: store.board_snapshot(c))
        return {"mimeType": "application/json",
                "text": json.dumps(snap, indent=2)}
    if uri == "frog://events":
        ev = _with_conn(db_path, lambda c: store.log_tail(c, limit=30, repo_ref=None))
        return {"mimeType": "application/json",
                "text": json.dumps(ev, indent=2)}
    if uri in ("frog://agents-md", "frog://agents-coop"):
        fn = "AGENTS.md" if uri.endswith("agents-md") else "agents-coop.md"
        fp = Path(_SRC_ROOT) / fn
        return {"mimeType": "text/markdown",
                "text": fp.read_text() if fp.is_file() else f"({fn} not present)"}
    raise KeyError(uri)


def _prompt_specs() -> list[dict]:
    return [{
        "name": "coordinate-before-edit",
        "description": "Frog coordination discipline before touching a repo.",
        "arguments": [{"name": "agent", "description": "Acting agent",
                       "required": False}],
    }]


def _get_prompt(name: str, arguments: dict) -> dict:
    if name != "coordinate-before-edit":
        raise KeyError(name)
    agent = (arguments or {}).get("agent", "${FROG_AGENT:-$USER}")
    text = (
        "Before editing this frog-coordinated tree:\n"
        f"1. `frog board` then `frog task next --agent {agent}`.\n"
        f"2. `frog task claim <slug> --agent {agent}` (assigns + locks).\n"
        "3. `frog lock check --file <path>` before touching shared files.\n"
        f"4. When done: `frog task finish <slug> --agent {agent}`.\n"
        "Read the frog://board and frog://agents-md resources for context."
    )
    return {"messages": [{"role": "user",
            "content": {"type": "text", "text": text}}]}


def serve(*, config_path: str | None = None) -> int:
    while True:
        message = _read_message()
        if message is None:
            return 0
        method = message.get("method")
        if method == "initialize":
            _write_message(
                _response(
                    message.get("id"),
                    {
                        "protocolVersion": _negotiate_protocol_version(message),
                        "capabilities": {"tools": {"listChanged": False}, "resources": {"listChanged": False}, "prompts": {"listChanged": False}},
                        "serverInfo": SERVER_INFO,
                    },
                )
            )
            continue
        if method == "notifications/initialized":
            continue
        if method == "ping":
            _write_message(_response(message.get("id"), {}))
            continue
        if method == "tools/list":
            _write_message(_response(message.get("id"), {"tools": _tool_specs()}))
            continue
        if method == "tools/call":
            params = message.get("params", {})
            name = params.get("name")
            arguments = params.get("arguments") or {}
            result = call_tool(name, arguments, config_path=config_path)
            _write_message(_response(message.get("id"), _tool_result(result)))
            continue
        if method == "resources/list":
            _write_message(_response(message.get("id"), {"resources": _resource_specs()}))
            continue
        if method == "resources/read":
            uri = (message.get("params") or {}).get("uri", "")
            try:
                body = _read_resource(uri)
                _write_message(_response(message.get("id"),
                    {"contents": [{"uri": uri, **body}]}))
            except KeyError:
                _write_message(_response(message["id"],
                    error={"code": -32602, "message": f"unknown resource: {uri}"}))
            continue
        if method == "prompts/list":
            _write_message(_response(message.get("id"), {"prompts": _prompt_specs()}))
            continue
        if method == "prompts/get":
            params = message.get("params") or {}
            try:
                _write_message(_response(message.get("id"),
                    _get_prompt(params.get("name"), params.get("arguments") or {})))
            except KeyError:
                _write_message(_response(message["id"],
                    error={"code": -32602, "message": "unknown prompt"}))
            continue
        if method == "exit":
            return 0
        if "id" in message:
            _write_message(
                _response(
                    message["id"],
                    error={"code": -32601, "message": f"method not found: {method}"},
                )
            )
