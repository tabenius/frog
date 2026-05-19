# Deploying & operating frog

## Install

frog is stdlib-only Python 3.11+, one package. From the repo:

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

### Optional continuous disaster recovery with Litestream

Snapshots are local rollback points. Litestream is the continuous DR
layer for losing the disk or host between snapshots. The intended run
model is a long-lived user service supervised by systemd; frog does not
spawn or supervise Litestream.

Litestream is opt-in. A normal frog install has no Litestream
dependency, does not require the `litestream` binary, and does not start
any replication service. Use this section only on hosts where continuous
off-host recovery is worth the extra moving parts.

Example-only files:

- `deploy/litestream/agents-db.litestream.yml.example`
- `deploy/systemd/frog-agents-litestream.service`

Install flow:

    mkdir -p ~/.config/ragbaz-frog
    cp deploy/litestream/agents-db.litestream.yml.example ~/.config/ragbaz-frog/litestream.yml
    install -m 0644 deploy/systemd/frog-agents-litestream.service ~/.config/systemd/user/

Create `~/.config/ragbaz-frog/litestream.env` outside git with the
replica target and credentials, for example:

    FROG_LITESTREAM_REPLICA_URL=sftp://backup.example.net/frog/AGENTS.db
    FROG_LITESTREAM_SFTP_KEY_PATH=/home/xyzzy/.ssh/frog-litestream

Then enable it:

    systemctl --user daemon-reload
    systemctl --user enable --now frog-agents-litestream.service
    systemctl --user status frog-agents-litestream.service

SQLite compatibility: frog opens the DB in WAL mode with `busy_timeout`
and `synchronous=NORMAL`, which is the normal Litestream operating mode.
`frog db migrate` uses short `BEGIN IMMEDIATE` transactions and the
snapshot commands use SQLite's online backup API; both coexist with
Litestream reading the WAL.

Restore runbook on a fresh box:

    systemctl --user stop frog-agents-litestream.service || true
    mkdir -p /data/src
    mv /data/src/AGENTS.db /data/src/AGENTS.db.broken.$(date +%Y%m%d%H%M%S) 2>/dev/null || true
    litestream restore -config ~/.config/ragbaz-frog/litestream.yml /data/src/AGENTS.db
    frog --db /data/src/AGENTS.db db migrate
    frog --db /data/src/AGENTS.db doctor --no-fix

The restore step rebuilds the latest available SQLite image from the
replica. Run `frog db migrate` after restore because the frog binary on
the new box may ship newer migrations than the restored DB. Keep the
schema-skew guard enabled; if the DB is behind, frog will refuse normal
commands until migration succeeds.

Operational checks:

    systemctl --user status frog-agents-litestream.service
    journalctl --user -u frog-agents-litestream.service -n 100
    litestream generations -config ~/.config/ragbaz-frog/litestream.yml /data/src/AGENTS.db

Keep replica credentials in `litestream.env` or a host secret manager,
not in this repo. Review retention (`168h` in the example) according to
how long bad writes might go unnoticed.

## Moving a repo

After a reorg, re-point a registered repo safely (transactional,
FK-checked) instead of editing the DB by hand:

    frog repo move /old/path /new/path

## Multi-box

See [FEDERATION.md](FEDERATION.md). Minimum: `frog box whoami` on
each box, then `frog box join user@peer:/path/AGENTS.db`.
