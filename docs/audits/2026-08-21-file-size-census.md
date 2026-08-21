# File-Size Enterprise Census — tensor-grep

Date: 2026-08-21. Read-only audit. No files edited.

## 1. Method

**Exact commands run:**

```bash
git log --oneline -30 --grep="refactor: split"
gh pr list --state open --limit 50
cat scripts/file_size_allowlist.json
grep -rl "file_size_allowlist" tests/ scripts/
sed -n '1,260p' scripts/file_size_budget.py
python scripts/file_size_budget.py --report
python3 -c "... m.census() grouped by category ..."
gh pr view 1071 --json title,files -q '.files[].path'
gh pr view 1072 --json title,files -q '.files[].path'
grep -nE '^(class |def |@app\.command)' <top-10 py files>
grep -nE '^(pub fn |fn |pub struct |struct |impl |mod )' <top-10 rs files>
```

**Existing gate found first, and used as the primary instrument (not reimplemented):**
`scripts/file_size_budget.py` + `scripts/file_size_allowlist.json`, enforced by a governance
test (grep hit on `file_size_allowlist` in `tests/`). This script already:
- Enumerates via `git ls-files -z` (never a directory walk — correctly excludes `.git`,
  `.venv`, `node_modules`, `target/`, `__pycache__`, worktrees, untracked scratch, binaries —
  because those are simply not git-tracked or are `.gitignore`d).
- Restricts to `.py` / `.rs` / `*.schema.json` suffixes (excludes lockfiles, minified/generated
  assets, vendored deps by construction — nothing under `node_modules`/vendored trees is
  git-tracked in this repo).
- Counts **physical lines including comments/blanks** (`sum(1 for _ in handle)` in binary mode)
  — exactly the metric requested.
- Classifies every file into one of the same four categories, with the same four limits:
  `contract<=500`, `core<=1500`, `test<=2000`, `fixture<=2000`.

**Verdict on the repo's own gate vs. the four limits given in the task: THEY MATCH EXACTLY.**
`scripts/file_size_budget.py` module docstring states the standard was set by "CEO, 2026-08-19"
using these identical four numbers, and `file_size_allowlist.json`'s `"limits"` block is
byte-identical to the task's spec. No mismatch to report.

**Classification logic (re-derived from `classify()` in `file_size_budget.py:78-100`, not
hand-judged):**
1. `contract` — only 2 explicit hardcoded paths (`src/tensor_grep/core/result.py`,
   `src/tensor_grep/cli/rg_contract.py`) plus any `*.schema.json` file. Deliberately NOT a path
   heuristic ("contract is a role, not a path pattern").
2. `fixture` — `tests/fixtures/*` or any `conftest.py`.
3. `test` — anything else under `tests/` or `rust_core/tests/`.
4. `core` — anything under `src/`, `rust_core/src/`, `scripts/`, or `benchmarks/`.
5. Everything else (docs, CI yaml, `.claude/skills/*.md`, JS/TS if any, non-`.py`/`.rs`) is
   **out of scope / not scanned** — this is a judgement call inherited from the existing gate,
   not one I introduced. I did not independently verify there are zero non-py/rs "source" files
   in this repo that a stricter definition might pull in (e.g. `*.ts` tooling) — a spot Glob
   found none of consequence, but this is the one point where I am trusting the existing tool's
   scope rather than re-deriving it from first principles.

I ran the tool rather than writing a second, independently-biased scanner, because a second
scanner sharing no code with the first would still share the same `git ls-files` assumption and
the same suffix filter — re-implementing it does not buy real independence, and the tool is
itself the enforced gate (its own governance test would catch drift between it and reality).

## 2. Files exceeding their limit (all 30; gate confirms 0 NEW violations, 0 ratchet regressions)

Ran `python scripts/file_size_budget.py --report`: **856 in-scope tracked files scanned, 30
over limit, 30 grandfathered (exactly matching, i.e. every current violation is already
pinned in the allowlist at its exact current line count).**

