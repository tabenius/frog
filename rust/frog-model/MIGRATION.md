# Legacy store migration

Run with Python's standard library; no pip dependencies:

```sh
python3 migrate_legacy.py \
  --source /srv/ragbaz/src/vendors/ragbaz/head/AGENTS.db \
  --output /path/to/new-model.db --workspace local
```

The source is opened read-only. SQLite's online backup captures committed WAL
pages consistently. The destination must not exist. A private staging database
is populated and checked, then atomically published without overwriting files.
The table prefix defaults to `frog_model_`; use `--table-prefix` to override it.

## Mapping and limitations

- Task slugs remain IDs when allowed by the model contract; other IDs are hashed.
- Repository keys identify repositories; worktrees sharing a key are coalesced.
- Workspace-only tasks use the synthetic `legacy:unscoped` repository.
- Priorities map from `p0`–`p3` to integers; revisions start at zero.
- `idea` and `blocked` map to `todo`. Dependency-derived blocking remains, but a
  manually blocked task without an unmet dependency becomes ready. Review these
  mappings before cutover. All other supported workflow states remain unchanged.
- Titles, assignees, why/what/ROI notes, provider identities,
  update/status-observation timestamps and dependency edges
  transfer into the model tables. Other legacy metadata, tags, conflicts, files, locks,
  task history, creation timestamps and event history remain in archival tables;
  they are **not** exposed by the new model API.
- `frog_model_legacy_mapping` retains original task/repository records and ID
  mappings. Historical events are not invented as new model events.

The output is an independent snapshot, not a synchronised replica. Do not use
legacy writers against it: the archived tables would diverge from the model.
For production cutover, pause writers and take a fresh migration, verify counts
and mappings, then switch the server DB path. This script does not perform that
cutover or configure Litestream.

## Live compatibility adapter

Alternatively, use `FROG_MODEL_STORAGE=legacy` and `FROG_MODEL_WORKSPACE=local`.
The Rust adapter reads live legacy tables through temporary views. Explicitly
initialise its receipt metadata with `FROG_MODEL_INIT_RECEIPTS=1` only after a
consistent backup. Supported writes are task creation and dependency add/remove,
with atomic receipts and legacy events. Claim/transition/cancel/repo-registration
are refused until legacy locks, verification and revisions are compatible.

## Upgrading an existing migrated model database

```sh
python3 upgrade_services.py --database /path/to/model.db --backup /path/to/new-backup.db
```

This creates a checked SQLite online backup without overwriting an existing file,
then transactionally adds service tables and hydrates task notes/provider IDs
from the retained migration records once. Existing model notes are not replaced.
It never writes to the original legacy database. Restart the updated server only
after the upgrade succeeds. Subsequent startups ensure service tables exist but
do not resynchronize the archive. See [SERVICES.md](SERVICES.md) for operations.
