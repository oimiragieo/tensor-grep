# Post-Reader-Reintegration Decomposition Design

Date: 2026-04-20

## Goal

Define the next safe work after the `CuDFBackend._read_text_series(...)` seam landed.

The repo now needs two different follow-up tracks:

1. a low-risk contract-repair and AST-correctness slice that can land immediately
2. a higher-risk specialized GPU-reader activation track that still needs adapter design and stricter capability boundaries

These should not be bundled into one patch.

## Current State

The single-file non-zero-copy cuDF fallback now routes through `CuDFReader.read_to_gpu()` with a fail-fast boundary for reader runtime bugs.

What remains:

- `TG_FORCE_CPU` is documented as an environment override but is not consumed by runtime code
- AST project-data cache invalidation is incomplete because traversed tree directories never make it into the cache key
- `reader_dstorage.py` is still effectively unwired
- `reader_kvikio.py` is still effectively unwired
- `reader_dstorage.py` and `reader_kvikio.py` still do not fit the line-oriented `IOBackend` contract
- AST/workflow cleanup still has dead or stale localized branches
- operator/docs/benchmark narration still has small contract drift

## Ranked Findings

### Highest-priority correctness and contract bugs

1. `docs/EXPERIMENTAL.md` / `docs/runbooks/gpu-troubleshooting.md` vs runtime
   - `TG_FORCE_CPU=1` is documented for operators
   - no runtime consumer exists for that env var
   - the underlying `force_cpu` behavior exists, but the documented override contract does not

2. `src/tensor_grep/cli/ast_workflows.py`
   - `_collect_candidate_files(...)` always returns `set()` for `tree_dirs`
   - `_load_ast_project_data()` therefore cannot include traversed-directory mtimes in cache invalidation
   - nested tree edits can leave stale `project_data_v6.json` state

### High-confidence dead or unwired

1. `src/tensor_grep/io/reader_dstorage.py`
2. `src/tensor_grep/io/reader_kvikio.py`

Why:

- neither is selected by runtime code
- both expose GPU-buffer style behavior rather than the current line-oriented interface
- both remain isolated unit-test targets rather than live runtime components

### High-confidence dead or stale localized code

1. `src/tensor_grep/cli/ast_workflows.py`
   - `tree_dirs_meta` is computed and dropped
   - a `DirectoryScanner(cfg)` local is instantiated and unused in `scan_command()`
   - `_describe_ast_backend_mode()` still reports `AstBackend` as `GPU-Accelerated GNNs`

2. `src/tensor_grep/io/directory_scanner.py`
   - `and not base_path.is_file()` is redundant because direct files return earlier

### Contract drift

1. `docs/EXPERIMENTAL.md`
   - documents `TG_RUST_FIRST_SEARCH` and `TG_RUST_EARLY_RG`
   - does not document `TG_RUST_EARLY_POSITIONAL_RG`
   - documents `TG_FORCE_CPU` even though the env var is not wired today
   - should also be aligned with `TG_RESIDENT_AST`

2. `tests/unit/test_cli_modes.py` / `src/tensor_grep/cli/main.py`
   - `doctor --json` omits parts of the live experimental/runtime flag surface

3. `benchmarks/run_ast_workflow_benchmarks.py`
   - narrative comments still describe `scan` / `test` as if native Rust only forwards to Python

## External Validation

### Official docs

Current official RAPIDS and NVIDIA documentation supports keeping specialized reader activation narrow:

- KvikIO remains an active, first-class RAPIDS I/O substrate
- GDS usefulness depends on capability, alignment, and I/O shape
- cuDF documents KvikIO/GDS support primarily for structured readers/writers such as parquet/json/orc/avro, not as a blanket promise for all text workflows

Current official or primary sources:

- KvikIO API: `https://docs.rapids.ai/api/kvikio/stable/api/`
- cuDF I/O / KvikIO integration: `https://docs.rapids.ai/api/cudf/latest/user_guide/io/io/`
- NVIDIA GDS best practices: `https://docs.nvidia.com/gpudirect-storage/best-practices-guide/index.html`
- Microsoft DirectStorage samples: `https://github.com/microsoft/DirectStorage`

