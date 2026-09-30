# Premise-Check Results — tensor-grep audit

Date: 2026-09-30. Base: `main` @ c2967cc (v1.123.1). Method: grep/read of `src/tensor_grep/cli/`,
`rust_core/src/`, `docs/`, `backlog.md`. Line numbers are as of this SHA; cite the SYMBOL, not the line.
Limitation: static read only. No shipped-binary dogfood was run, so "shipped" below means
"registered in the CLI/MCP source", not "exercised on the published wheel".

## Finding 1: "No LSP integration"
**Status:** REFUTED (an LSP server ships). The real gap is narrower: no hover, completion or call-hierarchy handlers.
**Evidence:**
- `src/tensor_grep/cli/lsp_server.py` defines `class TensorGrepLSPServer(LanguageServer)` and `run_lsp()`.
- Registered handlers (`@server.feature(...)`): `DID_OPEN`, `DID_CLOSE`, `DID_CHANGE`, `DID_SAVE`,
  `DEFINITION`, `REFERENCES`, `PREPARE_RENAME`, `RENAME`, `DOCUMENT_SYMBOL`, `WORKSPACE_SYMBOL`,
  plus an `INITIALIZE` hook that negotiates position encoding (utf-8/utf-16 column conversion helpers).
- CLI exposure: `tg lsp` (`main.py: def lsp`, `--provider native|lsp|hybrid`, `--debug-trace`) and
  `tg lsp-setup` (`main.py: def lsp_setup`). Both names are registered in `commands.py` and in the
  Rust front door (`rust_core/src/main.rs`: `Lsp {..}`, `lsp-setup`).
- `lsp_external_provider.py` (1452 LOC, confirmed by `wc -l`) is used, not dead. `ExternalLSPProviderManager` is
  imported by `lsp_server.py`, `repo_map.py` (provider modes at `normalized_provider == "lsp"`),
  `main.py` (`--provider` validation) and `doctor_report.py` (`tg doctor --with-lsp`).
- The Rust help text labels it: "Optional experimental semantic provider mode; provider availability is not LSP proof."
**Conclusion:** Shipped. Two separate things exist: (a) tg as an LSP server over its own repo map, and (b) tg
as a client of external language servers (hybrid mode). The accurate residual claim is "no hover, completion,
diagnostics or call-hierarchy", and the provider mode is labelled experimental. "No LSP integration" should be dropped.

## Finding 2: "No agentic graph tools" (`impact_radius`, `call_chain`, `architecture_overview`, `test_coverage`)
**Status:** MOSTLY REFUTED. Three of four have equivalents. `call_chain` is genuinely absent as a named, multi-hop caller walk.
**Evidence, per claimed tool:**
- `impact_radius()` — SHIPPED and TRANSITIVE. `repo_map.py: build_symbol_blast_radius` /
  `build_symbol_blast_radius_from_map` take `max_depth=3`. They run `_reverse_import_distances` (a BFS up to 3
  levels over reverse imports), `_reverse_importers`, `_personalized_reverse_import_pagerank`, then merge
  per-file `depth` and `graph_score`. They emit `file_matches` with reasons `graph-depth`, `caller` and
  `import-consumer`. The transitive unit is the FILE import graph. Symbol-to-symbol transitivity is not
  computed; callers are direct, and depth>1 comes from import edges. Exposed as `tg blast-radius`,
  `tg blast-radius-render`, `tg blast-radius-plan`, `tg impact`, `tg diff-impact`
  (`diff_impact.py`: "transitive blast radius ... for git diff") and MCP `tg_symbol_blast_radius*`,
  `tg_symbol_impact`, `tg_impact`.
- `call_chain()` — NOT FOUND as such. `tg callers` (`repo_map.py: build_symbol_callers_from_map`) returns
  exact direct callers plus `import_graph_consumers`. grep for `call_chain` / `transitive_callers` /
  `caller_depth` in `repo_map.py` returned no hits. A multi-hop symbol call chain (A calls B calls C) is a real gap.
- `architecture_overview()` — SHIPPED under another name. `tg orient` (`main.py: def orient`,
  `orient_capsule.py: build_orient_capsule`) surfaces top central files (`--max-central-files`) using
  PageRank-style centrality, plus `tg codemap` (`codemap.py`).
- `test_coverage()` — PARTIAL. `repo_map._relevant_tests_for_symbol` links a symbol to tests, via definition tests
  and caller-file tests. It is surfaced in the blast-radius and callers payloads, and as `tg route-test`
  (`main.py`, `@app.command(name="route-test")`) and `tg prepare`. This is a name/import-graph link, not
  line or branch coverage. If "test_coverage" means coverage percentage, that is absent by design.
**Conclusion:** The claim as written is false. The narrow true gap is a multi-hop symbol-level `call_chain`. Do
not build the other three. If anything, wrap them in agent-friendly aliases.

