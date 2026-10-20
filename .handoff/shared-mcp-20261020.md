# Frog shared MCP / HTTP / migration handoff — 2026-10-20

## Disposable preservation simulation — 2026-10-20

- Continued from published/green `e62d220` with `frog-model-migrate simulate`.
  Source inventory, mapping assessment and preservation read the same backup.
- New owned destination schema `sql/0003_migration_preservation.sql` stores
  source fingerprint/version/inventory, the exact SQLite snapshot, inert
  schema SQL/column metadata, and typed binary rows for every source table.
  It is used only in a disposable in-memory destination; it is not automatically
  applied to existing SQLite/D1 stores. No persistent destination option exists.
- Portable `preserved_row.rs` retains null/integer/real-bit/text-byte/blob-byte
  distinctions, including invalid UTF-8 and large integers. It reuses the
  already-published inventory hash encoding; inventory version remains `-3`.
- Simulation artifact `frog.migration-simulation.v1` uses data version
  `20261020-5`, nesting unchanged dry-run `-4` and inventory `-3` reports.
  Preservation is verified against trusted source metadata, snapshot SHA-256,
  per-table typed hashes, ordinals, storage classes, counts and destination
  integrity/FKs. Verification occurs before commit and again afterward.
- Source DDL is never executed in the destination. Generic preservation does
  not claim operational model parity: destination_simulated=true,
  operational_model_populated=false, ready_to_apply=false. Full runtime mapping,
  persistent apply/resume and cutover remain future work. Archive preservation
  schema/rows roll back on reconciliation, collision or capacity failures.
- Limits remain 256 MiB source/one million rows; destination pages are bounded
  to 512 MiB (working buffers/source memory are additional). Optional --snapshot
  writes the source backup exclusively only after simulation succeeds.
- Latest local checks: **43 Rust tests passed**, HTTP check and both WASM
  library builds passed. New tests cover typed roundtrips, complete legacy
  schema with populated history/locks/unknown fields, WAL, generated/WITHOUT
  ROWID tables, duplicates, inert source schema, tampering, version refusal,
  rollback/collisions/capacity and real CLI behavior.
- This note is captured before committing/pushing this milestone; inspect
  git log and GitHub CI for its final publication. No Kali/production access.

## Mapping dry-run continuation — 2026-10-20

- Inventory milestone was committed/pushed as `7ed534e`; its GitHub CI
  `39441631495` passed. Both published repositories were clean at that point.
- Continued on explicit user requests with `frog-model-migrate dry-run`.
  Inventory and row assessments now share one immutable online backup.
- New portable `import_rules.rs` defines conservative per-field candidates,
  exact legacy priority/status rules, stable repo identity requirements, and
  UTC timestamp normalization. New native `migration_dry_run.rs` reuses the
  Rust planner for structural repo/task/dependency validation, reports coverage
  gaps/typed values/identity/provider/graph/timestamp issues without private
  row values, and never executes destination writes or target commands.
- New artifact: `frog.migration-dry-run.v1`, data version `20261020-4`;
  nested inventory remains `20261020-3`. No format relabeling or DB cutover.
- Full legacy `idea`/`blocked` states and history/locks/relations need explicit
  destination schemas/preservation; no implicit lossy mapping is accepted.
  `ready_to_apply` and `destination_simulated` are false. Candidate counts do
  not establish operational parity. Full apply/resume remain unimplemented.
- Latest local checks: **34 Rust tests passed**, HTTP check and both WASM
  library builds passed. Includes six dry-run integration tests and two pure
  mapping/timestamp tests, with all earlier inventory tests still passing.
- This snapshot precedes committing/pushing the dry-run milestone; inspect
  git log for its final SHA and CI. No Kali or production data was accessed.

## Published continuation and inventory milestone — 2026-10-20

- User requested commit/push and continuation. Published to GitHub origin on
  `handoff/mcp-native-edge-20261020`: `ffb3f34` (validated MCP/model changes)
  and `daedbf9` (public CI isolation fix). Dash published `fd3a1a4` and
  `520bd0d` on its same-named branch.
- GitHub run `39440976962` passed both Frog jobs (fallback/SDK MCP tests,
  native model tests, HTTP check, both WASM builds). Dash run `39440975724`
  passed build and pinned public-Frog contract parity. The earlier Frog run
  failed because public CI could not clone private Dash; fixed by keeping
  cross-repository integration in private Dash's CI, without adding secrets.
- New native `frog-model-migrate inventory --source PATH [--snapshot NEW_PATH]`
  uses read-only source + consistent SQLite online backup (WAL included).
  Emits `frog.migration-inventory.v1` with data version `20261020-3`, physical
  snapshot SHA-256, schema/column/storage-class/count inventory and logical
  typed-content hashes. Optional snapshot creation is exclusive, mode 0600
  on Unix. No table data is mapped/dropped and no source migrations occur.
