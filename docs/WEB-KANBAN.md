# Live Frog web kanban

```sh
frog --db /path/to/AGENTS.db web serve
frog --db /path/to/AGENTS.db web snapshot > private-frog-board.json
```

The default host is `localhost` (bound as127.0.0.1). Without `--port`, Frog tries
8788–8807 and then an OS-assigned free port, printing the actual URL. Explicit
`--port` is deterministic and fails if occupied; `--port 0` asks the OS directly.
`--host 127.0.0.1` and `--host ::1` are also supported.

`GET /api/v1/kanban` returns
`frog.kanban-projection.v0`; HTML/JS/CSS are shipped in the Python package.
The database must already exist and be migrated. HTTP and snapshot reads use
SQLite `mode=ro`, `query_only`, and one consistent read transaction, without
command logging or migrations. Missing databases fail rather than being created.

## Contract / authority

The default **Start** view is a scrolling repository slideshow: one card per
registered active repository, a bounded README description and on-demand recent
Git commits, plus separate variants for completed Frog tasks, tasks at hand and
next work. Git history never implies task completion. Repository history is read
only when its slide is visited, with a two-second Git timeout; unavailable history
does not hide the authoritative task links. `/api/v1/repository?id=<registered-name>`
uses the same Host/Origin/auth rules as the board API. No filesystem path inputs.

**Board** opens the four lanes. Clearly labelled **Open task →** and blocker links
open a separate details view; **Back to board** restores filters and focus. The
dependency tree explicitly expands. No inspector sidebar appears by default.
The pinned navigation carries breadcrumbs and settings for auto-hide on scroll
and icons / icons+labels / labels. All preferences are saved in `localStorage`;
tokens and task snapshots are not. Icons retain accessible names and tooltips.
Authentication failure clears private cards; only transient source/transport
failures retain last-known data.

- `source: frog`, `read_only: true`, UTC `observed_at`, event `revision`.
- Four `lanes` and their `counts`; `cards` have task ID, title, priority, raw
  workflow status, lane, repository basename, assignee, task update and status
  observation times, dependency readiness, tags, direct/reverse dependency links
  and unmet dependencies. `repositories` supplies slideshow identities/titles.
- Lane assignment and readiness come directly from the same `board_snapshot`
  used by the Frog TUI. Review/published is not silently promoted to done.
- Absolute paths, task prose, event summaries, account data and command lines
  are not exported. Titles/IDs/agents can still be private: this is **not** a
  public-data scrubber. Publish only to authenticated private consumers.
- Capture freshness is separate from task-status freshness. Failed refreshes
  retain last-known cards but explicitly label them unavailable/stale. Captures
  older than30 seconds are stale, even if an intermediary served them just now.

## Safety and consumers

The local browser uses bearer-authenticated fetch streaming at `/api/v1/events`
(SSE), not a recurring board-fetch timer. One shared watcher samples SQLite
`data_version` every0.5 seconds, including writes from other Frog processes;
it refreshes the consistent projection on change and every15 seconds for source
liveness and time-sensitive readiness. Thus **browser polling is removed**, not
all server-side timing. At most eight streams are accepted, slow readers have
bounded socket writes, and five-minute streams reconnect with bounded backoff.
Failures retain visibly stale last-known tasks. Streams stop while the page is
hidden. Custom `?source=` snapshots remain manually refreshable, not streamed.
The Rebekah/Dash delivery adapters retain their existing polling/push behaviour;
SSE is currently a local Frog transport, not a promise of fleet-wide streaming.

By default the server binds loopback, checks Host against its bound authority,
rejects foreign Origin and cross-site fetches, sends no permissive CORS headers,
and refuses all mutations. Do not expose this unauthenticated backend to a LAN
or public tunnel. An explicit non-loopback bind such as `--host 0.0.0.0` requires
all of `--token-file` (regular0600 file,32+ characters), `--tls-cert`, `--tls-key`,
and `--public-origin https://frog.example:8788`. TLS and bearer authentication
protect the JSON; the browser unlock form keeps the token in memory only.
Unauthenticated HTML contains no task data. Rebekah must authenticate before proxying; Cloudflare must
use its existing instance/view access controls and a protected connector.

The dependency-free renderer accepts `?source=<same-origin-path>`, allowing the
same assets to render an authenticated gateway view or a private JSON snapshot.
No demo fallback, raw HTML from tasks, drag-to-write, or mirror-side task mutation.

## Fleet examples inspected

Survey: local fleet and GitHub code/repositories under `tabenius/*` and
`RAGBAZ/*`, not only Frog.

- `RAGBAZ/kanban-sketch`: richest keyboard/filter/theme/layout reference, but
  default data is a mock; its saved-view API is not Frog task authority.
- `tabenius/WeftMark`: versioned projection and `web/review` read-only renderer;
  evidence/readiness semantics must not be relabeled as Frog workflow status.
- `tabenius/rebekah`: already has an authenticated WeftMark Work board and Dash
  push bridge. Frog is an additive source, not a replacement of that board.
- `RAGBAZ/shambala`: authenticated private snapshot receipt/storage pattern.
- `RAGBAZ/ragbaz-dash`: authenticated instance views and pull/push freshness.

Reusing contracts and safe consumer paths is preferable to cloning a mock board.