## Finding 3: "Publish benchmarks"
**Status:** REFUTED as a new recommendation (already published), and BLOCKED by policy for anything new.
**Evidence:**
- `docs/benchmarks.md` is a committed, versioned public doc. It has a benchmark matrix of 18 scripts with
  artifact paths, and a "Latest Scripted Benchmark Snapshot (2026-04-29)". It also carries post-`v1.123.1`
  dogfood medians: tool comparison `rg 0.087s` vs `tg 0.097s`, native CPU rows, hot-query rows and AST ratios
  (for example `0.770x` vs `sg`). Also `docs/benchmarks_ast.md`, `docs/gpu_crossover.md`, and the `benchmarks/` tree.
- `backlog.md` section "CEO_GATED", `### #72 — New public benchmark claim`: Status CEO_GATED, AI-Doable NO,
  "CEO approval required for any new public speed or throughput claim." Opens "public benchmark docs &
  marketing materials". Related: #131 and #169 gate GPU claims.
- Existing docs are deliberately hedged, e.g. "do not market context/session speedups from one noisy run" and
  "Treat `rg` as the raw cold exact-text baseline."
**Conclusion:** Duplicates backlog #72 (open, CEO-gated). Do not recommend "publish benchmarks". Only
"request CEO approval on #72" is valid. Existing published numbers already concede parity-to-slower versus `rg`
on cold text search.

## Finding 4: "No multi-level indexing"
**Status:** PARTIALLY REFUTED. The audit's premise ("symbols only") is wrong, but no separate function-tier index exists.
**Evidence:**
- Index layer 1 (text): `rust_core/src/index.rs` `struct TrigramIndex`, `FileEntry`, `PostingEntry` with
  incremental update (`IncrementalUpdateStats`) and a tree fingerprint (`compute_tree_fingerprint`). That is a file-level
  trigram index.
- Layer 2 (structure): `repo_map.py: build_repo_map` returns `files`, `tests`, `symbols`, `imports`. Each symbol
  carries `kind`, `name`, `file`, `line` (`kind` is scored via `_score_symbol`), plus per-language extractors
  (`lang_registry.py`, `lang_*.py`, `repo_map_lang_*.py`; 10 parser-backed languages per project notes).
- Layer 3 (graph): per-file import edges (`imports_by_file`), reverse importers, and a cached map (`repo_map_cache.py`).
- Tiered scoring: `_score_symbol` = name term score x3 + kind term score + `_score_file_path`, plus name-coverage
  and exact-boundary bonuses, and a test-shadow penalty (`_TEST_SHADOW_PENALTY`). `_score_import_entry` scores file
  imports. So file and symbol tiers are both scored. Kind is a weak term-overlap signal, not a dedicated class/fn/file tier weight.
**Conclusion:** File, symbol and import tiers exist and are scored. What is missing is a dedicated
function-body or chunk-level tier (cAST-style chunking is noted as deliberately not the default in the repo's
failure-archaeology skill). Reframe to "no chunk/function-body tier", and verify against Vortexa's actual design before filing.

## Finding 5: "No knowledge graphs" (reachability, context expansion, transitive dependents)
**Status:** REFUTED for reachability and transitive dependents; PARTIAL for a general "knowledge graph".
**Evidence:**
- `repo_map._reverse_import_distances` (3-level reverse-import BFS, deadline-aware) and `_reverse_importers`
  are internal, but their results are exposed via `tg blast-radius*`, `tg impact`, `tg diff-impact`, `tg importers`
  / `tg imports` (MCP `tg_file_importers`, `tg_file_imports`), `tg callers`, `tg refs`, `tg defs`.
- `_personalized_reverse_import_pagerank` provides graph centrality and seed-personalised ranking. It feeds
  `graph_score` in blast-radius output and centrality in `tg orient`.
- Context expansion: `tg agent` / `tg context` / `tg prepare` build budgeted capsules (`_build_context_pack_from_map`)
  that pull callers, blast-radius and relevant tests around a seed.
- Known limit: the graph is import-edge-based at file granularity, not a typed symbol call graph. There is
  no generic graph query API, and no stored, persisted knowledge graph. Cross-file caller resolution falls back to
  a text prefilter for several languages per the repo's own notes.
**Conclusion:** Reachability, transitive dependents and context expansion are shipped and CLI/MCP-exposed. The true
gap is the typed symbol-level call graph (same root cause as `call_chain` in Finding 2), not "no graphs".

## Summary
| # | Claim | Verdict | Real residual gap |
|---|-------|---------|-------------------|
| 1 | No LSP | REFUTED | hover, completion and call-hierarchy absent; provider mode experimental |
| 2 | No agentic graph tools | MOSTLY REFUTED | multi-hop symbol `call_chain` absent; coverage is name/import-link, not %-coverage |
| 3 | Publish benchmarks | REFUTED / BLOCKED | none; backlog #72 is CEO_GATED and docs/benchmarks.md already exists |
| 4 | No multi-level indexing | PARTIAL | no chunk/function-body tier |
| 5 | No knowledge graphs | REFUTED (mostly) | no typed symbol-level call graph |

Only one real product gap survives across the audit: a typed, multi-hop symbol call graph. It is the common
root of Finding 2 `call_chain` and Finding 5. Before building it, apply the project's Step-0 premise check
(verify-plan-against-code) against `build_symbol_callers_from_map` and the per-language lang modules.
