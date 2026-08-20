# Cache and schema versioning in tensor-grep — what actually exists

> Verified against `origin/main` `7ee3a27e` (2026-08-20), by grepping every `*_VERSION`/
> `schema_version` constant under `src/tensor_grep/cli/*.py` and reading each one's consumer, plus
> the one cross-language pair under `rust_core/`. This doc reports what was found, not what a
> "cache migration system" is expected to look like in general — see the honesty note in §4.

## 1. The scope of "cache/schema migration" in this repo, stated plainly

There is **no migration framework** in tensor-grep — no versioned upgrade path that reads an old
on-disk schema and rewrites it forward to a new one in place. Every versioned artifact found in
this codebase does one of exactly two things on a version mismatch:

- **Treat the mismatch as a cache miss and rebuild from source** (real invalidation — §2), or
- **Never check the version on read at all** — the field is stamped into output for a downstream
  *consumer* to branch on, but the *producer* itself never re-reads and validates its own past
  output against it (§3).

Neither of these is "migration" in the schema-upgrade sense. If a future feature needs true
migration (rewrite old-format records to new-format in place, preserving history), that is new
work, not something to discover already built. This doc exists so the next engineer does not go
looking for a migration module that was never written.

## 2. Real, enforced schema-gated cache invalidation: `project_data_v6.json`

This is the one clear example of a *schema version actually gating what gets served*, and it is
notable because the schema is produced by **Rust** and consumed by **Python** — two independent
implementations that must agree on the version number or the cache is silently distrusted.

- **The cache file:** `<project>/.tg_cache/ast/project_data_v6.json`
  (`ast_workflows._get_cache_dir`, `src/tensor_grep/cli/ast_workflows.py:173`, joined with
  `"project_data_v6.json"` at the call site in `ast_workflow_rules.py`).
- **The version constants, kept in two languages and required to move together:**
  - Python: `_PROJECT_DATA_CACHE_SCHEMA_VERSION = 2`
    (`src/tensor_grep/cli/ast_workflows.py:184`).
  - Rust: `const PROJECT_DATA_V6_SCHEMA_VERSION: u32 = 2;` (`rust_core/src/backend_ast_workflow.rs:40`).

  The Python constant's own comment states the obligation directly: *"twin of
  PROJECT_DATA_V6_SCHEMA_VERSION in rust_core/src/backend_ast_workflow.rs... Python is a
  compatibility READER of the Rust-written `project_data_v6.json` cache... Bump BOTH constants
  together whenever the persisted rule-spec schema changes."* There is no automated check tying
  the two constants together (verified: no test file found under `tests/` that reads both source
  files and asserts they match) — keeping them in sync is a **discipline**, not a guard. A future
  auditor could reasonably add a static check for this; none exists today.
- **The gate itself:** `ast_workflow_rules.py:82-88`. The cache is read, an mtime freshness check
  passes, and *then* `cached_data.get("cache_schema_version") != ast_workflows._PROJECT_DATA_CACHE_SCHEMA_VERSION`
  is checked — on mismatch, `still_valid = False` and the caller falls through to rebuilding the
  data from source, exactly as if the cache file did not exist. A cache with no
  `cache_schema_version` key at all (the pre-versioning shape, `{}.get(...)` returning `None`)
  also fails this check and is treated as legacy/stale — this is the "M16/F3" fix referenced in
  the surrounding comment.
- **Proven by a real, currently-passing test, not just a docstring claim:**
  `tests/unit/test_scan_composite_rules_m16.py::test_load_ast_project_data_schema_gate` is
  parametrized over exactly the two failure shapes: a legacy cache with no
  `cache_schema_version` key (expects a rebuild — the fresh value wins over the stale cached one),
  and a cache stamped `cache_schema_version: 2` (current, expects the cache to be served as-is).
  The test's own comment records that this was RED before the fix: *"the mtime-fresh legacy cache
  was served with 'stale'"* — i.e., before the schema gate existed, a structurally incompatible
  cache was trusted purely because its mtime looked fresh.

**What this means for a rebuild:** if you are rebuilding or extending anything that reads
`project_data_v6.json`, the schema-version check is not optional decoration — remove it and a
pre-M16 cache (missing `rule_specs` composite members, severity, and message fields even though
mtime-fresh) is served as if valid, silently degrading scan results. Bumping the schema in only
one language (Python without Rust, or vice versa) reintroduces exactly this bug, because each side
only checks *its own* constant against the payload — there is nothing today that would catch two
constants drifting apart other than a human reading the paired comment.

