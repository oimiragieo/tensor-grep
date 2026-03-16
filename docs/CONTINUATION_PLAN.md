# tensor-grep Continuation Plan

## For: Next agent picking up from commit 8fcc2d9

## Current State

**95 Rust tests, 510 Python tests, all passing.**

### What exists and works

| Capability | Status | Key files |
|---|---|---|
| Native text search (rg passthrough) | Production | `main.rs`, `rg_passthrough.rs` |
| Native AST search (ast-grep-core) | Production, 1.37x faster than sg | `backend_ast.rs` |
| Native AST rewrite (plan/diff/apply/verify) | Production, parity with sg | `backend_ast.rs`, `main.rs` |
| Trigram index (build/persist/query/invalidate) | Production | `index.rs` |
| Auto-routing (warm index detection) | Production | `main.rs:398-420` |
| GPU sidecar routing | First cut, works | `main.rs`, `sidecar.py` |
| Harness JSON contracts (search, rewrite, verify) | v1, tested | `main.rs` |

### Key accepted performance lines

- Text search: within 1-10% of rg across 10 scenarios
- AST search: tg 325ms vs sg 444ms (1.37x faster, 1000 files / 50k LOC)
- AST rewrite plan: 545ms, diff: 628ms, apply: 739ms (sg apply: 702ms)
- Index warm query: 84-160ms vs 238ms cold rg scan (up to 2.8x faster)
- AST parity: 40/40 cases across Python, JS, TS, Rust

### Architecture (Rust-side, `rust_core/src/`)

```
main.rs          CLI entry, routing decisions, all command handlers
backend_ast.rs   AST search, rewrite plan/apply/verify, diff generation
backend_cpu.rs   Native regex/literal search + replace via memmap
index.rs         Trigram index build/persist/load/query/staleness
rg_passthrough.rs  Spawn ripgrep for cold text search
python_sidecar.rs  Spawn Python for sidecar commands (classify, scan, test, etc.)
backend_gpu.rs   GPU backend stubs
```

### CLI surface

```
tg PATTERN PATH                    # positional text search (auto-routes to rg or index)
tg search PATTERN PATH             # explicit text search
tg search --index PATTERN PATH     # force index build/use
tg run PATTERN PATH                # AST structural search
tg run --rewrite REPL PATTERN PATH # dry-run rewrite plan (JSON)
tg run --rewrite REPL --diff ...   # unified diff preview
tg run --rewrite REPL --apply ...  # apply edits
tg run --rewrite REPL --apply --verify ... # apply + byte-level verify
tg search --gpu-device-ids 0 ...   # GPU sidecar routing
```

---

## What is left to do (ordered by priority)

### Priority 1: JSON Schema Documentation and Examples

**Why:** The v1 JSON contracts exist and are tested, but there are no schema docs or example artifacts. Any harness consumer needs to know the exact shape.

**What to do:**

1. Create `docs/harness_api.md` documenting the three JSON contracts:

   **Search result** (`tg search --index --json`):
   ```json
   {
     "version": 1,
     "routing_backend": "TrigramIndex",
     "routing_reason": "index-accelerated",
     "query": "pattern",
     "path": "dir",
     "total_matches": 2,
     "matches": [{"file": "a.txt", "line": 1, "text": "..."}]
   }
   ```

   **Rewrite plan** (`tg run --rewrite REPL PATTERN PATH`):
   ```json
   {
     "version": 1,
     "pattern": "...",
     "replacement": "...",
     "lang": "python",
     "total_files_scanned": 100,
     "total_edits": 5,
     "edits": [{
       "id": "e0000:file.py:10-30",
       "file": "...",
       "line": 2,
       "byte_range": {"start": 10, "end": 30},
       "original_text": "...",
       "replacement_text": "...",
       "metavar_env": {"F": "add", "EXPR": "x + y"}
     }],
     "rejected_overlaps": []
   }
   ```

   **Apply+verify result** (`tg run --rewrite REPL --apply --verify --json`):
   ```json
   {
     "plan": { ... },
     "verification": {
       "total_edits": 5,
       "verified": 5,
       "mismatches": []
     }
   }
   ```

2. Create `docs/examples/` with saved example JSON artifacts from each command.

3. Add a compatibility test that parses the example artifacts and validates field presence/types. This locks the schema against accidental breakage.

**Files to create:** `docs/harness_api.md`, `docs/examples/*.json`
**Files to modify:** None
**Tests to add:** Schema compatibility test in `rust_core/tests/`

---

### Priority 2: Benchmark Matrix Expansion

**Why:** Current benchmarks use a single Python corpus (1000 files, 50k LOC). The AST "1.37x faster than sg" claim needs validation across languages and scales.

**What to do:**

