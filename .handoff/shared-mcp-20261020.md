# Frog shared MCP / HTTP / migration handoff — 2026-10-20

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
