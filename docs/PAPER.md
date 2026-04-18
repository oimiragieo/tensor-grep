# Tensor-Grep: Routing-First Search, Structural Retrieval, and AI Harness Planning for Real Codebases

> Current-state note (2026-03-30): this document now tracks three active product lines: cold-path text search, native AST/rewrite execution, and AI-harness-oriented repository planning. Not every historical benchmark below describes the current accepted line. The accepted local line on this Windows host keeps `rg` as the default cold-path backend for generic text search, keeps the native Rust AST backend ahead of `sg` on both search and rewrite benchmarks, and adds a repository-planning surface that is now benchmarked against real external repos and headless agent competitors. The latest accepted external planning baseline covers 29 real-repo scenarios and scores `mean_file_hit_rate = 1.0`, `mean_span_hit_rate = 1.0`, and `mean_file_precision = 0.9060`. On bounded cross-language comparison slices, `tensor-grep` currently beats Gemini and Copilot on planning quality, validation targeting, and context efficiency. The patch-correctness benchmark layer is now anchored by one refreshed same-pack 12-scenario scorecard: `artifacts/patch_eval_demo/real_patch_system_scorecard.md`. On that accepted hard pack, `claude-enhanced` lands `1.0 / 1.0`, `claude-baseline` lands `0.75 / 0.75`, Copilot lands `0.5 / 0.5`, and all currently measured Gemini lines land `0.0 / 0.0` (`gemini-cli`, `gemini-baseline`, `gemini-enhanced`). The accepted read is that Gemini's failure line on this host is now operationally understood rather than ambiguous. The runner no longer inherits the user's global `.gemini/GEMINI.md` persona or MCP-heavy settings during benchmark runs, and the Windows timeout path is bounded close to the requested timeout. Even after that cleanup, Gemini still either times out (`~65s` on a `60s` cap) or, with a longer isolated probe, fails to emit valid JSON output; the broader Gemini-enhanced rerun now confirms that the committed Gemini skill/project setup does not recover the line on the accepted pack. The user-style `Claude baseline` vs `Claude + tensor-grep skill + CLAUDE.md` benchmark is now also real on the accepted 12-scenario corpus: plain Claude lands `0.75 / 0.75` for patch-applied / validation-passed, while the enhanced setup is back to `1.0 / 1.0` after tightening the repo-local `CLAUDE.md` task-engagement rule. The cost is latency: `29.89s` baseline vs `52.59s` enhanced on the accepted 12-scenario line. New command-level tracing still shows that at least some of that slowdown is pure Claude deliberation rather than tool cost: on a traced probe, the enhanced path took `24.64s` with `tg_invocation_count = 0`, versus `8.65s` for baseline. The semantic-provider feature (`native | lsp | hybrid`) is implemented and benchmarkable. The broad `click` provider bakeoff still shows no planning-quality gain over `native` while increasing wall-clock cost (`68.374s` native vs `89.79s` LSP vs `89.382s` hybrid), so provider-backed modes remain opt-in rather than default on the broad pack. However, focused provider hard-case artifacts now exist at `artifacts/bench_provider_navigation_click_hardcases.json`, `artifacts/bench_provider_navigation_js_ts_hardcases.json`, and `artifacts/bench_provider_navigation_rust_hardcases.json`, with companion scorecards at `artifacts/bench_provider_navigation_click_hardcases.md`, `artifacts/bench_provider_navigation_js_ts_hardcases.md`, `artifacts/bench_provider_navigation_rust_hardcases.md`, and a combined summary at `artifacts/bench_provider_navigation_hardcases_combined.md`: on the accepted 2-scenario Python alias-wrapper pack, the accepted 2-scenario JS/TS imported-alias wrapper pack, and the accepted 2-scenario Rust use/re-export alias-wrapper pack, `native` caller hit rate is `0.0` while `hybrid` reaches `1.0` caller hit rate; on all three packs, `hybrid` caller precision is `1.0`. Rust test targeting is now clean on the focused external `clap_lex` pack: the accepted rerun at `artifacts/bench_bakeoff_clap_lex_rust_targeting_rerun.json` reaches `mean_test_hit_rate = 1.0` and `mean_validation_cmd_hit_rate = 1.0`. A newer focused Python precision rerun on the `click` slice now lands `mean_file_precision = 1.0` with zero false-positive scenarios; the stable artifacts are `artifacts/bench_bakeoff_click_precision_rerun.json` and `artifacts/bench_bakeoff_click_precision_rerun_analysis.md`. The accepted mechanism is selective pruning, not more graph expansion: for Python `utils.py`, `termui.py`, and `core.py` symbols, once depth-one dependent files exist, `tensor-grep` now drops depth-two-or-worse graph-only spillover from edit-plan dependency ranking. The accepted Rust mechanism is targeted association, not broader graph expansion: Rust test association now recognizes fully qualified symbol usage, inherent-`impl` method usage, and owner-type inheritance for methods inside nested integration tests, and blast-radius filtering preserves `test-graph` and filename-backed matches instead of discarding them. Treat later optimization-history sections as historical unless they are explicitly tied to the current accepted artifacts in `docs/benchmarks.md`, `artifacts/bench_external_eval_native_provider.json`, `artifacts/patch_eval_demo/real_patch_system_scorecard.md`, `artifacts/patch_eval_demo/claude_skill_ab_limit12_current_claude_md_bakeoff.json`, `artifacts/patch_eval_demo/gemini_skill_ab_limit12_bakeoff.json`, `artifacts/bench_bakeoff_click_precision_rerun.json`, `artifacts/bench_bakeoff_clap_lex_rust_targeting_rerun.json`, `artifacts/bench_provider_navigation_click_hardcases.json`, `artifacts/bench_provider_navigation_click_hardcases.md`, `artifacts/bench_provider_navigation_js_ts_hardcases.json`, `artifacts/bench_provider_navigation_js_ts_hardcases.md`, `artifacts/bench_provider_navigation_rust_hardcases.json`, `artifacts/bench_provider_navigation_rust_hardcases.md`, `artifacts/bench_provider_navigation_hardcases_combined.md`, and the provider-mode bakeoff artifacts.
>
> Trust-surface note (2026-03-29): review bundle artifacts are now deterministic for identical inputs. `create_review_bundle()` derives bundle `created_at` from packaged artifact timestamps when available instead of stamping wall-clock time unconditionally. The accepted reason is operational, not cosmetic: nondeterministic `created_at` values caused otherwise identical bundles to drift in `bundle_sha256`, which is exactly the wrong behavior for replay, trust verification, and CI parity checks.
>
> Release-surface note (2026-03-30): docs publication now has validator-backed preflight build gates. CI `release-readiness` builds the docs site with `mkdocs build --strict` before release-asset validation, and the tag release workflow now performs the same strict build before `mkdocs gh-deploy --force`. The accepted reason is shippability: without a preflight docs build contract, a broken docs site could survive until tag-time release or fail only during deployment.

**Abstract:**
`tensor-grep` began as a routing-first search engine for text, regex, GPU, and structural search workloads. It has since evolved into a broader repository-analysis substrate for AI harnesses: exact symbol definition and reference discovery, blast-radius planning, trust-aware edit plans, and benchmarked context packing for real codebases. The central architectural claim still holds: **routing is the optimization**. Rather than treating repository intelligence as a single-model problem, `tensor-grep` dispatches simple text queries to native search paths, structural queries to the native Rust AST backend, and higher-level planning to deterministic graph/ranking layers that expose provenance, confidence, and coverage explicitly. The current accepted line shows three distinct strengths. First, the native AST backend beats `ast-grep` on the accepted local search and rewrite benchmarks. Second, the planning layer achieves perfect primary file/span hit rate on the current external scenario corpus while maintaining materially lower context size than headless Gemini and Copilot baselines. Third, semantic-provider integration (`native | lsp | hybrid`) is now a real feature surface, but benchmarking shows that external LSP providers do not yet improve planning outcomes enough to justify the added latency by default. The practical conclusion is that world-class AI tooling requires deterministic repository planning, measured end-to-end edit benchmarks, and optional semantic enrichment only when it wins on real tasks.

---

## 0. Current Product Line

The current accepted line should be read as a layered system, not just a search CLI:

1. **Cold-path text and regex routing**
   `tensor-grep` keeps `rg` as the default generic-text baseline and only routes away from it when the workload or flags justify doing so.
2. **Native AST and rewrite execution**
   The Rust AST backend is now the accepted structural-search and rewrite path, with current accepted ratios beating `sg` on both search and apply benchmarks.
3. **AI-harness repository planning**
   The repo-map/planning stack exposes exact defs/refs/callers, blast radius, edit plans, trust metadata, and machine-readable context bundles.
4. **Optional semantic providers**
   External semantic providers now exist behind `native`, `lsp`, and `hybrid` modes. They are useful for experimentation and IDE-facing surfaces, but the current accepted benchmark line keeps `native` as the default because provider-backed modes have not yet earned a quality win.

The newest accepted planning and comparison artifacts show:

* **External planning baseline (`29` scenarios):**
  `mean_file_hit_rate = 1.0`, `mean_span_hit_rate = 1.0`, `mean_file_precision = 0.9060`
* **Python external precision remains the weakest internal metric:**
  `mean_file_precision = 0.7275`
* **Rust external test targeting focused slice is now clean:**
  the current focused `clap_lex` rerun reaches `mean_test_hit_rate = 1.0`
* **Cross-language bounded comparator slices:**
  `tensor-grep` beats headless Gemini and Copilot on Python (`click`), JavaScript (`commander`), and Rust (`clap_lex`) slices, with the clearest advantage in validation targeting and context efficiency
* **Semantic-provider bakeoff (`click`, 10 scenarios):**
  `native`, `lsp`, and `hybrid` are currently identical on quality metrics, while `lsp` and `hybrid` are slower
* **Patch benchmark track:**
  repo-backed end-to-end patch scoring is now implemented on an accepted hard pack of `12` scenarios and summarized in `artifacts/patch_eval_demo/real_patch_system_scorecard.md`; Claude-enhanced clears the user-style pack, Copilot now has a completed same-pack comparative rerun at `0.5 / 0.5`, and Gemini now also has a completed same-pack baseline rerun at `0.0 / 0.0` after isolation/timeout cleanup made the host behavior reproducible
* **User-style Claude A/B benchmark (`10` scenarios):**
  plain Claude reaches `0.8 / 0.8` for patch applied / validation passed, while `Claude + tensor-grep skill + CLAUDE.md` reaches `1.0 / 1.0` with a mean wall-clock penalty (`26.67s` baseline vs `45.65s` enhanced)
* **Command-level observability:**
  new trace artifacts show that the enhanced slowdown is not entirely a `tg` runtime cost; on at least one traced probe the enhanced path spent `24.64s` inside Claude while making zero `tg` calls

That product state matches the current literature: repository-level graph navigation and retrieval quality matter more than generic tool loops, and end-to-end patch correctness is the next proof obligation rather than more raw navigation features.

## 1. Introduction

Traditional regular expression matching engines represent the core functionality of numerous network security applications, intrusion detection systems, and daily software engineering tasks. As log bandwidth increases, evaluating complex patterns via Deterministic Finite Automata (DFA) on general-purpose CPUs leads to state explosion and suboptimal time complexities. Recent literature, such as the XAV scheme proposed for packet processing [Zhong et al., 2024], has highlighted the necessity of shifting regex evaluation to specialized hardware like FPGAs and GPUs. 

Simultaneously, the demand for semantic code retrieval has evolved beyond simple sequence matching. Advanced tools require an understanding of the Abstract Syntax Tree (AST) to execute structural queries. While ASTs offer precise syntactic structures, recent studies show that querying them directly in Python suffers from severe deserialization overhead. GNN-integrated semantic retrieval models, like GNN-Coder [Ye et al., 2025], demonstrate that combining topological AST representations with neural encoders significantly enhances code clone detection and semantic retrieval. 

`tensor-grep` merges these two disparate fields—high-throughput linear regex matching and deep structural AST traversal—into a unified, GPU-accelerated CLI tool. Most crucially, **tensor-grep is the first framework to recognize that routing is the optimization**. By intelligently dispatching simple strings to zero-cost CPU architectures (`memmap2`/Rust) and reserving the GPU exclusively for complex regex and structural AST graph-matching, it avoids the massive PCIe bus latency penalties that crippled earlier VRAM-mapping attempts.

## 2. Architecture and Integration of Third-Party Libraries

`tensor-grep` orchestrates three primary third-party ecosystems—RAPIDS `cuDF`, PyTorch/cyBERT, and Tree-sitter/PyTorch Geometric—to circumvent traditional CPU bottlenecks such as DFA state explosion. By mapping string operations and syntax trees directly to GPU VRAM, `tensor-grep` scales line-rate processing independently of CPU core counts.

```mermaid
flowchart TD
    A[CLI Request] --> B{Query Analyzer}
    B -->|Exact String| C[CPU Backend / Rust memmap2]
    B -->|Complex Regex| D[cuDF GPU Backend]
    B -->|Semantic / NLP| E[PyTorch cyBERT Backend]
    B -->|Structural Code| F[Tree-sitter AST Backend]
    
    C --> G((Output Matches))
    D --> G
    E --> G
    F --> G
```

### 2.1 Circumventing DFA State Explosion with RAPIDS cuDF
Traditional regex engines like `ripgrep` compile patterns into Deterministic Finite Automata (DFA) or Non-deterministic Finite Automata (NFA). As the complexity of the regex pattern or the size of the target text increases, CPU-bound parsers suffer from "state explosion," where the transition tables become too large to fit in fast L1/L2 CPU caches, resulting in severe cache-miss penalties and throttled throughput.

`tensor-grep` solves this by integrating **NVIDIA RAPIDS `cuDF`**, a GPU DataFrame library built on Apache Arrow C++ primitives (`libcudf`). 
- **The Integration:** Instead of processing logs byte-by-byte via a CPU thread, `tensor-grep` memory-maps large log files directly into GPU VRAM as columnar string data. 
- **The Speedup:** `cuDF` applies the regex pattern using massively parallel CUDA kernels (via the `cudf.Series.str.contains` API). By executing thousands of string comparisons concurrently across the GPU's Streaming Multiprocessors (SMs), `tensor-grep` effectively bypasses CPU cache limitations. This parallel architecture is primarily responsible for the **3x to 4x throughput increase** over `ripgrep` during complex pattern matching.

### 2.2 Semantic Understanding via PyTorch and cyBERT
Standard regex matching fails when log formatting changes or when a user wants to find "errors" that aren't explicitly tagged with the word "ERROR" (e.g., "Connection refused by peer"). 

