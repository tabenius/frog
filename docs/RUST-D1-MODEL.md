# Rust model and multi-workspace D1 pilot

This is an **opt-in first slice**, not a replacement for Python's `AGENTS.db`
coordination engine. No production database, Worker, credential or workspace has
been created or changed. Native CLI/server transport and legacy migration are
follow-up work; the existing `frog task ...` CLI does not yet select this backend.

## Ownership and boundary

`rust/frog-model/src/lib.rs` owns typed domain requests, validation and the
SQLite-compatible operation plans. Hosts consume those plans; they do not copy
task/claim SQL into JavaScript. `sqlite.rs` executes them natively and
`cloudflare/frog-model/worker.js` executes them through a D1 binding. SQLite/WAL
are not the domain contract: native tests use ordinary in-memory SQLite without
WAL. This first adapter family requires foreign-key enforcement and an atomic
batch/transaction; a different storage model must supply equivalent operations,
not pretend to execute these SQL plans non-atomically.

The canonical SQL migration is owned once in `rust/frog-model/sql/`; the build
copies that exact file into Wrangler's ignored `migrations/` staging directory.

Domain request serialization is `frog.model.v1`, responses are
`frog.model-result.v1`, and the initial SQL schema is independently versioned as1.
IDs and titles are bounded; user values are parameters, never SQL text. The
planner is **not authorization** and not a generic remote SQL endpoint. Hosts
must bind the authenticated actor and authorized workspace before calling it.

The initial operations are:

- Register a repository within a provisioned workspace.
- Create a P0–P3 task in that repository.
- Claim a dependency-ready task using its expected revision.
- Report sequential `in_progress → review → published → done` transitions as
  the assigned actor. These are reported workflow states, **not evidence of an
  independent review, approval, deployment or publication**.
- Query repository-filtered lanes and repositories (bounded first page,1–200).
- Page committed events with a scoped string cursor.

Task IDs are unique per workspace, not per repository, matching Frog's
workspace-wide task addressing. Identical workspace-local repository/task IDs
can coexist in other workspaces. All relationships include workspace identity;
there are no cross-workspace dependency edges. Event sequences are scoped per
workspace too, so cursor gaps do not reveal other workspaces' activity counts.

The schema can represent dependencies and the planner enforces unmet ones, but
dependency-editing operations, cancellation, lease renewal, file/scope locks,
fencing, full-board pagination and repository-description import are **not yet
exposed**. A model claim is task ownership, not a legacy Frog file-lock claim.
Do not use this pilot as the sole production coordination authority yet.

## Conditional mutations and retry semantics

Each mutation is one transaction containing an idempotency receipt, conditional
entity write, committed event, receipt outcome and result read. A request hash
includes workspace, actor, operation ID and typed command. Reusing the same ID
and content returns its original outcome; reusing it for different content is a
conflict. Failed claim/revision/precondition outcomes are stable, not retried
under the same ID as a different operation. State/events/receipts roll back
together on database failure. No event is inserted for a rejected operation.

D1 uses `batch()` for atomic execution; it does **not** emulate Python connection
transactions or send unsupported `BEGIN`/`COMMIT` statements. Requests use
`withSession('first-primary')` so authorization-sensitive coordination reads are
not based on an arbitrary lagging replica. The prototype uses events as a
durable cursor source; SSE/webhook delivery and retention remain host concerns.

## Native, core WASM and WIT

```sh
cd rust/frog-model
cargo test --locked --features sqlite
cargo clippy --locked --features sqlite --all-targets -- -D warnings
cargo build --locked --release --lib --target wasm32-unknown-unknown
cargo build --locked --release --lib --target wasm32-wasip2 --features component
wasm-tools validate target/wasm32-wasip2/release/frog_model.wasm
wasm-tools component wit target/wasm32-wasip2/release/frog_model.wasm
```

The native `frog-model-plan` binary reads one bounded JSON request on stdin and
prints its plan. Rust's native SQLite adapter is a library, not an installer or
an always-running server. Migration is explicit, outside ordinary operations.

Workers executes **core WASM**, using a bounded synchronous input/output adapter.
The same Rust model separately compiles to a WASI Preview-2 component exporting
typed `frog:model/planner@0.1.0` through `wit/model.wit`. It imports bounded
counter instrumentation from its host. WIT components are not natively loaded
by Workers; the core-WASM and component binaries are separate artifacts. The
component is a pure planner: a host must authorize and atomically execute plans.
It has no guest-side stream or hidden storage/network mutation. Future WIT
import/export changes require a package version bump, with compatibility kept.

## Cloudflare adapter and local evidence

```sh
cd cloudflare/frog-model
npm ci
npm run build:model
npm test
npm run dry-run
npm run test:runtime
```

Tests execute the real Rust WASM and compare it with native plans. The runtime
test uses **workerd's local D1 implementation**, not only a SQLite test double,
and exercises competing claims, tenant separation, idempotency and transaction
rollback. Native SQLite and local D1 evidence are not production D1/deployment
evidence or independent security approval.

`wrangler.jsonc` deliberately contains a placeholder database ID, no routes,
disabled workers.dev/preview URLs, and no tokens. To provision a development
deployment, explicitly create a dedicated D1 database, replace the placeholder,
apply the model migration, and provision workspace rows as the operator. Never
apply this schema to legacy `AGENTS.db` or reuse a production Dash binding by
accident. Production rollout requires separate review and authorization.

Configure the **Worker secret** `FROG_CLIENTS` as an array of principals:

```json
[{"actor":"client-identity","token_sha256":"<64 lowercase hex characters>",
  "workspaces":{"workspace-a":"writer","workspace-b":"viewer"}}]
```

Use random32+ character bearer credentials and compute their SHA256 hashes.
Only hashes, actor IDs and workspace roles enter this secret. The client retains
the raw token outside Git and supplies it in the Authorization header. Missing
configuration fails closed; unlisted workspaces return404, viewers cannot write,
and request-supplied actor/workspace/SQL fields are refused. No browser-cookie
auth or permissive CORS is introduced. Public rate limiting, key lifecycle,
fine-grained operation permissions and user onboarding remain rollout gates.

Authenticated routes:

```text
GET  /v1/workspaces/{id}/repositories?limit=100
GET  /v1/workspaces/{id}/board?repo=repository-id&limit=100
GET  /v1/workspaces/{id}/events?after=0&limit=100
POST /v1/workspaces/{id}/operations
```

POST body example (use a new stable operation ID for each intended mutation):

```json
{"operation_id":"claim-20261014-one",
 "command":{"kind":"claim_task","task_id":"one","expected_revision":0}}
```

400 means invalid input,401 missing/invalid auth,403 an insufficient role,
404 an unlisted workspace/path,409 a stable domain/idempotency conflict, and503
unavailable/incompatible storage. Retry an uncertain mutation with the **same**
operation ID and body, never a freshly generated ID.

## Next integration gates

1. Complete dependency/lease/file-lock semantics and backend conformance tests.
2. Add shared application operations and native/HTTP client transport selection.
3. Implement an explicit, validated legacy import with workspace identity mapping.
4. Reuse the existing kanban projection contract through a named adapter rather
   than relabeling this model response as `frog.kanban-projection.v0`.
5. Audit authentication, quotas, secret rotation, database recovery and upgrades
   before a production authority or appliance is published.

Cloudflare contracts used:
- <https://developers.cloudflare.com/d1/worker-api/d1-database/>
- <https://developers.cloudflare.com/d1/worker-api/prepared-statements/>
- <https://developers.cloudflare.com/workers/runtime-apis/webassembly/>
