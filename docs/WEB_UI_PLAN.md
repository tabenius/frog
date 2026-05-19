# Plan: `frog http serve` — a web UI mirroring `frog tui`

Status: **plan for approval — no implementation yet.**

## Goal

A browser UI with the same capabilities as the curses TUI: the task
board, the repo view (fold/expand, directory-tree and box/federation
navigation), claim/finish/edit actions, and live updates — served locally
with zero new dependencies.

## Command surface

    frog http serve --port PPPP --listen 127.0.0.1

- New top-level `http` command with a `serve` subcommand, matching
  `frog mcp serve` and the strict `<command> <subcommand>` grammar.
- `--listen` defaults to `127.0.0.1`; `--port` required (no default to
  avoid silent clashes).
- **Security:** loopback only in v1, no auth on `127.0.0.1`. A
  non-loopback `--listen` is **refused** unless `--insecure` is passed
  *and* an `--token` is set (sent as `?token=` / `X-Frog-Token`).
- Coexists with `frog mcp serve` (different transport/process); both
  are long-lived, neither spawned by the other.

## Architecture — reuse the pure model

The TUI already split logic from rendering. The web UI reuses the
**same pure, serialisable snapshots and state** so the two surfaces
cannot drift:

- `store.board_snapshot()` → board JSON.
- `store.repo_tree_snapshot()` → repos / tree / boxes JSON.
- `TuiState` / `RepoView` already encode fold/scroll/selection purely
  — the browser holds the equivalent client-side; the server stays
  stateless and just serves snapshots + accepts actions.
- Actions reuse existing store functions (`task_claim`,
  `task_finish`, `task_edit`) — identical semantics to CLI/TUI/MCP.
  Slug rename remains a separate future operation; the edit form only
  changes mutable task fields.

## Transport (stdlib only — honour the no-deps constraint)

- `http.server.ThreadingHTTPServer` + a `BaseHTTPRequestHandler`.
- JSON endpoints mirroring `_emit` payload shapes:
  - `GET /api/board` → `board_snapshot`
  - `GET /api/repos` → `repo_tree_snapshot`
  - `POST /api/task/{slug}/claim` · `/finish` (agent from query/header)
  - `PATCH /api/task/{slug}` with `title`, `why`, `what`, `roi_note`,
    `priority`, and `repo_ref`, backed by `store.task_edit`
  - `GET /api/events?since=<id>` → tail for live updates
- **Live updates:** Server-Sent Events (`text/event-stream`) driven by
  the existing DB-fingerprint change detection (same signal the TUI’s
  event-driven refresh uses) — no polling loop, no websockets lib.
  Fallback: `GET /api/board?poll=1` short-poll if EVS unavailable.
- **One self-contained asset:** a single `index.html` with inline CSS
  + vanilla JS (no build step, no CDN) — same philosophy as the
  hand-built `TODO.html`. Gruvbox + RAGBAZ palette to match.

## Testing strategy

- Pure handlers (snapshot→JSON, action dispatch, auth/listen guard)
  unit-tested without binding a socket.
- One thin integration test: bind to an ephemeral port on 127.0.0.1,
  hit `/api/board` and a claim round-trip.
- Security tests: non-loopback `--listen` refused without
  `--insecure`+`--token`; token required when bound off-loopback.
- The HTTP server module stays `pragma: no cover` for the socket
  glue; all logic lives in tested pure functions (same pattern as the
  curses shell vs `TuiState`).

## Phased build sequence

1. `frog http` command + `serve` subcommand + listen/auth guard
   (pure, fully tested) — **no server yet**.
2. Read-only server: `/api/board`, `/api/repos`, static `index.html`
   rendering the board + repo view (fold/expand, tree, boxes).
3. Live updates via SSE off the DB fingerprint.
4. Actions: claim/finish (POST) plus task edit (PATCH), reusing store
   fns + audit events.
5. Polish: palette, keyboard parity with the TUI, error toasts.
6. Docs: a section in `docs/DEPLOY.md` + link from README.

## Risks / decisions to confirm

- SSE through `BaseHTTPRequestHandler` needs a thread per client and
  careful flush; cap concurrent SSE clients (loopback dev tool, small
  N is fine).
- No auth on loopback is acceptable for a single-user dev box; the
  `--insecure`+token gate is the only safe non-loopback path — confirm
  that is the intended boundary.
- Action endpoints mutate the shared DB: they must go through the same
  store functions (lock-aware) as every other surface — no direct SQL.
- Scope check: this mirrors the TUI; it is **not** a multi-user app.
  If multi-user/remote is ever wanted, that is a separate design
  (auth, TLS, sessions) explicitly out of scope here.

## Deliverable

This document. Implementation begins only after approval; the phased
sequence above is the proposed order.