### Ecosystem signal

The Windows DirectStorage Python surface is currently much thinner than the Linux/RAPIDS path:

- `dstorage-gpu` exists on PyPI as a third-party beta package
- its contract is oriented around raw tensor loading, not line-oriented text ingestion
- it is promising, but not yet a credible default core-search dependency

Primary source:

- `https://pypi.org/project/dstorage-gpu/`

### Research signal

Recent GPUDirect Storage work still supports direct-storage as a planning substrate, but not as a reason to blindly route all text search through it:

- `TERAIO` shows that GDS can remove CPU bottlenecks when access plans are explicit and workload size amortizes the transfer path
- this supports a future adapter track with explicit capability and workload gating

Primary source:

- `https://arxiv.org/abs/2506.06472`

### Community signal

Current AI/code-tooling community energy is still concentrated on local exact/structural indexing and graph-aware context rather than aggressive default GPU I/O for source search.

Useful signal:

- `https://www.reddit.com/r/mcp/comments/1rc8wl2/built_an_offline_mcp_server_that_stops_llm/`
- `https://www.reddit.com/r/mcp/comments/1rp6q31/codegraphcontext_an_mcp_server_that_indexes_local/`
- `https://www.reddit.com/r/ClaudeAI/comments/1rtu97v/built_a_code_knowledge_graph_for_claude_code_cut/`

That reinforces the immediate priority: clean up deterministic local behavior and public/operator contracts first.

## Approaches

### Approach A: Activate `reader_dstorage` and `reader_kvikio` next

Pros:

- maximally faithful to the old project plan

Cons:

- current implementations are toy-level for the live contract
- they do not match `IOBackend`
- KvikIO text-path value is not yet justified by current repo benchmarks
- Windows DirectStorage path depends on a beta third-party Python surface

### Approach B: Decompose into immediate cleanup + later adapter activation

Pros:

- smallest regression risk
- fixes real dead code and docs/runtime drift immediately
- gives the specialized GPU-reader work its own explicit adapter design and benchmark gate

Cons:

- takes two patches instead of one

### Approach C: Delete the dormant specialized readers and standardize on cuDF-only

Pros:

- simplest runtime surface

Cons:

- gives up the designed expansion track prematurely
- loses a potentially useful substrate before it is fairly evaluated

## Recommendation

Use Approach B.

### Subproject 1: Contract repair + AST correctness

Immediate patch, correctness-only, safe to land now.

Scope:

- wire the documented `TG_FORCE_CPU` env override into runtime/bootstrap behavior
- repair AST project-data cache invalidation so traversed tree directories participate in freshness checks
- remove dead localized code in `ast_workflows.py`
- remove redundant guard in `directory_scanner.py`
- align `doctor --json` env snapshot with documented experimental runtime surface
- update `docs/EXPERIMENTAL.md` for `TG_RUST_EARLY_POSITIONAL_RG`
- fix stale benchmark-script narration without changing benchmark behavior

Benchmarks:

- run `benchmarks/run_ast_workflow_benchmarks.py`

Release intent:

- `fix: repair force-cpu and ast cache contracts`

### Subproject 2: Specialized GPU-reader adapter design

Separate spec/plan after Subproject 1 lands.

Scope:

- define a dedicated GPU-ingest interface instead of reusing `IOBackend`
- decide whether `KvikIOReader` should return device buffers, Arrow-compatible buffers, or an adapter object
- decide whether `DStorageReader` remains experimental-only or gains a real core-search contract
- benchmark each activation path before default routing changes

Benchmarks:

- `benchmarks/run_gpu_benchmarks.py`
- any additional focused harness needed for aligned-offset/device-buffer ingestion

Release intent:

- likely `fix:` if it only reconnects planned infrastructure
- `perf:` only if accepted measurements justify it

## Non-Goals

- do not change `Pipeline` again in the next immediate slice
- do not default-enable `KvikIOReader`
- do not default-enable `DStorageReader`
- do not claim a GPU speedup from the next slice
- do not change release workflow contracts
- do not make README or marketing claims without accepted benchmark evidence
