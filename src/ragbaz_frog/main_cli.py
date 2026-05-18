from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

from ragbaz_frog import DEFAULT_CONFIG_PATH
from ragbaz_frog import DEFAULT_DB_PATH
from ragbaz_frog import config as frog_config
from ragbaz_frog import mcp_server
from ragbaz_frog import store


TOP_LEVEL_COMMANDS = {
    "init",
    "agent-instructions",
    "completion",
    "ps",
    "snapshot",
    "status",
    "log",
    "config",
    "mcp",
    "repo",
    "repo-task",
    "unit",
    "task",
    "lock",
    "file",
}
REPO_ACTIONS = {
    "build",
    "clean",
    "test",
    "lint",
    "check",
    "verify",
    "diff",
    "status",
    "scan",
    "detect",
    "targets",
    "doctor",
    "artifacts",
    "artifact-stale",
}

REPO_ACTION_HELP = {
    "build": "Run detected build targets for a repo.",
    "clean": "Run detected clean targets for a repo.",
    "test": "Run detected test targets for a repo.",
    "lint": "Run detected lint targets for a repo.",
    "check": "Run detected lightweight verification targets for a repo.",
    "verify": "Run the repo's broader verification targets.",
    "diff": "Show diff against git HEAD, optionally with task and target impact.",
    "status": "Show repo-scoped task and lock summary.",
    "scan": "Scan manifests and detect repo targets and artifact hints.",
    "detect": "Alias for scan.",
    "targets": "List detected targets for a repo.",
    "doctor": "Summarize missing targets and stale or missing artifacts.",
    "artifacts": "List known artifact paths for a repo.",
    "artifact-stale": "List artifact paths that appear stale or missing.",
}



def _repo_name_words() -> str:
    try:
        conn = store.connect(DEFAULT_DB_PATH)
        try:
            return " ".join(store.repo_names(conn))
        finally:
            conn.close()
    except Exception:
        return ""


def _workspace_names() -> list[str]:
    try:
        return frog_config.workspace_names()
    except Exception:
        return []


def _completion_script(shell: str) -> str:
    top = "db new agent agent-instructions completion ps snapshot status log config mcp repo unit task lock file sync"
    repo_subs = "list register discover sync info task " + " ".join(sorted(REPO_ACTIONS))
    repo_names = _repo_name_words()
    workspace_names = " ".join(_workspace_names())
    if shell == "bash":
        return f"""_frog_complete() {{
  local cur
  _init_completion || return
  local top="{top}"
  local repo_subs="{repo_subs}"
  local repo_names="{repo_names}"
  if [[ $COMP_CWORD -eq 1 ]]; then
    COMPREPLY=( $(compgen -W "$top" -- "$cur") )
    return
  fi
  case "${{COMP_WORDS[1]}}" in
    completion) COMPREPLY=( $(compgen -W "bash fish" -- "$cur") ) ;;
    ps|snapshot|status) COMPREPLY=() ;;
    db) COMPREPLY=( $(compgen -W "migrate schema gc" -- "$cur") ) ;;
    new|agent-instructions) COMPREPLY=( $(compgen -d -- "$cur") ) ;;
    config)
      if [[ $COMP_CWORD -eq 2 ]]; then
        COMPREPLY=( $(compgen -W "info host workspace path" -- "$cur") )
      elif [[ $COMP_CWORD -eq 3 && "${{COMP_WORDS[2]}}" == "host" ]]; then
        COMPREPLY=( $(compgen -W "add list" -- "$cur") )
      elif [[ $COMP_CWORD -eq 3 && "${{COMP_WORDS[2]}}" == "workspace" ]]; then
        COMPREPLY=( $(compgen -W "add list use" -- "$cur") )
      elif [[ $COMP_CWORD -eq 4 && "${{COMP_WORDS[2]}}" == "workspace" && "${{COMP_WORDS[3]}}" == "use" ]]; then
        COMPREPLY=( $(compgen -W "{workspace_names}" -- "$cur") )
      elif [[ $COMP_CWORD -eq 3 && "${{COMP_WORDS[2]}}" == "path" ]]; then
        COMPREPLY=( $(compgen -W "bash fish" -- "$cur") )
      fi
      ;;
    mcp)
      [[ $COMP_CWORD -eq 2 ]] && COMPREPLY=( $(compgen -W "serve tools" -- "$cur") ) ;;
    repo)
      if [[ $COMP_CWORD -eq 2 ]]; then
        COMPREPLY=( $(compgen -W "$repo_subs" -- "$cur") )
      elif [[ $COMP_CWORD -eq 3 ]]; then
        COMPREPLY=( $(compgen -W "$repo_names" -- "$cur") )
      fi
      ;;
    unit) [[ $COMP_CWORD -eq 2 ]] && COMPREPLY=( $(compgen -W "discover list" -- "$cur") ) ;;
    task) [[ $COMP_CWORD -eq 2 ]] && COMPREPLY=( $(compgen -W "create list next claim finish info status dependency conflict tag assign" -- "$cur") ) ;;
    lock) [[ $COMP_CWORD -eq 2 ]] && COMPREPLY=( $(compgen -W "check acquire renew release list info" -- "$cur") ) ;;
    file) [[ $COMP_CWORD -eq 2 ]] && COMPREPLY=( $(compgen -W "upsert list info" -- "$cur") ) ;;
    log) COMPREPLY=( $(compgen -W "why blame --follow --limit --repo -f" -- "$cur") ) ;;
  esac
}}
complete -F _frog_complete frog
"""
    if shell == "fish":
        return f"""complete -c frog -f
complete -c frog -n '__fish_use_subcommand' -a '{top}'
complete -c frog -n '__fish_seen_subcommand_from completion' -a 'bash fish'
complete -c frog -n '__fish_seen_subcommand_from db' -a 'migrate schema gc'
complete -c frog -n '__fish_seen_subcommand_from new agent-instructions' -a '(__fish_complete_directories)'
complete -c frog -n '__fish_seen_subcommand_from config' -a 'info host workspace path'
complete -c frog -n '__fish_seen_subcommand_from path' -a 'bash fish'
complete -c frog -n '__fish_seen_subcommand_from mcp' -a 'serve tools'
complete -c frog -n '__fish_seen_subcommand_from host' -a 'add list'
complete -c frog -n '__fish_seen_subcommand_from workspace' -a 'add list use'
complete -c frog -n '__fish_seen_subcommand_from repo' -a '{repo_subs}'
complete -c frog -n '__fish_seen_subcommand_from {" ".join(sorted(REPO_ACTIONS))} info' -a '{repo_names}'
complete -c frog -n '__fish_seen_subcommand_from unit' -a 'discover list'
complete -c frog -n '__fish_seen_subcommand_from task' -a 'create list next claim finish info status dependency conflict tag assign'
complete -c frog -n '__fish_seen_subcommand_from lock' -a 'check acquire renew release list info'
complete -c frog -n '__fish_seen_subcommand_from file' -a 'upsert list info'
complete -c frog -n '__fish_seen_subcommand_from log' -a 'why blame --follow --limit --repo -f'
"""
    raise ValueError(f"unsupported shell: {shell}")


