"""GitHub adapter atop the task-provider contract.

All GitHub I/O goes through the injectable `_gh_exec` seam (wraps the
`gh` CLI) so the mapping logic is testable without network/auth.
"""
from __future__ import annotations

import json
import subprocess

from ragbaz_frog import store

_PRIO_LABELS = {"p0", "p1", "p2", "p3"}
_IN_PROGRESS_LABEL = "in progress"


def _gh_exec(args: list[str]) -> tuple[int, str, str]:  # pragma: no cover
    p = subprocess.run(["gh", *args], text=True, capture_output=True)
    return p.returncode, p.stdout, p.stderr


def _label_names(issue: dict) -> list[str]:
    return [str(label.get("name", "")) for label in issue.get("labels", [])]


def _issue_to_item(issue: dict) -> dict:
    labels = _label_names(issue)
    labels_lower = [label.lower() for label in labels]
    prio = next((label.lower() for label in labels if label.lower() in _PRIO_LABELS), "p3")
    status = "done" if issue.get("state", "").upper() == "CLOSED" else (
        "in_progress" if _IN_PROGRESS_LABEL in labels_lower
        else "open")
    return {
        "external_id": str(issue["number"]),
        "title": issue.get("title") or f"issue {issue['number']}",
        "status": status,
        "priority": prio,
        "why": (issue.get("body") or "")[:500] or None,
    }


def pull(conn, repo: str, *, exec=_gh_exec) -> dict:
    rc, out, err = exec(["issue", "list", "--repo", repo, "--state", "all",
                         "--json", "number,title,state,labels,body,assignees",
                         "--limit", "200"])
    if rc != 0:
        return {"ok": False, "error": f"gh issue list failed: {err.strip()}"}
    try:
        issues = json.loads(out or "[]")
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"gh issue list returned invalid JSON: {exc}"}
    items = [_issue_to_item(i) for i in issues]
    res = store.provider_sync_in(conn, "github", items)
    res["repo"] = repo
    return res


def _run_checked(exec, args: list[str], errors: list[dict]) -> None:
    rc, out, err = exec(args)
    if rc != 0:
        errors.append({"args": args, "error": err.strip() or out.strip()})


def push(conn, repo: str, *, exec=_gh_exec) -> dict:
    ob = store.provider_outbox(conn, "github")
    pushed = []
    errors = []
    for tk in ob["outbox"]:
        num = tk["external_id"]
        if tk["workflow_status"] == "done":
            _run_checked(exec, ["issue", "close", num, "--repo", repo], errors)
            pushed.append({"action": "close", "issue": num})
        elif tk["workflow_status"] in ("idea", "in_progress", "blocked"):
            _run_checked(exec, ["issue", "reopen", num, "--repo", repo], errors)
            pushed.append({"action": "reopen", "issue": num})
        priority = tk.get("priority") or "p3"
        if priority in _PRIO_LABELS:
            _run_checked(exec, ["issue", "edit", num, "--repo", repo,
                                "--add-label", priority], errors)
            pushed.append({"action": "label", "issue": num, "label": priority})
        if tk["workflow_status"] == "in_progress":
            _run_checked(exec, ["issue", "edit", num, "--repo", repo,
                                "--add-label", _IN_PROGRESS_LABEL], errors)
            pushed.append({"action": "label", "issue": num,
                           "label": _IN_PROGRESS_LABEL})
        assignee = tk.get("assigned_agent")
        if assignee:
            _run_checked(exec, ["issue", "edit", num, "--repo", repo,
                                "--add-assignee", assignee], errors)
            pushed.append({"action": "assignee", "issue": num,
                           "assignee": assignee})
    return {"ok": not errors, "repo": repo, "pushed": pushed,
            "errors": errors,
            "message": f"pushed {len(pushed)} issue state(s)"}


def sync(conn, repo: str, *, exec=_gh_exec) -> dict:
    inbound = pull(conn, repo, exec=exec)
    if not inbound.get("ok"):
        return {"ok": False, "repo": repo, "pull": inbound}
    outbound = push(conn, repo, exec=exec)
    return {"ok": outbound.get("ok", False), "repo": repo,
            "pull": inbound, "push": outbound}


def comment_board(conn, repo: str, pr: int, board_text: str,
                  *, exec=_gh_exec) -> dict:
    body = "```\n" + board_text + "\n```"
    rc, out, err = exec(["pr", "comment", str(pr), "--repo", repo,
                         "--body", body])
    return {"ok": rc == 0, "pr": pr,
            "error": None if rc == 0 else err.strip()}


def action_yaml() -> str:
    return """name: frog-affected
on: [pull_request]
jobs:
  affected:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - name: frog repo affected
        run: |
          pip install -e ./ragbaz-frog || true
          frog db migrate
          frog --json repo affected --since origin/${{ github.base_ref }}
"""