- Inventory always reports `ready_to_apply: false`; full importer/apply/resume
  remain unimplemented. Every table requires explicit mapping. Virtual/shadow
  tables are refused; integrity/FK failures are reported as blockers. Current
  limits: 256 MiB snapshot, one million total rows, 30-second backup deadline.
- Native checks after inventory: **26 tests passed** (including 8 new tests
  for full shipped legacy schema, typed values/schema names, deterministic
  hashing, WAL/standalone backup, preservation, FK damage, virtual tables and
  CLI). HTTP check and both WASM library builds passed. See migration doc for
  exact commands and limits. Importer build can be identified by compile-time
  `FROG_MODEL_BUILD_ID`; otherwise the report explicitly says unidentified.
- This section is captured before committing the inventory milestone; inspect
  git log/status for its final publication. No Kali access or production
  deployments/migrations were performed.

## Resumed validation — 2026-10-20

Current branch is `handoff/mcp-native-edge-20261020`, checkpoint HEAD
`3448eb3580b5cd3a51c520f138a32528d3b1b10b`. The historical sections below
describe the earlier snapshot. This section was captured before the validated
resume commit; the user has now requested committing/pushing and continuing.

- Fixed stdio test EOF/buffer races using unbuffered reads and shared exchange
  helper. Added real HTTP CLI integration tests plus official SDK HTTP client.
- HTTP now bounds accepted socket reads, validates params/IDs, redacts unknown
  MCP method names in access logs, and negotiates supported versions correctly.
- Latest full MCP checks: fallback 33 passed + 1 SDK-only skip; official MCP
  1.30.0/Python 3.12: all 34 passed. CLI rendering: all 21 passed. Final narrow
  HTTP rerun after signature/type refinements: 4 passed + 1 SDK-only skip.
- Added Bash/Fish serve completion and HTTP/shared-catalog documentation.
- Added cross-checkout pinned-contract CI and native/WASM model build CI.
- Rust: date-format version parser/serde/explicit support check in
  `src/data_format.rs`, format `20261020-3`; existing persisted stores are not
  relabelled. Closed WIT command coverage gap and gated HTTP dependencies.
- Native Rust tests: 18 passed (3 library + 12 conformance + 3 legacy).
  HTTP cargo check passed; both wasm32-wasip2 component and
  wasm32-unknown-unknown core library builds passed.
- `rustfmt --check` on modified component/new format module passed. Whole
  crate cargo fmt check still finds pre-existing formatting in legacy.rs,
  sqlite.rs and tests/legacy.rs. Native linking with component+sqlite together
  fails on WIT export symbols; supported validation uses native sqlite tests
  and the actual WASI component target separately.
- Full migration design is `docs/MODEL-MIGRATION.md`. Full importer, generated
  TS bindings and runtime WASM differential fixtures remain future work.
- Local contract sync/digest check and git diff --check passed. GitHub CI
  itself has not run on these unpushed changes. No Kali access or production
  deployment occurred. Resume publication only after inspecting current diff.

Recreated missing SDK environment with uv at
`/tmp/opencode/frog-mcp-test` (system Python lacks pip/ensurepip). Isolated
config remains `/tmp/opencode/frog-test-config.json`.

## Checkpoint publication and portable resume

User requested commit/push then pause. Checkpoint branch in both repositories:
`handoff/mcp-native-edge-20261020`. State descriptions below are the pre-commit
snapshot, not a statement that the checkpoint is on main or deployed.

If resuming from GitHub without the workspace wrapper, read this document and
the same branch's `ragbaz-dash/.handoff/shared-mcp-audit-20261020.md`. The root
`HANDOFF-BIG-PICKLE.md` exists locally at `/home/xyzzy/src/ragbaz/`; the wrapper
has no GitHub origin. Never contact offline Kali. Latest user priority is a
fuller legacy-to-new data migration and date versions such as `20261020-3`;
that migration/version change is NOT implemented yet. Extend the existing
Rust planner/WIT instead of inventing a second domain model. Its current WIT
commands lag the Rust enum, and legacy.rs is only a limited live adapter.
Keep auth/storage/transport in host adapters and deterministic rules in Rust.

Read `../../HANDOFF-BIG-PICKLE.md` for full user intent and deployment baseline.

## Repository state

- Path `/home/xyzzy/src/ragbaz/ragbaz-frog`, branch `main`.
- HEAD `854d16e` (`Merge model-first coordination services into main`).
- All latest work is **uncommitted**. `git diff --check` passed at handoff.
- Read `AGENTS.md`. Do NOT access offline Kali or mutate its task state.

## Changes on disk

