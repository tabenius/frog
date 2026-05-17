from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ragbaz_frog import DEFAULT_DB_PATH
from ragbaz_frog import store


def _preprocess(argv: list[str]) -> list[str]:
    globals_front = []
    remainder = []
    i = 0
    while i < len(argv):
        token = argv[i]
        if token == "--json":
            globals_front.append(token)
            i += 1
            continue
        if token == "--db" and i + 1 < len(argv):
            globals_front.extend([token, argv[i + 1]])
            i += 2
            continue
        remainder.append(token)
        i += 1
    argv = [*globals_front, *remainder]
    if len(argv) >= 4 and argv[0] == "repo" and argv[1] not in {
        "list",
        "register",
        "info",
        "task",
    } and argv[2] == "task":
        repo_ref = argv[1]
        subcommand = argv[3]
        rest = argv[4:]
        return ["repo-task", subcommand, "--repo-ref", repo_ref, *rest]
    return argv


def _completion_script(shell: str) -> str:
    commands = "init completion repo task lock file log status"
    if shell == "bash":
        return f"""_frog_complete() {{
  local cur prev words cword
  _init_completion || return
  local commands="{commands}"
  if [[ $COMP_CWORD -eq 1 ]]; then
    COMPREPLY=( $(compgen -W "$commands" -- "$cur") )
    return
  fi
  case "${{COMP_WORDS[1]}}" in
    completion) COMPREPLY=( $(compgen -W "bash fish" -- "$cur") ) ;;
    init) COMPREPLY=( $(compgen -W "migrate schema" -- "$cur") ) ;;
    repo) COMPREPLY=( $(compgen -W "list register info task" -- "$cur") ) ;;
    task) COMPREPLY=( $(compgen -W "create list info status dependency conflict tag assign" -- "$cur") ) ;;
    lock) COMPREPLY=( $(compgen -W "check acquire renew release list info" -- "$cur") ) ;;
    file) COMPREPLY=( $(compgen -W "upsert list info" -- "$cur") ) ;;
    log) COMPREPLY=( $(compgen -W "tail" -- "$cur") ) ;;
  esac
}}
complete -F _frog_complete frog
"""
    if shell == "fish":
        return """complete -c frog -f
complete -c frog -n '__fish_use_subcommand' -a 'init completion repo task lock file log status'
complete -c frog -n '__fish_seen_subcommand_from completion' -a 'bash fish'
complete -c frog -n '__fish_seen_subcommand_from init' -a 'migrate schema'
complete -c frog -n '__fish_seen_subcommand_from repo' -a 'list register info task'
complete -c frog -n '__fish_seen_subcommand_from task' -a 'create list info status dependency conflict tag assign'
complete -c frog -n '__fish_seen_subcommand_from lock' -a 'check acquire renew release list info'
complete -c frog -n '__fish_seen_subcommand_from file' -a 'upsert list info'
complete -c frog -n '__fish_seen_subcommand_from log' -a 'tail'
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
    if "message" in payload:
        print(payload["message"])
    elif "repos" in payload:
        for repo in payload["repos"]:
            marker = " [3p]" if repo["third_party"] else ""
            print(f"{repo['name']}  {repo['repo_path']}  {repo['status']}{marker}")
    elif "repo" in payload:
        repo = payload["repo"]
        print(f"{repo['name']}  {repo['repo_path']}")
        print(f"status: {repo['status']}")
        if repo.get("kind"):
            print(f"kind: {repo['kind']}")
        if "counts" in payload:
            print(f"tasks: {payload['counts']['tasks']}")
            print(f"active_locks: {payload['counts']['active_locks']}")
    elif "tasks" in payload:
        for task in payload["tasks"]:
            repo = Path(task["repo_path"]).name if task["repo_path"] else "-"
            print(
                f"{task['slug']}  {task['priority']}  {task['workflow_status']}  "
                f"{task['git_status']}  {repo}"
            )
    elif "task" in payload:
        task = payload["task"]
        print(f"{task['slug']}  {task['title']}")
        print(f"repo: {task['repo_path'] or '-'}")
        print(f"priority: {task['priority']}")
        print(f"workflow_status: {task['workflow_status']}")
        print(f"git_status: {task['git_status']}")
        if task.get("assigned_agent"):
            print(f"assigned_agent: {task['assigned_agent']}")
        if payload.get("tags"):
            print(f"tags: {', '.join(payload['tags'])}")
        if payload.get("dependencies"):
            print(f"dependencies: {len(payload['dependencies'])}")
        if payload.get("conflicts"):
            print(f"conflicts: {len(payload['conflicts'])}")
    elif "locks" in payload:
        for lock in payload["locks"]:
            repo = Path(lock["repo_path"]).name if lock["repo_path"] else "-"
            print(
                f"{lock['id']}  {lock['lock_kind']}  {lock['scope_key']}  "
                f"{repo}  {lock['agent_name']}  {lock['status']}"
            )
    elif "lock" in payload:
        lock = payload["lock"]
        print(f"lock {lock['id']}  {lock['lock_kind']}  {lock['scope_key']}")
        print(f"repo: {lock['repo_path'] or '-'}")
        print(f"agent: {lock['agent_name']}")
        print(f"status: {lock['status']}")
        print(f"started_at: {lock['started_at']}")
        print(f"eta_finish_at: {lock['eta_finish_at'] or '-'}")
        if lock["file_paths"]:
            print("files:")
            for path in lock["file_paths"]:
                print(f"  - {path}")
    elif "files" in payload:
        for item in payload["files"]:
            print(f"{item['file_type'] or '-'}  {item['file_path']}")
    elif "file" in payload:
        item = payload["file"]
        print(f"{item['file_path']}")
        print(f"repo: {item['repo_path'] or '-'}")
        print(f"type: {item['file_type'] or '-'}")
        print(f"source_of_truth: {item['source_of_truth'] or '-'}")
    elif "events" in payload:
        for event in payload["events"]:
            print(f"{event['created_at']}  {event['kind']}  {event['summary']}")
    elif "counts" in payload:
        print(
            f"repos={payload['counts']['repos']} files={payload['counts']['files']} "
            f"tasks={payload['counts']['tasks']} active_locks={payload['counts']['active_locks']}"
        )
        for item in payload.get("tasks_by_workflow_status", []):
            print(f"  {item['workflow_status']}: {item['count']}")
    else:
        json.dump(payload, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RAGBAZ workspace coordination CLI")
    parser.add_argument("--db", default=DEFAULT_DB_PATH)
    parser.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init")
    init_sub = init.add_subparsers(dest="init_command", required=True)
    init_sub.add_parser("migrate")
    init_sub.add_parser("schema")

    completion = sub.add_parser("completion")
    completion.add_argument("shell", choices=["bash", "fish"])

    status = sub.add_parser("status")
    status.add_argument("--repo-ref")

    log = sub.add_parser("log")
    log_sub = log.add_subparsers(dest="log_command", required=True)
    tail = log_sub.add_parser("tail")
    tail.add_argument("--limit", type=int, default=20)
    tail.add_argument("--repo-ref")

    repo = sub.add_parser("repo")
    repo_sub = repo.add_subparsers(dest="repo_command", required=True)
    repo_list = repo_sub.add_parser("list")
    repo_list.add_argument("--include-third-party", action="store_true")
    repo_register = repo_sub.add_parser("register")
    repo_register.add_argument("repo_path")
    repo_register.add_argument("--name")
    repo_register.add_argument("--kind")
    repo_register.add_argument("--status", default="active")
    repo_register.add_argument("--third-party", action="store_true")
    repo_register.add_argument("--notes")
    repo_info = repo_sub.add_parser("info")
    repo_info.add_argument("repo_ref")

    repo_task_cmd = repo_sub.add_parser("task")
    repo_task_sub = repo_task_cmd.add_subparsers(dest="repo_task_inline_command", required=True)
    repo_task_inline_list = repo_task_sub.add_parser("list")
    repo_task_inline_list.add_argument("--repo-ref", required=True)

    repo_task = sub.add_parser("repo-task")
    repo_task_sub = repo_task.add_subparsers(dest="repo_task_command", required=True)
    repo_task_list = repo_task_sub.add_parser("list")
    repo_task_list.add_argument("--repo-ref", required=True)

    task = sub.add_parser("task")
    task_sub = task.add_subparsers(dest="task_command", required=True)
    task_create = task_sub.add_parser("create")
    task_create.add_argument("--slug", required=True)
    task_create.add_argument("--repo-ref")
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

    task_list = task_sub.add_parser("list")
    task_list.add_argument("--repo-ref")
    task_list.add_argument("--workflow-status")
    task_list.add_argument("--assigned-agent")

    task_info = task_sub.add_parser("info")
    task_info.add_argument("slug")

    task_status = task_sub.add_parser("status")
    task_status.add_argument("slug")
    task_status.add_argument("--workflow-status")
    task_status.add_argument("--git-status")
    task_status.add_argument("--note")

    task_dep = task_sub.add_parser("dependency")
    task_dep.add_argument("slug")
    task_dep.add_argument("depends_on_slug")
    task_dep.add_argument("--relation", default="depends_on")

    task_conflict = task_sub.add_parser("conflict")
    task_conflict.add_argument("slug")
    task_conflict.add_argument("conflicts_with_slug")
    task_conflict.add_argument("--reason")

    task_tag = task_sub.add_parser("tag")
    task_tag.add_argument("slug")
    task_tag.add_argument("tags", nargs="+")

    task_assign = task_sub.add_parser("assign")
    task_assign.add_argument("slug")
    task_assign.add_argument("agent_name")
    task_assign.add_argument("--notes")

    lock = sub.add_parser("lock")
    lock_sub = lock.add_subparsers(dest="lock_command", required=True)
    lock_check = lock_sub.add_parser("check")
    lock_check.add_argument("--scope-key", required=True)
    lock_check.add_argument("--repo-ref")
    lock_check.add_argument("--file", action="append", default=[])

    lock_acquire = lock_sub.add_parser("acquire")
    lock_acquire.add_argument("--scope-key", required=True)
    lock_acquire.add_argument("--repo-ref")
    lock_acquire.add_argument("--lock-kind", required=True)
    lock_acquire.add_argument("--file", action="append", default=[])
    lock_acquire.add_argument("--agent", required=True)
    lock_acquire.add_argument("--pid", type=int)
    lock_acquire.add_argument("--reason")
    lock_acquire.add_argument("--lease-seconds", type=int, default=1800)
    lock_acquire.add_argument("--eta-minutes", type=int)
    lock_acquire.add_argument("--force", action="store_true")

    lock_renew = lock_sub.add_parser("renew")
    lock_renew.add_argument("lock_id", type=int)
    lock_renew.add_argument("--eta-minutes", type=int)

    lock_release = lock_sub.add_parser("release")
    lock_release.add_argument("lock_id", type=int)

    lock_list = lock_sub.add_parser("list")
    lock_list.add_argument("--repo-ref")
    lock_list.add_argument("--include-inactive", action="store_true")

    lock_info = lock_sub.add_parser("info")
    lock_info.add_argument("lock_id", type=int)

    file_cmd = sub.add_parser("file")
    file_sub = file_cmd.add_subparsers(dest="file_command", required=True)
    file_upsert = file_sub.add_parser("upsert")
    file_upsert.add_argument("file_path")
    file_upsert.add_argument("--repo-ref")
    file_upsert.add_argument("--file-type")
    file_upsert.add_argument("--source-of-truth")
    file_upsert.add_argument("--notes")

    file_list = file_sub.add_parser("list")
    file_list.add_argument("--repo-ref")
    file_list.add_argument("--file-type")

    file_info = file_sub.add_parser("info")
    file_info.add_argument("file_path")
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = _preprocess(list(sys.argv[1:] if argv is None else argv))
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "completion":
        print(_completion_script(args.shell), end="")
        return 0
    if args.command == "init":
        if args.init_command == "migrate":
            return _emit(store.migrate(args.db), args.json)
        if args.init_command == "schema":
            return _emit(store.schema_status(args.db), args.json)

    conn = store.connect(args.db)
    try:
        if args.command == "status":
            return _emit(store.status_summary(conn, repo_ref=args.repo_ref), args.json)
        if args.command == "log":
            return _emit(
                store.log_tail(conn, limit=args.limit, repo_ref=args.repo_ref),
                args.json,
            )
        if args.command == "repo":
            if args.repo_command == "list":
                return _emit(store.repo_list(conn, include_third_party=args.include_third_party), args.json)
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
            if args.repo_command == "info":
                return _emit(store.repo_info(conn, args.repo_ref), args.json)
            if args.repo_command == "task":
                if args.repo_task_inline_command == "list":
                    return _emit(
                        store.task_list(
                            conn,
                            repo_ref=args.repo_ref,
                            workflow_status=None,
                            assigned_agent=None,
                        ),
                        args.json,
                    )
        if args.command == "repo-task":
            if args.repo_task_command == "list":
                return _emit(
                    store.task_list(
                        conn,
                        repo_ref=args.repo_ref,
                        workflow_status=None,
                        assigned_agent=None,
                    ),
                    args.json,
                )
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
                return _emit(
                    store.task_list(
                        conn,
                        repo_ref=args.repo_ref,
                        workflow_status=args.workflow_status,
                        assigned_agent=args.assigned_agent,
                    ),
                    args.json,
                )
            if args.task_command == "info":
                return _emit(store.task_info(conn, args.slug), args.json)
            if args.task_command == "status":
                return _emit(
                    store.task_set_status(
                        conn,
                        slug=args.slug,
                        workflow_status=args.workflow_status,
                        git_status=args.git_status,
                        note=args.note,
                    ),
                    args.json,
                )
            if args.task_command == "dependency":
                return _emit(
                    store.task_add_dependency(conn, args.slug, args.depends_on_slug, args.relation),
                    args.json,
                )
            if args.task_command == "conflict":
                return _emit(
                    store.task_add_conflict(conn, args.slug, args.conflicts_with_slug, args.reason),
                    args.json,
                )
            if args.task_command == "tag":
                return _emit(store.task_add_tags(conn, args.slug, args.tags), args.json)
            if args.task_command == "assign":
                return _emit(store.task_assign(conn, args.slug, args.agent_name, args.notes), args.json)
        if args.command == "lock":
            if args.lock_command == "check":
                return _emit(
                    store.lock_check(
                        conn,
                        scope_key=args.scope_key,
                        repo_ref=args.repo_ref,
                        files=args.file,
                    ),
                    args.json,
                )
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
                    store.lock_list(conn, repo_ref=args.repo_ref, include_inactive=args.include_inactive),
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
                        return _emit(
                            {"ok": False, "error": f"repo not found: {args.repo_ref}"},
                            args.json,
                        )
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
        return _emit({"ok": False, "error": "unsupported command"}, args.json)
    finally:
        conn.close()
