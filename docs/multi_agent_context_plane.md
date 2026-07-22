# tensor-grep as the local shared code-intelligence plane for concurrent agents

When several AI agents work the same repository at once -- a planner, an implementer, a
reviewer, or a fan-out of subagents -- they each need the same machine-computed facts about the
code: the repo map, the context capsule for a query, the blast radius of a symbol. Recomputing
those per agent is wasted work and wasted tokens.

`tensor-grep` is already, today, a **local shared code-intelligence plane** for those agents.
One agent computes a repo map or a rendered edit-plan; the others reuse it. This page describes
exactly what is shared, how, and -- just as importantly -- what is **not** shared, so a harness
never overclaims.

> This is a positioning + mechanism page. Every mechanism below is grounded in a specific
> `file:line` in the shipped source so the claims stay honest and re-verifiable.

## Thesis: share the WHAT-CODE, let a task board own the WHO-DOES-WHAT

Multi-agent coordination has two distinct layers:

- **Task coordination** -- who claims which task, in what order, with what status. Tools like
  Claude Code Agent Teams or Beads own this. It is prose and workflow state.
- **Code intelligence** -- what the code actually is: the file inventory, the symbol map, the
  caller graph, the blast radius, a query-scoped context pack. Only `tg` computes this, and
  computing it is the expensive part.

`tg` shares the second layer. A task board and `tg` **compose**: the board tells agents what to
do; `tg` gives every agent the same, already-computed answer to "what does this code look like."
`tg` deliberately does not try to be a task board, a message bus, or an agent-to-agent protocol.

## Mechanism 1 -- the repo-scoped cross-process session store

A session is a cached repo snapshot on disk, scoped to one repository root, that any process on
the machine can load by id.

- **Where it lives.** Sessions are written under `<repo>/.tensor-grep/sessions/`
  (`session_store.py:323-324`, index at `session_store.py:327-328`), with the root resolved by
  `_resolve_root` (`session_store.py:318-320`).
- **Crash-safe writes.** Every session payload and the index are written atomically with an
  `fsync`-before-rename temp file (`_write_json_atomic`, `session_store.py:397-442`) so a crash
  can never publish a truncated snapshot.
- **Cross-process safety.** Session creation and refresh serialize their index read-modify-write
  under a cross-process lock (`open_session`, `session_store.py:609`; `refresh_session`,
  `session_store.py:674`), so two agents opening or refreshing at once never corrupt or silently
  drop an entry.
- **Bounded retention.** Old sessions are pruned to `TG_SESSION_MAX` (default 64,
  `session_store.py:53`) newest-first (`_prune_session_records`, `session_store.py:449`).
- **Load by id, across agents, confined to the root.** Another agent loads a session with
  `get_session` (`session_store.py:806`). A traversal-shaped or absolute session id is refused
  before any read (`_session_payload_path`, `session_store.py:331-346`), and a payload whose
  recorded root does not match the directory it is stored under is rejected
  (`get_session`, `session_store.py:806` onward) so an agent can never be pointed at a session
  outside its repo.
- **Never silently wrong.** Before serving, a session is validated against the current tree by
  mtime + size (`_stale_changeset`, `session_store.py:526`; `_ensure_session_not_stale`,
  `session_store.py:573`). A stale snapshot raises rather than returning outdated facts.

## Mechanism 2 -- the token-authenticated loopback daemon (a warm shared cache)

The session store is durable but cold: each read re-parses JSON from disk. The optional session
daemon keeps the parsed repo map warm in memory and serves it to every agent over an
authenticated loopback socket.

- **One daemon per root.** Startup is guarded by a start-lock
  (`_try_acquire_daemon_start_lock`, `session_daemon.py:263`) and a probe-or-reuse path
  (`start_session_daemon`, `session_daemon.py:532`) so concurrent agents converge on a single
  shared daemon instead of racing to spawn duplicates.
- **Authenticated + confined.** The daemon generates a per-daemon secret and publishes it to a
  `0600` `daemon.json` (`_write_daemon_metadata`, `session_daemon.py:187`; plus a best-effort
  Windows ACL lockdown, `_restrict_windows_file_to_current_user`, `session_daemon.py:196`).
  Every request is checked with a constant-time compare before dispatch (`is_authorized`,
  `session_daemon.py:1340`), the pre-auth read is byte-bounded and time-limited
  (`_read_bounded_request_line`, `session_daemon.py:1350`), and request paths are confined to
  the daemon's root (`_confine_path_to_root`, `session_daemon.py:243`;
  `_resolve_daemon_request_path`, `session_daemon.py:381`).
- **Self-limiting.** The daemon shuts itself down after an idle stretch (default 900s) or a hard
  max uptime (default 24h) (`session_daemon.py:82-83`; `_run_daemon_lifecycle_monitor`,
  `session_daemon.py:1577`), so a forgotten daemon never lingers.
- **How a second agent hits the first agent's warm cache.** Top-level `tg context-render` and
  `tg edit-plan` reuse a *running* daemon when one exists
  (`_maybe_context_render_via_running_daemon`, `main.py:7343`;
  `_maybe_edit_plan_via_running_daemon`, `main.py:7393`; via
  `request_running_session_daemon`, `session_daemon.py:703`). The `tg session ... --daemon`
  verbs start-or-reuse a daemon (`request_session_daemon`, `session_daemon.py:688`). Implicit
  sessions are keyed by `(root, max_repo_files)` and shared across clients
  (`_implicit_session_id_for_request`, `session_daemon.py`), and the daemon layers a payload
  cache plus a response cache on top so an identical render from a different agent is a cache
  hit, not a recompute.

