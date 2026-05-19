# Federation: many boxes, one coordination model

frog has no daemon and no shared database. Each machine ("box") runs
its own `AGENTS.db`. Federation is **SSH + each box's own DB** — boxes
exchange identity and learn where each logical repo lives, so locks,
`task next`, and causality work across machines without shared
infrastructure.

## Box identity

Every machine has a stable id, independent of hostname:

    frog box whoami

Resolution order: `FROG_BOX_ID` env → pinned file
(`~/.config/frog/box-id`, override dir with `FROG_HOME`) → hostname
(pinned on first use). The id is stamped on every lock and event, so
"who did what, where" survives a hostname change.

## Joining a peer

    frog box join [user@]host[:/path/to/AGENTS.db]

This SSHes to the peer, reads its box id and its
`repo_key → path` map, and records reciprocal aliases so the same
logical repo is resolvable on both boxes even at different paths.
List federated peers with `frog box peers`.

## Where does a repo live?

    frog whereis <repo_key>

Shows this box's path plus every peer alias. Repos are identified by a
stable `repo_key` (a committed `.frogid`, else git origin, else a
path-hash fallback) — not their absolute path, which differs per box.

## Cross-box safety

- **Locks**: a lock carries its origin box. A lock from a box that
  has gone silent past its lease (or, with no lease, past
  `FROG_FED_STALE_TTL`, default 3600s) is reaped as `remote_stale`
  (known peer) or `orphan_box` (unknown) — a dead remote box can no
  longer deadlock the federation. The renewal heartbeat is the
  liveness signal.
- **`frog task next`** reports which box hosts a task's repo:
  `also on box:path` if shared, or
  `not on this box — lives on box:path` if remote-only.
- **`frog log why` / `frog log blame`** events carry origin box +
  host, so causality answers cross-box questions.

## Mirroring

`frog sync pull` mirrors another workspace's event log read-only
(single-writer per DB; cross-box visibility via event replay).