Modified:
- `pyproject.toml`: packages canonical `mcp_contract.json`.
- `src/ragbaz_frog/mcp_server.py`: original specs renamed `_native_tool_specs`;
  shared specs overlaid; native write tools retained and annotated; local
  `dash_*` read adapters; shared argument validation; model backend gains status,
  log-tail, create dispatch. FastMCP tool `parameters` replaced with exact
  catalog schema to prevent Optional[Any] signature regeneration drift.
- `main_cli.py`: new `serve mcp --http --listen --foreground/--fg --clf`
  parser + foreground HTTP dispatch; honors FROG_CONFIG for default --config
  (old hard-coded YAML default bypassed it). Existing `frog mcp serve` remains.
- `tests/test_mcp_server.py`: five shared aliases added to expected tool list;
  sets `proc.stdin = None` after closing to fix Python 3.13 communicate flush.

New:
- `src/ragbaz_frog/mcp_contract.json`: canonical 8 read tools, version currently
  **1.0.0** (date-based version request arrived afterward; still to decide).
  Shared names: five existing `dash_*` tools plus `frog_task_list`,
  `frog_status`, `frog_log_tail`. Native Frog has 31 tools total including writes.
- `mcp_contract.py`: package-resource loader + stdlib validator for schema subset.
- `mcp_local_reads.py`: selected workspace represented as a stable uuid5 local
  Frog instance; adapters read actual chosen backend, label local source, refuse
  Rebekah-only views. UUID derives from selected DB path or model identity.
- `bin/sync-mcp-contract.py`: generates/checks Dash JSON + SHA256 lock offline:
  `python3 bin/sync-mcp-contract.py --dash ../ragbaz-dash --check`.
- `tests/test_mcp_contract.py`: actual stdio read-write roundtrip, exact advertised
  schemas, local source correctness, subset vocabulary + artifact parity.
- `mcp_http.py`: NEW UNTESTED HTTP transport, stdlib HTTPServer; `/mcp` JSON-RPC,
  explicit bind, foreground, JSONL/CLF stdout, token-env auth (mandatory outside
  loopback), Origin/loopback Host checks, request cap, version checks, SIGTERM stop.
  Uses same dispatcher as stdio. Does not let remote `exit` stop process.

## Tests / known issues

System Python without SDK:
```
FROG_CONFIG=/tmp/opencode/frog-test-config.json python3 -m unittest discover -s tests -p 'test_mcp*.py'
```
**29 passed** before HTTP file was added. Repeat after adding HTTP tests.

Official SDK:
```
FROG_CONFIG=/tmp/opencode/frog-test-config.json /tmp/opencode/frog-mcp-test/bin/python -m unittest discover -s tests -p 'test_mcp*.py'
```
**28 passed, 1 failed**. Remaining failure:
`test_mcp_transport.McpTransport.test_stdio_uses_newline_delimited_json`.
Existing helper uses `subprocess.run(input=...)`, closing stdin immediately;
SDK sometimes cancels tools/list before writing id=2 response. Reuse the
keep-stdin-open helper in `test_mcp_server.py` instead. That helper now fixes
closed-pipe flushing. This is an actual observed failure, not a production claim.

The user machine has both ~/.config/frog/config.yaml and config.json, so tests
must use isolated FROG_CONFIG. Do not delete/overwrite user configs. SDK venv
doesn't have PyYAML; explicit isolated JSON avoids that dependency.

HTTP needs integration tests NOW: spawn exact CLI with temp --db + isolated
--config, bind 127.0.0.1:0, read ready event or stderr CLF startup, call initialize,
tools/list, write/create/edit + shared reads; test errors, Host/Origin/auth,
max body, JSONL/CLF stdout, no args/secrets in logs, SIGTERM. Nonloopback auth
test with FROG_MCP_TOKEN and `--listen 0.0.0.0:0`. --json/--clf conflict test.
Inspect HTTP standards gaps: currently requires Content-Length, refuses chunked;
validate protocol negotiation and errors. No HTTP tests have run yet.

## Still to do

- Document exact user commands, JSONL/CLF examples and read/write authority.
- Update CLI completion/help command lists for `serve` as appropriate.
- Shared protocol schemas pass real stdio SDK tests; improve registration to
  avoid private FastMCP internals if practical, without weakening schemas.
- Add cross-checkout CI parity job; current artifact checks are local + pinned
  digest, not a full remote cross-repo CI workflow.
- Full Rust/WIT + date-versioned legacy migration PLAN is not written yet.
  Existing Rust findings and priorities are in root handoff. User asked to
  prioritize fuller migration and date versions e.g. `20261020-3`.
- Test model-backend local instance identity and new dispatch branches more.
- No commit/push/install of current Frog changes yet.
