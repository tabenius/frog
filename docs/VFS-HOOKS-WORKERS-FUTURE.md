# Future Plan: VFS Event Hooks, Webhooks, Callbacks & Workers

## TL;DR

Build a **reactive data plane** where workers subscribe to VFS/DB changes and act on them — enabling async pipelines, cross-system sync, audit trails, and policy enforcement without polling.

---

## Current State (as of this branch)

| Component | Status |
|-----------|--------|
| **VFS abstraction** | ✅ Local, SSH, S3, HTTP(S), WebDAV |
| **SQLite** | ✅ Local-first, remote sync on commit |
| **Repo discovery** | ✅ Manifest-first + git pruning + cache |
| **SSH optimization** | ✅ Server-side `find` / `git ls-files` |
| **Event log** | ✅ `event_log` table in SQLite |
| **Hook dispatch** | ✅ `frog hook dispatch` (manual/CLI) |
| **Webhooks** | ❌ Not implemented |
| **Worker framework** | ❌ Not implemented |
| **Change streams** | ❌ Not implemented |

---

## Target Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        DATA PLANE                                │
├─────────────────────────────────────────────────────────────────┤
│  VFS Layer (Local/SSH/S3/HTTP/WebDAV)                           │
│       │                    │                    │               │
│       ▼                    ▼                    ▼               │
│  ┌─────────┐         ┌─────────┐         ┌─────────┐          │
│  │  SQLite │         │  Files  │         │  Git    │          │
│  │ (DB)    │         │  (FS)   │         │  (Git)  │          │
│  └────┬────┘         └────┬────┘         └────┬────┘          │
│       │                   │                   │                │
│       └───────────────────┼───────────────────┘                │
│                           ▼                                     │
│              ┌────────────────────────┐                          │
│              │  EVENT BUS / CHANGELOG │                          │
│              │  (event_log table +     │                          │
│              │   VFS change events)   │                          │
│              └───────────┬────────────┘                          │
└───────────────────────────┼──────────────────────────────────────┘
                            │
              ┌─────────────┼─────────────┐
              ▼             ▼             ▼
        ┌──────────┐  ┌──────────┐  ┌──────────┐
        │ WORKER 1 │  │ WORKER 2 │  │ WORKER 3 │
        │ (sync)   │  │ (audit)  │  │ (policy) │
        └──────────┘  └──────────┘  └──────────┘
              │             │             │
              ▼             ▼             ▼
        ┌──────────┐  ┌──────────┐  ┌──────────┐
        │ S3/SSH   │  │ Nostoi   │  │ Webhook  │
        │ Sync     │  │ Anchor   │  │ Targets  │
        └──────────┘  └──────────┘  └──────────┘
```

---

## Plausible Use Cases (Concrete, Not Hypothetical)

### 1. Cross-System Repo Sync (Frog ↔ GitHub/GitLab)
- **Trigger**: `repo.initialized` or `file.upsert` event
- **Worker**: Push new repo to GitHub, create PR template, set up branch protection
- **Idempotency**: Use `repo_key` as deduplication key

### 2. Nostoi Anchor Publishing
- **Trigger**: `db.gc` or `workspace.snapshot` or periodic timer
- **Worker**: Read Nostoi chain head, call `nostoi-anchor` to publish to S3/R2 with Object Lock
- **Verification**: Separate verifier worker reads published checkpoint, verifies locally

### 3. Policy Enforcement (Pre-commit / Pre-push Gates)
- **Trigger**: `lock.acquire` with `kind=edit` on protected paths
- **Worker**: Run lint/test in sandbox, release lock only on pass
- **Response**: Webhook to Slack/GitHub PR comment with results

### 4. Artifact Collection & Promotion
- **Trigger**: `target_runs` entry with `status=ran` and new artifacts
- **Worker**: Copy artifacts to S3, update `repo_artifacts`, notify downstream consumers
- **Deduplication**: Content-addressed storage (SHA-256 in `repo_artifacts`)

### 5. Audit Trail Replication
- **Trigger**: Every `event_log` insert
- **Worker**: Batch events → Nostoi chain → S3 Object Lock → Verifiable audit log
- **Query**: `nostoi verify s3://bucket/audit-chain.jsonl`

### 6. Dependent Repo Build Trigger
- **Trigger**: `repo.build.completed` with `status=success`
- **Worker**: Query `repo_deps` for dependents, enqueue their builds
- **Ordering**: Topological sort via `repo_deps` DAG

### 7. Stale Lock Auto-Release with Notification
- **Trigger**: `doctor.repair` releasing stale locks
- **Worker**: Post to Slack/Teams with list of released locks + owners
- **Escalation**: If same lock re-acquired >3x/day, page on-call

### 8. Secret Scanning on Write
- **Trigger**: `file.upsert` with `source_of_truth=content`
- **Worker**: Run `gitleaks`/`trufflehog` on content, quarantine if matches
- **Response**: Webhook to security team, auto-revoke if API key detected

---

## Implementation Roadmap

### Phase 1: Event Infrastructure (Week 1-2)
- [ ] **Change capture**: Extend `record_event()` to emit to in-process async queue
- [ ] **VFS change events**: Add `vfs.on_change(path, callback)` to VFS base class
- [ ] **SQLite triggers**: Add `AFTER INSERT ON event_log` → notify listeners
- [ ] **Serialization**: Canonical JSON for all events (Nostoi-compatible)