Warm-served verbs (the shared surface): `repo_map`, `context`, `context_render`,
`context_edit_plan`, `defs`, `impact`, `refs`, `callers`, `blast_radius`,
`blast_radius_render`, `blast_radius_plan` (`_serve_session_request_from_payload`,
`session_store.py:1111` onward).

## Fleet quickstart

The daemon must be running for agents to share the *warm* plane. Start it once at the root, then
let every agent's context / edit-plan / session traffic reuse it.

```powershell
# 1. Start the shared plane once, at the repo root.
tg session daemon start .

# 2. Every agent now shares the warm cache automatically via the top-level verbs:
tg context-render . "invoice payment"      # first agent computes + caches
tg context-render . "invoice payment"      # any other agent -> cache hit
tg edit-plan . "add a refund path"

# 3. Inspect the shared plane (works even after the daemon has stopped):
tg session daemon status . --json
tg doctor . --json
```

## `tg` vs a task board

| | Task board (Agent Teams, Beads) | tensor-grep |
|---|---|---|
| Owns | who does what, task status, prose | machine-computed code facts |
| Unit | a task / claim / message | a repo map, capsule, blast radius |
| Shared how | a task queue | the session store + warm daemon |

They are complementary. Use a task board to coordinate work; use `tg` so every agent starts from
the same, already-computed picture of the code.

## Honesty: what is NOT shared (shipped behavior only)

To keep harnesses from overclaiming, be precise about the current limits:

- **MCP `tg_session_*` tools run in-process, not through the daemon socket.** For example,
  `tg_session_edit_plan` calls `session_context_edit_plan` directly against the on-disk store
  (`mcp_server.py:2147-2162`; `session_context_edit_plan`, `session_store.py:882`). They benefit
  from the durable session store, but they do **not** route through the warm daemon's in-memory
  response cache. To exercise the warm plane, use the CLI top-level verbs or `tg session ...
  --daemon`.
- **The daemon response-cache staleness check is `snapshot_mtime_only`.** It detects changes to
  files already in the snapshot but does **not** detect newly *added* files
  (`_DAEMON_RESPONSE_CACHE_STALE_DETECTION` / `_DAEMON_RESPONSE_CACHE_ADDED_FILE_DETECTION`,
  `session_daemon.py`). After creating files, run `tg session refresh` (or request
  `refresh_on_stale`) so the new files invalidate cached hits.
- **A ledger exists, but is EXPERIMENTAL/preview and explicit-invoke only.** `tg ledger
  claim`/`release`/`list` (advisory, code-scoped coordination -- `ledger_app`, `main.py:278`,
  mounted at `main.py:14166`; `submit_claim`/`release_claim`/`list_claims`,
  `ledger_store.py:581,714,782`) and `tg ledger record`/`find` (content-addressed artifact reuse
  -- `record_finding`/`find_findings`, `ledger_store.py:1118,1255`) are real, shipped commands --
  see `docs/CONTRACTS.md` sections 9-10 for the full contract, and
  `docs/enterprise_review_bundle_ci.md` for how `record`/`find` compose with the evidence-receipt
  CI gate. Still true: nothing in `tg agent`/`tg edit-plan`/the daemon consults the ledger
  automatically (explicit-invoke only), there is no MCP tool surface for it, and it does not
  extend into a general message bus or cross-repo lookup. Since 2026-07-22, `claim`/`release`/
  `list` (Slice 1 only) canonicalize `PATH` to the nearest `.git` ancestor rather than rooting
  themselves at `PATH` taken literally -- see `docs/CONTRACTS.md` section 9's "PATH scoping"
  bullet for the fixed footgun and the new claim `scope` field.

## Demand instrumentation (step 0 for a possible shared-context surface)

Whether `tg` should grow a richer shared-context surface is a **build decision that should be
made on real demand, not intuition.** To gather that evidence, a running daemon keeps a small,
opt-out, PII-free demand counter and persists it to
`<repo>/.tensor-grep/sessions/daemon_metrics.json`.

It records only two things, per UTC day:

1. **Concurrent distinct clients** -- how many distinct agent processes hit the same daemon, and
   whether their request windows overlapped. The calling process pid is injected client-side in
   `_daemon_request` (`session_daemon.py:347`) purely as a diagnostic tag; it is never used for
   auth or routing and never fragments the response cache.
2. **Repeated expensive artifacts** -- whether the same symbol/query was re-requested inside a
   short window (a shared plane would de-duplicate these). The target is stored **only as a
   truncated SHA-256 hash** -- the raw symbol/query text never touches disk.

Properties, by design:

- **PII-free.** No raw symbol or query text is ever persisted -- hashes only.
- **Fail-open.** The record call at the request seam is wrapped so a metrics bug can never break
  serving; the read-back is equally defensive.
- **Bounded.** At most 30 day-buckets and 32 duplicate-target hashes per day on disk.
- **Opt-out.** Set `TG_DAEMON_METRICS=0` to disable recording entirely.

Read it back with no new command or flag -- it rides on the existing status and doctor surfaces:

```powershell
tg session daemon status . --json   # includes a demand_metrics block + a trailing-14-day rollup
tg doctor .                         # one human summary line, e.g.:
# session_daemon_demand(14d): clients=4 concurrent_days=3 dup_requests=17 pre_gate=MET
```

The `pre_gate` field distinguishes three states so the signal is never misread:

- `NO-COVERAGE` -- no daemon ran in the window, so there is simply no data (not a "no demand"
  verdict).
- `NOT-MET` -- there is coverage, but concurrency and repeat-demand stayed below the documented
  thresholds.
- `MET` -- there was real cross-agent concurrency and real repeated-artifact demand over the
  trailing 14 days.

Because the metrics file is read straight from disk, the read-back works even when the daemon is
currently stopped.