def _emit(payload: dict, as_json: bool) -> int:
    if as_json:
        json.dump(payload, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return 0 if payload.get("ok", True) else 1
    if not payload.get("ok", True):
        print(f"error: {payload.get('error', 'unknown error')}", file=sys.stderr)
        return 1
    if "active_tasks" in payload and "active_locks" in payload:
        print(f"active_tasks={len(payload['active_tasks'])} active_locks={len(payload['active_locks'])}")
        if payload["active_tasks"]:
            print("Tasks:")
            for task in payload["active_tasks"]:
                repo = Path(task["repo_path"]).name if task["repo_path"] else "-"
                print(f"{task['slug']}  {task['priority']}  {task['workflow_status']}  {task['git_status']}  {repo}")
        if payload["active_locks"]:
            print("Locks:")
            for lock in payload["active_locks"]:
                repo = Path(lock["repo_path"]).name if lock["repo_path"] else "-"
                print(f"{lock['id']}  {lock['lock_kind']}  {lock['scope_key']}  {repo}  {lock['agent_name']}")
        if payload["recent_events"]:
            print("Recent:")
            for event in payload["recent_events"]:
                print(f"{event['created_at']}  {event['kind']}  {event['summary']}")
        return 0
    if "message" in payload:
        print(payload["message"])
        return 0
    if "tools" in payload:
        for tool in payload["tools"]:
            print(f"{tool['name']}  {tool['description']}")
        return 0
    if "snippet" in payload:
        print(payload["snippet"])
        return 0
    if "hosts" in payload:
        for host in payload["hosts"]:
            detail = host.get("ssh_target") or host.get("transport", "unknown")
            print(f"{host['name']}  {detail}")
        return 0
    if "workspaces" in payload:
        _render_workspace_list(payload["workspaces"], payload.get("_view", "default"))
        return 0
    if "repos" in payload:
        _render_repo_list(payload["repos"], payload.get("_view", "default"))
        return 0
    if "units" in payload:
        _render_unit_list(payload["units"], payload.get("_view", "default"))
        return 0
    if "repo" in payload and "counts" in payload and "targets" not in payload and "artifacts" not in payload:
        repo = payload["repo"]
        print(f"{repo['name']}  {repo['repo_path']}")
        print(f"status: {repo['status']}")
        print(f"tasks: {payload['counts']['tasks']}")
        print(f"active_locks: {payload['counts']['active_locks']}")
        print(f"targets: {payload['counts'].get('targets', 0)}")
        print(f"artifacts: {payload['counts'].get('artifacts', 0)}")
        return 0
    if "targets" in payload:
        for target in payload["targets"]:
            prefix = "*" if target.get("aggregate") else "-"
            command = target.get("command") or "(aggregate)"
            print(f"{prefix} {target['target_kind']}  {target['name']}  {Path(target['workdir']).name}  {command}")
        return 0
    if "artifacts" in payload and "repo" in payload:
        for artifact in payload["artifacts"]:
            suffix = []
            if "exists" in artifact:
                suffix.append("present" if artifact["exists"] else "missing")
            if artifact.get("stale"):
                suffix.append("stale")
            print(f"{artifact['artifact_name']}  {artifact['path_hint']}  {' '.join(suffix).strip()}")
        return 0
    if "results" in payload:
        for result in payload["results"]:
            print(f"{result['target_kind']} {result['name']}  rc={result['returncode']}")
            if result["stdout"].strip():
                print(result["stdout"].rstrip())
            if result["stderr"].strip():
                print(result["stderr"].rstrip(), file=sys.stderr)
        return 0 if payload.get("ok", True) else 1
    if "deps" in payload:
        for d in payload["deps"]:
            print(f"{d['dependent_repo_path']}  ->  {d['dependency_repo_path']}"
                  + (f"  ({d['note']})" if d.get("note") else ""))
        return 0
    if "affected" in payload and "changed_files" in payload:
        repo = payload.get("repo", {})
        rn = repo.get("name", "?") if isinstance(repo, dict) else "?"
        print(f"{rn}: {len(payload['changed_files'])} changed file(s), "
              f"{len(payload['affected'])} affected target(s)"
              + (f" since {payload['since']}" if payload.get("since") else ""))
        for tgt in payload["affected"]:
            print(f"  {tgt['target_kind']}  {tgt['name']}  {tgt['workdir']}")
        for ds in payload.get("downstream", []):
            dn = ds["repo"]["name"] if isinstance(ds.get("repo"), dict) else "?"
            print(f"  downstream {dn}: {len(ds['targets'])} target(s)")
        return 0
    if "diff" in payload:
        if payload.get("diff"):
            print(payload["diff"].rstrip())
        if payload.get("tasks"):
            print("\nTasks:")
            for task in payload["tasks"]:
                print(f"{task['slug']}  {task['workflow_status']}  {task['git_status']}")
        if payload.get("impacted_targets"):
            print("\nImpacted targets:")
            for item in payload["impacted_targets"]:
                print(f"{item['target_kind']}  {item['name']}  {item['workdir']}")
        return 0
    if "advice" in payload:
        print(f"{payload['repo']['name']}  {payload['repo']['repo_path']}")
        if payload["advice"]:
            for item in payload["advice"]:
                print(f"- {item}")
        else:
            print("no obvious issues")
        return 0
    if "unblocked" in payload and "task" in payload and "verification" in payload:
        tk = payload["task"]
        print(f"finished {tk['slug']}  ({tk['workflow_status']})")
        v = payload.get("verification")
        if v and v.get("ran"):
            print(f"  verify: {'FAILED' if v['failed'] else 'passed'}")
        if payload.get("released_locks"):
            print(f"  released locks: {payload['released_locks']}")
        if payload["unblocked"]:
            print("  unblocked: " + ", ".join(payload["unblocked"]))
        else:
            print("  unblocked: (none)")
        return 0
    if "eligible" in payload and "considered" in payload:
        print(f"agent {payload['agent']}: {payload['eligible']} eligible "
              f"of {payload['considered']} considered")
        for tk in payload["tasks"]:
            print(f"  -> {tk['slug']}  {tk['priority']}  {tk['title']}")
        if not payload["tasks"]:
            print("  (nothing unblocked)")
        for s in payload.get("skipped", []):
            print(f"  skip {s['slug']}: {s['reason']}")
        return 0
    if "tasks" in payload:
        for task in payload["tasks"]:
            repo = Path(task["repo_path"]).name if task["repo_path"] else "-"
            print(f"{task['slug']}  {task['priority']}  {task['workflow_status']}  {task['git_status']}  {repo}")
        return 0
    if "task" in payload:
        task = payload["task"]
        print(f"{task['slug']}  {task['title']}")
        print(f"repo: {task['repo_path'] or '-'}")
        print(f"priority: {task['priority']}")
        print(f"workflow_status: {task['workflow_status']}")
        print(f"git_status: {task['git_status']}")
        return 0
    if "findings" in payload and "agent" in payload:
        repo = payload.get("repo", {})
        rname = repo.get("name", "?") if isinstance(repo, dict) else "?"
        if not payload["findings"]:
            print(f"{rname}: clean ({payload.get('dirty_count', 0)} dirty, all covered)")
            return 0
        print(f"{rname}: {len(payload['findings'])} lock-audit finding(s)")
        for f in payload["findings"]:
            who = ("held by " + ", ".join(f["holders"])) if f.get("holders") else "no active lock"
            print(f"  {f['kind']:9} {f['file']}  ({who})")
        return 1
    if "reaped" in payload:
        print(payload.get("message", f"reaped {len(payload['reaped'])}"))
        for r in payload["reaped"]:
            print(f"  {r['id']}  {r.get('scope_key','-')}  {r.get('agent_name','-')}")
        return 0
    if "locks" in payload:
        for lock in payload["locks"]:
            repo = Path(lock["repo_path"]).name if lock["repo_path"] else "-"
            print(f"{lock['id']}  {lock['lock_kind']}  {lock['scope_key']}  {repo}  {lock['agent_name']}  {lock['status']}")
        return 0
    if "lock" in payload:
        lock = payload["lock"]
        print(f"lock {lock['id']}  {lock['lock_kind']}  {lock['scope_key']}")
        print(f"repo: {lock['repo_path'] or '-'}")
        print(f"agent: {lock['agent_name']}")
        print(f"status: {lock['status']}")
        print(f"started_at: {lock['started_at']}")
        print(f"eta_finish_at: {lock['eta_finish_at'] or '-'}")
        for path in lock["file_paths"]:
            print(f"file: {path}")
        return 0
    if "files" in payload:
        for item in payload["files"]:
            print(f"{item['file_type'] or '-'}  {item['file_path']}")
        return 0
    if "file" in payload:
        item = payload["file"]
        print(item["file_path"])
        print(f"repo: {item['repo_path'] or '-'}")
        print(f"type: {item['file_type'] or '-'}")
        print(f"source_of_truth: {item['source_of_truth'] or '-'}")
        return 0
    if "mirrored_events" in payload:
        for e in payload["mirrored_events"]:
            print(f"{e['workspace']}#{e['remote_id']}  {e['created_at']}  "
                  f"{e['kind']}  {e['summary']}")
        return 0
    if "file_path" in payload and "locks" in payload and "tasks" in payload:
        print(f"blame {payload['file_path']}  (repo: {payload.get('repo_path') or '-'})")
        if payload["tasks"]:
            print("tasks:")
            for tk in payload["tasks"]:
                print(f"  {tk['slug']}  {tk.get('role') or '-'}  {tk['workflow_status']}  {tk['title']}")
        if payload["locks"]:
            print("locks:")
            for lk in payload["locks"]:
                print(f"  {lk['id']}  {lk['agent_name']}  {lk['lock_kind']}  {lk['status']}  {lk['started_at']}")
        if payload["events"]:
            print("recent repo events:")
            for e in payload["events"][:15]:
                print(f"  {e['created_at']}  {e['kind']}  {e['summary']}")
        return 0
    if "events" in payload:
        for event in payload["events"]:
            _print_event(event)
        return 0
    if "counts" in payload:
        print(
            f"repos={payload['counts']['repos']} files={payload['counts']['files']} "
            f"tasks={payload['counts']['tasks']} active_locks={payload['counts']['active_locks']}"
        )
        for item in payload.get("tasks_by_workflow_status", []):
            print(f"  {item['workflow_status']}: {item['count']}")
        return 0
    if "config_path" in payload:
        print(f"config_path: {payload['config_path']}")
        print(f"current_workspace: {payload.get('current_workspace') or '-'}")
        print(f"local_root: {payload.get('local_root') or '-'}")
        print(f"hosts: {payload.get('host_count', 0)}")
        print(f"workspaces: {payload.get('workspace_count', 0)}")
        return 0
    json.dump(payload, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


def _dirty_mark(value) -> str:
    if value is True:
        return "dirty"
    if value is False:
        return "clean"
    return "-"


def _render_repo_list(repos: list[dict], view: str) -> None:
    for repo in repos:
        marker = " [3p]" if repo.get("third_party") else ""
        if view == "long":
            scope = "/".join(
                part for part in [repo.get("category"), repo.get("suite"), repo.get("subgroup")] if part
            ) or "-"
            print(
                f"{repo['name']}  {repo.get('kind') or '-'}  {repo['status']}{marker}  "
                f"{_dirty_mark(repo.get('dirty'))}  git={repo.get('last_git_change_at') or '-'}  "
                f"fs={repo.get('last_fs_change_at') or '-'}  scope={scope}  {repo['repo_path']}"
            )
        elif view == "one":
            print(
                f"{repo['name']}\tkind={repo.get('kind') or '-'}\tstatus={repo['status']}{marker}"
                f"\tdirty={_dirty_mark(repo.get('dirty'))}\tgit={repo.get('last_git_change_at') or '-'}"
                f"\tfs={repo.get('last_fs_change_at') or '-'}\tpath={repo.get('relative_path') or repo['repo_path']}"
            )
        else:
            print(f"{repo['name']}  {repo['repo_path']}  {repo['status']}{marker}")


def _render_unit_list(units: list[dict], view: str) -> None:
    for unit in units:
        repo_name = Path(unit["repo_path"]).name
        if view == "long":
            print(
                f"{repo_name}  {unit['rel_path']}  {unit.get('kind') or '-'}  "
                f"{_dirty_mark(unit.get('dirty'))}  git={unit.get('last_git_change_at') or '-'}  "
                f"fs={unit.get('last_fs_change_at') or '-'}  {unit['unit_path']}"
            )
        elif view == "one":
            print(
                f"{repo_name}\trel={unit['rel_path']}\tkind={unit.get('kind') or '-'}"
                f"\tdirty={_dirty_mark(unit.get('dirty'))}\tgit={unit.get('last_git_change_at') or '-'}"
                f"\tfs={unit.get('last_fs_change_at') or '-'}"
            )
        else:
            print(f"{repo_name}  {unit['rel_path']}  {unit.get('kind') or '-'}")


def _render_workspace_list(workspaces: list[dict], view: str) -> None:
    for workspace in workspaces:
        marker = " *" if workspace.get("is_current") else ""
        if view == "long":
            print(
                f"{workspace['name']}{marker}  host={workspace['host']}  repos={workspace.get('repo_count', 0)}  "
                f"last_db={workspace.get('last_db_change_at') or '-'}  root={workspace['root']}  db={workspace['db']}"
            )
        elif view == "one":
            print(
                f"{workspace['name']}\thost={workspace['host']}\trepos={workspace.get('repo_count', 0)}"
                f"\tlast_db={workspace.get('last_db_change_at') or '-'}\troot={workspace['root']}{marker}"
            )
        else:
            print(f"{workspace['name']}  {workspace['host']}  {workspace['root']}  {workspace['db']}{marker}")


def _payload_with_view(payload: dict, args) -> dict:
    view = "default"
    if getattr(args, "long", False):
        view = "long"
    elif getattr(args, "one", False):
        view = "one"
    if view == "default":
        return payload
    enriched = dict(payload)
    enriched["_view"] = view
    return enriched


def _event_color(kind: str) -> str:
    if kind.startswith("frog.command"):
        return "\033[36m"
    if kind.startswith("lock."):
        return "\033[33m"
    if kind.startswith("task."):
        return "\033[32m"
    if kind.startswith("repo."):
        return "\033[35m"
    if kind.startswith("file."):
        return "\033[34m"
    if kind.startswith("workspace."):
        return "\033[31m"
    return "\033[0m"


def _print_event(event: dict) -> None:
    color = _event_color(event["kind"])
    reset = "\033[0m"
    print(f"{event['created_at']}  {color}{event['kind']}{reset}  {event['summary']}", flush=True)


def _record_command(conn, argv: list[str]) -> None:
    store.record_event(
        conn,
        kind="frog.command",
        summary="frog " + " ".join(argv),
        actor=os.environ.get("USER", "unknown"),
        payload={"argv": argv},
    )
    conn.commit()


def _follow_log(conn, *, repo_ref: str | None, limit: int) -> int:
    initial = store.log_tail(conn, limit=limit, repo_ref=repo_ref)
    if not initial.get("ok", True):
        return _emit(initial, False)
    seen = 0
    for event in initial["events"]:
        _print_event(event)
        seen = max(seen, event["id"])
    try:
        while True:
            time.sleep(1.0)
            update = store.log_since(conn, after_id=seen, repo_ref=repo_ref)
            if not update.get("ok", True):
                return _emit(update, False)
            for event in update["events"]:
                _print_event(event)
                seen = max(seen, event["id"])
    except KeyboardInterrupt:
        return 0


def _hide_subcommands(parser: argparse.ArgumentParser, hidden: set[str]) -> None:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            action._choices_actions = [
                choice for choice in action._choices_actions if getattr(choice, "dest", None) not in hidden
            ]


def _split_workspace_repo_ref(repo_ref: str | None) -> tuple[str | None, str | None]:
    if not repo_ref or ":" not in repo_ref:
        return None, repo_ref
    workspace_name, actual = repo_ref.split(":", 1)
    if workspace_name in _workspace_names():
        return workspace_name, actual
    return None, repo_ref


def _workspace_for_args(args, conn) -> dict | None:
    explicit = getattr(args, "workspace", None)
    repo_ref = getattr(args, "repo_ref", None)
    workspace_from_repo, actual_repo_ref = _split_workspace_repo_ref(repo_ref)
    if workspace_from_repo and hasattr(args, "repo_ref"):
        args.repo_ref = actual_repo_ref
    workspace_name = explicit or workspace_from_repo
    if workspace_name:
        return frog_config.resolve_workspace(workspace_name, getattr(args, "config", None))
    if args.command in {"completion", "config", "agent-instructions"}:
        return None
    if (
        args.command == "repo"
        and getattr(args, "repo_command", None) in REPO_ACTIONS
        and not getattr(args, "repo_ref", None)
    ):
        inferred = store.infer_repo_from_cwd(conn)
        if inferred:
            return None
    current = frog_config.resolve_workspace(None, getattr(args, "config", None))
    return current


def _forwardable_argv(argv: list[str]) -> list[str]:
    cleaned: list[str] = []
    skip_next = False
    for index, token in enumerate(argv):
        if skip_next:
            skip_next = False
            continue
        if token == "--json":
            continue
        if token in {"--workspace", "--config"} and index + 1 < len(argv):
            skip_next = True
            continue
        cleaned.append(token)
    return cleaned


def _remote_exec(ssh_target: str, remote_cmd: str) -> tuple[int, str, str]:
    """SSH boundary, isolated so tests can inject a fake transport.
    Returns (returncode, stdout, stderr)."""
    proc = subprocess.run(
        ["ssh", ssh_target, remote_cmd], text=True, capture_output=True
    )
    return proc.returncode, proc.stdout, proc.stderr


def _dispatch_workspace(workspace: dict, argv: list[str]) -> dict:
    transport = workspace["host"].get("transport", "local")
    if transport == "local":
        return {"ok": True}
    forwarded = _forwardable_argv(argv)
    if len(forwarded) >= 2 and forwarded[0] == "repo" and forwarded[1] in {"discover", "sync"} and "--root" not in forwarded:
        forwarded.extend(["--root", workspace["root"]])
    remote_argv = [workspace["frog_bin"], "--db", workspace["db"], "--json", *forwarded]
    remote_cmd = shlex.join(remote_argv)
    rc, out, err = _remote_exec(workspace["host"]["ssh_target"], remote_cmd)
    if rc != 0:
        return {
            "ok": False,
            "error": f"remote frog failed on workspace {workspace['name']}",
            "returncode": rc,
            "stdout": out,
            "stderr": err,
        }
    try:
        payload = json.loads(out or "{}")
    except json.JSONDecodeError:
        return {
            "ok": False,
            "error": f"remote frog returned non-JSON output for workspace {workspace['name']}",
            "stdout": out,
            "stderr": err,
        }
    if err.strip():
        payload.setdefault("remote_stderr", err)
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="RAGBAZ workspace coordination CLI",
        epilog=(
            "Grammar:  frog <command> <subcommand> [args]   (strict; no positional guessing)\n\n"
            "JSON:\n"
            "  Most operational commands support --json.\n"
            "  Exceptions: --help stays text; 'frog mcp serve' speaks MCP over stdio.\n"
            "  'frog completion ...' returns the script text, or JSON with --json.\n\n"
            "Repo addressing:\n"
            "  1. Explicit:        frog repo build mailstack\n"
            "  2. Cwd repo:        frog repo build           (repo arg omitted)\n"
            "  3. Another box:     frog --workspace konsonans-src repo list\n"
            "  (No bare 'frog mailstack build' or 'frog build' shorthand any more.)\n\n"
            "Examples:\n"
            "  frog repo list\n"
            "  frog repo list -l\n"
            "  frog repo list --json | jq '.repos[] | {name, path: .repo_path, dirty}'\n"
            "  frog repo targets detcordon\n"
            "  frog repo build mailstack\n"
            "  frog repo build                 # when cwd is inside a known repo\n"
            "  frog repo task list --repo detcordon\n"
            "  frog unit discover --repo detcordon\n"
            "  frog unit list -l --repo detcordon\n"
            "  frog status --json | jq '.counts'        # now reachable (was shadowed)\n"
            "  frog log --limit 5 --json | jq '.events[]'\n"
            "  frog log --follow\n"
            "  frog config workspace list --json | jq '.workspaces[]'\n"
            "  frog completion fish --json | jq -r '.script'\n"
            "  frog mcp tools --json | jq '.tools[].name'\n"
            "  frog db migrate\n"
            "  frog new my-new-idea            # defaults under /data/src/experiments\n"
            "  frog new ~/sandbox/x            # explicit path"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="Path to frog config JSON")
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="Path to AGENTS.db")
    parser.add_argument("--workspace", help="Named workspace to use; may point at another host over SSH")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON output")
    sub = parser.add_subparsers(
        dest="command",
        required=True,
        metavar="{db,new,agent,agent-instructions,snapshot,ps,completion,status,log,config,mcp,repo,unit,task,lock,file,sync}",
    )

    db_cmd = sub.add_parser(
        "db",
        help="Initialize or inspect the shared AGENTS.db schema",
        description=(
            "AGENTS.db schema operations.\n"
            "  frog db migrate   # apply pending migrations\n"
            "  frog db schema    # show applied migrations\n"
            "To scaffold a new repo/draft path, use 'frog new NAME'."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    db_sub = db_cmd.add_subparsers(dest="init_command", required=True)
    db_sub.add_parser("migrate", help="Apply pending AGENTS.db migrations")
    db_sub.add_parser("schema", help="Show applied AGENTS.db migrations")
    db_gc = db_sub.add_parser("gc", help="Prune event/target_runs history + VACUUM")
    db_gc.add_argument("--older-than", dest="older_than", type=int,
                       help="Drop rows older than N days (newest --keep retained)")
    db_gc.add_argument("--keep", type=int, default=200,
                       help="Always retain the newest N (default 200)")

    new_cmd = sub.add_parser(
        "new",
        help="Scaffold a new repo/draft path",
        description=(
            "Create a new repo/draft scaffold.\n"
            "  frog new my-idea       # defaults under /data/src/experiments\n"
            "  frog new ~/sandbox/x   # explicit path"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    new_cmd.add_argument("path_or_name")
    new_cmd.add_argument("--kind")
    new_cmd.add_argument("--notes")

    agent_instructions = sub.add_parser(
        "agent-instructions",
        help="Write a local AGENTS.md that tells agents to read /data/src/AGENTS.md",
    )
    agent_instructions.add_argument("path", nargs="?", help="Repo directory or AGENTS.md path; defaults to cwd")
    agent_instructions.add_argument("--force", action="store_true", help="Overwrite an existing AGENTS.md")

    sub.add_parser(
        "snapshot",
        help="Rotate /data/backups/src.last to src.prev and rsync /data/src to src.last",
    )
    ps = sub.add_parser(
        "ps",
        help="Show active tasks, active locks, and recent frog activity",
    )
    ps.add_argument("--repo", dest="repo_ref", metavar="REPO")

    completion = sub.add_parser(
        "completion",
        help="Emit shell completion script for bash or fish",
    )
    completion.add_argument("shell", choices=["bash", "fish"])

    status = sub.add_parser(
        "status",
        help="Show workspace or repo-scoped task and lock summary",
    )
    status.add_argument("--repo", dest="repo_ref", metavar="REPO")

    log = sub.add_parser(
        "log",
        help="Show recent frog event log entries or follow them live",
        description=(
            "Show the frog event log.\n"
            "Use --follow or -f to stream new events as they are written to AGENTS.db."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    log.add_argument("--limit", type=int, default=20, help="Number of recent events to show initially")
    log.add_argument("--repo", dest="repo_ref", metavar="REPO", help="Limit events to one repo by name or path")
    log.add_argument("-f", "--follow", action="store_true", help="Follow new events live")
    log_sub = log.add_subparsers(dest="log_command")
    log_why = log_sub.add_parser("why", help="Event timeline + history for a task")
    log_why.add_argument("slug")
    log_blame = log_sub.add_parser("blame", help="Tasks/locks/events that touched a file")
    log_blame.add_argument("file")

    config_cmd = sub.add_parser(
        "config",
        help="Manage frog hosts and workspaces",
    )
    config_sub = config_cmd.add_subparsers(dest="config_command", required=True)
    config_sub.add_parser("info", help="Show config path and current workspace")
    config_host = config_sub.add_parser("host", help="Manage host definitions")
    config_host_sub = config_host.add_subparsers(dest="config_host_command", required=True)
    config_host_add = config_host_sub.add_parser("add", help="Add or update a host")
    config_host_add.add_argument("name")
    config_host_add.add_argument("--ssh", dest="ssh_target")
    config_host_add.add_argument("--transport", default="ssh")
    config_host_add.add_argument("--notes")
    config_host_sub.add_parser("list", help="List configured hosts")
    config_workspace = config_sub.add_parser("workspace", help="Manage named workspaces")
    config_workspace_sub = config_workspace.add_subparsers(dest="config_workspace_command", required=True)
    config_workspace_add = config_workspace_sub.add_parser("add", help="Add or update a workspace")
    config_workspace_add.add_argument("name")
    config_workspace_add.add_argument("--host", required=True)
    config_workspace_add.add_argument("--root", required=True)
    config_workspace_add.add_argument("--db")
    config_workspace_add.add_argument("--notes")
    config_workspace_add.add_argument("--default", action="store_true")
    config_workspace_list = config_workspace_sub.add_parser("list", help="List configured workspaces")
    config_workspace_list_view = config_workspace_list.add_mutually_exclusive_group()
    config_workspace_list_view.add_argument("-1", dest="one", action="store_true", help="One workspace per line")
    config_workspace_list_view.add_argument("-l", dest="long", action="store_true", help="Long workspace listing")
    config_workspace_use = config_workspace_sub.add_parser("use", help="Select the default workspace")
    config_workspace_use.add_argument("name")
    config_path_cmd = config_sub.add_parser("path", help="Show how to add frog to PATH")
    config_path_cmd.add_argument("shell", choices=["bash", "fish"])

    mcp = sub.add_parser(
        "mcp",
        help="Expose frog operations over a stdio MCP server",
    )
    mcp_sub = mcp.add_subparsers(dest="mcp_command", required=True)
    mcp_sub.add_parser("serve", help="Run a stdio MCP server")
    mcp_sub.add_parser("tools", help="List the MCP tools that frog exposes")

    sync_cmd = sub.add_parser(
        "sync",
        help="Mirror another workspace's event log locally (read-only)",
    )
    sync_sub = sync_cmd.add_subparsers(dest="sync_command", required=True)
    sync_pull = sync_sub.add_parser("pull", help="Pull new events from a workspace")
    sync_pull.add_argument("workspace")
    sync_pull.add_argument("--limit", type=int, default=500,
                           help="Max remote events to fetch per pull")
    sync_list = sync_sub.add_parser("list", help="Show mirrored events")
    sync_list.add_argument("workspace", nargs="?")
    sync_list.add_argument("--limit", type=int, default=20)

    agent_cmd = sub.add_parser("agent", help="Acting-agent identity")
    agent_sub = agent_cmd.add_subparsers(dest="agent_command", required=True)
    agent_sub.add_parser("whoami", help="Show the resolved acting agent + session")
    ag_reg = agent_sub.add_parser("register", help="Register/update an agent")
    ag_reg.add_argument("name", nargs="?", help="Defaults to the resolved agent")
    ag_reg.add_argument("--kind")
    ag_reg.add_argument("--notes")

    repo = sub.add_parser(
        "repo",
        help="Repo registry, target discovery, and repo-level actions",
        description=(
            "Repo registry + per-repo actions. Strict grammar:\n"
            "  frog repo <subcommand> [REPO]\n"
            "  REPO is optional for action subcommands; defaults to the cwd repo.\n"
            "    frog repo info mailstack\n"
            "    frog repo build mailstack\n"
            "    frog repo build              # cwd repo\n"
            "  'discover' walks a root, registers repos in AGENTS.db, can scan them.\n"
            "  'scan' detects targets from manifests in one repo; 'detect' aliases 'scan';\n"
            "  'sync' aliases 'discover'."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    repo_sub = repo.add_subparsers(
        dest="repo_command",
        required=True,
        metavar="{list,register,discover,sync,info,task,artifact-stale,artifacts,build,check,clean,detect,diff,doctor,lint,scan,status,targets,test,verify}",
    )
    repo_list = repo_sub.add_parser("list", help="List registered repos")
    repo_list.add_argument("--include-third-party", action="store_true")
    repo_list_view = repo_list.add_mutually_exclusive_group()
    repo_list_view.add_argument("-1", dest="one", action="store_true", help="One repo per line with activity fields")
    repo_list_view.add_argument("-l", dest="long", action="store_true", help="Long repo listing with activity and scope")
    repo_register = repo_sub.add_parser("register", help="Register a repo path in AGENTS.db")
    repo_register.add_argument("repo_path")
    repo_register.add_argument("--name")
    repo_register.add_argument("--kind")
    repo_register.add_argument("--status", default="active")
    repo_register.add_argument("--third-party", action="store_true")
    repo_register.add_argument("--notes")
    repo_discover = repo_sub.add_parser("discover", help="Find repos under a root path and update AGENTS.db")
    repo_discover.add_argument("--root")
    repo_discover.add_argument("--no-scan", action="store_true", help="Register repos without scanning targets")
    repo_sync = repo_sub.add_parser("sync", help="Alias for discover")
    repo_sync.add_argument("--root")
    repo_sync.add_argument("--no-scan", action="store_true", help="Register repos without scanning targets")
    repo_info = repo_sub.add_parser("info", help="Show repo metadata and counts")
    repo_info.add_argument("repo_ref")
    repo_task_inline = repo_sub.add_parser("task", help="Repo-scoped task commands")
    repo_task_inline_sub = repo_task_inline.add_subparsers(dest="repo_task_inline_command", required=True)
    repo_task_inline_list = repo_task_inline_sub.add_parser("list", help="List tasks for one repo")
    repo_task_inline_list.add_argument("--repo", dest="repo_ref", metavar="REPO", required=True)
    repo_dep = repo_sub.add_parser("dep", help="Declared cross-repo dependency edges")
    repo_dep_sub = repo_dep.add_subparsers(dest="repo_dep_command", required=True)
    rd_add = repo_dep_sub.add_parser("add", help="Declare: DEPENDENT depends on DEPENDENCY")
    rd_add.add_argument("dependent")
    rd_add.add_argument("dependency")
    rd_add.add_argument("--note")
    rd_list = repo_dep_sub.add_parser("list", help="List dependency edges")
    rd_list.add_argument("repo_ref", nargs="?", help="Filter to one repo")

    repo_affected = repo_sub.add_parser(
        "affected", help="List targets affected by working-tree / since-REF changes")
    repo_affected.add_argument("repo_ref", nargs="?",
                               help="Repo name or path; defaults to cwd repo")
    repo_affected.add_argument("--since", help="Diff since this git ref "
                               "(default: working-tree changes vs HEAD)")
    _runnable = {"build", "clean", "test", "lint", "check", "verify"}
    for command_name in sorted(REPO_ACTIONS):
        cmd = repo_sub.add_parser(command_name, help=REPO_ACTION_HELP[command_name])
        cmd.add_argument("repo_ref", nargs="?", help="Repo name or path; defaults to cwd repo when omitted")
        if command_name in _runnable:
            cmd.add_argument(
                "--no-cache", dest="no_cache", action="store_true",
                help="Ignore the target_runs cache; always run",
            )
            cmd.add_argument(
                "--affected", dest="affected", action="store_true",
                help="Only run targets affected by changed files",
            )
            cmd.add_argument(
                "--since", dest="since",
                help="With --affected: diff since this git ref",
            )
        if command_name == "diff":
            cmd.add_argument("--stat", action="store_true")
            cmd.add_argument("--tasks", action="store_true")
            cmd.add_argument("--impact", action="store_true")

    unit = sub.add_parser(
        "unit",
        help="Discover and inspect nested buildable units inside repos",
    )
    unit_sub = unit.add_subparsers(dest="unit_command", required=True)
    unit_discover = unit_sub.add_parser("discover", help="Discover units in one repo or all repos")
    unit_discover.add_argument("--repo", dest="repo_ref", metavar="REPO")
    unit_discover.add_argument("--all-repos", action="store_true")
    unit_list = unit_sub.add_parser("list", help="List units in one repo or across all repos")
    unit_list.add_argument("--repo", dest="repo_ref", metavar="REPO")
    unit_list_view = unit_list.add_mutually_exclusive_group()
    unit_list_view.add_argument("-1", dest="one", action="store_true", help="One unit per line with activity fields")
    unit_list_view.add_argument("-l", dest="long", action="store_true", help="Long unit listing with activity")

    task = sub.add_parser(
        "task",
        help="Create and manage task slices in AGENTS.db",
    )
    task_sub = task.add_subparsers(dest="task_command", required=True)
    task_create = task_sub.add_parser("create", help="Create a task slice")
    task_create.add_argument("--slug", required=True)
    task_create.add_argument("--repo", dest="repo_ref", metavar="REPO")
    task_create.add_argument("--title", required=True)
    task_create.add_argument("--why")
    task_create.add_argument("--what")
    task_create.add_argument("--roi-note")
    task_create.add_argument("--priority", default="p3")
    task_create.add_argument("--workflow-status", default="idea")
    task_create.add_argument("--git-status", default="not_started")
    task_create.add_argument("--assigned-agent")
    task_create.add_argument("--delegation-current")
    task_create.add_argument("--delegation-other")
    task_create.add_argument("--parent-task-slug")
    task_list = task_sub.add_parser("list", help="List tasks")
    task_list.add_argument("--repo", dest="repo_ref", metavar="REPO")
    task_list.add_argument("--workflow-status")
    task_list.add_argument("--assigned-agent")
    task_next = task_sub.add_parser(
        "next", help="Highest-ROI unblocked slice you can safely take now")
    task_next.add_argument("--agent", help="Acting agent (default $USER)")
    task_next.add_argument("--repo", dest="repo_ref", metavar="REPO")
    task_next.add_argument("--limit", type=int, default=1)
    task_claim = task_sub.add_parser("claim", help="Take ownership + lock + mark in_progress")
    task_claim.add_argument("slug")
    task_claim.add_argument("--agent")
    task_claim.add_argument("--lock-kind", default="edit")
    task_claim.add_argument("--force", action="store_true")
    task_finish = task_sub.add_parser("finish", help="Verify (affected build/test) -> done + release + report unblocks")
    task_finish.add_argument("slug")
    task_finish.add_argument("--agent")
    task_finish.add_argument("--no-verify", dest="no_verify", action="store_true")
    task_info = task_sub.add_parser("info", help="Show one task")
    task_info.add_argument("slug")
    task_status = task_sub.add_parser("status", help="Update or show task status")
    task_status.add_argument("slug")
    task_status.add_argument("--workflow-status")
    task_status.add_argument("--git-status")
    task_status.add_argument("--note")
    task_dep = task_sub.add_parser("dependency", help="Add a task dependency")
    task_dep.add_argument("slug")
    task_dep.add_argument("depends_on_slug")
    task_dep.add_argument("--relation", default="depends_on")
    task_conflict = task_sub.add_parser("conflict", help="Add a task conflict")
    task_conflict.add_argument("slug")
    task_conflict.add_argument("conflicts_with_slug")
    task_conflict.add_argument("--reason")
    task_tag = task_sub.add_parser("tag", help="Add tags to a task")
    task_tag.add_argument("slug")
    task_tag.add_argument("tags", nargs="+")
    task_assign = task_sub.add_parser("assign", help="Assign a task to an agent")
    task_assign.add_argument("slug")
    task_assign.add_argument("agent_name")
    task_assign.add_argument("--notes")

    lock = sub.add_parser(
        "lock",
        help="Acquire, inspect, renew, and release coordination locks",
    )
    lock_sub = lock.add_subparsers(dest="lock_command", required=True)
    lock_check = lock_sub.add_parser("check", help="Check whether a lock would conflict")
    lock_check.add_argument("--scope-key", required=True)
    lock_check.add_argument("--repo", dest="repo_ref", metavar="REPO")
    lock_check.add_argument("--file", action="append", default=[])
    lock_acquire = lock_sub.add_parser("acquire", help="Acquire a coordination lock")
    lock_acquire.add_argument("--scope-key", required=True)
    lock_acquire.add_argument("--repo", dest="repo_ref", metavar="REPO")
    lock_acquire.add_argument("--lock-kind", required=True)
    lock_acquire.add_argument("--file", action="append", default=[])
    lock_acquire.add_argument("--agent", required=True)
    lock_acquire.add_argument("--pid", type=int)
    lock_acquire.add_argument("--reason")
    lock_acquire.add_argument("--lease-seconds", type=int, default=1800)
    lock_acquire.add_argument("--eta-minutes", type=int)
    lock_acquire.add_argument("--force", action="store_true")
    lock_renew = lock_sub.add_parser("renew", help="Renew a lock lease or ETA")
    lock_renew.add_argument("lock_id", type=int)
    lock_renew.add_argument("--eta-minutes", type=int)
    lock_release = lock_sub.add_parser("release", help="Release a coordination lock")
    lock_release.add_argument("lock_id", type=int)
    lock_list = lock_sub.add_parser("list", help="List locks")
    lock_list.add_argument("--repo", dest="repo_ref", metavar="REPO")
    lock_list.add_argument("--include-inactive", action="store_true",
                           help="Alias for --status all")
    lock_list.add_argument("--status",
                           choices=["active", "stale", "released", "all"],
                           help="Filter by lock status (default: active)")
    lock_reap = lock_sub.add_parser(
        "reap", help="Mark lease-expired active locks as stale and report them")
    lock_audit = lock_sub.add_parser(
        "audit",
        help="Flag working-tree changes not covered by your active lock")
    lock_audit.add_argument("--repo", dest="repo_ref", metavar="REPO",
                            help="Repo to audit (defaults to the cwd repo)")
    lock_audit.add_argument("--agent",
                            help="Acting agent name (defaults to $USER)")
    lock_info = lock_sub.add_parser("info", help="Show one lock")
    lock_info.add_argument("lock_id", type=int)

    file_cmd = sub.add_parser(
        "file",
        help="Classify and inspect important files in AGENTS.db",
    )
    file_sub = file_cmd.add_subparsers(dest="file_command", required=True)
    file_upsert = file_sub.add_parser("upsert", help="Register or update one file")
    file_upsert.add_argument("file_path")
    file_upsert.add_argument("--repo", dest="repo_ref", metavar="REPO")
    file_upsert.add_argument("--file-type")
    file_upsert.add_argument("--source-of-truth")
    file_upsert.add_argument("--notes")
    file_list = file_sub.add_parser("list", help="List files")
    file_list.add_argument("--repo", dest="repo_ref", metavar="REPO")
    file_list.add_argument("--file-type")
    file_info = file_sub.add_parser("info", help="Show one file")
    file_info.add_argument("file_path")
    return parser


def _run_repo_action(conn, repo_ref: str | None, action: str, args) -> dict:
    if not repo_ref:
        inferred = store.infer_repo_from_cwd(conn)
        if not inferred:
            return {
                "ok": False,
                "error": "no repo specified and cwd is not inside a known or discoverable repo",
            }
        repo_ref = inferred["repo_path"]
    if action in {"scan", "detect"}:
        return store.repo_scan(conn, repo_ref)
    if action == "targets":
        return store.repo_targets(conn, repo_ref)
    if action == "doctor":
        return store.repo_doctor(conn, repo_ref)
    if action == "artifacts":
        return store.repo_artifacts(conn, repo_ref)
    if action == "artifact-stale":
        return store.repo_artifacts_stale(conn, repo_ref)
    if action == "diff":
        return store.repo_diff(conn, repo_ref, stat_only=args.stat, include_tasks=args.tasks, include_impact=args.impact)
    if action == "status":
        return store.status_summary(conn, repo_ref=repo_ref)
    if action == "affected":
        return store.repo_affected(conn, repo_ref, since=getattr(args, "since", None))
    use_cache = not getattr(args, "no_cache", False)
    only = None
    if getattr(args, "affected", False):
        aff = store.repo_affected(
            conn, repo_ref, since=getattr(args, "since", None), target_kind=action
        )
        if not aff.get("ok"):
            return aff
        only = {t["name"] for t in aff["affected"]}
    return store.repo_run(conn, repo_ref, action, use_cache=use_cache, only_targets=only)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "completion":
        script = _completion_script(args.shell)
        if args.json:
            return _emit({"ok": True, "shell": args.shell, "script": script}, args.json)
        print(script, end="")
        return 0
    if args.command == "mcp":
        if args.mcp_command == "serve" and args.json:
            return _emit(
                {
                    "ok": False,
                    "error": "frog mcp serve speaks MCP over stdio and does not support --json",
                },
                args.json,
            )
        if args.mcp_command == "serve":
            return mcp_server.serve(config_path=args.config)
        if args.mcp_command == "tools":
            return _emit({"ok": True, "tools": mcp_server._tool_specs()}, args.json)
    if args.command == "config":
        if args.config_command == "info":
            return _emit(frog_config.info(args.config), args.json)
        if args.config_command == "host":
            if args.config_host_command == "add":
                return _emit(
                    frog_config.add_host(
                        args.name,
                        ssh_target=args.ssh_target,
                        transport=args.transport,
                        notes=args.notes,
                        path=args.config,
                    ),
                    args.json,
                )
            if args.config_host_command == "list":
                return _emit(frog_config.list_hosts(args.config), args.json)
        if args.config_command == "workspace":
            if args.config_workspace_command == "add":
                return _emit(
                    frog_config.add_workspace(
                        args.name,
                        host_name=args.host,
                        root=args.root,
                        db=args.db,
                        notes=args.notes,
                        use_default=args.default,
                        path=args.config,
                    ),
                    args.json,
                )
            if args.config_workspace_command == "list":
                return _emit(_payload_with_view(frog_config.list_workspaces(args.config), args), args.json)
            if args.config_workspace_command == "use":
                return _emit(frog_config.use_workspace(args.name, args.config), args.json)
        if args.config_command == "path":
            return _emit(frog_config.path_setup(args.shell, args.config), args.json)
    if args.command == "log" and getattr(args, "follow", False) and not getattr(args, "log_command", None):
        workspace = frog_config.resolve_workspace(args.workspace, args.config)
        if workspace and workspace["host"].get("transport") != "local":
            return _emit(_dispatch_workspace(workspace, argv), args.json)
        if workspace and workspace["host"].get("transport") == "local":
            args.db = workspace["db"]
        conn = store.connect(args.db)
        try:
            _record_command(conn, argv)
            return _follow_log(conn, repo_ref=args.repo_ref, limit=args.limit)
        finally:
            conn.close()
    if args.command == "db":
        workspace = frog_config.resolve_workspace(args.workspace, args.config)
        if workspace and workspace["host"].get("transport") != "local":
            return _emit(_dispatch_workspace(workspace, argv), args.json)
        if workspace and workspace["host"].get("transport") == "local":
            args.db = workspace["db"]
        if args.init_command == "migrate":
            return _emit(store.migrate(args.db), args.json)
        if args.init_command == "schema":
            return _emit(store.schema_status(args.db), args.json)

    conn = store.connect(args.db)
    try:
        workspace = _workspace_for_args(args, conn)
        if workspace and workspace["host"].get("transport") != "local":
            return _emit(_dispatch_workspace(workspace, argv), args.json)
        if workspace and workspace["host"].get("transport") == "local":
            args.db = workspace["db"]
            conn.close()
            conn = store.connect(args.db)
        _record_command(conn, argv)
        if args.command == "new":
            return _emit(
                store.init_repo(conn, args.path_or_name, kind=args.kind, notes=args.notes),
                args.json,
            )
        if args.command == "agent-instructions":
            return _emit(store.write_agent_instructions(conn, args.path, force=args.force), args.json)
        if args.command == "db" and getattr(args, "init_command", None) == "gc":
            return _emit(
                store.db_gc(conn, older_than_days=args.older_than, keep=args.keep),
                args.json,
            )
        if args.command == "snapshot":
            return _emit(store.snapshot_workspace(conn), args.json)
        if args.command == "ps":
            return _emit(store.ps_summary(conn, repo_ref=args.repo_ref), args.json)
        if args.command == "status":
            return _emit(store.status_summary(conn, repo_ref=args.repo_ref), args.json)
        if args.command == "log":
            lc = getattr(args, "log_command", None)
            if lc == "why":
                return _emit(store.log_why(conn, args.slug), args.json)
            if lc == "blame":
                return _emit(store.log_blame(conn, args.file), args.json)
            return _emit(store.log_tail(conn, limit=args.limit, repo_ref=args.repo_ref), args.json)
        if args.command == "repo":
            if args.repo_command == "list":
                return _emit(
                    _payload_with_view(
                        store.repo_list_with_activity(conn, include_third_party=args.include_third_party),
                        args,
                    ),
                    args.json,
                )
            if args.repo_command == "register":
                return _emit(
                    store.register_repo(
                        conn,
                        repo_path=args.repo_path,
                        name=args.name,
                        kind=args.kind,
                        status=args.status,
                        third_party=args.third_party,
                        notes=args.notes,
                    ),
                    args.json,
                )
            if args.repo_command in {"discover", "sync"}:
                root = args.root or (workspace["root"] if workspace else "/data/src")
                return _emit(store.discover_repos(conn, root=root, scan=not args.no_scan), args.json)
            if args.repo_command == "info":
                return _emit(store.repo_info(conn, args.repo_ref), args.json)
            if args.repo_command == "task":
                return _emit(store.task_list(conn, repo_ref=args.repo_ref, workflow_status=None, assigned_agent=None), args.json)
            if args.repo_command == "dep":
                if args.repo_dep_command == "add":
                    return _emit(store.repo_dep_add(conn, args.dependent, args.dependency, args.note), args.json)
                if args.repo_dep_command == "list":
                    return _emit(store.repo_dep_list(conn, args.repo_ref), args.json)
            if args.repo_command == "affected":
                return _emit(store.repo_affected(conn, args.repo_ref, since=getattr(args, "since", None)), args.json)
            return _emit(_run_repo_action(conn, args.repo_ref, args.repo_command, args), args.json)
        if args.command == "unit":
            if args.unit_command == "discover":
                return _emit(store.unit_discover(conn, repo_ref=args.repo_ref, all_repos=args.all_repos), args.json)
            if args.unit_command == "list":
                return _emit(_payload_with_view(store.unit_list(conn, repo_ref=args.repo_ref), args), args.json)
        if args.command == "task":
            if args.task_command == "create":
                return _emit(
                    store.create_task(
                        conn,
                        slug=args.slug,
                        repo_ref=args.repo_ref,
                        title=args.title,
                        why=args.why,
                        what_text=args.what,
                        roi_note=args.roi_note,
                        priority=args.priority,
                        workflow_status=args.workflow_status,
                        git_status=args.git_status,
                        assigned_agent=args.assigned_agent,
                        delegation_current=args.delegation_current,
                        delegation_other=args.delegation_other,
                        parent_task_slug=args.parent_task_slug,
                    ),
                    args.json,
                )
            if args.task_command == "list":
                return _emit(store.task_list(conn, repo_ref=args.repo_ref, workflow_status=args.workflow_status, assigned_agent=args.assigned_agent), args.json)
            if args.task_command == "claim":
                return _emit(store.task_claim(conn, slug=args.slug,
                    agent=(args.agent or store.current_agent()),
                    lock_kind=args.lock_kind, force=args.force), args.json)
            if args.task_command == "finish":
                return _emit(store.task_finish(conn, slug=args.slug,
                    agent=(args.agent or store.current_agent()),
                    verify=not args.no_verify), args.json)
            if args.task_command == "next":
                agent = args.agent or store.current_agent()
                return _emit(
                    store.task_next(conn, agent=agent,
                                    repo_ref=args.repo_ref, limit=args.limit),
                    args.json,
                )
            if args.task_command == "info":
                return _emit(store.task_info(conn, args.slug), args.json)
            if args.task_command == "status":
                return _emit(store.task_set_status(conn, slug=args.slug, workflow_status=args.workflow_status, git_status=args.git_status, note=args.note), args.json)
            if args.task_command == "dependency":
                return _emit(store.task_add_dependency(conn, args.slug, args.depends_on_slug, args.relation), args.json)
            if args.task_command == "conflict":
                return _emit(store.task_add_conflict(conn, args.slug, args.conflicts_with_slug, args.reason), args.json)
            if args.task_command == "tag":
                return _emit(store.task_add_tags(conn, args.slug, args.tags), args.json)
            if args.task_command == "assign":
                return _emit(store.task_assign(conn, args.slug, args.agent_name, args.notes), args.json)
        if args.command == "lock":
            if args.lock_command == "check":
                return _emit(store.lock_check(conn, scope_key=args.scope_key, repo_ref=args.repo_ref, files=args.file), args.json)
            if args.lock_command == "acquire":
                return _emit(
                    store.lock_acquire(
                        conn,
                        scope_key=args.scope_key,
                        repo_ref=args.repo_ref,
                        lock_kind=args.lock_kind,
                        files=args.file,
                        agent=args.agent,
                        pid=args.pid,
                        reason=args.reason,
                        lease_seconds=args.lease_seconds,
                        eta_minutes=args.eta_minutes,
                        force=args.force,
                    ),
                    args.json,
                )
            if args.lock_command == "renew":
                return _emit(store.lock_renew(conn, args.lock_id, args.eta_minutes), args.json)
            if args.lock_command == "release":
                return _emit(store.lock_release(conn, args.lock_id, force=False), args.json)
            if args.lock_command == "list":
                return _emit(
                    store.lock_list(
                        conn,
                        repo_ref=args.repo_ref,
                        include_inactive=args.include_inactive,
                        status=args.status,
                    ),
                    args.json,
                )
            if args.lock_command == "reap":
                return _emit(store.lock_reap(conn), args.json)
            if args.lock_command == "audit":
                agent = args.agent or store.current_agent()
                return _emit(
                    store.lock_audit(conn, repo_ref=args.repo_ref, agent=agent),
                    args.json,
                )
            if args.lock_command == "info":
                return _emit(store.lock_info(conn, args.lock_id), args.json)
        if args.command == "file":
            if args.file_command == "upsert":
                repo_path = None
                if args.repo_ref:
                    repo = store.resolve_repo(conn, args.repo_ref)
                    if not repo:
                        return _emit({"ok": False, "error": f"repo not found: {args.repo_ref}"}, args.json)
                    repo_path = repo["repo_path"]
                return _emit(
                    store.upsert_file(
                        conn,
                        file_path=args.file_path,
                        repo_path=repo_path,
                        file_type=args.file_type,
                        source_of_truth=args.source_of_truth,
                        notes=args.notes,
                    ),
                    args.json,
                )
            if args.file_command == "list":
                return _emit(store.file_list(conn, repo_ref=args.repo_ref, file_type=args.file_type), args.json)
            if args.file_command == "info":
                return _emit(store.file_info(conn, args.file_path), args.json)
        if args.command == "sync":
            if args.sync_command == "pull":
                ws = frog_config.resolve_workspace(args.workspace, args.config)
                if not ws:
                    return _emit({"ok": False, "error": f"unknown workspace: {args.workspace}"}, args.json)
                if ws["host"].get("transport", "local") == "local":
                    return _emit({"ok": True, "message": f"workspace {args.workspace} is local; nothing to mirror"}, args.json)
                remote = _dispatch_workspace(ws, ["log", "--limit", str(args.limit)])
                if not remote.get("ok", True):
                    return _emit(remote, args.json)
                return _emit(
                    store.event_mirror_pull(conn, workspace=args.workspace,
                                            events=remote.get("events", [])),
                    args.json,
                )
            if args.sync_command == "list":
                return _emit(
                    store.event_mirror_list(conn, workspace=args.workspace, limit=args.limit),
                    args.json,
                )
        if args.command == "agent":
            if args.agent_command == "whoami":
                return _emit(store.agent_whoami(conn), args.json)
            if args.agent_command == "register":
                return _emit(store.agent_register(conn, args.name, kind=args.kind, notes=args.notes), args.json)
        return _emit({"ok": False, "error": "unsupported command"}, args.json)
    finally:
        conn.close()