| path | category | lines | limit | overage | allowlisted? | split in open PR? |
|---|---|---:|---:|---:|---|---|
| tests/unit/test_cli_modes.py | test | 17204 | 2000 | 15204 | yes (17204) | **yes — PR #1072** (splits into 9 sibling modules) |
| src/tensor_grep/cli/repo_map.py | core | 15243 | 1500 | 13743 | yes (15243) | no (a *different*, now-merged split, #1053, cut repo_map from 4519→7 modules; this remaining 15243-line repo_map.py is the file left after that split — not touched by any currently-open PR) |
| rust_core/src/main.rs | core | 15126 | 1500 | 13626 | yes (15127; pinned value is 1 line stale — file shrank by 1 line since pin, gate reports `pinned=15127` vs live 15126, non-blocking since shrink-only direction is fine) | no |
| src/tensor_grep/cli/main.py | core | 13523 | 1500 | 12023 | yes (13523) | no (already split once, #1052, 17983→13523; no open PR carries it further) |
| tests/unit/test_benchmark_scripts.py | test | 10689 | 2000 | 8689 | yes (10689) | **yes — PR #1071** (splits into 7 part-modules + a correctness file) |
| tests/unit/test_mcp_server.py | test | 9729 | 2000 | 7729 | yes (9729) | no |
| src/tensor_grep/cli/mcp_server.py | core | 5341 | 1500 | 3841 | yes (5341) | no (already split once, #1051, into mcp_rewrite_tools/mcp_audit_tools/mcp_symbol_tools; the remaining 5341-line core file is untouched by any open PR) |
| tests/unit/test_release_assets_validation.py | test | 5258 | 2000 | 3258 | yes (5258) | no |
| rust_core/src/gpu_native.rs | core | 4952 | 1500 | 3452 | yes (4952) | no |
| rust_core/tests/test_schema_compat.rs | test | 4412 | 2000 | 2412 | yes (4412) | no |
| tests/unit/test_session_cli.py | test | 3337 | 2000 | 1337 | yes (3337) | no |
| rust_core/tests/test_routing.rs | test | 2995 | 2000 | 995 | yes (2995) | no |
| tests/unit/test_cli_bootstrap.py | test | 2987 | 2000 | 987 | yes (2987) | no |
| tests/unit/test_file_deps.py | test | 2901 | 2000 | 901 | yes (2901) | no |
| rust_core/src/native_search.rs | core | 2686 | 1500 | 1186 | yes (2686) | no |
| rust_core/src/backend_ast.rs | core | 2553 | 1500 | 1053 | yes (2553) | no |
| rust_core/tests/test_ast_rewrite.rs | test | 2509 | 2000 | 509 | yes (2509) | no |
| tests/unit/test_apply_policy.py | test | 2375 | 2000 | 375 | yes (2375) | no |
| rust_core/tests/test_index.rs | test | 2356 | 2000 | 356 | yes (2356) | no |
| rust_core/tests/test_public_native_cli_parity.rs | test | 2318 | 2000 | 318 | yes (2318) | no |
| tests/unit/test_semantic_provider_navigation.py | test | 2251 | 2000 | 251 | yes (2251) | no |
| tests/unit/test_gpu_benchmark_scale_contracts.py | test | 2244 | 2000 | 244 | yes (2244) | no |
| src/tensor_grep/cli/session_daemon.py | core | 2139 | 1500 | 639 | yes (2139) | no |
| rust_core/src/backend_ast_workflow.rs | core | 2109 | 1500 | 609 | yes (2109) | no |
| benchmarks/run_gpu_native_benchmarks.py | core | 1919 | 1500 | 419 | yes (1919) | no |
| src/tensor_grep/cli/session_store.py | core | 1828 | 1500 | 328 | yes (1828) | no |
| rust_core/src/backend_cpu.rs | core | 1817 | 1500 | 317 | yes (1817) | no |
| rust_core/src/index.rs | core | 1756 | 1500 | 256 | yes (1756) | no |
| src/tensor_grep/cli/bootstrap.py | core | 1696 | 1500 | 196 | yes (1696) | no |
| rust_core/src/python_sidecar.rs | core | 1519 | 1500 | 19 | yes (1519) | no |

No file is over limit and unallowlisted — the gate is green (`0 regressions`). Every violation
above is a **pre-existing, tracked, grandfathered** exception, not a new finding.

## 3. Near-miss watch list (within 10% of limit, currently compliant)

Computed as `lines >= 0.9 * limit` and `lines <= limit`.

| path | category | lines | limit | % of limit |
|---|---|---:|---:|---:|
| src/tensor_grep/cli/lsp_external_provider.py | core | 1452 | 1500 | 96.8% |
| src/tensor_grep/cli/checkpoint_store.py | core | 1431 | 1500 | 95.4% |
| src/tensor_grep/cli/ast_workflows.py | core | 1422 | 1500 | 94.8% |
| src/tensor_grep/cli/ledger_store.py | core | 1417 | 1500 | 94.5% |
| src/tensor_grep/cli/codemap.py | core | 1408 | 1500 | 93.9% |
| tests/unit/test_validation_commands.py | test | 1822 | 2000 | 91.1% |
| tests/unit/test_agent_readiness_script.py | test | 1813 | 2000 | 90.7% |

7 files. Note `checkpoint_store.py` and `ast_workflows.py` were already split-touched by the
2026-07 "wave 1" commit (`7a24e86`, "split ast_workflows and checkpoint_store") and have grown
back to 94-95% of limit since — worth flagging as regrowth risk, not just first-time risk.
`test_agent_readiness_script.py` is also touched by open PR #1072's file list (it appears as a
modified file there, likely incidental context change, not a size-reducing split) — worth a
follow-up check before assuming it stays under budget post-merge.

## 4. Top 10 worst offenders — responsibilities, proposed split, blast radius

### 1. tests/unit/test_cli_modes.py — 17204 lines (test, limit 2000) — **SPLIT ALREADY IN FLIGHT, PR #1072**
Already has 568 top-level def/class. PR #1072 splits it into 9 sibling modules
(`test_cli_modes_agent_capsule.py`, `_ast_backend.py`, `_ast_misc.py`, `_blast_radius.py`,
`_cli_json.py`, `_doctor.py`, `_navigation.py`, `_search_guards.py`, `_shared.py`,
`_upgrade.py`), touching `scripts/agent_readiness.py`, `scripts/diagnose_gpu_delegation_route.py`,
and `scripts/file_size_allowlist.json`. **No further split recommended pending that PR merging** —
recommend re-running this census after #1072 lands rather than proposing a competing split.

### 2. src/tensor_grep/cli/repo_map.py — 15243 lines (core, limit 1500) — no open PR
Distinct responsibilities visible from the def/class scan: repo-context caching
(`_remember_repo_context`, `_get_repo_context_cache_entry`, `_clear_all_source_caches`), a
profiling subsystem (`_ProfilePhase`, `_ProfileCollector`, `_profiling_phase`,
`_attach_profiling`), language/symbol descriptors (`_language_scope_descriptor`,
`_symbol_navigation_descriptor` — the function this repo's own CLAUDE.md tells agents to query
for the language-tier count), envelope/result-shaping (`_envelope`, `_mark_result_incomplete`,
`_copy_scan_limit`, `_copy_partial_signal`, `_scan_did_not_finish`), and deadline/test-scan
plumbing (`_TestScanCounts`, `_DeadlineBreakFlag`, `_UnreadablePathFlag`,
`_deadline_monotonic_from_seconds`). This is the file left over after PR #1053 already carved
out 7 sibling modules from an *earlier* 4519-line ancestor — the file has since regrown to
15243, well past the earlier split point, meaning a **second decomposition wave is now due**.
Proposed split (mirroring the file's own already-visible internal groupings):
- `repo_map_cache.py` — repo-context cache + clear/remember/get entry helpers.
- `repo_map_profiling.py` — `_ProfilePhase`/`_ProfileCollector`/`_profiling_phase`/`_attach_profiling`.
- `repo_map_envelope.py` — `_envelope`, `_mark_result_incomplete`, `_copy_scan_limit`,
  `_copy_partial_signal`, `_scan_did_not_finish`, deadline/flag classes.
- `repo_map.py` (remaining core) — symbol graph build/query, language descriptors.
Tests/imports to change: any test importing these helpers directly from
`tensor_grep.cli.repo_map` (grep `from tensor_grep.cli.repo_map import` /
`tensor_grep.cli.repo_map\.` across `tests/`), plus `AGENTS.md`/`CLAUDE.md` mentions of
`repo_map._symbol_navigation_descriptor()` which must keep resolving (re-export or keep in the
core remainder file). This module is high-risk to touch: the change-control skill flags it as a
front-door / registration-site-adjacent file, so re-verify the 4-registration-site checklist
after any split.

### 3. rust_core/src/main.rs — 15126 lines (core, limit 1500) — no open PR
CLI-arg struct definitions dominate the visible top: `CommandCli`, `PositionalCli`,
`SearchArgs`, `RunArgs`, `CalibrateArgs`, `AuditVerifyArgs`, `ClassifyArgs`,
`GpuNativeStatsArgs`, `GpuTransferBenchArgs`, `GpuCudaGraphArgs`, `GpuOomProbeArgs`, plus
`main`/`main_inner`, help/version-passthrough detection (`print_native_top_level_help`,
`is_top_level_version_invocation`, `is_search_version_invocation`,
`is_top_level_pcre2_version_invocation`, `is_top_level_type_list_invocation`,
`is_search_pcre2_version_invocation`, `is_search_type_list_invocation`,
`parse_public_help_passthrough`) and env/require-ripgrep helpers. Proposed split:
- `cli_args.rs` — every `*Args`/`*Cli` clap struct (the bulk of the file by line count, though
  not by function count — clap derive structs run long).
- `cli_passthrough.rs` — the `is_*_invocation`/`parse_public_help_passthrough` family (argv
  shape-sniffing before Typer/clap parsing — this is exactly the "front-door shadowing" class
  this repo's own AGENTS.md A83 warns about, so any split here needs the corresponding shadowing
  test re-run, not just a mechanical file move).
- `main.rs` (remaining) — `main`/`main_inner` dispatch only.
Tests/imports to change: `rust_core/tests/test_routing.rs`, `test_public_native_cli_parity.rs`,
and any test asserting on `main.rs`'s module path directly (Rust `mod` visibility — check for
`pub(crate)` vs private items losing visibility across the split, which is a compile-time not a
runtime failure, so CI will catch it but it should be anticipated).

### 4. src/tensor_grep/cli/main.py — 13523 lines (core, limit 1500) — no open PR
Already split once (#1052: 17983→13523, "five sibling modules"). Remaining visible groupings:
version/schema plumbing (`_cli_package_version`, `_version_detail_lines`, `_print_version`,
`_json_output_version`, `_with_schema_version`), native-delegation gate
(`_can_delegate_to_native_tg_search`, `_build_native_tg_search_command`,
`_delegate_to_native_tg_search` — load-bearing per `tensor-grep-architecture-contract`, DO NOT
split without re-running the field-coverage ratchet test), path/output helpers
(`_collect_candidate_files`, `_write_path_list`, `_path_output_sort_key`,
`_ordered_path_output`, `_looks_like_binary_path`, `_path_has_hidden_component`,
`_safe_stdout_line`), and semantic-rerank (`_search_with_cpu_fallback`,
`_set_semantic_rank_fallback_reason`, `_note_late_rerank_degraded`, `_apply_semantic_rerank`,
`_friendly_dense_unavailable_message`, `_find_dense_weight`). Proposed further split:
- `main_native_delegate.py` — the delegation gate trio (isolate the highest-risk seam).
- `main_output.py` — path/output helper cluster.
- `main_semantic_rerank.py` — rerank cluster.
- `main.py` (remaining) — version/CLI group wiring, Typer app registration.
Tests/imports: this is THE front-door file — any split must be re-verified against the 4
command-registration sites + 2 flag front-doors named in this repo's own CLAUDE.md, and the
native-delegation ratchet test, before merge. Treat as council-verified-build territory
(load-bearing).

### 5. tests/unit/test_benchmark_scripts.py — 10689 lines (test, limit 2000) — **SPLIT ALREADY IN FLIGHT, PR #1071**
239 top-level defs, mostly `test_run_*`/`test_build_*` benchmark-script behavior tests plus
shared fixtures (`_load_script_module`, `_passing_native_gpu_scale_summary`,
`_native_gpu_scale_summary_with_speed_failure`, `_passing_many_pattern_payload`). PR #1071
splits into `test_benchmark_scripts_part1..7.py` + `test_gpu_benchmark_correctness.py`. No
further split recommended pending merge.

### 6. tests/unit/test_mcp_server.py — 9729 lines (test, limit 2000) — no open PR
381 top-level defs. Shared assertion/fixture helpers duplicate ones seen in
`test_cli_modes.py` (`_canonical_manifest_bytes`, `_write_audit_manifest`,
`_write_scan_results`, `_assert_audit_manifest_envelope`, `_assert_enriched_edit_plan_seed`,
`_assert_navigation_pack`) — worth de-duplicating into a shared `tests/unit/_mcp_test_helpers.py`
or `conftest.py` fixture module rather than just splitting by test name, since the same helper
block appears to be copy-pasted across at least these two giant files. Beyond helpers, tests
group by MCP tool surface: `tg_search`/`tg_ast_search` variants (~20+ tests visible in the head
alone). Proposed split:
- `tests/unit/_mcp_test_helpers.py` (or promote to `conftest.py`) — the manifest/envelope/
  navigation-pack assertion helpers, shared with `test_cli_modes.py`.
- `test_mcp_server_search.py` — `tg_search`/`tg_ast_search` behavior tests.
- `test_mcp_server_*` per remaining tool families (need a deeper grep past the visible head to
  finalize groupings — the visible 25 defs are all search-related; the file almost certainly
  covers other MCP tools further down that I did not enumerate here).
Tests/imports: none outside this file (test files are leaves), but the helper de-duplication
would change `test_cli_modes.py`'s post-split modules too (they'd both import from the shared
helper module) — coordinate with PR #1072's split so both efforts don't reinvent the same
helpers independently.

### 7. src/tensor_grep/cli/mcp_server.py — 5341 lines (core, limit 1500) — no open PR
Already split once (#1051: extracted `mcp_rewrite_tools`/`mcp_audit_tools`/`mcp_symbol_tools`).
Remaining visible groupings: tool registration/versioning (`_mcp_server_version`,
`_apply_mcp_server_metadata`, `_legacy_tools_enabled`, `_register_legacy_tool`,
`_build_mcp_tool_capabilities`), envelope/error shaping (`_envelope_base`,
`_log_tool_exception`, `_sanitized_tool_error`, `_sanitized_tool_error_text`, `_meta_envelope`,
`_meta_unknown_action_error`, `_meta_missing_param_error`, `_meta_confinement_error`,
`_meta_workspace_roots_cap_error`), session-error handling (`_session_error_payload`,
`_session_exception_payload`, `_effective_auto_refresh`), and capsule/capabilities
(`_agent_capsule_error`, `_mcp_capabilities_payload`, `_inject_mcp_contract_fields`,
`tg_mcp_capabilities`). Proposed split:
- `mcp_server_errors.py` — the `_sanitized_*`/`_meta_*`/`_log_tool_exception` error-shaping
  cluster.
- `mcp_server_capabilities.py` — capabilities payload + contract-field injection + capsule
  error.
- `mcp_server.py` (remaining) — registration/versioning + FastMCP app wiring.
Tests/imports: `tests/unit/test_mcp_server.py` (itself a top-10 offender, #6 above) and the MCP
contract-version bump discipline (`_TG_MCP_SERVER_CONTRACT_VERSION`) — this repo's own docs
flag a new MCP tool as a "5th registration site"; a split here is not itself a new tool but
still touches the same file the version constant lives in, so re-verify the constant's location
survives the split.

### 8. tests/unit/test_release_assets_validation.py — 5258 lines (test, limit 2000) — no open PR


> **SUPERSEDED (W4-g split):** this file was split into themed siblings `tests/unit/test_release_assets_validation_*.py` (plus `_shared` + a no-`test_*` shim). The 5258-line monolith no longer exists; each sibling is under the 2000-line test limit.
148 top-level defs, clearly organized by concern already (visible in the head): version-parity
(`uv.lock`/`Cargo.lock` vs `pyproject.toml`), README/docs content-pinning
(`test_should_require_readme_canonical_doc_links_and_release_markers`,
`test_should_reject_readme_current_release_asset_list_with_gpu_binaries`,
`test_should_reject_readme_faster_than_rg_positioning`), winget-manifest validation, CI-pin
validation (`test_should_reject_unpinned_uv_bootstrap_in_ci`,
`..._in_public_gpu_proof_workflow`), and Dependabot config validation. Proposed split:
- `test_release_assets_version_parity.py` — lockfile/pyproject version-pin tests.
- `test_release_assets_readme_docs.py` — README/benchmarks-doc content-pinning tests.
- `test_release_assets_winget.py` — winget manifest tests.
- `test_release_assets_ci_pins.py` — unpinned-uv/CI-pin + Dependabot tests.
Tests/imports: none outside file; low risk, purely mechanical split (test-content pinning, no
shared runtime state visible in the head scan).

### 9. rust_core/src/gpu_native.rs — 4952 lines (core, limit 1500) — no open PR
25+ struct/impl defs visible: match/position types (`MatchPosition`, `PatternMatchPosition`,
`GpuNativeSearchMatch`), config (`GpuNativeSearchConfig`), stats/benchmark types
(`GpuPipelineStats` + its `Default` impl, `GpuPinnedTransferBenchmark`,
`GpuNativeDeviceStats`, `GpuNativeSearchStats`, `GpuCudaGraphBenchmark`), and
internal batching/dispatch plumbing (`SearchFileEntry`, `DeviceFileAssignment`, `BatchedFile`,
`LineDescriptor`, `ClassifiedLineBatch` + impl, `FileBatchPlan`, `PatternBatchPlan`,
`LoadedFileBatch`, `DevicePatternBatch`, `AdaptiveDispatchStats`, `SlotAdaptiveDispatch` + impl,
`SearchExecutionOptions`, `GraphCaptureSignature`). Proposed split:
- `gpu_native_types.rs` — public-facing config/stats/match/benchmark structs (the API surface).
- `gpu_native_dispatch.rs` — internal batching/dispatch plumbing (private structs, the bulk of
  the implementation logic).
- `gpu_native.rs` (remaining) — orchestration functions tying the two together.
Tests/imports: this is the experimental/CI-var-gated GPU backend
(`tensor-grep-gpu` skill scope) — re-verify GPU-gated tests still compile under the CI var-gate
after any module split, and that `rust_core/src/main.rs`'s `Gpu*Args` structs (item #3 above)
still resolve their imports.

### 10. rust_core/tests/test_schema_compat.rs — 4412 lines (test, limit 2000) — no open PR
25+ `struct *Example` types visible, one per JSON schema shape under test: search, ruleset
(metadata/findings/evidence/baseline/suppressions), repo-symbol (defs/refs/callers/blast-radius/
source), coverage, diagnostics. This file is fundamentally a **flat list of independent
serde-deserialize fixture structs**, one per schema surface — an unusually mechanical split
candidate. Proposed split by schema domain:
- `test_schema_compat_search.rs` — `SearchExample`, `SearchMatch`, `SearchRangeExample`.
- `test_schema_compat_ruleset.rs` — the `Ruleset*Example` cluster (7+ structs).
- `test_schema_compat_symbols.rs` — `RepoSymbolExample`, `SymbolDefsExample`,
  `SymbolImpactExample`, `SymbolSourceBlockExample`, `SourceLineMapEntryExample`,
  `SymbolReferenceExample`, `SymbolRefsExample`, `SymbolCallersExample`,
  `BlastRadiusTreeLevelExample`, `SymbolBlastRadiusExample`.
- `test_schema_compat_misc.rs` — `CoverageExample`, `RenderDiagnosticsExample`.
Tests/imports: schema-compat tests are explicitly called out in this repo's docs ("Static
manifests are not live receipts... verifiers re-derive the Actions/artifact tuple and
cross-check Python JUnit plus Rust census") — any split must preserve that every schema example
is still exercised by whichever CI job currently globs `rust_core/tests/test_schema_compat.rs`
by name (check `Cargo.toml`/CI workflow for a hardcoded test-binary name before splitting, since
Rust integration tests each compile to their own binary — a rename changes the `cargo test`
target name, which could silently drop coverage from a name-pinned CI step).

## 5. Count control

| category | files scanned |
|---|---:|
| test | 434 |
| core | 413 |
| fixture | 6 |
| contract | 3 |
| **total** | **856** |

All four categories returned non-zero counts — no empty-scan category to flag. `contract`
(3 files: `src/tensor_grep/core/result.py`, `src/tensor_grep/cli/rg_contract.py`, and one
`*.schema.json` match — the classifier also matches any file ending `.schema.json` in addition
to the 2 hardcoded paths) has the smallest population and is worth double-checking if the
"contract" definition is ever loosened, since a 3-file population is small enough that a single
misclassification would be a large relative swing. Zero `contract` files are over their 500-line
limit and zero are near-miss.

## Cross-check against W4 campaign / open PRs

Recent `refactor: split` commits on `main` (from `git log --grep`):
- `7dfff2f` split `mcp_server.py` → `mcp_rewrite_tools`/`mcp_audit_tools`/`mcp_symbol_tools` (#1051)
- `834ba4c` split `repo_map.py` (4,519→7 modules) (#1053)
- `ae395cd` split `cli/main.py` (17,983→13,523) (#1052)
- `d487937` split `agent_capsule` wave 4 (3,652→926) (#1033)
- `7a24e86` split `ast_workflows`/`checkpoint_store` wave 1 (#1025)
- `d242ce4` split GPU benchmark scripts wave 3 (#1029)
- `6b47b60` split `validate_release_assets` wave 2 (3,780→340) (#1021)

Open PRs (`gh pr list --state open`) relevant to this census: **#1071** (splits
`test_benchmark_scripts.py`) and **#1072** (splits `test_cli_modes.py`) — both are W4-d and both
are the #1 and #5 worst offenders by line count. No open PR currently targets any of
`repo_map.py`, `main.rs`, `main.py`, `test_mcp_server.py`, `mcp_server.py`,
`test_release_assets_validation.py`, `gpu_native.rs`, or `test_schema_compat.rs` — none of the
splits proposed in Section 4 items 2, 3, 4, 6, 7, 8, 9, 10 duplicate in-flight work.