## 3. Version fields that are stamped but not enforced on read

These all carry a `version` / `schema_version` field in their JSON output, and none of them was
found (via `grep -rn` across `src/tensor_grep/cli/*.py`) to gate a read the way §2's does — they
exist so a *downstream consumer* (a caller parsing the JSON, a future schema bump) has something to
branch on, not because the producer re-validates its own prior output against them:

| Constant | File | What it stamps |
|---|---|---|
| `_CHECKPOINT_VERSION = 1` | `checkpoint_store.py:32` | `metadata.json` / `index.json` records (see `docs/rebuild-guides/tg-checkpoint.md` §4). `_load_index` (`checkpoint_store.py:574`) reconstructs `CheckpointRecord(**entry)` directly from the stored dict with no version branch — a future field rename or removal in `CheckpointRecord` would raise a `TypeError` on an old `index.json`, not migrate it. This is a real, currently-unguarded gap: nothing in this codebase would gracefully handle a `_CHECKPOINT_VERSION` bump today. |
| `_SESSION_VERSION = 1` | `session_store.py:51` | Session-daemon response payloads. Stamped into many response dicts; no read-side check found. |
| `LEDGER_SCHEMA_VERSION = 1` | `ledger_store.py:117` | Advisory-claim ledger entries, surfaced back out through several `main.py` JSON payloads (`main.py:17341` and four siblings) as `ledger_schema_version`. |
| `RECEIPT_SCHEMA_VERSION = 1` | `evidence_receipt.py:51` | Signed evidence receipts, as `receipt_schema_version`. |
| `INVENTORY_SCHEMA_VERSION = 1` | `inventory.py:37` | `tg inventory` output. |
| `_COVERAGE_SCHEMA_VERSION = 1` | `codemap.py:63` | `tg docs-coverage` output. |
| `_DOCTOR_SCHEMA_VERSION = 3` / `_DOCTOR_LSP_SCHEMA_VERSION = 2` | `main.py:128-129` | `tg doctor` output — the highest version number found in this sweep, implying it has actually been bumped at least twice, but still with no read-side compatibility branch found in this codebase (it is emitted, not re-ingested). |
| `_AUDIT_INDEX_VERSION = 1` | `audit_manifest.py:16` | Rewrite-audit manifest index entries. |
| `_DAEMON_METRICS_SCHEMA_VERSION = 1` | `session_daemon.py:113` | Session-daemon metrics payloads. |
| `_TG_MCP_SERVER_CONTRACT_VERSION = "1.7.0"` | `mcp_server.py:191` | The MCP tool-contract version string — this one IS load-bearing for a downstream consumer (an MCP client is expected to branch on it), but it still is not a *cache* schema and this repo has no code that reads an old contract version and migrates a stored artifact forward. |

All of these are consistent with a single house convention: **stamp a version so a future
consumer (a client, a schema bump, an auditor) has something to check against; do not build
migration machinery until an actual incompatible read is a real, demonstrated problem.** That is a
defensible, minimal-YAGNI choice — but it is a choice, not an oversight, and it should be named as
one rather than assumed to be a gap when a new engineer goes looking for "the migration code."

## 4. Honesty note

This document was produced by grepping for version-constant patterns across
`src/tensor_grep/cli/*.py` and reading each hit's surrounding code, plus a targeted check of the
one Rust file the Python side names as a schema twin. It is **not** an exhaustive audit of every
cache or on-disk artifact in the repo — for example, `.tensor-grep/checkpoints/checkpoint-
discovery-cache.json`'s own `_DISCOVERY_CACHE_VERSION = 2` (`checkpoint_store.py:39`) uses the
same strict-equality-invalidation pattern as §2 and was found in the same sweep, but its consumer
(`_read_cached_checkpoint_index_paths`, `checkpoint_store.py:376`) was read only enough to confirm
the pattern, not exercised with a crafted stale-version fixture the way §2's `project_data_v6.json`
gate was (that one had an existing test to point at; this one was not separately verified by
running anything). Treat §3's table as a representative sample proven by grep, and §2 as the one
claim in this document backed by an executed, currently-green test.
