# Deploying & operating frog

## Install

frog is stdlib-only Python 3.13, one package. From the repo:

    pipx install .            # or: pip install .
    frog --help

The CLI entry point is `frog` (see `pyproject.toml`).

## The database

One SQLite file, `AGENTS.db` (default `/data/src/AGENTS.db`, override
with `--db` or a configured workspace). Initialise / upgrade:

    frog --db /path/AGENTS.db db migrate

**Schema-skew guard:** if the code is newer than the DB, every
command except `db migrate` refuses with the exact remediation, so
queries never run against a stale schema. Override (unsafe) with
`FROG_ALLOW_SCHEMA_SKEW=1`.

## Backups

    frog --db /path/AGENTS.db snapshot          # consistent copy, .last/.prev

Destructive operations (`repo move`, `db gc`) auto-take a
`<db>.pre-<op>` backup first (disable with `FROG_NO_AUTOSNAPSHOT=1`).
Backups use SQLite's online backup API — safe under concurrent
writers.

## Moving a repo

After a reorg, re-point a registered repo safely (transactional,
FK-checked) instead of editing the DB by hand:

    frog repo move /old/path /new/path

## Multi-box

See [FEDERATION.md](FEDERATION.md). Minimum: `frog box whoami` on
each box, then `frog box join user@peer:/path/AGENTS.db`.
