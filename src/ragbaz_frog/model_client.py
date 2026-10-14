"""Client for the Rust Frog model served over the versioned D1/HTTP contract.

The model owns task semantics; this module owns transport only: bounded reads,
TLS/redirect rules, token handling and honest failure. Nothing here interprets
workflow status or grants authority - the service does.
"""
from __future__ import annotations

import json
import os
import stat
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

CONTRACT = "frog.model.v1"
RESULT_SCHEMA = "frog.model-result.v1"
MAX_BODY = 1024 * 1024
TIMEOUT = 15
LOOPBACK = {"localhost", "127.0.0.1", "::1"}
MUTATIONS = {"register_repo", "create_task", "claim_task", "transition_task",
             "cancel_task", "add_dependency", "remove_dependency"}
# Frog's historical priority words, mapped to the model's numeric levels.
PRIORITIES = {"p0": 0, "p1": 1, "p2": 2, "p3": 3}
STATUSES = {"review": "review", "published": "published", "done": "done"}


class ModelError(RuntimeError):
    """A refusal, conflict or transport failure, with the code it arrived with."""

    def __init__(self, code: str, message: str, *, status: int | None = None):
        super().__init__(message)
        self.code = code
        self.status = status


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def read_token(path: str | os.PathLike[str]) -> str:
    """Read a bearer credential from an owner-only file, or the environment."""
    if path is None:
        token = os.environ.get("FROG_MODEL_TOKEN", "").strip()
        if len(token) < 32:
            raise ModelError("missing_token", "set --model-token-file or FROG_MODEL_TOKEN")
        return token
    descriptor = os.open(Path(path).expanduser(), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
        metadata = os.fstat(handle.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077:
            raise ModelError("unsafe_token_file", "model token file must be an owner-only regular file (0600)")
        token = handle.read(4097).strip()
    if not 32 <= len(token) <= 4096:
        raise ModelError("missing_token", "model token must contain 32-4096 characters")
    return token


def endpoint(url: str) -> str:
    """Accept only HTTPS, or plain HTTP to loopback; never credentials/query."""
    parts = urlsplit(str(url or "").strip())
    if not parts.scheme and not parts.netloc:
        raise ModelError("missing_url", "set --url or FROG_MODEL_URL")
    host = (parts.hostname or "").lower()
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ModelError("invalid_url", "model URL must not carry credentials, query or fragment")
    if parts.scheme == "http" and host in LOOPBACK:
        pass
    elif parts.scheme != "https":
        raise ModelError("invalid_url", "model URL must be https:// (plain http only on loopback)")
    if not host:
        raise ModelError("invalid_url", "model URL must name a host")
    return f"{parts.scheme}://{parts.netloc}{parts.path.rstrip('/')}"


class ModelClient:
    """Bounded, read/write client. Every call is one request/response."""

    def __init__(self, url: str, token: str | None = None, *, timeout: int = TIMEOUT):
        self.url = endpoint(url)
        self.token = token
        self.timeout = timeout
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def _request(self, method: str, path: str, *, body: dict | None = None) -> tuple[int, dict]:
        data = json.dumps(body, sort_keys=True).encode() if body is not None else None
        if data is not None and len(data) > 16384:
            raise ModelError("request_too_large", "model request exceeds the service limit")
        headers = {"Accept": "application/json", "User-Agent": "frog/1"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(f"{self.url}{path}", data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                return int(response.status), self._decode(response)
        except urllib.error.HTTPError as error:
            status = int(error.code)
            try:
                payload = self._decode(error)
            except ModelError:
                payload = {}
            error.close()
            return status, payload
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise ModelError("unreachable", f"model service unreachable ({type(error).__name__})") from None

    @staticmethod
    def _decode(response) -> dict:
        body = response.read(MAX_BODY + 1)
        if len(body) > MAX_BODY:
            raise ModelError("response_too_large", "model response exceeded the read cap")
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ModelError("not_json", "model service did not answer with JSON") from None
        if not isinstance(data, dict):
            raise ModelError("not_json", "model service did not answer with an object")
        return data

    def _ok(self, status: int, payload: dict) -> dict:
        if payload.get("schema") != RESULT_SCHEMA:
            raise ModelError("wrong_schema", "unexpected model result schema", status=status)
        items = payload.get("items")
        if not isinstance(items, list):
            raise ModelError("wrong_schema", "model result carried no item list", status=status)
        return payload

    def read(self, workspace: str, resource: str, params: dict) -> dict:
        query = urlencode({key: value for key, value in params.items() if value not in (None, "")})
        path = f"/v1/workspaces/{quote(workspace, safe='')}/{resource}"
        status, payload = self._request("GET", f"{path}?{query}" if query else path)
        if status == 401:
            raise ModelError("rejected_token", "model service rejected the credential", status=status)
        if status == 403:
            raise ModelError("forbidden", "this workspace role may not read that view", status=status)
        if status == 404:
            raise ModelError("not_found", "unknown workspace or view for this principal", status=status)
        if status != 200:
            raise ModelError("unavailable", payload.get("error") or f"model service returned {status}", status=status)
        return self._ok(status, payload)

    def write(self, workspace: str, operation_id: str, command: dict) -> dict:
        kind = command.get("kind")
        if kind not in MUTATIONS:
            raise ModelError("invalid_request", f"unsupported model operation: {kind}")
        body = {"operation_id": operation_id, "command": command}
        status, payload = self._request("POST", f"/v1/workspaces/{quote(workspace, safe='')}/operations", body=body)
        if status == 401:
            raise ModelError("rejected_token", "model service rejected the credential", status=status)
        if status == 403:
            raise ModelError("forbidden", "this workspace role may not write", status=status)
        if status == 404:
            raise ModelError("not_found", "unknown workspace for this principal", status=status)
        if status == 409:
            outcome = (payload.get("items") or [{}])[0].get("status", "conflict")
            raise ModelError(outcome, "operation did not change state; retry with a fresh operation id", status=status)
        if status != 200:
            raise ModelError("unavailable", payload.get("error") or f"model service returned {status}", status=status)
        return self._ok(status, payload)

    # --- typed operations, one call each -----------------------------------

    def board(self, workspace: str, *, repo: str | None = None, limit: int = 100,
              after: str | None = None) -> list[dict]:
        return self.read(workspace, "board", {"repo": repo, "limit": limit, "after": after})["items"]

    def repositories(self, workspace: str, *, limit: int = 200) -> list[dict]:
        return self.read(workspace, "repositories", {"limit": limit})["items"]

    def events(self, workspace: str, *, after: str = "0", limit: int = 100) -> list[dict]:
        return self.read(workspace, "events", {"after": after, "limit": limit})["items"]

    def task(self, workspace: str, task_id: str) -> dict | None:
        items = self.read(workspace, "tasks", {"task": task_id})["items"]
        return items[0] if items else None

    def register_repo(self, workspace: str, operation_id: str, repo_id: str, title: str) -> dict:
        return self.write(workspace, operation_id, {"kind": "register_repo", "repo_id": repo_id, "title": title})

    def create_task(self, workspace: str, operation_id: str, *, repo_id: str, task_id: str,
                    title: str, priority: str = "p3") -> dict:
        if priority.lower() not in PRIORITIES:
            raise ModelError("invalid_request", "priority must be p0, p1, p2 or p3")
        return self.write(workspace, operation_id, {"kind": "create_task", "repo_id": repo_id,
                                                    "task_id": task_id, "title": title,
                                                    "priority": PRIORITIES[priority.lower()]})

    def claim_task(self, workspace: str, operation_id: str, *, task_id: str, expected_revision: int) -> dict:
        return self.write(workspace, operation_id, {"kind": "claim_task", "task_id": task_id,
                                                    "expected_revision": expected_revision})

    def transition_task(self, workspace: str, operation_id: str, *, task_id: str,
                        expected_revision: int, status: str) -> dict:
        if status not in STATUSES:
            raise ModelError("invalid_request", "status must be review, published or done")
        return self.write(workspace, operation_id, {"kind": "transition_task", "task_id": task_id,
                                                    "expected_revision": expected_revision, "status": status})

    def cancel_task(self, workspace: str, operation_id: str, *, task_id: str, expected_revision: int) -> dict:
        return self.write(workspace, operation_id, {"kind": "cancel_task", "task_id": task_id,
                                                    "expected_revision": expected_revision})

    def depend(self, workspace: str, operation_id: str, *, task_id: str, depends_on: str,
               block: bool = True) -> dict:
        return self.write(workspace, operation_id, {"kind": "add_dependency" if block else "remove_dependency",
                                                    "task_id": task_id, "depends_on": depends_on})


def board_projection(client: ModelClient, workspace: str, *, repo: str | None = None,
                     limit: int = 200) -> dict:
    """Adapt the model into the existing `frog.kanban-projection.v0` contract.

    One mapping, so Rebekah, Dash and the packaged renderer keep working against
    either backend. Priorities become words, dependencies stay links, and the
    service's own capture time is preserved rather than invented here.
    """
    import datetime as dt

    def links(row: dict, key: str) -> list[str]:
        values = row.get(key) or []
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise ModelError("wrong_schema", f"model returned a non-text {key} list")
        return list(values)

    cards = []
    for row in client.board(workspace, repo=repo, limit=limit):
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not isinstance(row.get("title"), str):
            raise ModelError("wrong_schema", "model returned a task without an id or title")
        revision = int(row.get("revision") or 0)
        status = str(row.get("status") or "todo")
        unmet = links(row, "unmet_dependencies")
        # P0 is 0, so never treat a falsy priority as "missing".
        level = row.get("priority")
        level = level if isinstance(level, int) else 3
        cards.append({
            "id": str(row["id"]), "title": str(row["title"]), "lane": str(row.get("lane") or "idea"),
            "priority": f"p{min(3, max(0, level))}", "status": status,
            "assignee": row.get("assignee"), "repository": row.get("repository"),
            "updated_at": row.get("updated_at"), "created_at": row.get("updated_at"),
            "status_observed_at": row.get("status_observed_at"),
            "unmet_dependencies": unmet,
            "dependencies": links(row, "dependencies"),
            "blocks": links(row, "blocks"),
            "tags": [], "ready": status == "todo" and not unmet,
            "revision": revision,
        })
    now = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "schema": "frog.kanban-projection.v0", "source": "frog-model", "read_only": False,
        "observed_at": now, "revision": max((card["revision"] for card in cards), default=0),
        "lanes": [{"id": key, "title": title} for key, title in
                  (("idea", "Idea / ready"), ("blocked", "Blocked"),
                   ("in_progress", "In progress / review"), ("done", "Done / closed"))],
        "counts": {lane: sum(1 for card in cards if card["lane"] == lane)
                   for lane in ("idea", "blocked", "in_progress", "done")},
        "repositories": [{"id": str(repo["id"]), "title": str(repo["title"]), "repository": str(repo["id"])}
                         for repo in client.repositories(workspace)],
        "cards": cards,
    }