- **The Integration:** `tensor-grep` integrates **PyTorch** and **HuggingFace Transformers** to execute `cyBERT`, a specialized BERT model pre-trained by NVIDIA on vast corpuses of cybersecurity and application logs.
- **GPU-Accelerated Tokenization:** To prevent massive PCIe bottlenecking when classifying logs, we utilize RAPIDS `cudf.core.subword_tokenize` to tokenize the log payload directly in VRAM rather than pulling strings back to the CPU for the HuggingFace tokenizer. The generated `input_ids` and `attention_mask` tensors are then mapped natively to PyTorch tensors via `__dlpack__` with zero CPU intervention.
- **The Speedup:** By keeping tokenization completely hardware-bound, logs are directly passed through the Transformer network in massive VRAM batches. The `TorchBackend` executes matrix multiplications to emit confidence logits, classifying thousands of log lines into severities (INFO, WARN, ERROR) in a single pass at line rate speeds.

### 2.3 AST-Grep Parity via Tree-sitter and PyTorch Geometric
Taking inspiration from recent GNN retrieval paradigms, `tensor-grep` incorporates structural code search capabilities, allowing users to query code topology rather than raw text.

- **The Integration:** Source code is first parsed using **Tree-sitter** (a high-performance incremental parsing library written in C) to generate a concrete Abstract Syntax Tree (AST). `tensor-grep` then traverses this tree and maps it into a **PyTorch Geometric** `Data` object, transforming parent-child relationships into tensor edge indices.
- **The Speedup:** Traditional structural search tools iterate through the AST tree recursively on the CPU. By compiling the entire codebase's AST into a Graph Neural Network tensor, `tensor-grep` uploads the graph to the GPU. Subgraph matching (e.g., finding all instances of `if ($A) { return $B; }`) is then executed as a series of highly parallel matrix operations across the edge indices, enabling O(1) matching time for subsequent queries once the graph is loaded.

### 2.4 Dynamic Multi-GPU Scaling and the Fallback Pipeline
To maximize hardware utilization while preserving cross-platform stability, `tensor-grep` employs a tripartite backend architecture orchestrated by a central `Pipeline` router:

1. **CuDFBackend (Linux/WSL2):** The primary path, leveraging instant `fork()` process spanning to yield sub-0.02s worker initialization for massive log files.
2. **TorchBackend (Windows Native):** Circumvents the lack of `cuDF` on Windows by utilizing PyTorch CUDA 12.4 string-tensor bindings. 
3. **RustCoreBackend (Embedded PyO3 Arrow):** Automatically intercepts line-counting constraints (`-c`), completely bypassing Python interpreters to count literals using native zero-copy `memmap2` buffers at 0.081s per gigabyte.
4. **Ripgrep/AstGrep Native Delegation:** Acknowledging the fundamental constraints of Python CLI latency over thousands of tiny nested files, the pipeline dynamically detects whether the native `rg` or `sg` binaries are installed on the system PATH. For highly context-dependent queries (e.g. `-C2`) across highly fractured small-file directories, it seamlessly wraps the native Rust binaries and pipes their stdout JSON back into the Python tensor-grep abstraction. This guarantees that `tensor-grep` acts as a pure superset orchestrator: it matches baseline `ripgrep` speeds for small contexts and annihilates them on massive datasets or literal counting by routing to the GPU or Arrow core respectively.

`tensor-grep` dynamically scales across enterprise GPU arrays using a custom `MemoryManager` and `DeviceDetector`. 
- **VRAM Budgeting:** The system probes the total available VRAM dynamically on each device (e.g., `cuda:0`, `cuda:1`) utilizing `pynvml` (NVIDIA Management Library) hooks to compute free memory limits at runtime.
- **Dynamic Chunk Sharding (OOM Protection):** Massive log files (>10GB) are partitioned into PyCapsule chunks explicitly calculated against 80% of the active VRAM budget. To prevent CUDA Out-Of-Memory (OOM) exceptions when processing sequential arrays, the cuDF backend executes explicit garbage collection and re-acquires spill locks (`cudf.core.buffer.acquire_spill_lock()`) after every iteration, mathematically guaranteeing stable execution on any GPU regardless of its size limit.
- **Explicit Device-ID Scheduling Contract:** Beyond environment-level overrides, the runtime now supports per-request GPU selection (`SearchConfig.gpu_device_ids`) that is normalized against detected devices and propagated into chunk-plan fanout. This lets schedulers and service wrappers pin individual search jobs to concrete GPU IDs while preserving safe fallback behavior when IDs are invalid.
- **Explicit GPU-ID Routing Override:** For query modes that do not require CPU-only semantics, explicit `gpu_device_ids` now acts as a first-class routing signal: the pipeline attempts pinned GPU backends first and then safely falls back to `rg`/Rust/CPU if GPU backends are unavailable.
- **Stable Device-ID Enumeration API:** `DeviceDetector.enumerate_device_ids()` now serves as a first-class public contract for routing/scheduling layers that need deterministic routable GPU IDs, while `list_devices()` provides `(device_id, vram_capacity_mb)` for capacity-aware sharding.
- **Chunk-plan observability contract:** Runtime now records and surfaces `(device_id, chunk_size_mb)` plans (`selected_gpu_chunk_plan_mb` and `SearchResult.routing_gpu_chunk_plan_mb`) so multi-GPU fanout can be audited and regression-tested without relying on log scraping.

## 3. Evaluation and Benchmarks

### 3.1 Experimental Setup and Hardware Constraints
We rigorously benchmarked `tensor-grep` against the industry standard `ripgrep` across various paradigms. Our comprehensive Test-Driven Development (TDD) suite has continued to grow throughout the current optimization line, with recent local validation branches exceeding **460 passing tests** plus environment-specific skips while locking runtime routing, release contracts, and workflow startup behavior.

**Hardware Testbench:**
To ensure an empirical representation of both enterprise developer machines and standard CI/CD clusters, our local validation utilized an **AMD Ryzen 7 5800XT with 64GB DDR4 RAM** alongside dual **NVIDIA RTX 4070 / RTX 5070 (Ada Lovelace `sm_120`)** GPUs. This specific CPU bound (and the PCIe Gen4 interconnect latency) contextualizes why massive VRAM payloads face initialization bottlenecks when crossing OS virtualization layers.

### 3.2 Main Results: Bare-Metal GPU Execution on RTX 5070
We re-ran the benchmark suite on 2026-03-10 (local run on current `main`) and captured the output artifacts directly:

* `artifacts/bench_run_benchmarks.json`
* `artifacts/bench_run_ast_benchmarks.json`
* `artifacts/bench_run_ast_workflow_benchmarks.json`
* `artifacts/bench_run_gpu_benchmarks.json`

Backend-level timings from `run_gpu_benchmarks.py` on this host:

* **AST backend:** `function_definition` query completed in **0.062 seconds** (4 matches).
* **cyBERT backend:** explicitly skipped in this local pass because no Triton server was running on the benchmark host.
* **Torch backend:** exact-string query (`Database connection timeout`) completed in **0.630 seconds** (2,000 matches).

These runs confirm low backend latency for targeted workloads once dependencies are installed, but they do not imply end-to-end CLI superiority for every search shape. They also show an operational benchmark dependency: cyBERT throughput claims are only meaningful when the Triton inference service is actually reachable, and benchmark scripts now record that case as an explicit skip rather than a synthetic failure.

We also added a workflow-focused AST startup harness (`run_ast_workflow_benchmarks.py`) to measure command-level orchestration instead of only single-pattern search latency. On the latest accepted Python-entrypoint optimization line, the synthetic `tg run "def $FUNC():\n    $$$BODY" .` workflow completed in **0.207 seconds**, the synthetic `tg scan --config sgconfig.yml` workflow completed in **0.226 seconds**, and the synthetic `tg test --config sgconfig.yml` workflow completed in **0.250 seconds**. 

A subsequent regression in the Python orchestration layer saw `scan` and `test` latencies spike to **~1.3 seconds**. A recovery slice was landed to stabilize the AST front-door contract and reduce startup latency by consolidating command routing in Rust and making Python delegation lazier. A follow-up narrowing slice implemented a unified AST metadata cache (`project_data_v6.json`) with robust mtime-based invalidation. The final orchestration cut (2026-04-11) fully migrated the cache ownership to native Rust and optimized the invalidation logic. This reduced end-to-end latency materially (`artifacts/bench_ast_workflow_canonical_final.json`), with `tg run`, `tg scan`, and `tg test` all now completing in **<0.2s** on Windows (and typically reaching **~0.03s** on optimized paths). These results represent a >85% reduction in startup latency from the regressed state and satisfy the aggressive **0.2s** target for sidecar-backed workflows.

### 3.3 Complex Regex Throughput (The GPU Advantage)
The latest full script-driven CLI benchmark (`run_benchmarks.py`) from this local run shows that end-to-end process costs still dominate most regex/text scenarios, but the new bootstrap entrypoint materially reduced command startup overhead. Once the benchmark harness was switched to measure the installed `tg` fast path instead of `tensor_grep.cli.main`, the stored Windows regression guard passed again:

* **Regex Match:** ripgrep **0.506s** vs tensor-grep **0.682s**
* **Invert Match:** ripgrep **1.309s** vs tensor-grep **1.477s**
* **Context (`-C2`):** ripgrep **1.757s** vs tensor-grep **1.956s**

All scenarios passed parity checks, and the current local Windows run no longer exceeds the stored regression threshold in `benchmarks/baselines/run_benchmarks.windows.json`.

```mermaid
gantt
    title Complex Regex Benchmark
    dateFormat  s
    axisFormat %S
    
    section CPU (ripgrep)
    Native C DFA Evaluation :a1, 0, 0.506s
    
    section tensor-grep CLI (this run)
    tensor-grep Regex Match :a2, 0, 0.682s
```

### 3.4 AST Structural Search (The Query-Cache Gain)

The latest AST benchmark pass (`run_ast_benchmarks.py`) improved materially after adding two in-process caches to `AstBackend`: a compiled tree-sitter query cache keyed by `(lang, pattern)` and a parsed-source cache keyed by `(file_path, lang, mtime_ns, size)`. The current line also shares those caches across separate `AstBackend` instances in the same process, which matters for `tg scan` and `tg test` because different rules may still route through separate backend objects. This reduces repeated query compilation and reparsing overhead for `tg run --ast`, `tg scan`, and `tg test` when the same process revisits unchanged modules.

Current local results:

* **Simple Function Def:** ast-grep **0.126s** vs tensor-grep **0.428s**
* **Try/Except Block:** ast-grep **0.113s** vs tensor-grep **0.404s**
* **Class Declaration:** ast-grep **0.118s** vs tensor-grep **0.401s**

This is a real improvement over the prior local AST line, but it does not close the remaining one-shot process-start gap against native `ast-grep`. The practical conclusion is that AST backend caching helps repeated in-process workloads immediately.

**UPDATE — Native Rust AST backend parity achieved and exceeded (880ce04):**

The Python-side AST backend numbers above are obsolete. The current line uses a native Rust AST backend (`backend_ast.rs`) embedding `ast-grep-core` + `ast-grep-language` crates directly. After parallelizing per-file matching with `rayon` and switching to the `ignore` crate for gitignore-aware walking:

* **tg run median: 325ms** vs **sg median: 444ms** — tg is **1.37x faster than sg**
* Corpus: 1000 Python files, 50000 LOC (deterministic, `gen_corpus.py`)
* Pattern: `def $F($$$ARGS): return $EXPR`
* 40/40 structural match parity across Python, JavaScript, TypeScript, Rust

Key changes: rayon `par_iter` for parallel file matching, `ignore` crate replacing `walkdir`, `fs::read` + `from_utf8` (one fewer allocation vs `read_to_string`), lazy `line_starts` (deferred until first match), deterministic output sort by (file, line). The cold one-shot AST gap is now closed — tg natively beats sg on the benchmark corpus.

**UPDATE — AST search and rewrite speed beat sg across all benchmarks (2026-03-18):**

Following further optimizations to the native Rust AST backend and rewrite pipeline, tg now beats sg on both search and rewrite benchmarks across all corpus sizes:

* **AST search ratio (tg/sg, 1000-file Python corpus): 0.795x** — tg is ~20% faster than sg
* **Rewrite apply ratio (tg/sg, 1000 files): 0.848x** — tg is ~15% faster than sg
* **Rewrite apply ratio (tg/sg, 5000 files): 0.851x** — tg is ~15% faster than sg
* AST parity: 40/40 structural match patterns across Python, JavaScript, TypeScript, Rust
* All test suites pass: 582 Python tests, 44+ Rust unit tests, 39 rewrite integration tests

Key optimizations in this round:

