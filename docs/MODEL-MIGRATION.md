# Frog model and full-fidelity data migration

## Decision and current implementation

Extend the existing `rust/frog-model` planner as the shared deterministic
domain core. Keep SQLite/D1 execution, authorization, clocks, process/network
I/O, filesystem/VFS identity and presentation in host adapters. Prioritize
full legacy data migration before migrating more UI or transport code.

Implemented foundations on this branch:

- `data_format::DataFormatVersion`: validated calendar date plus positive
  numeric revision, serialized as a string such as `20261020-3`.
  `require_current` explicitly rejects unsupported versions; chronological
  ordering alone never implies compatibility. The current constant reserves
  the first migration-manifest format; no existing database is relabelled.
- WIT now exposes all current Rust `Command` variants, including revisioned
  edits, provider import/outbox, verification and locks.
- HTTP dependencies are optional behind `http`, leaving the default planner
  suitable for core WASM; `component` builds the typed WIT interface.

**A comprehensive importer and cutover command are not implemented yet.**
`legacy.rs` is a limited live adapter, using TEMP views and zero-valued read
revisions. It supports create/dependency writes, not legacy claim/finish.
Neither those views nor successful planner builds establish migration parity.

## Independent version axes

| Axis | Meaning | Policy |
| --- | --- | --- |
| Data format `20261020-3` | Persisted export/manifest interpretation | Calendar validation, exact support check and explicit upgrade paths |
| `frog.model.v1` | Request/plan API contract | Retain existing identifier while API-compatible |
| `frog:model@0.1.0` | WIT package type/ABI contract | Semver as required by WIT; update for published breaking changes |
| SQL migration numbers | Ordered physical DB changes | Keep existing numbered migration history |
| MCP catalog `1.0.0` | Tool names and input schema compatibility | Version independently of persisted data |

Every new export/manifest must carry `data_format_version` separately from
its API `schema`. Once a date revision is released, its meaning is immutable.
Add another date/revision for changed interpretation. Accept unversioned
legacy input only via an explicitly selected legacy importer. Reject unknown
newer, older or malformed formats before destination writes. Ship explicit
source-to-target upgrade functions and fixtures; never infer upgrades from
date ordering. The version utility does not yet add metadata to existing
SQLite/D1 schemas or export paths.

## Migration phases and gates

### 1. Inventory a consistent source snapshot

The native host opens the source read-only and uses SQLite's backup API for
a consistent snapshot, including committed WAL data. Do not hash/copy only
the live main file while WAL writes continue. Keep the source intact and
record snapshot SHA-256, schema SQL/user_version, migration history, table
and column inventory, row counts, foreign-key/integrity checks and importer
build identity. Treat schema names as untrusted identifiers; quote them.

Inventory every table/column before mapping. Unknown data is a blocking
finding by default. Explicit preservation stores original typed values and
provenance in a namespaced archival envelope; retaining an opaque archive
does not establish operational parity for fields the new runtime needs.
No progress or dry run should contact offline Kali. Obtain its authoritative
snapshot only after it is available and the user resumes that operation.

### 2. Build a deterministic, reviewable mapping

| Legacy domain | Destination requirement / unresolved work |
| --- | --- |
| Workspace/box identities, paths, repo UUIDs, aliases/remotes | Stable identity map, retained aliases and original path/VFS semantics; don't merge equal display names |
| Task slug/ID, title, status, priority, descriptions, why/what/ROI | Preserve stable IDs; map known vocabulary explicitly; archive original values; reject unmapped operational states |
| Assignment, agent/session identity, timestamps | Retain source identity/UTC times and provenance; no fabricated fresh observation time |
| Dependencies, conflicts, tags and file associations | Import all edges and supporting entities; check duplicates, self-edges, orphans and cycles where prohibited |
| Active and historic locks/leases | Preserve owner, scope, lease/fence identifiers and expiry; no implicit renewal or fabricated safe release |
| Task history and event logs | Retain IDs, original ordering/timestamps and actor; keep imported history distinct from new model-generated operation events |
| Targets, units, build results, artifacts and verification | Preserve target definitions/results/receipts and their revision relationship; never relaunch a command during import |
| Provider identities, imports, outbox/cursors | Preserve source/external keys and pending delivery state; prevent duplicate provider dispatch |
| Federation/configuration/other tables | Explicit domain mapping or typed archival preservation plus a documented operational blocker |