1. **Multi-language AST benchmark**: Extend `run_ast_benchmarks.py` or create `run_ast_multi_lang_benchmarks.py` to measure tg vs sg across Python, JavaScript, TypeScript, and Rust corpora. Use `gen_corpus.py` to add JS/TS/Rust generators (it currently only generates Python).

2. **Large-scale rewrite benchmark**: Extend `run_ast_rewrite_benchmarks.py` with:
   - 5000-file corpus (not just 1000)
   - Many-edits-per-file cases (10+ matches per file)
   - Mixed shrink/grow replacements

3. **Warm harness loop benchmark**: Create `run_harness_loop_benchmarks.py` that measures the full agent loop:
   - search -> plan -> diff -> apply -> verify
   - Repeated 10x on same corpus with mutations between iterations
   - This measures what an AI agent actually does, not one-shot CLI

4. **Index scaling benchmark**: Measure index build time, warm query time, and index file size for 1k, 5k, 10k, 50k file corpora.

**Files to create/modify:** `benchmarks/run_ast_multi_lang_benchmarks.py`, `benchmarks/gen_corpus.py` (add JS/TS/Rust generators), `benchmarks/run_harness_loop_benchmarks.py`
**Benchmark commands to add to AGENTS.md:** Document the new benchmark scripts

---

### Priority 3: Routing Policy Hardening

**Why:** Auto-routing to warm index is implemented but needs explicit policy rules, tests, and benchmark-backed thresholds.

**What to do:**

1. **Document routing policy** in `docs/routing_policy.md`:
   - Cold text search: always rg (fastest cold path)
   - Warm text search (`.tg_index` exists, not stale, pattern >= 3 chars, no unsupported flags): TrigramIndex
   - AST search: always AstBackend (native Rust, no Python)
   - GPU: only when `--gpu-device-ids` explicitly provided
   - Rewrite: always AstBackend
   - Never auto-select GPU. Never auto-select Python sidecar for search.

2. **Add routing reason to all JSON outputs**: Currently `tg search` (non-index path) emits `RoutingMetadata` without routing reason when going through rg. Make routing reason explicit in every code path.

3. **Add routing regression test matrix**: Create `rust_core/tests/test_routing.rs` with tests for every routing decision:
   - `tg search` -> rg (no index)
   - `tg search` with warm index -> TrigramIndex (auto)
   - `tg search --index` -> TrigramIndex (explicit)
   - `tg search -v` with warm index -> rg (invert not supported by index)
   - `tg search -C 3` with warm index -> rg (context not supported)
   - `tg search -w` with warm index -> rg (word boundary not supported)
   - `tg search -g "*.py"` with warm index -> rg (glob not supported)
   - `tg search` short pattern with warm index -> rg (pattern < 3 chars)
   - `tg run` -> AstBackend
   - `tg search --gpu-device-ids 0` -> GpuSidecar

4. **Benchmark threshold validation**: Verify that auto-routing to index is actually faster for representative workloads. If it's not measurably faster for a given scenario, don't route there.

**Files to create:** `docs/routing_policy.md`, `rust_core/tests/test_routing.rs`
**Files to modify:** `main.rs` (ensure all paths emit routing reason)

---

### Priority 4: GPU Crossover Policy

**Why:** GPU sidecar routing exists (`--gpu-device-ids`) but there's no measured crossover point. Nobody has benchmarked when GPU actually wins vs native CPU/rg.

**What to do:**

1. **Run GPU benchmarks at scale**: Use `benchmarks/run_gpu_benchmarks.py` with varying corpus sizes (1MB, 10MB, 100MB, 1GB) to find the crossover point where GPU actually beats rg.

2. **Document crossover in `docs/gpu_crossover.md`**:
   - Corpus size threshold
   - Pattern complexity threshold
   - Transfer overhead
   - When GPU should NEVER be selected
   - When GPU MAY win

3. **Add `--gpu-auto` routing** (only if benchmarks show a clear crossover): If corpus > N MB and pattern is complex regex, offer to route to GPU. Otherwise keep GPU strictly opt-in via `--gpu-device-ids`.

4. **Harden GPU sidecar error handling**: Test what happens when:
   - CUDA is not available
   - GPU device ID is invalid
   - Sidecar Python crashes mid-search
   - Sidecar returns malformed output

**Files to create:** `docs/gpu_crossover.md`
**Files to modify:** `benchmarks/run_gpu_benchmarks.py`, potentially `main.rs`
**Note:** If benchmarks show GPU never wins for typical workloads, document that honestly and keep GPU strictly opt-in. Do not add auto-GPU routing without measured proof.

---

### Priority 5: Editor-Grade Safety Guarantees

**Why:** The rewrite path is correct for happy-path cases, but not yet hardened for adversarial conditions.