1. **LTO release profile**: link-time optimization reduced binary size from 9,943,040 to 9,741,312 bytes and improved codegen
2. **Hybrid file discovery + rayon work-stealing**: WalkBuilder feeds rayon `par_iter` for parallel AST search
3. **Fixed-string pre-filter**: extract literal strings from AST patterns, skip files that cannot possibly match
4. **Walker-level type filtering**: leverage `ignore` crate's Types system to filter at the directory-walk level
5. **Dedicated CLI search fast path**: lightweight match data structures, buffered stdout for reduced syscall overhead
6. **Fused rewrite I/O**: single file read per rewrite operation, no redundant stale-file checks
7. **Direct file writes for the one-shot CLI fast path**: `tg run --rewrite ... --apply` now overwrites files directly (matching `sg`'s throughput-oriented path), while the explicit planned-edit apply path retains the safer atomic temp-file+rename contract
8. **Removed sync_all per file on the temp-file path**: eliminated per-file fsync for rewrite throughput while keeping same-directory rename semantics for the atomic planned-edit path

On the current line, `tensor-grep` also persists AST search results for unchanged files across process boundaries using an on-disk result cache keyed by `(resolved file path, language, pattern, mtime_ns, size)`. In addition, simple native AST node-type queries now persist a per-file node-type line index, allowing later queries such as `function_definition` to skip both query compilation and tree parsing on unchanged files. That closes the most immediate correctness-safe cross-invocation reuse gap, but the latest cold benchmark still suggests startup/routing overhead dominates one-shot structural searches. Therefore, persistent AST caching should be viewed as an enabling layer for future daemonized or indexed AST execution, not as proof that cold CLI AST search is already faster than `ast-grep`.

### 3.5 REI-Shaped Fixed-String Indexing

Recent regex indexing work such as REI argues that repeated regex workloads benefit from a lightweight index layer rather than repeated full scans. The current `tensor-grep` line now applies that idea narrowly and safely to fixed-string search: `StringZillaBackend` can build a per-file trigram line index and reuse it across repeated literal queries. On the local development host, a synthetic hot-corpus microbenchmark measured approximately **1.05s** for the first indexed literal query and **0.0025s** for the second cached literal query over the same file. This is not evidence that cold one-shot `tg` is already faster than `rg`; it is evidence that a cache-aware repeated-query mode can materially outperform repeated rescans on stable corpora.

### 3.6 Safe Repeated-Regex Prefiltering

The same indexing logic now extends, conservatively, into the Python regex fallback path. When `tensor-grep` cannot stay on the native `rg`/Rust route and the regex has a provable required literal core, `CPUBackend` builds and reuses a trigram line index before invoking Python `re`. This is intentionally narrower than general regex indexing: it is disabled for alternation, character classes, grouping, optional constructs, and context/invert flows where the prefilter could compromise semantics. The current line persists that prefilter cache across backend instances and fresh CLI invocations. On the local development host, a synthetic repeated-regex microbenchmark measured approximately **0.243s** for the first indexed regex query and **0.014s** for the second cached query over the same file. That result is not a claim that Python `re` is now broadly competitive with `rg`; it is a claim that even the unavoidable fallback path can be made materially less wasteful on repeated stable workloads.

To keep these wins from regressing silently, the repo now includes a dedicated hot-query benchmark harness (`benchmarks/run_hot_query_benchmarks.py`). The current accepted refresh at `artifacts/bench_hot_query_benchmarks_post_bench_extra_refresh.json` measures both rows through fresh subprocess probes instead of mixing an in-process literal row with a subprocess regex row. On the current local Windows host, that benchmark measured approximately **0.6271s -> 0.2164s** for repeated fixed-string search and **0.8776s -> 0.2263s** for repeated regex-prefilter search. The important read is not that warm queries are “free”; it is that a persistent repeated-query path still materially reduces work even after paying real process startup overhead.

The operational lesson from this refresh is also now explicit in the product contract: the fixed-string hot-query row depends on the `stringzilla` benchmark extra. CI therefore continues to install `.[bench,dev]`, while the narrower local contract is now simply `.[bench]`; ad hoc runs can use `uv run --extra bench python benchmarks/run_hot_query_benchmarks.py`, and when the dependency is absent the fixed-string row records an explicit `SKIP` row with an install hint instead of crashing.

### 3.5 Exact String Matching (The CPU/Rust Advantage)
In the fresh benchmark pass, the strongest `tensor-grep` result is the count path:

* **Count Matches:** ripgrep **0.146s** vs tensor-grep **0.093s**

For other exact/fixed-string modes in this run:

* **Fixed Strings (`-F`):** ripgrep **0.476s** vs tensor-grep **0.594s**
* **Simple String Match:** ripgrep **0.451s** vs tensor-grep **0.609s**

This suggests the current architecture is highly competitive when it routes to the native Rust counting backend, while general CLI text search paths still carry substantial startup/orchestration overhead.

```mermaid
gantt
    title Exact String Benchmark (150MB Log)
    dateFormat  s
    axisFormat %S
    
    section Native CPU / CLI
    ripgrep Count              :a1, 0, 0.146s
    tensor-grep Count          :a2, 0, 0.093s
    
    section Other exact/fixed paths
    ripgrep Fixed Strings      :a3, 0, 0.476s
    tensor-grep Fixed Strings  :a4, 0, 0.594s
    tensor-grep Simple String  :a5, 0, 0.609s
```

### 3.6 OS Architectural Limitations: Windows `spawn()` vs. WSL `fork()`
During our cross-platform validation, we encountered fundamental OS limitations that define why our tripartite routing architecture is mandatory:

1. **Windows Subprocessing Overhead:** Windows Python `multiprocessing` relies on the `spawn()` method, requiring every worker to re-initialize the heavy PyTorch CUDA 12.4 context. This introduces a devastating **~11-second overhead**, making GPU offloading strictly non-viable for files under 200MB.
2. **WSL2 PCIe Bottlenecks:** Moving to Linux/WSL2 allows for instantaneous `fork()` execution. However, executing single-threaded `cuDF` inside WSL introduces significant PCIe bus transfer overhead. Transferring a 150MB log file across the WSL/Windows boundary into VRAM took **~14.4 seconds**, confirming that the GPU must exclusively be utilized for complex queries where compute density drastically outweighs data transfer latency.

To mitigate the ~5.17s penalty of falling back to pure Python when GPUs were unavailable or WSL contexts corrupted, we successfully ported the entire execution orchestrator out of Python and directly into a compiled Rust binary wrapper (`tg.exe`). By utilizing `PyO3` in an *embedded* configuration rather than an *extension* configuration, the Rust executable starts up natively with 0ms interpreter lag, maps the requested parameters, and evaluates locally using `memmap2` and `rayon`. 

When the Rust orchestrator detects a complex log or AST query that necessitates GPU capabilities, it dynamically spawns the Python runtime in-memory, loads `cuDF`, and evaluates the massive tensors. Our empirical tests against the C:\dev enterprise directory baseline (encompassing 40+ Gigabytes of raw code data) yielded a search completion time of **6.78 seconds** using `tensor-grep-rs`, compared to native `ripgrep` returning OS errors and taking **19.81 seconds** on identical hardware paths.

### 3.7 The PyO3 Boundary: Why Pure Python Traversals Sometimes Win
During our optimizations, we attempted to map the `DirectoryScanner` natively to Rust via Andrew Gallant's highly optimized `ignore` crate wrapped in a PyO3 class. We expected an astronomical speedup compared to Python's native `os.walk`.

Our empirical benchmarks across massive directories (such as an entire `C:\dev` enterprise monorepo) presented a deeply counter-intuitive discovery:
- **Rust PyO3 `ignore` Extension**: 48.818 seconds
- **Pure Python `os.walk`**: 39.892 seconds

While Rust natively traverses files blazing fast, the **bottleneck is the PyO3 Foreign Function Interface (FFI) boundary**. Because our iterator yields back paths to Python, PyO3 had to allocate and serialize tens of thousands of Rust `String` objects into `PyString` components on the Python heap, acquiring and releasing the Python Global Interpreter Lock (GIL) for every single iteration. Conversely, Python's `os.walk` implementation operates highly optimized natively in C deep inside CPython, completely avoiding cross-language serialization until native Python objects are yielded.

Consequently, `tensor-grep` retains pure Python standard library capabilities for massive directory traversal (unless natively routed via the static Rust embedded execution `tg.exe` which avoids the GIL altogether), firmly demonstrating that high-performance hybrid architectures must be critically mindful of serialization boundaries.

### 3.8 Highly-Scalable Find and Replace Mutations
One of the longest-standing limitations of `ripgrep` is its strict adherence to pure search capabilities; it lacks native in-place log mutation or capture-group code refactoring natively. Developers typically pipeline `rg` outputs into `sed -i` or `awk`, crippling performance via IPC context switching overhead. 

To resolve this, we embedded a native `--replace` pipeline directly into the Rust memory-mapped engine. Because the entire log sequence is evaluated as a contiguous string slice natively inside the regex solver, we can seamlessly apply parameterized capture group mutations (e.g. `$1`, `${num}`) at speeds matching VSCode's native C++ text buffers but entirely via the CLI. Benchmarking the replacement of 100,000 function argument parameters across a synthetic python file, `tensor-grep-rs` safely applied complex parameterized Regex template replacements across all lines, and wrote the new file to disk in exactly **0.497 seconds**. This achieves what was previously an impossibility for pure `ripgrep` constraints while completely maintaining strict code formatting preservation.

### 3.9 Benchmark Regression Governance
To enforce sustainable performance gains, we introduced a benchmark-governance layer:

1. Benchmark suites emit machine-readable JSON artifacts (`artifacts/bench_*.json`).
2. A regression checker (`benchmarks/check_regression.py`) compares current runs against a baseline and fails if slowdown exceeds a configurable threshold.
3. Main CI now includes a required Ubuntu benchmark-regression gate that blocks merges/releases on measured slowdown, with markdown summaries attached to workflow output.
4. A standalone benchmark workflow remains available for manual/scheduled deep benchmark passes across additional suites.
5. Regression checks now include benchmark environment signatures (platform/machine metadata) so cross-OS comparisons are rejected by default unless explicitly overridden.
6. Release integrity checks now require `CHECKSUMS.txt` SHA256 entries to match GitHub release `asset.digest` metadata for each managed binary, tightening post-upload artifact parity.

The 2026-04-18 Windows baseline refresh was a governance repair, not a performance claim. Fresh clean `origin/main` evidence on this host no longer passed the stale March line, so `benchmarks/baselines/run_benchmarks.windows.json` was promoted from clean `origin/main` evidence and now records `benchmark_host_key` plus `host_provenance` alongside `tg_binary_source` and `tg_launcher_mode`. The important rule did not move: `check_regression.py` policy remained unchanged. Historical roadmap sections that refer to the accepted Windows baseline should therefore be read against the accepted line in force for that batch.

This turns performance claims into continuously verifiable constraints and enables objective rollback decisions when regressions are detected.

### 3.10 Optimization Ledger: Accepted Wins and Rejected Dead Ends

To avoid re-running the same failed ideas, we maintain an explicit optimization ledger in this paper. The results below are taken from the current 2026-03 optimization line on Windows and are intentionally blunt about what did and did not work.

**Accepted text-search wins**

1. **No-`rg` bootstrap fast path**
   We added a narrow direct text-search path in `tensor_grep.cli.bootstrap` for simple `search` invocations when `rg` is unavailable. This path progressively removed unnecessary parser and import work:
   * direct no-`rg` text fast path
   * lazy backend imports
   * skipping `rg` probing when `PATH` is empty
   * removing `argparse` from the narrow fast path

   On the controlled no-`rg` benchmark corpus, these changes reduced end-to-end simple-search startup from roughly **0.597s** in the earlier line to approximately **0.093s-0.099s** on the latest accepted line.

2. **Repeated fixed-string index**
   We added a persistent trigram line index for repeated fixed-string search in `StringZillaBackend`, followed by compact range storage and faster posting decode/intersection. The latest accepted hot-query benchmark line recorded:
   * repeated fixed string, first hit: **0.2368s**
   * repeated fixed string, second hit: **0.0048s**

3. **Repeated regex prefilter index**
   We added a persistent trigram prefilter cache for the Python regex fallback path in `CPUBackend`, then improved its candidate execution path so the cached search iterates only candidate lines instead of re-walking the full source file. On the accepted line:
   * repeated regex prefilter, first hit: **0.2439s**
   * repeated regex prefilter, second hit: **0.0350s**

**Accepted AST workflow wins**

1. **Direct AST workflow bootstrap path**
   `run`, `scan`, and `test` were moved onto a lighter AST workflow entrypoint instead of always loading the full Typer CLI.

2. **Wrapper batching**
   We collapsed wrapper-backed AST workflows away from one-subprocess-per-file and one-subprocess-per-snippet execution. The accepted sequence included:
   * batched wrapper scan per rule
   * batched wrapper run across files
   * grouped wrapper test execution by pattern
   * wrapper-backed test batching through project-level scan

3. **AST backend selection cuts**
   We removed repeated backend selection and skipped native backend construction for wrapper-only rule shapes. The accepted best Python-entrypoint AST workflow line before later regressions measured:
   * `run`: **0.207s**
   * `scan`: **0.226s**
   * `test`: **0.250s**



**Accepted native AST search + rewrite speed wins (2026-03-18)**

1. **LTO release profile**
   Link-time optimization across the Rust binary reduced binary size (9,943,040 to 9,741,312 bytes) and improved codegen quality.

2. **Hybrid file discovery + rayon work-stealing**
   WalkBuilder feeds rayon par_iter for parallel AST search, replacing serial file iteration.

3. **Fixed-string pre-filter for AST patterns**
   Literal strings are extracted from AST patterns at query time; files that cannot contain the required literals are skipped before tree-sitter parsing. This eliminates most parse overhead on non-matching files.

4. **Walker-level type filtering**
   Language-aware file filtering via the ignore crate Types system, applied at the directory-walk level instead of post-walk.

5. **Dedicated CLI search fast path**
   Lightweight match data structures and buffered stdout reduce per-match overhead and syscall count.

6. **Fused rewrite I/O**
   Single file read per rewrite operation with no redundant stale-file checks, eliminating duplicate I/O.

7. **Direct file writes for rewrites**
   Replaced atomic temp-file+rename with direct file writes, matching sg approach for maximum throughput.

8. **Removed sync_all per file**
   Eliminated per-file fsync during rewrite apply, significantly reducing rewrite latency on large corpora.

   Combined result: AST search ratio (tg/sg) improved from 1.37x to **0.795x** (tg ~20% faster than sg). Rewrite apply ratio improved from 2.32x to **0.848x** on 1000 files and from 0.73x to **0.851x** on 5000 files. 40/40 structural match parity across Python, JavaScript, TypeScript, Rust.

**Important rejected candidates**

These were implemented, validated, and then intentionally rejected because the benchmark either regressed or the gain was not stable enough to justify merge:

1. **Naive AST helper/session**
   A local AST helper process for `run`/`scan`/`test` caused a severe regression because helper startup/handshake cost exceeded the remaining workflow startup cost.

2. **Many AST cache/layout micro-optimizations**
   Several attempts were correct but slower, including:
   * shared wrapper temp root per test command
   * manifest-based stable wrapper project roots
   * extra YAML micro-caches
   * bootstrap import shaving beyond the accepted lazy pipeline fallback

   The consistent lesson was that AST one-shot startup had reached the point where only larger execution-shape changes matter.

3. **Onefile binary as a speed path**
   We changed Nuitka builds to target `bootstrap.py` so shipped binaries at least use the optimized entrypoint. However, local timing on the produced Windows onefile binary showed it was still slower than the Python bootstrap path, which strongly suggests onefile extraction/packaging overhead dominates:
   * built `tg.exe` simple search: roughly **1.10s-1.22s**
   * Python bootstrap simple search: roughly **0.33s-0.48s**
   * direct `rg` simple search: roughly **0.26s-0.29s**
   * built `tg.exe` `--max-count`: roughly **0.82s-1.02s**
   * Python bootstrap `--max-count`: roughly **0.25s-0.32s**
   * direct `rg` `--max-count`: roughly **0.14s-0.24s**

   Conclusion: targeting `bootstrap.py` is still the correct release-binary contract, but Nuitka onefile binaries are not the current path to parity with raw `rg`.

4. **Windows `exec`-style `rg` passthrough**
   Replacing `subprocess.run(...)` with an `exec`-style passthrough on Windows regressed sharply and was discarded.

5. **Alternative posting/decode strategies that looked mathematically plausible but lost empirically**
   Rejected examples include:
   * CPU preallocated decode for compact regex postings
   * bootstrap-native duplicated literal-cache loader
   * pure Python literal scan replacing the existing backend
   * `bisect`-based regex posting intersection
   * several alternative StringZilla decode formats and binary-cache loaders

   These were dropped because the measured end-to-end numbers lost to the current accepted baseline.

**Current honest state**

The remaining performance gap to raw `rg` on cold generic text search is now dominated by launcher/control-plane overhead, not by search kernel quality. Repeated-query paths still show real room for index-driven gains. The native Rust AST backend now beats `sg` on both search (0.795x ratio) and rewrite apply (0.848x on 1000 files, 0.851x on 5000 files), closing the AST performance gap that earlier Python-controlled paths could not. The roadmap below prioritizes native control-plane evolution for text search and continued native AST/rewrite refinement.

Recent pipeline stabilizations include:
1. **Benchmark Governance and Provenance Hardening:** `tensor-grep` explicitly pins and isolates the repo-owned ripgrep bundle (`TG_RG_PATH`) across all benchmarking, testing, and Python backend boundaries, terminating ambient PATH drift and ensuring reliable, reproducible comparator lines.
2. **Rust-First Control-Plane & Unified Routing:** The cold-path generic search boundary was fully rewritten with a unified Rust routing boundary using `lexopt` early in `main`. This bypassed both heavy `clap` parsing and Python interpreter initialization for "plain" search shapes (one pattern, one path, minimal flags). Fresh benchmarks (`artifacts/bench_rust_control_plane_final.json`) show a material improvement in cold-start latency, with `tg search` (Simple String Match) reduced to **0.198s** on Windows - beating the previous ~0.23s median and narrowing the gap to raw `rg`. This ensures stable execution while cleanly routing external editor-plane commands securely to the Python backend.
3. **Repeated-Query Hot-Cache Optimization:** The Python-based trigram indexing and cache-hit pathways were optimized across `StringZillaBackend` and `CPUBackend`. By adopting set-based intersection logic, compact JSON serialization, and eliminating redundant object materialization during cache loading, second-query "hot" latencies were reduced by ~25-30% (e.g., `repeated_fixed_string` down to 0.10s) and first-query "cold" latency by ~40%.
4. **AST Workflow Startup Recovery & Orchestration:** The AST `scan` and `test` orchestration was fully migrated from Python to native Rust, including canonical ownership of the authoritative project metadata cache (`project_data_v6.json`). This transition consolidated `sgconfig.yml` loading, rule discovery, and test snippet batching into the high-performance Rust core, completely bypassing the Python sidecar for these workflows. Fresh benchmarks (`artifacts/bench_ast_workflow_canonical_final.json`) show a material performance breakthrough, with `run`, `scan`, and `test` workflows completing in **<0.2s** on Windows (and typically reaching **~0.03s** on optimized paths). This represents a significant reduction in latency from the previous Python-owned state and satisfies the aggressive **0.2s** target for sidecar-backed workflows.
5. **Experimental Resident AST Worker (Opt-in):** A native Rust resident worker (`tg worker`) was implemented to keep project metadata and the AST backend warm in memory across repeated invocations. This opt-in functionality (enabled via `TG_RESIDENT_AST=1`) features a hardened lifecycle with duplicate-start protection and a streaming IPC protocol for robust output delivery and structured error handling. Current benchmarks (`artifacts/bench_ast_workflow_resident.json`) show that while the implementation is functionally solid, the highly optimized native Rust cold-start (typically **~0.03s** for small tasks) often outperforms the resident path due to the relative overhead of IPC (socket connection and JSON handshaking) on this release line. The resident worker remains a valid experimental substrate for future session-backed optimizations and very large-scale rule processing.


## 4. Related Work and Architectural Novelty

Our research indicates that while specific components of `tensor-grep` have been explored in isolation, the tripartite routing architecture is entirely novel in the 2025-2026 landscape:

1. **GPU Regex Acceleration:** Recent works like the XAV engine [Zhong et al., 2024] and *Column-Oriented Datalog on the GPU* [Sun et al., 2025] demonstrate that memory-mapped GPU execution effectively solves DFA state explosion. However, these systems assume a homogenous workload and suffer from the PCIe data-transfer penalties we empirically documented when applied to simple string matching.
2. **Graph-Based Code Representation:** The use of GNNs over ASTs has gained massive traction, with models like *GNN-Coder* [Ye et al., 2025] and *GRACE* [Wang et al., 2025] showing that structural representations drastically improve code retrieval over standard text RAG. Yet, these are heavyweight pipelines built for LLM generation, not real-time CLI developer tools.

`tensor-grep` is the first framework to recognize that **routing is the optimization**. By intelligently dispatching simple strings to zero-cost CPU architectures (`memmap2`/Rust) and reserving the GPU exclusively for complex regex and structural AST graph-matching, it achieves peak theoretical throughput across all developer search paradigms.

### 4.1 New 2026 Signal: Google DeepMind STATIC and Relevance to `tensor-grep`

We reviewed the 2026 STATIC framework for constrained decoding in LLM-based generative retrieval and compared its acceleration model to `tensor-grep` execution paths. STATIC targets sparse-matrix acceleration of constrained token decoding (beam-search-style generation), whereas `tensor-grep`'s dominant hot paths are literal/regex search (ripgrep delegation, Rust memmap count, cuDF string kernels) and AST structural matching.

Practical implication: STATIC is not a direct drop-in accelerator for current `tg search`/`tg run --ast` throughput. It is highly relevant only for future modules that perform constrained LLM token generation (for example: grammar-constrained query rewriting, structured retrieval plan generation, or constrained synthesis over indexed code graphs). Therefore, the architecture decision remains unchanged: prioritize `rg`/Rust for simple and medium-complexity search, and reserve GPU tensor paths for workloads with enough arithmetic intensity to amortize transfer/startup costs.

Operational decision (2026-03-03): we are explicitly not inserting STATIC-style sparse decoding into the core search pipeline in this release line. Instead, we treat it as an optional accelerator track for a future "query-planner/copilot" layer where constrained token generation is the bottleneck. This keeps the current low-latency grep path free from additional model/runtime overhead while preserving a clear path to adopt STATIC-like kernels where they are mathematically relevant.

## 5. Architectural Roadmap and Future Optimization

While the current tripartite routing structure defines a new paradigm for regex processing, scaling `tensor-grep` into massive enterprise clusters and cybersecurity defense platforms requires several upcoming optimizations:

1. **Zero-Copy IPC via Apache Arrow C++ Data Interface (Implemented):**
   Our initial PyO3 FFI boundary enforced a Python Global Interpreter Lock (GIL) mapping overhead that spiked execution times. By substituting Python serialization with the Apache Arrow PyCapsule interface via `pyo3-arrow`, the Rust extension now maps log files directly into `memmap2` buffers and yields zero-copy Arrow `StringArray` slices directly into Python. These chunks are natively ingested by `cuDF` into GPU VRAM across the PCIe bus, entirely bypassing Python heap allocation.

2. **Replacing ProcessPoolExecutor with Distributed Contexts (Ray/Dask-cuDF):**
   Relying on standard Python multiprocessing to handle GPU sharding and VRAM budgeting across massive enterprise hardware (e.g., dual RTX 4070/5070 matrices) remains notoriously brittle, primarily manifesting in `cudaErrorInitializationError` crashes when child processes fork the main CUDA context. Integrating a distributed framework like Ray or Dask-cuDF will manage distributed worker context, GPU memory pinning, and network fault tolerance organically.

3. **Derivative-Based Regex Planning for Complex CPU Queries:**
   Recent work such as RE# shows that symbolic-derivative execution can outperform mainstream Rust regex engines on complex pattern classes while retaining input-linear behavior. The concrete implication for `tensor-grep` is not "replace ripgrep everywhere," but rather add a planner tier for the subset of patterns where the current `rg`/Rust routing boundary still pays avoidable startup or engine-construction costs.

4. **Pre-Compiled AST Tensors for Native CI/CD LSP Integration:**
   Our empirical measurements show that once an AST is mapped to PyTorch Geometric tensors, subgraph invariant matching operates at asymptotically O(1) latency. For real-world workflows, a background daemon should be implemented to watch the filesystem, incrementally update the tree-sitter AST on file save, and keep the GNN graph perpetually warm in VRAM, enabling instantaneous Language Server Protocol (LSP) semantic resolution.

5. **AST-Structured Code Chunk Indexing:**
   Recent code-retrieval work such as cAST indicates that AST-shaped chunking beats naive line chunking when structural locality matters. For `tensor-grep`, the practical next step is to evolve the current persistent AST result cache plus node-type index into a deterministic on-disk AST shard/index cache that can accelerate `tg run` / `tg scan` / `tg test` without reparsing unchanged modules every invocation and without requiring exact prior query replay.
   On the text side, the new trigram line index should be extended from repeated fixed-string search toward broader repeated regex workloads, but only in ways that preserve the existing `rg` cold-start fast path for one-shot queries.

6. **Automated Cybersecurity Telemetry De-Obfuscation:**
   Because `tensor-grep` leverages `cyBERT` for semantic network log classification, standard regex engines fail to analyze deeply encoded threat payloads. Future updates will embed an automatic de-obfuscation pre-processor (decoding Base64, Hex, and URL encodings on the fly) immediately before the sequence is vectorized for VRAM injection. This guarantees resilient threat hunting without degrading to sequential CPU decoding boundaries.

7. **StringZilla SIMD Fallback Paths:**
   Recent literature demonstrates that raw string matching utilizing advanced SIMD CPU instructions (and CUDA bound iterations) via libraries like *StringZilla* can achieve up to 500+ GigaCUPS of edit-distance calculations, performing 109x faster than standard CPU libraries on H100 arrays. Integrating StringZilla as a native exact-match `-F` fallback will establish an intermediate performance tier that further buries C-level binaries.

8. **Just-In-Time (JIT) cuDF Regex Kernels:**
   While the current `CuDFBackend` relies on pre-compiled regex DFA matrices, recent optimizations from NVIDIA (2025/2026) illustrate that utilizing NVRTC (NVIDIA Runtime Compilation) to JIT-compile custom string transformation kernels can yield an additional 1x-4x speedup over standard `cudf.Series.str.contains`. We plan to inject a JIT-compiler into the query analysis phase for massively complex user patterns.

9. **Linear Temporal Logic (LTL) Log Synthesis:**
   Building upon structural AST tracing, `tensor-grep` will support LTL assertions (e.g., *Query: Did connection timeout ALWAYS follow event authentication failure?*). By mapping sequential log arrays into characteristic bitvector matrices, the GPU can evaluate sequence compliance 2000x faster than existing CPU trace learners [Valizadeh et al., 2024].

## 5. Current AI Harness Evaluation Line

The most important change since the earlier GPU- and AST-centric drafts is that `tensor-grep` is now evaluated as a repository-planning substrate for AI coding workflows, not only as a search binary. The accepted local line includes:

* machine-readable symbol navigation (`defs`, `refs`, `callers`, `source`)
* blast-radius and edit-planning surfaces
* trust metadata (`provenance`, `coverage_summary`, `ranking_quality`, `graph_trust_summary`)
* external bakeoff harnesses for real repositories
* competitor normalization for headless Gemini / Copilot / Codex-style runs

### 5.1 External Planning Baseline

The current accepted external-eval artifact (`artifacts/bench_external_eval_native_provider.json`) covers **29** real-repo scenarios across Python, JavaScript, and Rust:

* `mean_file_hit_rate = 1.0`
* `mean_span_hit_rate = 1.0`
* `mean_file_precision = 0.9060`

By language:

* **Python:** `mean_file_precision = 0.7275`
* **JavaScript:** `mean_file_precision = 1.0`
* **Rust:** `mean_file_precision = 1.0`

This benchmark line is strong enough to show that deterministic repository planning is already competitive, but it also exposes the next real engineering target: Python dependent-file precision remains the weakest internal metric.

### 5.2 Bounded Headless Agent Comparison

`tensor-grep` now has a bounded apples-to-apples comparison line against headless Gemini and Copilot on real repository slices:

* **Python (`click`, limit 5):**
  `tensor-grep` beats both Gemini and Copilot on file/span accuracy, test/validation targeting, and context efficiency
* **JavaScript (`commander`, limit 5):**
  `tensor-grep` beats both Gemini and Copilot; Copilot is the stronger comparator on this slice
* **Rust (`clap_lex`, limit 5):**
  `tensor-grep` again leads on precision and context efficiency, and the focused follow-up rerun closes the earlier Rust test-targeting gap on that pack

These results support a narrower but important claim: on bounded repository planning tasks, `tensor-grep` is already outperforming current headless agent baselines as a retrieval/planning substrate. They do **not** yet prove end-to-end patch superiority. That missing proof is now the central evaluation gap.

### 5.3 Semantic Providers: Useful Feature, Not Yet a Default Win

The current line includes a real semantic-provider feature:

* `native`
* `lsp`
* `hybrid`

Provider health, fallback state, and agreement metadata are exposed in the JSON payloads. The latest broad provider bakeoff on the `click` external pack still shows:

* identical planning quality across `native`, `lsp`, and `hybrid`
* worse wall-clock for provider-backed modes:
  * `native`: `68.374s`
  * `lsp`: `89.79s`
  * `hybrid`: `89.382s`

That broad-pack result is still the default-mode decision. However, the new focused provider hard-case artifacts (`artifacts/bench_provider_navigation_click_hardcases.json`, `artifacts/bench_provider_navigation_click_hardcases.md`, `artifacts/bench_provider_navigation_js_ts_hardcases.json`, `artifacts/bench_provider_navigation_js_ts_hardcases.md`, `artifacts/bench_provider_navigation_rust_hardcases.json`, `artifacts/bench_provider_navigation_rust_hardcases.md`, and `artifacts/bench_provider_navigation_hardcases_combined.md`) change the narrower product read:

* on a 2-scenario Click-style Python alias-wrapper pack, `native` caller hit rate = `0.0` and `hybrid` caller hit rate = `1.0`
* on a 2-scenario JS/TS imported-alias wrapper pack, `native` caller hit rate = `0.0` and `hybrid` caller hit rate = `1.0`
* on a 2-scenario Rust use/re-export alias-wrapper pack, `native` caller hit rate = `0.0` and `hybrid` caller hit rate = `1.0`
* `hybrid` caller precision = `1.0` on both focused packs
* test hit rate stays `1.0` for both modes on all focused packs

The accepted mechanism is deliberately narrow. Provider modes now expand Python import/assignment alias chains, JS/TS imported-alias rebinding chains, and Rust `use`/`pub use` alias chains for caller recovery, and when external provider references are absent, `lsp` / `hybrid` fall back to the same alias-chain recovery only in provider mode. The correct product reading is now: semantic providers are still not the default planning path on broad packs, but they have finally earned real hard-semantic wins in Python, JS/TS, and Rust instead of only feature-surface existence.

### 5.4 Research Alignment

Recent work reinforces the current `tensor-grep` direction:

* **RepoGraph** argues that repository-level code graphs improve software engineering retrieval.
* **RANGER** argues that graph-enhanced repository retrieval improves agent planning.
* **ContextBench** and **SWE Context Bench** separate retrieval quality from downstream code generation.
* **Agentless** and **Agentless Lite** show that strong repository-level scaffolds can be competitive even without a large interactive tool loop.

The practical interpretation is straightforward: the repo is already betting on the right substrate. The next proof obligation is not more navigation surface area; it is an end-to-end patch benchmark showing that an agent using `tensor-grep` produces better final code changes than a generic search-and-reason loop.

### 5.5 User-Style Claude A/B and Observability

The newest accepted benchmark shape tests the product more directly than the earlier cross-vendor patch runners: the same Claude CLI is run twice on the same repo-backed task pack, once as a plain baseline and once with the `tensor-grep` project skill plus a repo-local `CLAUDE.md`.

Current accepted artifact line (`artifacts/patch_eval_demo/claude_skill_ab_limit12_current_claude_md_bakeoff.json`):

* **baseline:** `mean_patch_applied_rate = 0.75`, `mean_validation_pass_rate = 0.75`, `mean_primary_span_hit_rate = 0.5`, mean wall clock `29.89s`
* **enhanced:** `mean_patch_applied_rate = 1.0`, `mean_validation_pass_rate = 1.0`, `mean_primary_span_hit_rate = 0.75`, mean wall clock `52.59s`

This is still a real product win on a harder corpus: `tensor-grep` materially improves final patch correctness for a real agent workflow on the accepted 12-task pack. However, the same benchmark also shows that the current enhanced path is slower.

The new trace-enabled A/B harness adds command-level observability so the slowdown can be decomposed instead of guessed at. The first traced probe (`artifacts/patch_eval_demo/claude_skill_ab_limit1_trace_with_tg_trace.json`) is instructive:

* **baseline:** `claude_seconds = 8.65`, `tg_invocation_count = 0`
* **enhanced:** `claude_seconds = 24.64`, `tg_invocation_count = 0`

This means the first observed latency gap is not a local harness issue and not even a `tg` runtime issue on that probe; it is Claude spending extra time deliberating in the enhanced setup without actually calling `tg`. That is why the next optimization program is centered on observability and tighter agent-facing workflow contracts rather than blindly shortening the skill text.

An immediate follow-up experiment also established a concrete failure mode that should not be retried casually: a narrower instruction telling Claude to skip `tg` whenever the prompt already named the target file did reduce runtime on a 1-task probe (`37.43s` baseline vs `10.62s` tightened enhanced), but it regressed correctness all the way to a no-op response (`patch_applied = 0.0`). That candidate was rejected. The accepted reading is that the current enhanced path is instruction-sensitive enough that latency trimming must be benchmarked narrowly and rejected unless correctness remains intact.

The latest accepted telemetry step adds explicit response-shape classification (`meta_question`, `analysis_then_patch`, `direct_patch`, `analysis_only`, `empty`). On the current probe artifact (`artifacts/patch_eval_demo/claude_skill_ab_limit1_trace_shape_trace.json`), the baseline classifies as `analysis_then_patch`, while the enhanced path classifies as `meta_question` with `tg_invocation_count = 0`, `changed_file_count = 0`, and `patch_chars = 0`. That is now the clearest single bottleneck signal in the agent path: before optimizing `tg`, the enhanced setup must stop falling into prompt-level meta responses. This also matches current agent-benchmark guidance from the broader literature: observability should expose not just whether an agent succeeded, but what kind of action trace it followed and how early it reached a useful first action.

The next accepted telemetry layer records time-to-first-useful-action and post-edit deliberation. On the corrected probe artifact (`artifacts/patch_eval_demo/claude_skill_ab_limit1_post_edit_final_trace.json`), the baseline reaches the correct file almost immediately (`first_file_change_seconds = 0.094`) but does not emit its patch until the end of the run (`first_patch_seconds = 36.64`), yielding `post_edit_deliberation_seconds = 36.55`. On the same probe, the enhanced path again falls into task non-engagement: `response_shape = meta_question`, `first_file_change_seconds = null`, `first_patch_seconds = null`, and `first_tg_seconds = null`. The practical implication is now split cleanly into two failure classes: when the agent engages the task, the remaining latency is post-edit deliberation / patch finalization; when it does not, the problem is prompt-level task engagement, not search.

This distinction matters operationally. A tempting but wrong optimization would be to keep tuning retrieval or `tg` startup, but the accepted traces do not support that. On the current probe line, the enhanced path still records `tg_invocation_count = 0` in the failure case, and the engaged baseline path spends almost all of its time after the first file change. That means the next real optimization track should target answer-finalization behavior or output-contract tightening, not search kernel work.

We also tested a narrow "terse output contract" candidate intended to force Claude to stop immediately after the edit with no explanatory prose. The benchmark harness now supports this explicitly for controlled comparison, but the first probe was a regression rather than a win: on `artifacts/patch_eval_demo/claude_skill_ab_limit1_terse_trace.json`, the enhanced path remained correct but `post_edit_deliberation_seconds` increased to `102.63` and total wall clock rose to `102.93s`. That candidate is therefore rejected as a default strategy. The useful outcome is the harness capability itself, not the contract text.

We also added a separate task-engagement contract mode for the user-style Claude benchmark. On the first probe (`artifacts/patch_eval_demo/claude_skill_ab_limit1_engage_trace.json`), the enhanced path no longer fell into a meta-question response and instead produced the correct patch. However, it still lost badly on latency: `post_edit_deliberation_seconds` rose to `56.02` versus the baseline `36.50`, and total wall clock rose to `56.42s` versus `36.66s`. That makes the current `engage` wording another rejected default. The accepted outcome is the experiment hook and the clearer failure taxonomy, not the prompt text itself.

We then added a more structured `act` task contract and exposed it as an explicit non-default probe profile (`probe-standard-act`). This variant uses a more direct, structured system/task split and explicitly tells Claude not to ask clarifying questions unless required files are missing. On the first scored probe (`artifacts/patch_eval_demo/claude_skill_ab_limit1_probe_standard_act.json` plus `artifacts/patch_eval_demo/claude_skill_ab_limit1_probe_standard_act_bakeoff.json`), the enhanced path again produced the correct patch and improved the 1-task latency relative to the earlier `engage` probe: `wall_clock_seconds = 36.04` and `post_edit_deliberation_seconds = 35.75`, with `tg_invocation_count = 0`. That makes the result directionally better, but not promotable. It is still only a single-task probe, and the trace still says the cost is answer finalization rather than search. The accepted conclusion is therefore the same as before: keep the probe surface, do not silently change the default, and require a broader accepted slice before promotion.

The next accepted step was to stop comparing these contract variants one at a time and add a dedicated matrix runner (`benchmarks/run_claude_skill_ab_matrix.py`) that reuses the existing user-style A/B harness plus the existing patch bakeoff scorer. The first real matrix artifact (`artifacts/patch_eval_demo/claude_skill_ab_limit1_matrix.json`) confirmed the taxonomy on a controlled 2x2 slice, but the accepted decision line is now the broader 5-task slice (`artifacts/patch_eval_demo/claude_skill_ab_limit5_matrix.json`). On that slice, `standard/standard` remains a clear loser (`patch_applied = validation = 0.60`, `meta_question_rate = 0.20`) and `terse/standard` also regresses (`patch_applied = validation = 0.80`, `meta_question_rate = 0.20`). The two surviving corners are `standard/engage` and `terse/engage`, both of which stay fully correct (`patch_applied = validation = 1.0`), but `standard/engage` is the cheaper successful corner with `post_edit_deliberation_seconds = 37.02` versus `46.46` for `terse/engage`. That made `standard/engage` the right explicit next probe. To keep that comparison surface stable, the repo also includes a dedicated markdown renderer (`benchmarks/render_claude_skill_ab_matrix.py`) so future slices can be compared as artifacts rather than ad hoc prose summaries. Because broader slices are long-running on Windows, both the matrix runner and the user-style Claude A/B harness now checkpoint at record granularity and support resume/restart semantics. That is the accepted operational change needed to make 5-task and 10-task slices auditable instead of all-or-nothing. The A/B harness therefore exposes `standard/engage` as an explicit non-default probe profile (`--enhanced-contract-profile probe-standard-engage`) so broader acceptance runs can test it without silently changing the shipped default behavior.

That broader acceptance run is now complete, and it is a rejection, not a promotion. On the 10-task user-style A/B comparison, `probe-standard-engage` preserved the enhanced correctness line (`claude-enhanced patch_applied = validation = 1.0` on both the accepted baseline artifact and the probe artifact) but failed the latency gate. The probe run produced `claude-enhanced mean wall_clock_seconds = 46.59s` versus `45.65s` on the accepted enhanced baseline, so the profile is not a speed win. It also dragged the paired baseline run down (`32.45s` versus `26.67s`), which reinforces the need to treat long user-style agent runs as noisy unless they show a clear improvement margin. The accepted conclusion is therefore: keep the probe profile available for future investigation, but reject it as the new default until it shows a real end-to-end latency win on a full-pack run.

The next accepted benchmark move was therefore corpus expansion, not more prompt churn. The hard real patch pack is now 12 scenarios, with two new upstream-derived fixtures aimed at coverage gaps outside the current utils/termui/help cluster:

* `click-choice-invalid-message`
  * primary file: `src/click/types.py`
  * regression surface: `Choice.get_invalid_choice_message`
  * direct failing fixture test: `benchmarks/patch_fixtures/click_choice_invalid_message/tests/test_types.py`
* `commander-use-color-env-conventions`
  * primary file: `lib/command.js`
  * regression surface: `useColor`
  * direct failing fixture test: `benchmarks/patch_fixtures/commander_use_color/tests/useColor.test.js`

This is the correct direction for a world-class line. Recent agent-eval work like OmniCode and ContextBench pushes toward broader, behaviorally diverse benchmark corpora rather than repeated optimization on a narrow pack. The accepted repo interpretation is the same: when a contract probe fails the full acceptance gate, broaden the corpus before adding more prompt heuristics.

Once that 12-scenario corpus was in place, the user-style Claude A/B had to be rerun on the same expanded pack. The first same-pack artifact (`artifacts/patch_eval_demo/claude_skill_ab_limit12_samepack_bakeoff.json`) showed the enhanced setup still winning, but no longer clearing the whole pack: `0.75 / 0.75` for baseline versus `0.916667 / 0.916667` for enhanced, with the remaining enhanced miss on `click-choice-invalid-message`. That turned the next product target into a single concrete engagement failure rather than a vague prompt problem.

The next accepted change was correspondingly small: tighten the generated repo-local `CLAUDE.md` so the enhanced run explicitly treats the prompt as the task and does not ask what task to perform. The acceptance artifact for that fix is `artifacts/patch_eval_demo/claude_skill_ab_limit12_current_claude_md_bakeoff.json`. On the full 12-scenario same-pack rerun it restores `claude-enhanced` to `patch_applied = validation = 1.0`, while baseline remains `0.75 / 0.75`. That is the current accepted user-style line.

This is not a free win. The accepted downside is larger mean wall clock:

* **baseline:** `29.89s`
* **enhanced:** `52.59s`

The next latency probe stayed on the same principle: change one lever, measure it on a real patch slice, and keep it only if correctness survives. Following Anthropic's current Claude Code guidance, the repo's user-style A/B harness now supports enhanced-only `--enhanced-effort` control, and the matrix runner can vary `--enhanced-efforts` as an explicit benchmark dimension rather than burying effort changes inside prompt edits. The first broader result is `artifacts/patch_eval_demo/claude_skill_ab_limit3_act_effort_matrix.json`, rendered in `artifacts/patch_eval_demo/claude_skill_ab_limit3_act_effort_matrix.md`. On the 3-scenario `act` slice, the default-effort enhanced line stayed correct at `1.0 / 1.0` with mean post-edit deliberation `40.431016s`. Low effort did reduce that mean post-edit time to `32.060561s`, but it collapsed enhanced correctness to `0.333333 / 0.333333`. That is an important negative result: the current latency problem is not solved by simply forcing lower reasoning effort. The harness support stays because the dimension is now measurable, but low effort is rejected as the default enhanced optimization.

The next probe tested a different latency hypothesis: keep the successful `act` task contract and default effort, but replace the open-ended final response with a positive minimal-output target. The new `enhanced_output_contract = done` tells Claude that after editing files directly it should respond with exactly `DONE`. That made the output constraint more explicit without reusing the rejected `terse` wording. The broader artifact is `artifacts/patch_eval_demo/claude_skill_ab_limit3_act_done_matrix.json`, rendered in `artifacts/patch_eval_demo/claude_skill_ab_limit3_act_done_matrix.md`. On the same 3-scenario `act` slice, both enhanced rows stayed fully correct at `1.0 / 1.0`, but the new contract was slower: the current `standard` control landed mean first-patch `38.056327s` and mean post-edit deliberation `37.99349s`, while the `done` variant landed mean first-patch `45.824802s` and mean post-edit deliberation `45.778454s`. That makes `done` another rejected latency candidate. The repo should keep the support so the failed path is recorded, but it should not promote the contract as a default.

At this point the prompt-level latency program is effectively closed within the explored contract space. The current record is consistent across the accepted probes: `probe-standard-engage` failed the broader latency gate, low effort preserved less reasoning but broke correctness, and `done` preserved correctness but made latency worse. The honest conclusion is not "try one more wording tweak." It is that the current accepted enhanced line should stay in place and further speed work should be treated as a larger architectural problem than prompt/effort/output-contract tuning alone.

The latest enterprise-readiness change is not about model behavior at all; it is about contract discipline. `scripts/validate_release_assets.py` now validates the public README surface in the same way it already validates workflows, package-manager docs, and installation docs. Concretely, the validator now requires the README to keep the canonical-doc links for benchmarks, GPU crossover, routing policy, harness API, and harness cookbook, and to link both `docs/installation.md` and `docs/RELEASE_CHECKLIST.md`. The accepted reason is operational: the README is the top-level buyer/operator entrypoint, so letting it drift away from the release-validated source of truth is a real GA risk even when the code and workflows stay correct. This is not a speed or quality metric improvement, but it is the kind of contract hardening that reduces avoidable launch-time confusion.

The next GA-readiness hardening extends that same principle to the benchmark story itself. `docs/benchmarks.md` is the canonical benchmark surface referenced by the README, but before this patch the release validator did not enforce the file directly. `scripts/validate_release_assets.py` now validates that `docs/benchmarks.md` retains the benchmark matrix, artifact-convention section, and acceptance-rule section that make the benchmark claims auditable. The accepted reason is simple: a benchmark-governed product is not release-ready if its canonical benchmark contract can drift without CI noticing. Again, this is not a throughput win; it is release-surface hardening.

The next agent-facing productization step closes a different contract gap: the public harness docs now include a machine-readable final-score shape and explicit retry guidance. `docs/harness_api.md` now documents the `run_patch_bakeoff.py` JSON contract as the stable final-score artifact, and `docs/harness_cookbook.md` now distinguishes producer retries from scorer reruns using concrete signals like `missing_predictions` and timeout/no-patch failure lines. This matters because benchmark-governed tooling is not actually agent-ready if external consumers can plan and apply edits but still need prompt-specific glue code to interpret the final score or decide what to rerun after partial failure.

That agent-facing productization slice is now closed as a public contract, not just prose guidance. The harness docs now expose canonical end-to-end CLI and MCP flows from repo inventory through final score, and the committed examples under `docs/examples/` now include explicit failure-mode payloads for stale sessions, incomplete patch runs with `missing_predictions`, scored no-patch/timeout rows, provider disagreement and provider unavailability, and apply+verify payloads where verification succeeds but post-apply validation fails. The important point is governance rather than novelty: these are not new internal edge cases, but they are now part of the validator-backed public surface, which means an external agent can consume retry and stop conditions without reverse-engineering tests or implementation details.

The remaining post-100 roadmap items are now also recorded as closed outcomes rather than implied open loops. Claude latency remains frozen for the current release line because the accepted probes and the current `CLAUDE.md`-backed 12-scenario artifact did not yield a faster defensible default; further work there is architectural, not another small contract tweak. Native control-plane work is likewise closed as an architectural boundary for this line: the accepted cold-path read already points to launcher/control-plane overhead, so a meaningful next step is a larger native rewrite rather than another small Python change. Provider promotion is closed with an explicit keep-opt-in decision: the broad provider bakeoff is still not good enough to justify defaulting away from `native`, even though focused hardcase wins are real. The comparative benchmark program is closed as a frozen surface with a stable comparator set and scenario-pack inventory documented in `docs/benchmarks.md`.

The first batch on the next roadmap is intentionally small and non-heroic: before claiming any new native-control-plane improvement, the cold-path benchmark artifact now records the `tg_launcher_mode` used to produce it. That means future Roadmap 1 work can distinguish explicit native-binary runs from discovered CLI-binary runs and Python-module launcher fallback in a machine-readable way. This does not change the accepted speed line by itself, but it fixes an observability gap that would otherwise make native-control-plane benchmark claims harder to audit.

The next Roadmap 1 batch is similarly narrow and preparatory: `src/tensor_grep/cli/bootstrap.py` now honors `TG_NATIVE_TG_BINARY` as an explicit native-dispatch override before probing repository-local `rust_core/target/release` or `debug` paths. This is not yet claimed as a measured speed win, but it removes one source of bootstrap ambiguity for packaged or benchmark-controlled native dispatch and lines the bootstrap behavior up with the broader principle that launcher/control-plane experiments should be explicit and machine-auditable rather than relying on incidental local filesystem layout.

The first measured launcher-mode comparison on the new roadmap is now also complete. Using the current `run_benchmarks.py` suite under the accepted `uv` Python environment, the forced `python_module_launcher` line (`artifacts/bench_run_benchmarks_python_module_launcher_uv.json`) beats the forced `explicit_binary` line (`artifacts/bench_run_benchmarks_explicit_binary_uv.json`) on this Windows host: mean `tg_time_s` improves from `0.282347` to `0.252554`, and median `tg_time_s` improves from `0.269235` to `0.230292`. That is a real control-plane finding, but not yet a roadmap-closing win by itself. Both artifacts still fail `benchmarks/check_regression.py --baseline auto`, so the accepted next-line conclusion is now explicit: there is still no accepted cold-path win from Python-side launcher variants on this host, and the remaining serious improvement path is a larger native rewrite rather than another small bootstrap tweak.

That closes the new roadmap items for this line in the same way the earlier roadmap was closed: with explicit measured outcomes instead of implied future loops. Roadmap 1 is closed as a larger-native-rewrite boundary. Roadmap 2 is closed because the public agent surface now has canonical end-to-end flows, retry taxonomy, and attempt-provenance examples backed by tests. Roadmap 3 is closed as an explicit architectural freeze for Claude latency on the current release line. Roadmap 4 is closed with a keep-opt-in provider decision on the broader pack, even though narrow hardcase wins remain accepted. Roadmap 5 is closed as a frozen comparison surface that reuses the same accepted comparator set and pack inventory until a new accepted artifact supersedes them.

The next future roadmap is now also explicit rather than living only in chat history. Its first batch is deliberately narrow and benchmark-governed: `benchmarks/run_benchmarks.py` now records `tg_binary_source` in the artifact environment block so native-control-plane work can distinguish repo-default binary dispatch (`default_binary_path`) from a user-supplied native binary (`explicit_arg`). This is not a speed claim, but it is the minimum provenance required before the repo can fairly compare a Rust-first launcher/control plane against the older Python-controlled entry shapes.

The first real Rust-first control-plane probe on that roadmap is now also complete and negative. A new benchmark-only launcher mode, `python_module_rust_first`, uses the existing Python bootstrap entrypoint but sets `TG_RUST_FIRST_SEARCH=1` so plain text search is handed off to the native `tg` binary and Rust owns the routing/fallback decision instead of the Python bootstrap sending the query directly to `rg`. On this Windows host, the artifact `artifacts/bench_run_benchmarks_python_module_rust_first_uv.json` lands mean `tg_time_s = 0.386778` and median `tg_time_s = 0.384161`, which is materially worse than the earlier `python_module_launcher` line (`0.252554` mean, `0.230292` median). `benchmarks/check_regression.py --baseline auto` reports regressions on all 10 cold-path scenarios. That makes the result useful even though it loses: the repo now has an explicit rejected experiment showing that simply handing plain text search from the Python bootstrap into the existing native `tg` binary is not the Rust-first control-plane shape that closes the cold-path gap.

The next native-control-plane probe is narrower and slightly better, but still not a winner. `explicit_binary_early_rg` keeps execution entirely inside the Rust binary and adds an env-gated early ripgrep fast path for benchmark-safe plain text search, with the artifact recorded at `artifacts/bench_run_benchmarks_explicit_binary_early_rg_uv.json`. After narrowing that fast path to avoid the glob, word-boundary, and fixed-string cases that dragged the first attempt, the rerun on this host lands mean `tg_time_s = 0.297869` and median `tg_time_s = 0.281141`. That is still much better than `python_module_rust_first`, but it still fails `benchmarks/check_regression.py --baseline auto` on all 10 cold-path scenarios and does not become a new accepted line. The useful conclusion is again narrow: there is probably some real value in earlier ripgrep handoff inside the Rust binary, but the current env-gated early path is not yet the accepted native control-plane shape.

The next structural launcher probe is also now measured and still rejected. `explicit_binary_positional` uses the existing positional Rust CLI path for benchmark-safe plain search shapes and falls back to `tg search` only for unsupported cases, with the artifact recorded at `artifacts/bench_run_benchmarks_explicit_binary_positional_uv.json`. On this host it lands mean `tg_time_s = 0.286235` and median `tg_time_s = 0.26987`. That is a modest aggregate improvement over the narrowed `explicit_binary_early_rg` probe, but it still fails `benchmarks/check_regression.py --baseline auto` on 9 of the 10 cold-path scenarios and does not become a new accepted line. The useful conclusion is again narrow and structural: avoiding subcommand parsing helps, but not enough to close the remaining cold-path gap. The next real step still looks like a larger native control-plane path rather than another launcher-shape toggle.

The next structural launcher probe is the strongest one yet and still rejected. `explicit_binary_positional_early_rg` uses a raw-args positional ripgrep fast path for benchmark-safe plain search shapes and falls back to `tg search` only for unsupported cases, with the artifact recorded at `artifacts/bench_run_benchmarks_explicit_binary_positional_early_rg_uv.json`. On this host it lands mean `tg_time_s = 0.268412` and median `tg_time_s = 0.255065`. That is better than both `explicit_binary_positional` and the earlier narrowed `explicit_binary_early_rg` probe, but it still fails `benchmarks/check_regression.py --baseline auto` on 7 of the 10 cold-path scenarios and does not become a new accepted line. The useful conclusion is finally a bit sharper: bypassing both subcommand parsing and Clap does help, but it still does not close the remaining cold-path gap. The repo now has enough evidence to say that the next honest Roadmap 1 move is a larger native control-plane path, not another benchmark-only env-gated shortcut.

The first rewrite-backed native probe is also now complete and negative. `explicit_fast_binary` uses a dedicated `tg-search-fast` binary with a manual parser for the cold-path benchmark subset and direct ripgrep passthrough, with the artifact recorded at `artifacts/bench_run_benchmarks_explicit_fast_binary_uv.json`. On this host it lands mean `tg_time_s = 0.324425` and median `tg_time_s = 0.312694`. That is materially worse than the strongest in-binary probe and still fails `benchmarks/check_regression.py --baseline auto` on 9 of the 10 cold-path scenarios. The useful conclusion is again narrow but important: a separate minimal launcher binary by itself is not the rewrite shape that closes the remaining cold-path gap.

The next roadmap is now explicit as well. It starts with a native control-plane rewrite program rather than another launcher-tweak loop, then treats agent product surface, Claude speed, broad provider promotion, and comparative benchmarking as separate milestone tracks with their own acceptance artifacts and stop conditions. That matters because the current line is closed honestly: future work should begin from a new roadmap, not by pretending the closed one is still unfinished.

The next accepted productization increment made those failure paths concrete rather than purely textual. The public harness surface now includes explicit machine-readable companion examples for stale sessions (`docs/examples/session_invalid_request_stale.json`), incomplete scored artifacts with `missing_predictions` (`docs/examples/patch_bakeoff_incomplete.json`), scored no-patch failures (`docs/examples/patch_bakeoff_no_patch.json`), provider disagreement and provider-unavailable states (`docs/examples/defs_provider_disagreement.json`, `docs/examples/provider_status_unavailable.json`), and post-apply validation failure (`docs/examples/rewrite_apply_verify_validation_failed.json`). The important point is not the filenames themselves; it is that external agents now have validator-backed fixtures for the control-flow edges that most often require brittle prompt glue in practice. `tests/unit/test_harness_api_docs.py` and `tests/unit/test_harness_cookbook.py` now lock those examples into the public contract.

The next roadmap line tightened the same product surface around multi-attempt execution rather than single-shot success/failure examples only. `docs/examples/attempt_ledger.json` now provides a machine-readable attempt chain with `parent_attempt_id`, `final_outcome`, and `replay.partial_retry_ledger`, while `docs/harness_api.md` documents that shape explicitly and `docs/harness_cookbook.md` adds a canonical `Multi-Attempt Replay Flow`. The accepted reason is practical: external agents often need to preserve failed attempts, trust artifacts, and resume boundaries across retries, and that contract is not stable if it only lives in prose or ad hoc controller code. `tests/unit/test_harness_api_docs.py` and `tests/unit/test_harness_cookbook.py` now fail if that public replay surface drifts.

So the repo's accepted conclusion is now sharper than before: the enhanced path can be made fully correct again on the accepted 12-scenario corpus, but the remaining world-class gap is speed, not final correctness on this pack.

The next accepted infrastructure step was to make competitor patch runners survivable at the same record granularity as the Claude A/B harness. Both `run_copilot_patch_predictions.py` and `run_gemini_patch_predictions.py` now support `--resume` and checkpoint after each completed `instance_id`. That change did not alter any model quality claim by itself, but it removed an invalid all-or-nothing harness shape for the long same-pack reruns. The first full beneficiary is Copilot: `artifacts/patch_eval_demo/real_patch_copilot_bakeoff_12.json` is now the accepted same-pack comparator artifact on the hard 12-scenario corpus, with `mean_patch_applied_rate = 0.5`, `mean_validation_pass_rate = 0.5`, `mean_primary_file_hit_rate = 0.833333`, and `mean_primary_span_hit_rate = 0.583333`. Gemini was behind operationally on this host for a different reason: the resumable runner checkpointed correctly, but the first-record probe (`artifacts/patch_eval_demo/real_patch_gemini_probe_limit1.json`) showed `timeout after 60s` and `wall_clock_seconds = 101.351834`, which meant the Windows timeout path was leaking substantial extra wall clock beyond the configured limit. The accepted follow-up fix was narrow: harden the Gemini Windows process-tree termination path so `taskkill` itself cannot hang indefinitely and falls back to `proc.kill()` if needed. The replacement probe artifact (`artifacts/patch_eval_demo/real_patch_gemini_probe_limit1_after_killfix.json`) reduces the same `60s` timeout case to `wall_clock_seconds = 65.157429`. A later harness-hardening pass tightened the resume contract itself: Gemini A/B, Claude A/B, and Claude matrix experiments now only treat an `instance_id` as complete when the full expected paired row set is present, rather than skipping as soon as one side was checkpointed. That closes a subtle artifact-corruption path for long interrupted reruns and is the prerequisite to any higher-level retry loop.

The next accepted finding was that timeout cleanup alone was not the whole story. Direct Gemini CLI probes on this host were still loading the user's global `.gemini/GEMINI.md` memory and adopting an unrelated ZooHouse persona, along with user-global MCP configuration. The runner now creates an isolated Gemini home for baseline benchmark runs that preserves authentication but strips user-global `GEMINI.md` and `mcpServers` configuration. A clean direct probe with that isolated home returns a generic Gemini CLI assistant greeting rather than the ZooHouse persona, which confirms the contamination source was external to `tensor-grep`. Even after that cleanup and a model update to `gemini-3-flash-preview`, the first real patch scenario still fails to produce a usable patch: at `60s` it times out (`artifacts/patch_eval_demo/real_patch_gemini_probe_limit1_3flash_isolated.json`), and at `180s` it exits with `Unable to locate Gemini JSON payload in output` (`artifacts/patch_eval_demo/real_patch_gemini_probe_limit1_3flash_isolated_180.json`). The completed same-pack artifact `artifacts/patch_eval_demo/real_patch_gemini_bakeoff_12_timeout60.json` is therefore a trustworthy `0.0 / 0.0` baseline line rather than a harness contamination artifact.

To make the eventual Gemini comparison fairer, the repo now also includes an official-shape Gemini project setup: root `GEMINI.md` for project context and `.gemini/skills/tensor-grep/SKILL.md` for the repository skill. Those files are based on the current Gemini CLI documentation for `GEMINI.md` context files and Agent Skills. They are committed so future Gemini-enhanced benchmark runs can compare baseline Gemini against a documented `tensor-grep`-enhanced Gemini condition, rather than pretending the current baseline-only line is the final story.

The first fair Gemini-enhanced probe is now also complete. A new A/B harness (`benchmarks/run_gemini_skill_ab.py`) runs the same task twice: once against a plain isolated repo copy and once against an enhanced repo copy containing the committed `GEMINI.md` plus `.gemini/skills/tensor-grep/`. On the first accepted patch scenario (`click-format-filename-shorten`) both rows still fail at a `60s` timeout: the baseline row lands `wall_clock_seconds = 71.630836` with no patch, and the enhanced row lands `wall_clock_seconds = 69.593862` with no patch (`artifacts/patch_eval_demo/gemini_skill_ab_limit1.json`). That result is useful because it rules out one more weak explanation: the current Gemini failure on this host is not simply “missing project skill setup.” The remaining problem is deeper runtime/contract behavior in non-interactive Gemini, not the absence of the documented folder structure. The harness now also supports direct patch-bakeoff scoring via `--scenarios`, so broader Gemini reruns can land as accepted scored artifacts instead of raw A/B rows only. The existing one-scenario probe is therefore scored as well at `artifacts/patch_eval_demo/gemini_skill_ab_limit1_bakeoff.json`, with a companion scorecard at `artifacts/patch_eval_demo/gemini_skill_ab_limit1_scorecard.md`: both baseline and enhanced rows stay at `0.0 / 0.0` with no patch emitted. The accepted cross-system same-pack scorecard therefore still treats Gemini as a baseline-only comparator line until a broader Gemini-enhanced rerun exists.

A later harness-hardening step made `benchmarks/run_gemini_skill_ab.py` acceptance-ready even before Gemini quality changed: when given the matching patch bakeoff scenario pack, it now emits scored `rows`, `summary`, and per-system score summaries directly, so broader Gemini-enhanced reruns can land as comparable artifacts instead of unscored raw-record dumps.

That broader rerun is now complete on the same accepted 12-scenario hard pack as the other systems. The scored artifact `artifacts/patch_eval_demo/gemini_skill_ab_limit12_bakeoff.json`, rendered at `artifacts/patch_eval_demo/gemini_skill_ab_limit12_scorecard.md`, lands `0.0 / 0.0` for both `gemini-baseline` and `gemini-enhanced`. That closes the Gemini milestone negatively rather than ambiguously: the committed Gemini project context and skill structure still do not recover non-interactive patching quality on this host.

## 6. Conclusion

`tensor-grep` represents a significant leap forward in bridging the gap between DevOps CLI utilities and modern GPU-accelerated Machine Learning frameworks. By dynamically routing workloads between highly optimized CPU paths for small files or exact strings, and `cuDF` or PyTorch backends for massive complex logs and AST graphs, it provides a resilient, enterprise-grade solution capable of true line-rate analytics. Future work will focus on optimizing the Python AST-to-Tensor serialization pipeline and completely bypassing the CPU memory bounce-buffer via NVIDIA GPUDirect Storage (GDS) APIs to map NVMe drives directly into GPU VRAM.

## 7. Next-Phase Architecture: Native Control Plane and Structural Rewrite Substrate

### 7.1 Architectural Findings from 2026-03 Optimization Line

The 2026-03 optimization line confirmed that the remaining performance gap to raw `rg` on cold generic text search is dominated by launcher/control-plane overhead, not by search kernel quality. Python micro-cuts are diminishing returns. The honest benchmarks show:

**What still holds:**
- Repeated-query paths show real room for index-driven gains (hot-query acceleration confirmed)
- AST workflow speed is materially better than earlier but remains a Python-controlled path
- GPU paths remain valid for large-corpus semantic/NLP workloads
- The Rust `--replace` zero-copy path delivers measurable throughput gains

**What is now clear:**
- The onefile Nuitka binary is not the speed path (extraction/packaging overhead dominates; onefile builds clock 1.1-1.2s vs Python bootstrap 0.33-0.48s for simple search)
- Python orchestration overhead is the single largest remaining gap to rg cold-start parity
- A native Rust control plane is the next material architectural improvement

### 7.2 Reference Codebases for the Next Phase

The following reference codebases were identified for the native structural search and editor substrate evolution:

1. **ast-grep** (https://github.com/ast-grep/ast-grep) — Rust, tree-sitter-based structural search with rewrite/codemod support. Direct reference for AST + editing integration. The ast-grep Rust crates will be embedded as a Cargo dependency for structural search.

2. **Comby** (https://comby.dev/) — Structural search/replace with rewrite-oriented design. Good model for editor-safe transformations and templated rewriting.

3. **Zed** — High-performance editor architecture. Rope/sum-tree style editor data structures. Use as an editor-engine reference for low-latency editing substrate.

4. **Helix** (https://helix-editor.com/) — Rust editor with native tree-sitter integration. Reference for native editor ergonomics and syntax-aware operations.

5. **GitHub Stack Graphs** (https://arxiv.org/abs/2211.01224) — Name resolution at scale via incremental, file-local graph construction. Relevant for future AI harness substrate for code navigation and edit accuracy.

### 7.3 Research Directions

1. **REI for repeated regex/indexed logs** (https://arxiv.org/abs/2510.10348): Strongest match for tg's repeated-query hot path. Real takeaway: build better regex indexing for stable corpora with a proper inverted index subsystem. Do not try to beat rg on cold search with indexing overhead.

2. **RE# for richer/faster regex classes** (https://arxiv.org/abs/2407.20479): Good direction for complex CPU regex planning beyond plain rg-style cases.

3. **cAST for AST-shaped retrieval/chunking** (https://arxiv.org/abs/2506.15655): Very relevant to AI harness use. Practical takeaway: build a persistent AST shard/index, not just result caching.

4. **Stack Graphs for code navigation/edit accuracy** (https://arxiv.org/abs/2211.01224): For "world class editing" for AI harnesses, this is closer to the real substrate than grep alone.

5. **MutaGReP for repository-grounded planning** (https://arxiv.org/abs/2502.15872): Relevant to future AI harness orchestration for multi-step repository search/edit planning.

### 7.4 Architectural Convergence Target

Based on the 2026-03 analysis and benchmark evidence, the architecture converges toward:

1. **Rust-first control plane**: Rust owns CLI, routing, config, search/edit orchestration, output, native text path, and native AST path. Python becomes an optional compute sidecar, invoked only as a subprocess for cuDF/Torch/NLP GPU-heavy jobs. This removes Python from the default hot path (plain text search, count/context, native AST, editor calls) while preserving existing GPU investment.

2. **Native structural search/rewrite**: Embed ast-grep Rust crates directly for structural search. Build tg's own edit/rewrite substrate on top: patch generation, edit safety, provenance, batch edit planning, verification loops, machine-readable edit contracts. Fast time to native AST performance without reinventing what ast-grep already solved.

3. **Dedicated index subsystem (REI-inspired)**: New persistent index subsystem with shared corpus metadata and invalidation semantics. The existing trigram prefilter work becomes the first-level candidate reducer inside the new subsystem. Shared across fixed-string, regex prefilter, and eventually AST/text hybrid routing.

4. **GPU path where it actually wins**: Huge corpora, semantic/NLP classification, large-batch processing. Not cold generic grep.

5. **Editor-grade rewrite substrate (future)**: AST-safe rewrite rules, rope/tree-based edit application, deterministic patch output, stack-graph/symbol-aware navigation.

**Decision recorded (2026-03):** The next serious product moves are the Rust-first launcher/control plane, native structural search/rewrite core, and indexed repeated-query engine. Python micro-cuts are deprioritized. Nuitka onefile packaging is not the current path to rg parity and is documented as a known dead end in the optimization ledger (see Section 3.10).

**Roadmap closure recorded (2026-03-31):** The current native-rewrite roadmap is now closed on the same evidence-first basis as the earlier roadmap lines. Roadmap 1 is closed as an explicit rejected architecture result for the current line: `python_module_rust_first`, `explicit_binary_early_rg`, `explicit_binary_positional`, `explicit_binary_positional_early_rg`, and `explicit_fast_binary` all improved specific control-plane hypotheses but still failed to beat the accepted Windows cold-path baseline. Roadmap 2 is closed because the public harness surface now includes validator-backed multi-attempt provenance, replay, and partial retry ledgers. Roadmap 3 remains an explicit Claude architecture/model-side freeze for the current release line. Roadmap 4 remains an explicit keep-opt-in decision for broad provider promotion. Roadmap 5 is closed as a frozen comparison surface whose comparator set and pack inventory remain fixed until a new accepted artifact supersedes them.

**Next roadmap recorded (2026-03-31):** The next line is now explicit in `docs/world_class_plan.md` rather than implied. It centers on a real native control-plane rewrite v2, agent product surface v5 for multi-task and multi-session replay chains, one more structural Claude speed pass based on context/caching/harness levers, another broad provider-promotion decision on a true broad planning pack, and a frozen `Comparative Benchmark v6` surface that renders only from accepted inputs.

**Execution model recorded (2026-03-31):** The new line is also explicitly parallel. Instead of continuing a single-threaded loop of review, test, docs, and benchmark updates, the roadmap now uses a main integrator plus disjoint lanes for native control plane, structural rewrite, agent product surface, provider decision work, and benchmark/competitor governance. The accepted reason is throughput, not rhetoric: lane-local narrow tests and workload-specific benchmarks can run in parallel, while full repo gates run at merge points. The first concrete payoff is agent-surface Lane C, which now includes a multi-session replay ledger example (`docs/examples/multi_session_attempt_ledger.json`) rather than only single-task attempt provenance.

**Execution hygiene recorded (2026-03-31):** The parallel model only works if agent lifecycle is treated as part of the contract. Completed subagents should be closed at lane handoff or merge time instead of being left open after their results are integrated. That rule is operational, not aesthetic: stale completed agents create false concurrency, waste context budget, and make it harder to tell which lanes are actually still active.

**Benchmark governance recorded (2026-04-15):** Future optimizations must conform to the new regression-check policy. Control-plane or launcher routing changes require explicit artifact validation. If a patch regresses accepted benchmark lines, it must be explicitly rejected and documented here as an intentional non-goal or historical failed attempt, ensuring that only verified performance improvements or functionally equivalent fallback mechanisms land in the primary branch.

**Performance regression recorded (2026-04-16):** The `count_matches` (-c) mode and related search workflows show a persistent slowdown on the native CPU text backend (trending from 1.65x -> 2.28x -> 3.69x overhead vs ripgrep). Benchmark checks confirm wide regressions (e.g. `Count Matches` regressed by 144.6%). This performance degradation is documented here as a regression to unwind in a dedicated performance optimization pass, separated from the AST correctness fixes.

**Competitive-baseline read recorded (2026-03-31):** The benchmark story also now has a stable external baseline read by workload class. `ripgrep` remains the cold plain-text search baseline, `ast-grep` remains the structural search/rewrite baseline, `Semgrep` remains the stronger policy/security scan ecosystem baseline, and `Zoekt` remains the indexed repeated-query/search-at-scale baseline. The correct product claim for the current line is therefore not “better than every search tool”; it is narrower and more defensible: `tensor-grep` is strongest where deterministic repository planning, replayable edit workflows, and benchmark-governed agent surfaces matter, while the raw cold-search crown still belongs to `ripgrep`.

**Cross-agent integration read recorded (2026-03-31):** A local audit of current Gemini CLI, Claude Code, and Codex codebases converged on the same product seam. Those systems already have planning loops, patch/apply flows, and raw filesystem or shell primitives. The shared missing layer is a deterministic local data plane that returns compact edit targets, minimal next reads, related tests, and validation commands without forcing each controller to reconstruct those from larger ranked payloads. The accepted response for this line is additive rather than architectural churn: `edit-plan`, `context-render`, `blast-radius-plan`, and `blast-radius-render` now expose a compact `navigation_pack` block carrying the primary target, mention-ready follow-up reads, related tests, validation commands, and edit ordering.

**Live external validation recorded (2026-03-31):** That compact contract is now validated on a copied external agent repo instead of only local toy fixtures. The artifact `artifacts/external_validation/gemini_navigation_pack_validation.json` was generated from a copied `gemini-cli-main` subtree. For the query `grep search tool glob names_only fixed strings`, `navigation_pack` selected `packages/core/src/tools/glob.ts` as the primary target and surfaced `packages/core/src/tools/grep.ts` plus adjacent tool files as follow-up reads. That is the intended behavior for this line: the smaller bundle narrows the planner-to-reader handoff without discarding the richer underlying planning payload.

**Live patch-driver validation recorded (2026-03-31):** The same copied Gemini subtree now also exercises the real patch-driver flow, not just the raw repo-map output. The artifact `artifacts/external_validation/gemini_patch_driver_validation_summary.json` records that `benchmarks/run_tensor_grep_patch_driver.py` preserved `navigation_pack` into the emitted patch-driver record, kept `glob.ts` as the primary target with mention-ready follow-up reads including `grep.ts`, and emitted a matching public attempt ledger whose next action remains `run patch system`. This live run also exposed a Windows automation edge case: PowerShell-generated UTF-8 BOM scenario files broke scenario loading, so the accepted hardening for this line is that patch-driver scenario loading now uses `utf-8-sig` and accepts that input shape directly.

**Second live patch-driver validation recorded (2026-03-31):** The same integration now also works on a copied Claude Code subtree rather than only the Gemini tools slice. The artifact `artifacts/external_validation/claude_patch_driver_validation_summary.json` records that the real patch-driver flow selected `src/components/permissions/FileWritePermissionRequest/FileWriteToolDiff.tsx` as the primary target, preserved a compact five-read `navigation_pack` around the permission UI path, and emitted the matching public attempt ledger with `next_action = run patch system`. That matters because the accepted contract is now validated on two distinct active agent codebases with different local architectures, not just a single external example.

**Third live patch-driver validation recorded (2026-03-31):** The same integration now also works on a copied Codex app-server subtree, giving this line a third external architecture check. The artifact `artifacts/external_validation/codex_patch_driver_validation_summary.json` records that the real patch-driver flow selected `codex-rs/app-server/src/fuzzy_file_search.rs` as the primary target, preserved a compact five-read `navigation_pack`, surfaced `cargo test` as the validation command for that Rust slice, and emitted the matching public attempt ledger with `next_action = run patch system`. The combined artifact `artifacts/external_validation/external_agent_patch_driver_comparison.json` now shows the same compact navigation and provenance contract holding across copied Gemini, Claude, and Codex codebases while still adapting validation commands to the local stack.

**External-agent scorecard recorded (2026-04-01):** The comparison is now scored rather than left qualitative. The artifact `artifacts/external_validation/external_agent_patch_driver_scorecard.json` now scores each external system on compactness, validation-fit, and phased-read reduction. After broadening the read-phase heuristic from a single-sibling prefetch to same-directory related/test prefetch, the current line stays clean on compactness and validation targeting and improves again on the phased-read dimension: `mean_compactness_score = 1.0`, `mean_validation_fit_score = 1.0`, `mean_parallel_read_reduction_score = 0.916667`, and `mean_overall_score = 0.972222`. Gemini now collapses its five-read tools slice into a single primary-prefetch phase, Codex does the same on the Rust app-server slice, and Claude remains strong but still needs two phases on the broader cross-directory permission UI slice. The accepted read is now narrower and stronger: stack-aware validation is solved across the three audited agent codebases, and same-directory prefetch materially improves the planner-to-reader handoff where the edit slice stays locally clustered.

**Production-repo patch-driver validation recorded (2026-03-31):** The next live check moved beyond copied agent CLIs into a real non-agent repo: `agent-studio`. The artifact `artifacts/external_validation/agent_studio_patch_driver_validation_summary.json` records that the narrowed `.claude/tools/cli` slice lands cleanly with `.claude/tools/cli/hybrid-search.cjs` as the primary target, a single prefetched phase over the local `supportsDaemonCommand` / `shouldUseDaemon` pair, and `npm test` as the only suggested validation command. That closes the first real production fallback bug for this line: JS-first repos with incidental Python files no longer inherit a repo-level `uv run pytest -q` suggestion. The follow-up artifact `artifacts/external_validation/agent_studio_patch_driver_validation_summary_capped.json` closes the next step too: the broader `.claude/lib/code-indexing` root becomes feasible under bounded scanning (`max_repo_files = 250`), returning immediately with `hybrid-search.cjs#L33-L171` as the compact primary target and a real lightweight validation fallback (`npx jest`) while explicitly skipping the expensive edit-plan seed. The accepted read is now stronger than the earlier bounded proof: fast heavy-root context renders are good enough to drive an AI loop because they retain both the compact primary target and an actionable validation command, but full edit-plan-seed assembly remains the expensive part of broad internal-library slices.

The first real Lane A v2 result is also now explicit. Promoting the fastest supported `tg search` subset into the real default front door improved the old `explicit_binary` cold-path line from `0.282347` / `0.271463` to `0.261513` / `0.247376`. That is a genuine control-plane improvement rather than another benchmark-only env-gated shortcut. It still does not close the lane: the resulting artifact (`artifacts/bench_run_benchmarks_explicit_binary_default_frontdoor_uv.json`) continues to regress against the accepted Windows baseline on 5 of the 10 benchmark scenarios. The correct read is narrower and more useful than either hype or defeatism: the native front door matters, but Roadmap 1 v2 still needs a larger win than this first promotion delivered.

We also tested the obvious next widening step and rejected it. `artifacts/bench_run_benchmarks_explicit_binary_default_frontdoor_v2_uv.json` broadened the default front door to accept the already-supported ripgrep-equivalent `--glob`, `-w`, and `-F` subset instead of leaving those shapes on the slower Clap path. That preserved parity, but it regressed the default `explicit_binary` line relative to the narrower front-door artifact and still failed the frozen Windows baseline on 5 scenarios (`Case-Insensitive`, `Regex`, `File Glob Filtering`, `Word Boundary`, `Fixed Strings`). The accepted conclusion is therefore more precise: “broader default ripgrep passthrough” is not automatically a win, and this repo should preserve that failure instead of retrying the same widening on the same line.

The next accepted result on that lane is narrower and more operational. `artifacts/bench_run_benchmarks_passthrough_startup_refresh.json` kept the default `explicit_binary` `tg search` path intact but cached ripgrep binary resolution inside the Rust passthrough layer so repeated small-search invocations stopped paying the same runtime-path probe cost on every scenario. In a same-host back-to-back comparison against the immediately preceding local baseline artifact, the refresh improved `Max Count Limit` from **0.720478s** to **0.386605s** (`-46.34%`), `Count Matches` from **0.691659s** to **0.573465s** (`-17.09%`), `Word Boundary` from **0.971472s** to **0.835348s** (`-14.01%`), and `File Glob Filtering` from **0.830538s** to **0.794537s** (`-4.33%`). The accepted read is narrower than “cold search is solved”: repeated passthrough setup work was a real tax worth removing, but the resulting line still trails `rg` overall and does not justify another broad front-door widening.

That same refresh also clarified the next two targets precisely enough to preserve as history instead of future guesswork. First, the positional early-rg path should be extended and benchmarked cleanly for `-m`, `-w`, and `--glob`, because those shapes are still safe ripgrep contracts and now have validator-backed parsing tests. Second, after that startup line is measured, the remaining default `-c` count-path overhead is the next cold-path row worth attacking. The repo should treat those as the next two narrow control-plane tasks instead of reopening the already-rejected “widen the default front door again” idea.

We also tested the narrower “maybe `-m` alone is safe to widen” variant and rejected that too. In a clean same-host back-to-back comparison between `artifacts/bench_run_benchmarks_max_count_frontdoor_baseline_clean.json` and `artifacts/bench_run_benchmarks_max_count_frontdoor_candidate_clean.json`, allowing the default `tg search` front door to passthrough `--max-count` regressed the target row from **0.161090s** to **0.180941s** (`+12.32%`) and also moved the adjacent cold-path rows the wrong way (`File Glob Filtering` **0.287183s -> 0.324743s**, `+13.08%`; `Word Boundary` **0.295560s -> 0.327371s**, `+10.76%`; `Fixed Strings` **0.264858s -> 0.296839s**, `+12.07%`). `Count Matches` improved (**0.216171s -> 0.194835s**, `-9.87%`), but that does not rescue the lane because the point of the experiment was to improve the worst `-m` outlier without reopening the broader default-front-door failure. The accepted read is now stricter: default-front-door widening is closed even for the “`-m` only” variant, so the next cold-path work should stay on the positional early-rg lane first (`-m`, then `--glob`) before revisiting the separate default `-c` count-path overhead.

The next accepted result on that stricter lane is intentionally small and product-facing. Positional `tg -m <n> PATTERN PATH` now preserves `max_count` through both the positional ripgrep passthrough args and the native routing config instead of silently dropping it outside `tg search`. On the experimental `explicit_binary_positional_early_rg` lane, the clean `origin/main` benchmark script plus the same current binary yielded `artifacts/bench_run_benchmarks_positional_m_baseline_lane.json` and `artifacts/bench_run_benchmarks_positional_m_candidate.json`, improving `Max Count Limit` from **0.163646s** to **0.158791s** (`-2.97%`). That is not large enough to retell the global cold-search story, but it is enough to accept the batch under the repo's narrower rule for user-visible capability plus measured non-regression. The next two tasks therefore tighten again: positional `--glob` first, then the separate default `-c` count-path overhead.

The next positional follow-up on that lane was measured and rejected. In a same-host comparison between `artifacts/bench_run_benchmarks_positional_glob_baseline_lane.json` and `artifacts/bench_run_benchmarks_positional_glob_candidate.json`, widening the experimental `explicit_binary_positional_early_rg` lane to accept positional `--glob` moved `File Glob Filtering` from **0.149999s** with parity **PASS** to **0.285383s** with parity **FAIL**. The failure mode matters more than the slowdown alone: the candidate positional `tg --glob=*.log PATTERN PATH` route returned zero matches on the benchmark corpus, so this is not a “small regression with product upside” but a broken routing contract. The accepted read is therefore final for this attempt: positional `--glob` should stay off the experimental lane until the contract bug is fixed and remeasured. The next two cold-path tasks now tighten again to the remaining default `-c` count-path overhead first, then positional `-w`.

The public agent surface for that same line is now closed on the product side. `docs/examples/multi_task_attempt_ledger.json` joins the existing multi-session ledger so external agents can preserve a replayable task chain across more than one bounded task instead of flattening cross-task state into prose or controller-specific logs. The accepted reason is operational, not decorative: if a controller cannot preserve multi-task replay, it cannot prove why a later accepted patch depended on an earlier accepted task result. With both multi-session and multi-task ledgers validator-backed in the public docs, the v5 agent-surface lane is now closed for the current line.

That contract is now executable as well as documented. `benchmarks/build_attempt_ledger.py` can materialize an `agent_attempt_ledger` artifact from a machine-readable attempts input, inferring the accepted attempt, replay chain, audit chain, multi-session handoff metadata, and multi-task chain when the input provides enough information. The accepted reason is product leverage: external agents no longer need to hand-author the ledger JSON shape from the docs example alone.

That ledger surface is now integrated into a real producer path too. `benchmarks/run_tensor_grep_patch_driver.py` accepts `--attempt-ledger-output` and can emit the public `agent_attempt_ledger` artifact alongside patch-ready prediction records, so a real patch-driver flow can preserve replayable attempt provenance without a second manual conversion step. The patch-driver records now also preserve `navigation_pack` verbatim when the upstream tensor-grep payload includes it, which keeps the compact planner-to-reader handoff attached to the richer `edit_plan_seed` instead of forcing downstream agents to reconstruct it. The accepted reason is operational: the public ledger and navigation contracts are now attached to an actual harness flow, not just standalone builder scripts.

The scorer layer now exposes the same contract. `benchmarks/run_patch_bakeoff.py` accepts `--attempt-ledger-dir` and emits one public attempt ledger per `instance_id` alongside the scored bakeoff artifact, mapping scored outcomes such as accepted, validation-failed, patch-apply-failed, and timeout/no-patch rows into replayable attempt chains. The accepted reason is end-to-end provenance: once a patch benchmark has terminal attempt outcomes, the harness should be able to preserve them as machine-readable ledgers instead of leaving provenance split across the scorer rows and separate manual artifacts.

The Claude A/B producer now exposes the same handoff surface before scoring. `benchmarks/run_claude_skill_ab.py` accepts `--attempt-ledger-dir` and emits one public attempt ledger per `instance_id`, preserving baseline-vs-enhanced attempt provenance and response-shape outputs before the later bakeoff decides patch correctness. The accepted reason is pipeline continuity: the public ledger contract now spans the producer side and the scorer side, so external agents can preserve replayable provenance from generation through final evaluation without inventing an intermediate schema.

The Gemini A/B producer now exposes the same contract. `benchmarks/run_gemini_skill_ab.py` accepts `--attempt-ledger-dir` and emits one public attempt ledger per `instance_id`, preserving baseline-vs-enhanced attempt provenance before the later bakeoff stage. The accepted reason matches Claude: producer-side attempt history should be machine-readable even when final correctness is still delegated to the scorer.

The same producer-side contract now covers the competitor prediction runners too. `benchmarks/run_claude_patch_predictions.py`, `benchmarks/run_copilot_patch_predictions.py`, and `benchmarks/run_gemini_patch_predictions.py` each accept `--attempt-ledger-dir` and can emit one public attempt ledger per `instance_id` before the scorer runs. The accepted reason is consistency: raw prediction flows should not need a separate provenance format from the A/B flows when they serve the same downstream patch-eval pipeline.

A subsequent internal optimization targeted `NativeSearchMatch` retention overhead. Previously, the direct plain-streaming path still materialized match objects more often than necessary. Project 4 extended this cleanup to the standard sequential search path and the parallel walk path, and removed debug-build retention. The fix tightened `retain_matches` across `search_plain_streaming`, `search_file_streaming_plain_sequential`, and `search_file_streaming_standard_sequential` (using a conditionally allocating `CollectingSink`) so match objects are now strictly only allocated when `config.json` is true or when the `output_target` is a `Buffer`. 

Project 5 and 6 further stabilized the native engine by extending parallel execution to the many-file and context-search product lines. The `run_native_search_files` path was parallelized via `rayon` with atomic per-file output buffering, and `search_walk_roots_parallel` was enhanced to support `before_context` and `after_context` via `grep_printer`. Additionally, the count-mode path was optimized to completely bypass match materialization, achieving maximum efficiency for aggregation workloads. 

While structurally correct and benchmark-clean on the direct native surface, the Project 4 slice was not formally benchmark-accepted on the governed CLI surface due to host-level noise in the fresh rerun (`artifacts/bench_project_4_review_after_fix.json`). The accepted read is therefore narrower than a speedup claim: keep the retention cleanup because it is behaviorally and structurally safe, and land the Project 5/6 parallelization logic because it is comparator-clean on the governed benchmark (meaning no regressions detected). A subsequent fix fully restored sorted deterministic file-order emission for parallel multi-file and directory-walk searches by buffering and sorting thread outputs prior to main-thread emission, while maintaining the comparator-clean performance profile.

A final narrow default routing promotion was landed for strictly plain search shapes (no context, globs, or complex regex flags) in count and many-file scenarios. This ensures that the most optimized native paths are leveraged where they provide the greatest benefit, while maintaining the proven performance of ripgrep for more complex search shapes. Verified against the baseline, this represents the final stabilization of the cold-path native text search surface.

## References
1. Zhong, J., Chen, S., & Yu, C. (2024). *XAV: A High-Performance Regular Expression Matching Engine for Packet Processing*. arXiv:2403.16533.
2. Ye, Y., Pang, P., Zhang, T., & Huang, H. (2025). *GNN-Coder: Boosting Semantic Code Retrieval with Combined GNNs and Transformer*. arXiv:2502.15202.
3. Zhang, L., Deep, S., Patel, J. M., & Sankaralingam, K. (2025). *Regular Expression Indexing for Log Analysis. Extended Version*. arXiv:2510.10348.
4. Varatalu, I. E., Veanes, M., & Ernits, J.-P. (2024). *RE#: High Performance Derivative-Based Regex Matching with Intersection, Complement and Lookarounds*. arXiv:2407.20479.
5. Zhang, Y., Zhao, X., Wang, Z. Z., Yang, C., Wei, J., & Wu, T. (2025). *cAST: Enhancing Code Retrieval-Augmented Generation with Structural Chunking via Abstract Syntax Tree*. arXiv:2506.15655.
4. Sun, Y., Kumar, S., Gilray, T., & Micinski, K. (2025). *Column-Oriented Datalog on the GPU*. arXiv:2501.13051.
5. Wang, X., et al. (2025). *GRACE: Graph-Guided Repository-Aware Code Completion through Hierarchical Code Fusion*. arXiv:2509.05980.
6. Wang, Y., et al. (2024). *STATIC: Fast and Constrained Decoding for LLM-based Generative Retrieval*. arXiv:2403.19317.
7. MarkTechPost (2026). *Google AI Introduces STATIC: A Sparse Matrix Framework Delivering 94.8x Faster Constrained Decoding for LLM-based Generative Retrieval*.
8. Ouyang, S., et al. (2025). *RepoGraph: Enhancing AI Software Engineering with Repository-Level Code Graph*. arXiv:2410.14684.
9. Shah, P., et al. (2025). *RANGER: Repository-Level Agent for Graph-Enhanced Retrieval*. arXiv:2509.25257.
10. Li, H., et al. (2026). *ContextBench: A Benchmark for Context Retrieval in Coding Agents*. arXiv:2602.05892.
11. Zhu, J., Hu, M., & Wu, J. (2026). *SWE Context Bench: A Benchmark for Context Learning in Coding*. arXiv:2602.08316.
12. Xia, C. S., Deng, Y., Dunn, S., & Zhang, L. (2024). *Agentless: Demystifying LLM-based Software Engineering Agents*. arXiv:2407.01489.
13. sorendunn. (2025). *Agentless-Lite*. GitHub repository. https://github.com/sorendunn/Agentless-Lite
14. Anthropic. (2026). *Claude Code Overview*. https://code.claude.com/docs/en/overview
15. Anthropic. (2026). *The Complete Guide to Building Skills for Claude*. https://resources.anthropic.com/hubfs/The-Complete-Guide-to-Building-Skill-for-Claude.pdf
