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
- Native `frog-model-migrate inventory` produces a date-versioned inventory
  from SQLite's online backup API, with optional exclusive snapshot creation,
  exact snapshot SHA-256 and typed logical-content fingerprints.
- `frog-model-migrate dry-run` assesses per-field coverage, model-valid core
  candidates, repository/provider identities, dependencies and timestamps from
  the same immutable snapshot. Mapping rules live in portable `import_rules.rs`.
- `frog-model-migrate simulate` imports a typed preservation envelope into a
  disposable SQLite destination and verifies every row/column/schema record
  and the exact snapshot against the source inventory. Its codec is portable.
- `frog-model-migrate project` imports supported repository/task/provider/edge
  data into operational model tables on a disposable destination, using pure
  Rust seed plans and field-by-field source reconciliation.

**A comprehensive operational importer and cutover command are not implemented yet.**
`legacy.rs` is a limited live adapter, using TEMP views and zero-valued read
revisions. It supports create/dependency writes, not legacy claim/finish.
Neither those views nor successful planner builds establish migration parity.

## Available inventory command

```bash
cargo run --manifest-path rust/frog-model/Cargo.toml --features sqlite \
  --bin frog-model-migrate -- inventory --source /explicit/path/AGENTS.db

cargo run --manifest-path rust/frog-model/Cargo.toml --features sqlite \
  --bin frog-model-migrate -- inventory --source /explicit/path/AGENTS.db \
  --snapshot /explicit/new/path/legacy-snapshot.db
```

Source selection is mandatory. It opens read-only and takes a consistent
in-memory backup, including committed WAL data. It does not use the limited
legacy adapter or execute model mutations. The optional snapshot destination
must not exist (including symlinks); creation is exclusive and mode 0600 on
Unix. Source contents are never migrated/checkpointed. An unsuccessful write
retains its partial artifact and emits no success manifest. The in-memory
preflight currently limits snapshots to 256 MiB, total rows to one million,
and backup acquisition to 30 seconds; larger sources need a disk-backed
bounded implementation before importing.

Successful command execution means inventory completed, **not** import
approval. Every table is labelled `requires_explicit_mapping`, and
`ready_to_apply` is always false with `full_import_not_implemented`. Integrity
and foreign-key findings add blockers. Virtual/shadow tables require an
explicit adapter and are refused before publishing a snapshot.

JSON includes schema SQL (including defaults), user_version, every table and
column (including generated-column metadata), counts and observed SQLite
storage classes. It includes indexes/triggers/views and internal sequence
tables; it does not print row values. Treat schema defaults as source data.
SHA-256 fingerprints distinguish null/integer/real/text/blob, preserve blob
bytes and duplicate rows, and ignore physical row ordering. Logical digests
include schema and columns; implicit rowids/physical layout remain represented
by the exact snapshot digest. A future apply must verify the snapshot digest
as well as operational mapping/parity; logical equality alone is insufficient.
Set `FROG_MODEL_BUILD_ID` at compile time to record a concrete build revision;
unidentified developer builds are explicitly labelled `unidentified`.

## Available mapping dry run

```bash
cargo run --manifest-path rust/frog-model/Cargo.toml --features sqlite \
  --bin frog-model-migrate -- dry-run --source /explicit/path/AGENTS.db \
  --snapshot /explicit/new/path/reviewed-source.db
```

The report uses schema `frog.migration-dry-run.v1` and date data version
`20261020-4`; its nested inventory retains `20261020-3`. The original inventory
format remains unchanged. Version checks are artifact-specific; the existing
`data_format::require_current` checks inventory compatibility, not the new dry
run format. This command is a mapping assessment, not destination execution:
`destination_simulated` and `ready_to_apply` remain false.

Every source column gets a candidate destination/rule or an explicit
`field_requires_schema_or_preservation` finding, even for empty tables. Thus
history, file/tag/conflict relations, target/artifact/provider state, aliases,
box/federation data and lock semantics remain visible schema work. Unknown
fields are never silently treated as migrated. Counts of candidate repos/tasks
and dependency edges describe structural candidates, not complete/importable
records; inspect all findings and the inventory's integrity/FK blockers.

The assessment reuses the current Rust planner's validation for core task,
repository and dependency candidates. It reports missing/ambiguous repo
identities, unresolved task repositories, unsupported status/priority, missing
or incorrectly typed fields, incomplete/duplicate provider identity, invalid
timestamps, unsupported dependency relations, self/dangling/duplicate edges
and cycle-or-cycle-blocked nodes. It never invents repo keys from names/paths,
maps `idea`/`blocked` to `todo`, assigns imported work, renews a lease, or
executes target commands. UTC `+00:00` normalization to `Z` is counted without
changing the source; other offsets/unknown instants require an explicit
conversion adapter. Private row titles/paths and metadata values are omitted
from findings; source schema/default SQL remains included in the inventory.

Typed preservation is now exercised by the simulation below. Next implement
the missing operational destination schemas and execute reviewed model mappings,
then prove operational parity before persistent apply/resume or cutover.

## Available disposable preservation simulation

```bash
cargo run --manifest-path rust/frog-model/Cargo.toml --features sqlite \
  --bin frog-model-migrate -- simulate --source /explicit/path/AGENTS.db \
  --snapshot /explicit/new/path/reviewed-source.db
```

The report is `frog.migration-simulation.v1`, data format `20261020-5`, with
the unchanged dry-run (`20261020-4`) and inventory (`20261020-3`) nested inside.
`destination_simulated` is true for preservation execution;
`operational_model_populated`, `preservation.operational_parity` and
`ready_to_apply` remain false. A verified preservation archive does not make
unsupported statuses, lease semantics, history or relationship operations work
in the new runtime. The nested assessment still reports those gaps.