**What to do:**

1. **Encoding preservation**: Test and fix rewrite behavior with:
   - UTF-8 with BOM
   - Mixed line endings (CRLF + LF in same file)
   - Non-ASCII content (CJK, emoji, combining characters)
   - Binary files (should be skipped, not corrupted)

2. **Stale-file detection for rewrites**: Before applying edits, verify file mtime hasn't changed since planning. If it has, abort with a clear error rather than applying stale edits to modified content. Add `file_mtime_ns` to `RewriteEdit` during planning, check in `apply_rewrites()`.

3. **Atomic write**: Currently `apply_edits_to_file` does `read_to_string` + `write`. If the process is killed between read and write, the file is lost. Use write-to-temp + rename pattern for atomic replacement.

4. **Large file handling**: The current rewrite path reads entire files into memory. Test behavior on files > 100MB. Consider streaming or chunked processing for very large files, or at minimum fail gracefully with a clear error.

**Files to modify:** `backend_ast.rs` (apply_edits_to_file, plan_file_rewrites)
**Tests to add:** Encoding tests, stale-file detection test, atomic write test

---

### Priority 6: Index Subsystem Scaling

**Why:** The trigram index works for 1000-file corpora. It needs to be validated at 10k-100k file scale.

**What to do:**

1. **Benchmark index build/load/query at scale**: Measure for 1k, 5k, 10k, 50k files. The binary format may need optimization (currently ~19MB for 1000 files with 2.4M postings).

2. **Index compression**: If the binary format is too large at scale, add optional compression (e.g., varint encoding for posting lists, or zstd compression of the whole file).

3. **Incremental index updates**: Currently the entire index is rebuilt when any file changes. Add incremental update: remove stale file entries, add new file entries, update changed file entries. This is critical for large repos where rebuild takes seconds.

4. **Index-accelerated regex**: The current regex path extracts the longest literal substring for trigram prefiltering. Extend to handle:
   - Alternation: `foo|bar` -> prefilter on both
   - Character classes: `[abc]def` -> skip prefilter (too many trigrams)
   - Document which regex subsets are safe for index acceleration

**Files to modify:** `index.rs`
**Tests to add:** Scale tests, incremental update tests, regex subset tests

---

### Priority 7: Harness Workflow Integration

**Why:** The individual pieces (search, plan, apply, verify) are tested independently. The full agent workflow needs integration testing and tooling.

**What to do:**

1. **MCP server integration**: `tg mcp` exists but the MCP server may not expose the new rewrite/index capabilities. Verify and extend the MCP tools to include:
   - `search` with index support
   - `rewrite_plan` (dry-run)
   - `rewrite_apply` (apply + verify)
   - `rewrite_diff` (diff preview)

2. **Streaming output for large results**: For large search results, emit NDJSON (one JSON object per line) instead of a single large JSON array. This allows streaming consumption by agents.

3. **Batch rewrite API**: Allow multiple pattern/replacement pairs in a single invocation. The current API does one rewrite at a time.

---

## What NOT to do

- Do not go back to Python startup micro-tuning
- Do not chase AST micro-optimizations beyond the current 1.37x line
- Do not widen GPU routing without measured crossover benchmarks
- Do not add features to the Python sidecar that should be native Rust
- Do not break the v1 JSON contracts without incrementing the version number
- Do not add auto-GPU routing without explicit benchmark proof

## Validation commands

```powershell
# Rust tests
cd rust_core && cargo test

# Python validation
uv run ruff check .
uv run mypy src/tensor_grep
uv run pytest -q

# Text search benchmarks
python benchmarks/run_benchmarks.py --output artifacts/bench_run_benchmarks.json
python benchmarks/check_regression.py --baseline auto --current artifacts/bench_run_benchmarks.json

# AST search benchmark
python benchmarks/run_ast_benchmarks.py --output artifacts/bench_run_ast_benchmarks.json

# AST parity check
python benchmarks/run_ast_parity_check.py --output artifacts/ast_parity_report.json

# AST rewrite benchmark
python benchmarks/run_ast_rewrite_benchmarks.py --output artifacts/bench_ast_rewrite.json
```

## Key invariants to preserve

1. `tg search PATTERN PATH` without `--index` must always work via rg passthrough
2. `tg run` must never spawn Python -- it is 100% native Rust
3. `tg run --rewrite` dry-run must never modify files
4. `tg run --rewrite --apply` must validate overlaps BEFORE writing
5. Verify must use byte-level exact text matching, not heuristic re-search
6. Index format must start with `TGI\x00` magic + version byte
7. Auto-routing to index must be conservative (only when warm, not stale, pattern >= 3, no unsupported flags)
8. All JSON outputs must be single documents, never multiple concatenated objects
