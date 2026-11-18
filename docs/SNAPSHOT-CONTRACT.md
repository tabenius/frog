# Owner snapshot validation

`frog --json snapshot --verify /explicit/detached/AGENTS.db` validates an
owner-exported legacy snapshot without opening a live workspace, contacting the
model service, creating a missing database or applying migrations.
`--verify` and `--dest` are mutually exclusive.

The versioned response schema is `frog.snapshot.validation.v1`. Validation
checks SQLite integrity, required owner tables, row counts and migrations against
the installed Frog schema. `migration.status` is `current`, `required` (behind)
or `unavailable` (ahead); only current passes. Nonempty adjacent WAL is refused:
use `frog snapshot --dest DIR` to obtain a consistent detached export first.

Rebeqah delegates to this owner contract rather than embedding Frog's table and
migration logic in a Rust application. Rebeqah remains responsible for chunk and
whole-file hashes, transfer and restore-to-new-path semantics. An older Frog
without this command must be upgraded; there is no automatic schema-bypass
fallback. The historical rbq `frog.python` setting is no longer executed.

This command validates **legacy** owner exports. It does not certify an
incomplete native-model staging checkpoint as an operationally migrated
workspace, nor provide a native-model snapshot API. Native migration readiness
must follow the separate owner inventory/projection/checkpoint contracts.
