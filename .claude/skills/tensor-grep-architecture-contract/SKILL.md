---
name: tensor-grep-architecture-contract
description: Understand tensor-grep routing, shared services, local state, and compatibility boundaries before changing behavior.
---

# Architecture and contracts

Start with [architecture](../../../docs/architecture.md),
[routing policy](../../../docs/routing_policy.md), and
[contracts](../../../docs/CONTRACTS.md).

## Trace the actual entry point

A managed native installation starts in the Rust CLI. A Python package
installation starts through Python and may delegate eligible operations.
Native search can use ripgrep, CPU search, an index, or optional GPU paths.
Python-backed commands may run through a sidecar. A successful sidecar invocation
does not establish native execution.

Inspect `rust_core/src/cli_app/` and `rust_core/src/native_search/` for the modular native command parser, routing, and CPU search,
`src/tensor_grep/cli/` (`routers/`, `navigation/`, `agent/`, `mcp/`, `state/`) for modular Python command handlers,
`src/tensor_grep/backends/` for backend implementations, and
`src/tensor_grep/core/` for shared logic.

## Preserve observable behavior

When changing a command, trace all entry points that expose it, including MCP.
Verify argument registration, output schemas, exit codes, fallback behavior,
and incomplete-result reporting. Unsupported features must not be represented
as successful native execution.

Ranking combines symbols, filenames, text, and relationships. Pin existing
unrelated ordering before changing ranking, and test output budgets as well as
full candidate lists.

## Distinguish stored state

- `.tg_cache/manifest_v1.json` is the HMAC-signed unified cache manifest, and `.tg_cache/.gitignore` keeps rebuildable cache artifacts untracked.
- `.tg_cache/index_v1/` holds native trigram index files (`*.tg_index`, with legacy root `.tg_index` compatibility).
- `.tg_cache/ast/` holds rebuildable AST project data.
- `.tg_cache/symbols_v1/` holds content-addressed symbol products and SQLite generation metadata;
  repository-map builds reconcile bounded content, including same-mtime edits.
- `.tg_cache/graph_v1/tdad_tests.sqlite3` holds the confined TDAD source-to-test graph cache.
- `.tensor-grep/sessions/` holds reusable context snapshots.
- `.tensor-grep/checkpoints/` holds recovery data and must not be cleared as cache.

Read [cache management](../../../docs/runbooks/cache-management.md) before
changing cleanup behavior. Preserve compatibility or document an explicit
migration whenever persistent data or public responses change.
