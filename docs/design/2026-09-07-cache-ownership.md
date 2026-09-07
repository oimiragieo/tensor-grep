# Cache/index ownership map and dormant-index disposition (P9 extension, Task 06)

Source: `docs/plans/2026-09-07-agentic-quality-simplification.md` Task 06, checkboxes 1-2.
Scope: record which component owns each in-memory/on-disk cache state that a warm agent
turn could reuse, and make an explicit, documented retain/adopt/deprecate call on the
dormant persisted-BM25 building blocks. This is a documentation-only slice; no code moved.

## Component ownership map

| State | Owning module | Key | Lifetime | Invalidation | Byte caps | Writers |
|---|---|---|---|---|---|---|
| Repository symbol map (defs/refs/callers) | `src/tensor_grep/cli/repo_map.py` (`build_repo_map`) | `(root, ignore set, language registry version)` | Rebuilt fresh on every `build_repo_map` call — **no cross-call cache exists at this layer**. | N/A (always rebuilt) | `_DEFAULT_*_REPO_MAP_LIMIT` file-count ceilings per caller (prepare/orient/session) | Any caller of `build_repo_map` |
| Session payload (repo_map + `last_prepare` decision) | `src/tensor_grep/cli/session_store.py` | `(root, session_id)` -> `<session_dir>/<session_id>.json` | Until `refresh_session`/`open_session` rewrites it | `SessionStaleError` on fingerprint mismatch; refresh re-derives inside `index_lock` (AGT-02 fix, `611ca8d`) | none observed at this layer (delegates to repo_map limits) | `open_session`, `refresh_session`, `session_prepare` (via `index_lock`) |
| Native trigram index (`.tg_index`) | Rust `rust_core` (`index.rs`), driven from `src/tensor_grep/backends/` | `root` mtime/path fingerprint | Until source files change | mtime-fingerprint mismatch triggers rebuild | not audited in this slice | native backend build path |
| Persisted chunk-BM25 index (`.tg_semantic_index/`) | `src/tensor_grep/core/semantic_index.py` | `(root, TG_SEMANTIC_INDEX_DIR)` -> `bm25_chunks.json` + `bm25_meta.json` | Until `build_and_save` overwrites it | SHA-256 fingerprint over sorted paths+mtimes at load; mismatch warns to stderr and returns `None` (in-memory fallback) | none (unbounded corpus size) | `build_and_save` (test-only caller today, see below) |
| `tg prepare`/`tg session prepare` repo-map build | `src/tensor_grep/cli/prepare_service.py` (`_build_prepare_payload`) | none — always calls `build_repo_map` from scratch | N/A | N/A | inherits repo_map limits | every `tg prepare` and `session_prepare` call |

## Warm-reuse gap (pre-existing, not fixed by this slice)

`docs/BACKLOG.md` already records this under the P9 entry: `session_prepare` calls
`build_prepare_snapshot(path=path, query=query)`, which always delegates to
`_build_prepare_payload` and does one full `build_repo_map` from scratch — a "warm" session
gets zero benefit from its already-loaded `payload["repo_map"]`. Fixing this (extracting a
prepare-from-map service that accepts a caller-supplied, freshness-checked map, plus the spy
control proving zero redundant `build_repo_map` calls on a warm turn) is real design work
against shared, heavily-used code (`prepare_service.py`, `session_store.py`,
`session_resume_service.py`) and remains **open** under AGT-03/P9 in `docs/BACKLOG.md` and
`docs/TASK_BOARD.md` — deliberately not attempted in this bounded slice to avoid rushing a
regression into code every `tg prepare` call depends on.

## Dormant `semantic_index.py`: explicit disposition — **RETAIN, library-only, not adopted**

- **Current state (verified 2026-09-07):** `git grep -n semantic_index` across `src/` shows
  the module is imported only by `tests/unit/test_semantic_index.py`. No production caller
  (`prepare_service.py`, `main.py`, `mcp_server.py`, `session_*` modules) imports it. There is
  no `tg index` CLI command.
- **Self-documentation:** the module's own docstring (`semantic_index.py:1-16`) already states
  this explicitly: *"this persisted-index acceleration is NOT yet wired into the CLI... These
  helpers are the building blocks for a future indexed-acceleration command."*
- **History:** `docs/BACKLOG.md`'s dead-code audit trail (2026-08-01 campaign) already
  evaluated this exact module and chose **retain, not delete** — it is intentionally-unwired
  library code (`build_and_save`/`load_or_warn`, chunk-BM25 building blocks under
  `.tg_semantic_index/`), tracked as future scope for `tg index` (R2 in
  `docs/plans/2026-08-06-agentic-cli-audit-campaign.md` and
  `docs/plans/2026-08-08-backlog-completion-plan.md`).
- **Decision (this slice, reaffirming and formalizing the above):** **RETAIN as library-only.**
  Not "safely adopt" (no CLI wiring in this slice — that's the R2/P9-daemon scope, unstarted).
  Not "deprecate" (no in-repo callers is not public-API deletion authority per the plan's own
  Task 06 wording, and prior campaigns already confirmed it is deliberate, documented,
  forward-looking scaffolding, not orphaned dead code).
- **Test enforcing this decision:** `tests/unit/test_semantic_index_dormant_disposition.py`
  fails if (a) the module loses its "NOT yet wired into the CLI" honesty docstring without this
  doc being updated, or (b) a production module outside `tests/` starts importing it without
  this doc's ownership table being updated to reflect the new caller — catching silent drift
  in either direction (module goes dead-and-undocumented, or module goes live-and-undocumented).

## Generation-safe publication (checkbox 3 of Task 06)

**Out of scope for this slice.** Extracting a prepare-from-map service with lock-hold
measurement and generation-checked publication touches the same shared `prepare_service.py`/
`session_store.py` code path as the warm-reuse gap above, and per Task 06's own ordering this
depends on the prepare-from-map extraction happening first. Left open under P9 in
`docs/BACKLOG.md`.
