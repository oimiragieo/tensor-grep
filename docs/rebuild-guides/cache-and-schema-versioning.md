# Cache and schema versioning

Version fields have different purposes. A cache schema controls whether persisted
data can be read, while an output schema or MCP contract version tells a client
how to interpret a response. Neither implies an automatic migration framework.

## AST project cache

`<project>/.tg_cache/ast/project_data_v6.json` is rebuildable AST data. Python's
`_PROJECT_DATA_CACHE_SCHEMA_VERSION` in `ast_workflows.py` and Rust's
`PROJECT_DATA_V6_SCHEMA_VERSION` in `backend_ast_workflow.rs` describe the same
persisted schema. Update both when the serialized rule specifications change.

The Python reader in `ast_workflow_rules.py` checks `cache_schema_version` after
freshness checks. A missing or mismatched version invalidates the cache and causes
a rebuild from source. Fresh modification times alone do not prove schema
compatibility. See
`tests/unit/test_scan_composite_rules_m16.py::test_load_ast_project_data_schema_gate`
for legacy and current-cache cases.

## Other local state

- The checkpoint discovery cache uses `_DISCOVERY_CACHE_VERSION` and can be
  invalidated and rebuilt. It is separate from durable checkpoint snapshots.
- Checkpoint metadata and indexes carry a storage version. Before changing their
  shape, design compatibility for old records and test restoration; discarding
  rollback data as a cache miss is not an acceptable migration.
- Sessions and experimental ledger records have their own versioned formats.
  Review their readers, writers, retention rules, and malformed-record behavior
  before introducing a new format.
- The library-only persisted BM25 index has independent schema and chunker-mode
  checks; it is not the native text index. See [architecture](../architecture.md#persisted-semantic-index-library).

## Output contracts

Doctor, inventory, coverage, evidence receipts, and MCP output use version fields
for their consumers. Preserve existing fields within the documented compatibility
boundary, add new fields deliberately, and update schema tests and examples in the
same change. The live constants and [Contracts](../CONTRACTS.md) are authoritative;
a historical example is not proof of the current version.

An incompatible persisted-state change needs an explicit policy: reject it with
an actionable error, rebuild it when it is disposable, or migrate it while
preserving required data. Test missing, current, old, malformed, and unexpected
future versions. Do not assume a version stamp enforces any of those behaviors
unless the reader checks it.
