"""HTTP adapters for external task providers.

The durable frog contract lives in store.provider_sync_in/provider_outbox.
This module only translates provider payloads to that normalized shape and
pushes frog status back through provider-specific update APIs.
"""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

from ragbaz_frog import store

RequestFn = Callable[[str, str, dict[str, str], dict | None], tuple[int, dict, str]]

_SUPPORTED = {"asana", "linear", "jira"}
_FROG_STATUSES = {"idea", "in_progress", "blocked", "done"}


def load_config(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _secret(config: dict, name: str = "token") -> str | None:
    env_name = config.get(f"{name}_env")
    if env_name:
        return os.environ.get(env_name)
    return config.get(name)


def _http_json(
    method: str, url: str, headers: dict[str, str], body: dict | None = None
) -> tuple[int, dict, str]:  # pragma: no cover - exercised through fake request seams
    data = None
    req_headers = dict(headers)
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        req_headers.setdefault("content-type", "application/json")
    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw or "{}"), ""
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw or "{}")
        except json.JSONDecodeError:
            payload = {}
        return exc.code, payload, raw
    except OSError as exc:
        return 0, {}, str(exc)


def _bearer(token: str | None) -> dict[str, str]:
    if not token:
        return {}
    return {"authorization": f"Bearer {token}"}


def _basic(email: str | None, token: str | None) -> dict[str, str]:
    if not email or not token:
        return {}
    raw = f"{email}:{token}".encode("utf-8")
    return {"authorization": "Basic " + base64.b64encode(raw).decode("ascii")}


def _status_from_text(text: str | None, status_map: dict | None = None) -> str:
    value = (text or "").strip().lower().replace(" ", "_").replace("-", "_")
    mapped = (status_map or {}).get(value) or (status_map or {}).get(text or "")
    if mapped in _FROG_STATUSES:
        return mapped
    if value in {"done", "closed", "complete", "completed", "resolved"}:
        return "done"
    if value in {"doing", "in_progress", "started", "active"}:
        return "in_progress"
    if value in {"blocked", "stuck"}:
        return "blocked"
    return "idea"


def _priority_from_text(text: str | None, priority_map: dict | None = None) -> str:
    value = (text or "").strip().lower()
    mapped = (priority_map or {}).get(value)
    if mapped in {"p0", "p1", "p2", "p3"}:
        return mapped
    if value in {"urgent", "highest", "critical", "blocker"}:
        return "p0"
    if value in {"high", "p1"}:
        return "p1"
    if value in {"medium", "normal", "p2"}:
        return "p2"
    return "p3"


def _fail(provider: str, action: str, status: int, err: str, payload: dict) -> dict:
    return {
        "ok": False,
        "source": provider,
        "error": f"{provider} {action} failed: {err or status}",
        "status": status,
        "payload": payload,
    }


def _asana_headers(config: dict) -> dict[str, str]:
    return {**_bearer(_secret(config)), "accept": "application/json"}


def _asana_item(task: dict, config: dict) -> dict:
    custom_status = None
    status_field = config.get("status_custom_field_gid")
    for field in task.get("custom_fields") or []:
        if status_field and field.get("gid") != status_field:
            continue
        enum_value = field.get("enum_value") or {}
        custom_status = enum_value.get("name") or field.get("text_value")
        if custom_status:
            break
    section_status = None
    memberships = task.get("memberships") or []
    if memberships:
        section_status = ((memberships[0].get("section") or {}).get("name"))
    status = "done" if task.get("completed") else _status_from_text(
        custom_status or section_status, config.get("status_map")
    )
    return {
        "external_id": str(task["gid"]),
        "title": task.get("name") or f"asana task {task['gid']}",
        "status": status,
        "priority": _priority_from_text(None, config.get("priority_map")),
        "why": task.get("notes") or None,
    }


def asana_pull(config: dict, *, request: RequestFn = _http_json) -> dict:
    project = config.get("project_gid")
    if not project:
        return {"ok": False, "source": "asana", "error": "project_gid is required"}
    base = config.get("base_url", "https://app.asana.com/api/1.0").rstrip("/")
    fields = ",".join([
        "gid",
        "name",
        "notes",
        "completed",
        "memberships.section.name",
        "custom_fields.gid",
        "custom_fields.text_value",
        "custom_fields.enum_value.name",
    ])
    query = urllib.parse.urlencode({"limit": "100", "opt_fields": fields})
    url = f"{base}/projects/{urllib.parse.quote(project)}/tasks?{query}"
    items: list[dict] = []
    while url:
        status, payload, err = request("GET", url, _asana_headers(config), None)
        if status < 200 or status >= 300:
            return _fail("asana", "pull", status, err, payload)
        items.extend(_asana_item(task, config) for task in payload.get("data", []))
        next_page = payload.get("next_page") or {}
        url = next_page.get("uri")
    return {"ok": True, "source": "asana", "items": items}