The existing model schemas cover only part of this inventory. Extend them
with versioned migrations and pure validated import plans before claiming
full coverage; do not squeeze relations into a task title or arbitrary SQL
escape hatch. Mappings must distinguish null/empty/missing, integer/real/text/
blob values, terminal vs active states, source revisions and synthesized
initial model revisions. Importing a task must not claim it on behalf of the
import operator. Unknown timestamps remain unknown.

### 3. Dry run against a separate destination

Produce a date-versioned manifest containing source fingerprint, importer
build, mapping version, stable identity map, per-domain/table/field counts,
normalizations, preservation locations, errors/warnings, and expected
relation/content digests. Run pure validation and execute plans only against
a disposable destination transaction/database. Report unsupported domains
and ambiguous mappings explicitly. Do not modify the source or selected
runtime backend. A dry run with blocking findings cannot be applied.

### 4. Explicit apply, checkpoint and resume

Apply requires the reviewed manifest/fingerprint and an explicit new target.
Use a single transaction for small imports; for larger imports use staged,
dependency-ordered batches with an atomic per-batch checkpoint and stable
operation IDs derived from manifest/domain/source identity. Record mapping
and source fingerprints with every checkpoint. Repeating a committed batch
must produce the same result; reject a reused ID with different contents.

Resume validates source snapshot, format/mapping/build compatibility and
destination checkpoint before continuing. Never silently overwrite a
different target or resume after the source changed. Failure rolls back the
active batch. Staged data stays unavailable to ordinary writers until the
complete verification gate succeeds.

### 5. Reconcile and cut over deliberately

Verify counts **and** field/relationship content, identity bijections,
referential integrity, event ordering, status/priority distributions,
assignment and lock ownership/expiry, pending provider work and artifact
references. Differential fixtures compare legacy reads and native/edge new
reads after only explicitly documented normalizations. Counts alone miss
lost values and swapped edges.

Cutover requires zero unexplained losses and parity for operationally used
fields. Pause legacy writers, take a final consistent snapshot and repeat
reconciliation (or implement a separately tested change-capture protocol).
Switch backend configuration explicitly after verification. Keep source,
backup, manifest and report available for rollback; never auto-fallback from
an unavailable model service. After new writes begin, rollback needs export/
reconciliation of those writes, not just pointing at the old database.

## Shared Rust and platform contracts

1. Keep deterministic validation, allowed state transitions, dependency rules,
   revision/idempotency checks, lease fencing and mapping/normalization in
   Rust. Reuse the planner instead of duplicating rules in TypeScript.
2. Native host: link the rlib/SQLite adapter or use the existing controlled
   model CLI/HTTP bridge from Python. Preserve explicit backend selection.
3. Edge host: generate TypeScript bindings and component-to-JS glue from WIT.
   Validate exact D1 parameter/result mappings and execute mutation plans
   atomically. A successful WASI component build does not demonstrate that
   Workers runs the component model natively; prove the glue/runtime path in
   a Worker before adopting it. Keep the handwritten core-WASM JSON/pointer
   ABI only as a bounded, fixture-tested transition bridge.
4. Evolve WIT to typed errors (`invalid`, `conflict`, `unsupported-format`,
   `unavailable`) and trusted execution context (actor/workspace, clock,
   operation/revision/fence). Current WIT still returns string errors and
   current SQL uses host database time; those are future contract changes.
   User JSON cannot assert trusted identity or clock. Host authenticates and
   authorizes before planning and executing.
5. Host owns credentials, D1/SQLite/R2, network, subprocesses and telemetry.
   The model never grants permission because a caller supplied an actor ID.
   Telemetry records counters/correlation without private arguments/results.

## Verification and delivery sequence

- Keep shared MCP wire schemas/digest pinned across repositories in CI.
- Native: planner conformance, legacy preservation/refusal, format parser and
  compatibility tests, then importer fixtures for every inventory domain.
- WASM: build both targets and run the same golden request/plan/error fixtures
  through native and generated component bindings; add D1 execution parity.
- Migration: malformed/newer versions; unknown tables/columns; WAL snapshots;
  all supported schema revisions; blobs/nulls; dangling relations; duplicate
  identities; stale revisions/leases; interrupted batches; resume after source
  changes; deterministic reapply; preservation and full-content reconciliation.
- Ship inventory/dry-run and lossless schema support first, explicit apply/
  resume second, cutover tooling only after end-to-end parity. Broader Python
  or TypeScript rewrites follow the working migration, not the reverse.

Current local verification covers native planner/legacy tests and both WASM
build targets. Generated TS bindings, runtime differential execution and a
full importer remain follow-up implementation work.