The destination is always new and in memory, limited to 512 MiB of SQLite
pages. There is no persistent destination option. It initializes only owned
model schema and imports into the `frog_migration_*` preservation tables
defined in `sql/0003_migration_preservation.sql`. This file is not automatically
applied to existing native/D1 stores. Foreign keys and strict storage types
are enforced. Preservation schema creation and inserts share a transaction;
in-transaction reconciliation failure rolls back both DDL and rows. Source
snapshots and legacy row limits still apply, and total process memory also
includes source, inventory and codec/verifier working buffers.

The envelope retains the exact source snapshot and its fingerprint, schema
SQL as inert text, full column metadata and an ordered sequence of typed binary
rows for every table. No source trigger/view/default DDL is executed in the
destination; a source table named like a model table cannot replace it.
The snapshot preserves physical layout/implicit rowids while row envelopes
preserve semantic values, including unknown fields and duplicate rows.

`preserved_row.rs` encodes each row with the already-published inventory hash
header followed by storage-class name, big-endian u64 byte length and raw
bytes per cell. Null has zero bytes; integers are signed 64-bit big-endian;
real values retain their 64-bit representation; text/blob bytes are distinct
and not UTF-8/JSON-coerced. Column order and count come from retained metadata.
Changing this encoding requires a new artifact version. Integer values larger
than JavaScript's exact-number range remain binary, not JSON numbers.

Verification checks the exact snapshot digest, artifact versions, schema and
column metadata, contiguous archive ordinals, row counts, per-column storage
classes and sorted typed-content fingerprints, plus destination integrity/FKs.
Evidence is emitted only after verification succeeds; it contains hashes and
counts rather than private row contents. The optional `--snapshot` still writes
the source backup only, exclusively, after successful simulation. The archive
itself is discarded with the in-memory destination.

Tests cover the complete shipped legacy schema, populated legacy status/history/
locks/unknown metadata, WAL, large integers, non-UTF-8 text, blobs, generated
columns, WITHOUT ROWID tables, duplicate rows, inert source SQL, tampering,
capacity errors, collisions and transactional rollback.

## Available disposable operational projection

```bash
cargo run --manifest-path rust/frog-model/Cargo.toml --features sqlite \
  --bin frog-model-migrate -- project --source /explicit/path/AGENTS.db \
  --workspace chosen-workspace --snapshot /explicit/new/path/reviewed-source.db
```

Both source and destination workspace identity must be selected explicitly.
`--workspace` is accepted only by `project`; no command accepts a persistent
destination. The new report is `frog.migration-projection.v1`, data version
`20261020-6`. It retains the unchanged assessment/inventory formats and the
same lossless preservation evidence. The existing `simulate` command retains
its `20261020-5` behavior and does not populate operational model rows.

`import_plan.rs` owns migration-only typed seed validation and deterministic
SQL plans. It reuses existing model validation for identifiers, titles,
priority, task details and provider identities, plus conservative legacy status
and UTC normalization rules. Plans bind workspace, importer actor, exact source
digest and original seed values into deterministic receipts. The native host
anchors the digest to the verified archive before executing plans. This pure
planner is built for native/WASM but is not exposed as a migration HTTP/MCP/WIT
mutation endpoint; authenticated host execution remains a separate adapter.

Supported data is actually inserted into `frog_model_repos`,
`frog_model_tasks`, `frog_model_task_details`, `frog_model_provider_items`
and `frog_model_dependencies`. Source `todo`, `in_progress`, `review`,
`published`, `done` and `cancelled` states are preserved directly, without
replaying workflow transitions. Source assignees are preserved; the importer
is not assigned their tasks. Updated/status-observed times remain source
instants rather than the import clock. Empty and null metadata stay distinct.
Repository/source creation times and source identity are retained in owned
provenance tables from `sql/0004_migration_projection.sql`; that schema is
created only for the disposable projection, not applied to existing stores.

Each task receives an explicit **new baseline revision of zero**; provenance
records `source_revision = NULL`. This does not claim the legacy task had
revision zero. Sources with a task `revision` column are refused for task
projection until a revision mapping is defined. No source history is fabricated
as model events, no locks are renewed/acquired, and no target commands run.
Import receipts/provenance are distinct from historical domain events.

Ambiguous repository/task/provider identities are not arbitrarily selected.
Unsupported states (`idea`/`blocked`), invalid or oversize metadata, invalid
assignee identifiers, bad timestamps and unresolved references produce grouped
skip findings. Counts show exactly how many source repos/tasks/edges were not
projected. Their original rows remain in the verified archive. Supported
dependency edges require both endpoints to be projected, and cyclic dependency
insertion aborts and rolls back the entire model projection, including its
provenance schema and receipts. The preservation archive remains intact.

Reconciliation compares every imported field, timestamp, assignee, provider
identity, dependency and provenance/receipt to the normalized source seeds;
it checks row counts, FK/integrity and equal source/destination model hashes.
It runs before and after commit. Tests also read imported data through the
existing board/details/provider APIs and exercise revision-checked transitions.
An existing operational destination is never overwritten or replayed into.

`operational_model_populated` reports whether any supported repo/task rows were
inserted, and `model.fields_verified` covers those projected fields only.
`ready_to_apply` and whole-source `preservation.operational_parity` remain false:
history, leases, additional relationships and unsupported vocabulary still need
runtime contracts. Missing projected dependency edges are explicitly reported;
the disposable result must not be treated as an authoritative readiness view.
Persistent apply/resume and real cutover require full parity and a writer pause.
Source/destination limits remain the same; source seeds and reconciliation
working buffers require additional process memory.

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
