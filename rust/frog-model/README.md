# Frog model (experimental)

Portable Rust requests, validation and SQL operation plans; native SQLite,
core WASM for Cloudflare D1, and typed WASI Preview-2/WIT planning exports.

See [migration contracts, tests and integration gates](../../docs/MODEL-MIGRATION.md).
This is not yet the production Python Frog engine or a complete lock service.

Read-only legacy preflight (explicit source required):

```bash
cargo run --features sqlite --bin frog-model-migrate -- inventory --source /path/AGENTS.db
cargo run --features sqlite --bin frog-model-migrate -- dry-run --source /path/AGENTS.db
cargo run --features sqlite --bin frog-model-migrate -- simulate --source /path/AGENTS.db
cargo run --features sqlite --bin frog-model-migrate -- project --source /path/AGENTS.db --workspace chosen
```

All four commands can write a consistent, exclusively created source backup with
`--snapshot NEW_PATH`. Inventory data version is `20261020-3`; mapping dry-run
version is `20261020-4`; disposable preservation simulation uses `20261020-5`.
Simulation verifies a lossless typed archive in memory. Operational projection
uses `20261020-6`, executing supported repository/task/provider/dependency seeds
and reconciling fields with the source on a disposable target. Unsupported data
is retained and reported; full runtime parity, persistent apply/resume and
cutover remain unimplemented.
The default library and portable mapping rules do not require SQLite or HTTP;
native preflight uses `sqlite`, the server uses `http`, and typed WIT uses
`component` on the WASI target.