def asana_push(config: dict, outbox: list[dict], *, request: RequestFn = _http_json) -> dict:
    base = config.get("base_url", "https://app.asana.com/api/1.0").rstrip("/")
    pushed, errors, skipped = [], [], []
    for task in outbox:
        data: dict = {"completed": task["workflow_status"] == "done"}
        field = config.get("status_custom_field_gid")
        status_values = config.get("status_values", {})
        option = status_values.get(task["workflow_status"])
        if field and option:
            data["custom_fields"] = {field: option}
        elif field and task["workflow_status"] != "done":
            skipped.append({"external_id": task["external_id"], "reason": "missing status_values mapping"})
        url = f"{base}/tasks/{urllib.parse.quote(str(task['external_id']))}"
        status, payload, err = request("PUT", url, _asana_headers(config), {"data": data})
        if status < 200 or status >= 300:
            errors.append(_fail("asana", "push", status, err, payload))
        else:
            pushed.append({"external_id": task["external_id"], "fields": sorted(data.keys())})
    return {"ok": not errors, "source": "asana", "pushed": pushed, "skipped": skipped, "errors": errors}


def _linear_graphql(config: dict, query: str, variables: dict, *, request: RequestFn) -> tuple[int, dict, str]:
    url = config.get("url", "https://api.linear.app/graphql")
    token = _secret(config)
    headers = {"accept": "application/json"}
    if token:
        if config.get("auth_scheme") == "bearer":
            headers["authorization"] = f"Bearer {token}"
        else:
            headers["authorization"] = token
    return request("POST", url, headers, {"query": query, "variables": variables})


def _linear_priority(value) -> str:
    return {1: "p0", 2: "p1", 3: "p2", 4: "p3"}.get(value, "p3")


def linear_pull(config: dict, *, request: RequestFn = _http_json) -> dict:
    team_id = config.get("team_id")
    filter_arg = {"team": {"id": {"eq": team_id}}} if team_id else None
    query = """
query FrogIssues($first: Int!, $filter: IssueFilter) {
  issues(first: $first, filter: $filter) {
    nodes {
      id identifier title description priority
      state { name type }
      assignee { name email }
    }
  }
}
"""
    status, payload, err = _linear_graphql(
        config, query, {"first": int(config.get("limit", 100)), "filter": filter_arg}, request=request
    )
    if status < 200 or status >= 300 or payload.get("errors"):
        return _fail("linear", "pull", status, err or json.dumps(payload.get("errors", [])), payload)
    items = []
    for issue in payload.get("data", {}).get("issues", {}).get("nodes", []):
        state = issue.get("state") or {}
        items.append({
            "external_id": str(issue["id"]),
            "title": issue.get("title") or issue.get("identifier") or issue["id"],
            "status": _status_from_text(state.get("type") or state.get("name"), config.get("status_map")),
            "priority": _linear_priority(issue.get("priority")),
            "why": issue.get("description") or None,
        })
    return {"ok": True, "source": "linear", "items": items}


def linear_push(config: dict, outbox: list[dict], *, request: RequestFn = _http_json) -> dict:
    state_ids = config.get("state_ids", {})
    priority_values = config.get("priority_values", {"p0": 1, "p1": 2, "p2": 3, "p3": 4})
    mutation = """
mutation FrogIssueUpdate($id: String!, $input: IssueUpdateInput!) {
  issueUpdate(id: $id, input: $input) { success issue { id } }
}
"""
    pushed, errors, skipped = [], [], []
    for task in outbox:
        fields = {}
        if state_ids.get(task["workflow_status"]):
            fields["stateId"] = state_ids[task["workflow_status"]]
        else:
            skipped.append({"external_id": task["external_id"], "reason": "missing state_ids mapping"})
        if task.get("priority") in priority_values:
            fields["priority"] = priority_values[task["priority"]]
        if not fields:
            continue
        status, payload, err = _linear_graphql(
            config, mutation, {"id": task["external_id"], "input": fields}, request=request
        )
        ok = 200 <= status < 300 and not payload.get("errors") and payload.get("data", {}).get("issueUpdate", {}).get("success", True)
        if not ok:
            errors.append(_fail("linear", "push", status, err or json.dumps(payload.get("errors", [])), payload))
        else:
            pushed.append({"external_id": task["external_id"], "fields": sorted(fields.keys())})
    return {"ok": not errors, "source": "linear", "pushed": pushed, "skipped": skipped, "errors": errors}