### Phase 2: Worker Framework (Week 2-3)
- [ ] **Worker base class**: `class Worker: subscribe(pattern) → async handle(event)`
- [ ] **Registry**: `frog worker register <name> <module.Class> --pattern "repo.*"`
- [ ] **Execution**: `frog worker run` (single process, asyncio) or `frog worker run --worker sync`
- [ ] **Config**: Worker definitions in `frog.json` under `workers` key
- [ ] **Health**: `/health` endpoint, liveness/readiness probes

### Phase 3: Webhooks & External Integrations (Week 3-4)
- [ ] **Webhook delivery**: POST with retry/backoff, HMAC signature
- [ ] **Target registry**: `frog webhook add <url> --events "repo.*,file.*" --secret <hmac>`
- [ ] **Transformers**: Jinja2 templates per target (Slack, GitHub, PagerDuty)
- [ ] **Dead letter queue**: Failed deliveries → SQLite table for replay

### Phase 4: Nostoi Integration (Week 4-5)
- [ ] **Chain writer worker**: Subscribe to all events → append to Nostoi chain
- [ ] **Anchor publisher**: Periodic `nostoi-anchor` invocation with `--outbox`
- [ ] **Verifier worker**: Independent process, reads S3, verifies, alerts on failure

### Phase 5: Operational Tooling (Week 5-6)
- [ ] **CLI**: `frog worker list`, `frog worker logs <name>`, `frog worker replay <event-id>`
- [ ] **Metrics**: Prometheus `/metrics` endpoint (event throughput, worker latency, error rates)
- [ ] **Replay**: `frog worker replay --from <event-id> --worker <name>`

---

## Data Models

### Event Envelope (canonical JSON)
```json
{
  "event_id": "sha256:...",
  "event_type": "repo.initialized",
  "timestamp": "2026-10-02T10:30:42Z",
  "source": "vfs:ssh://host/path",
  "payload": { "repo_path": "...", "name": "..." },
  "causation_id": "sha256:...",
  "correlation_id": "sha256:..."
}
```

### Worker Definition (in frog.json)
```json
{
  "workers": {
    "s3-sync": {
      "class": "frog.workers.S3SyncWorker",
      "subscribe": ["repo.initialized", "file.upsert", "target_runs.completed"],
      "config": { "bucket": "s3://frog-artifacts", "prefix": "repos/" },
      "retry": { "max_attempts": 3, "backoff": "exponential" }
    },
    "nostoi-anchor": {
      "class": "frog.workers.NostoiAnchorWorker",
      "schedule": "0 */15 * * * *",
      "config": { "chain": "/path/to/chain.jsonl", "outbox": "/var/lib/nostoi/outbox" }
    }
  }
}
```

### Webhook Target
```json
{
  "webhooks": {
    "slack-alerts": {
      "url": "https://hooks.slack.com/services/...",
      "events": ["lock.stale", "doctor.repair", "file.secret_detected"],
      "hmac_secret": "env:SLACK_WEBHOOK_SECRET",
      "template": "slack/alert.j2"
    }
  }
}
```

---

## Non-Goals (Explicit)

- **Not a message broker**: Workers subscribe to SQLite/VFS events directly; no Kafka/RabbitMQ
- **Not a workflow engine**: No DAG execution, no retries with compensation
- **Not a scheduler**: Workers define their own schedules (cron-style in config)
- **Not multi-tenant**: Single frog workspace = single event bus

---

## Security Considerations

- **Event payloads**: Never include secrets, tokens, raw credentials
- **Worker isolation**: Each worker runs in same process but separate async task; future: subprocess sandbox
- **Webhook HMAC**: Mandatory for all outgoing webhooks
- **Nostoi chain**: Immutable, append-only; verification is read-only

---

## Open Questions

1. **Event ordering**: SQLite `event_log` has autoincrement ID — sufficient for total order?
2. **Worker crashes**: In-process workers share fate; should critical workers be external processes?
3. **Schema evolution**: Event payloads need versioning (`event_type/v2`?)
4. **Backpressure**: What if event rate > worker throughput? (SQLite WAL has limits)
5. **Multi-workspace**: Coordinator workspace receives events from peers — deduplication needed?

---

## Related Files (This Branch)

- `src/ragbaz_frog/vfs.py` — VFS base class, add `on_change()`
- `src/ragbaz_frog/vfs_ssh.py` — SSH optimized discovery (done)
- `src/ragbaz_frog/store.py` — `discover_repos`, `record_event`, event log
- `src/ragbaz_frog/main_cli.py` — `frog worker` command (future)
- `docs/VFS-HOOKS-WORKERS-FUTURE.md` — This document

---

## Next Immediate Action

Start **Phase 1** by adding in-process event bus to `store.py`:

```python
# In store.py
_event_subscribers: dict[str, list[Callable]] = defaultdict(list)

def subscribe_event(pattern: str, handler: Callable[[dict], Awaitable[None]]) -> None:
    _event_subscribers[pattern].append(handler)

async def _emit_event(event: dict) -> None:
    for pattern, handlers in _event_subscribers.items():
        if fnmatch.fnmatch(event["kind"], pattern):
            for h in handlers:
                await h(event)

# In record_event():
await _emit_event(event_dict)
```

Then implement a minimal `frog worker run` that registers handlers and runs the event loop.