def _jira_headers(config: dict) -> dict[str, str]:
    token = _secret(config, "api_token") or _secret(config)
    headers = {"accept": "application/json"}
    if config.get("email"):
        headers.update(_basic(config.get("email"), token))
    else:
        headers.update(_bearer(token))
    return headers


def jira_pull(config: dict, *, request: RequestFn = _http_json) -> dict:
    base = (config.get("base_url") or "").rstrip("/")
    if not base:
        return {"ok": False, "source": "jira", "error": "base_url is required"}
    body = {
        "jql": config.get("jql", "ORDER BY updated DESC"),
        "maxResults": int(config.get("limit", 100)),
        "fields": ["summary", "status", "priority", "assignee", "description"],
    }
    status, payload, err = request("POST", f"{base}/rest/api/3/search/jql", _jira_headers(config), body)
    if status < 200 or status >= 300:
        return _fail("jira", "pull", status, err, payload)
    items = []
    for issue in payload.get("issues", []):
        fields = issue.get("fields") or {}
        status_info = fields.get("status") or {}
        status_name = ((status_info.get("statusCategory") or {}).get("key") or status_info.get("name"))
        priority = (fields.get("priority") or {}).get("name")
        items.append({
            "external_id": issue.get("key") or issue["id"],
            "title": fields.get("summary") or issue.get("key") or issue["id"],
            "status": _status_from_text(status_name, config.get("status_map")),
            "priority": _priority_from_text(priority, config.get("priority_map")),
        })
    return {"ok": True, "source": "jira", "items": items}


def jira_push(config: dict, outbox: list[dict], *, request: RequestFn = _http_json) -> dict:
    base = (config.get("base_url") or "").rstrip("/")
    if not base:
        return {"ok": False, "source": "jira", "error": "base_url is required"}
    transitions = config.get("transition_ids", {})
    pushed, errors, skipped = [], [], []
    for task in outbox:
        transition = transitions.get(task["workflow_status"])
        if not transition:
            skipped.append({"external_id": task["external_id"], "reason": "missing transition_ids mapping"})
            continue
        url = f"{base}/rest/api/3/issue/{urllib.parse.quote(str(task['external_id']))}/transitions"
        status, payload, err = request(
            "POST", url, _jira_headers(config), {"transition": {"id": str(transition)}}
        )
        if status < 200 or status >= 300:
            errors.append(_fail("jira", "push", status, err, payload))
        else:
            pushed.append({"external_id": task["external_id"], "transition": str(transition)})
    return {"ok": not errors, "source": "jira", "pushed": pushed, "skipped": skipped, "errors": errors}


def pull(source: str, config: dict, *, request: RequestFn = _http_json) -> dict:
    if source == "asana":
        return asana_pull(config, request=request)
    if source == "linear":
        return linear_pull(config, request=request)
    if source == "jira":
        return jira_pull(config, request=request)
    return {"ok": False, "source": source, "error": f"unsupported provider: {source}"}


def push(conn, source: str, config: dict, *, request: RequestFn = _http_json) -> dict:
    if source not in _SUPPORTED:
        return {"ok": False, "source": source, "error": f"unsupported provider: {source}"}
    outbox = store.provider_outbox(conn, source)["outbox"]
    if source == "asana":
        return asana_push(config, outbox, request=request)
    if source == "linear":
        return linear_push(config, outbox, request=request)
    return jira_push(config, outbox, request=request)


def sync(
    conn,
    source: str,
    config: dict,
    *,
    direction: str = "both",
    request: RequestFn = _http_json,
) -> dict:
    if direction not in {"pull", "push", "both"}:
        return {"ok": False, "source": source, "error": f"unsupported direction: {direction}"}
    inbound = None
    if direction in {"pull", "both"}:
        inbound = pull(source, config, request=request)
        if not inbound.get("ok"):
            return {"ok": False, "source": source, "pull": inbound}
        store.provider_sync_in(conn, source, inbound["items"])
    outbound = None
    if direction in {"push", "both"}:
        outbound = push(conn, source, config, request=request)
    return {
        "ok": bool((outbound or inbound or {}).get("ok", True)),
        "source": source,
        "direction": direction,
        "pull": inbound,
        "push": outbound,
    }
