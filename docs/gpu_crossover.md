# Native GPU Benchmark Requirements

GPU search is experimental and explicitly requested with `--gpu-device-ids`.
Device discovery or a successful CPU fallback does not prove GPU acceleration.
`NativeGpuBackend` identifies native execution; `GpuSidecar` identifies a Python sidecar.
`fallback_or_sidecar_counts_as_gpu_proof` must remain false.
A qualifying result must identify the installed binary, selected backend,
workload, correctness oracle, and timed phases.

```bash
uv run python benchmarks/run_gpu_native_benchmarks.py --output artifacts/bench_run_gpu_native_benchmarks.json
```

Keep file reads, host preprocessing, CPU staging, host-to-device transfer, kernel
execution, and output materialization visible. Record CUDA compilation/cache
state and end-to-end wall time. Fixed-string multi-pattern workloads and cold
single-pattern searches must be reported separately; no crossover is guaranteed.

The artifact separates `scale_gate_summary.native_cuda_runtime_gate`,
`correctness_gate`, `speed_gate`, and `promotion_ready`. Unsupported routes must
retain `gpu_proof = false`, `native_gpu_unavailable`, `gpu_evidence_status`, and
`not_gpu_proof_reason`. The `native_gpu` route must be observed rather than inferred from requested flags.
A local CUDA-feature build is not public managed-release
proof.

## Required Promotion Rule

The fair many-pattern baseline is a single `rg -F -e ... -e ...` invocation.
Sidecar-routed rows are unsupported for native CUDA promotion.

Do not promote GPU speed from device discovery, sidecar availability, or correctness alone. A promotion-ready artifact must show all of the following:

1. Native CUDA backend, not only Python/Torch sidecar rows.
2. Exact match and file-set correctness at every required 1GB and 5GB corpus against both `tg --cpu` and direct `rg --json`.
3. GPU faster than both `rg` and `tg_cpu` at the required scale and declared workload class.
4. No failed error-handling or throughput gates.
5. For public managed promotion, the dispatch-only `public-gpu-proof.yml` workflow, managed NVIDIA `tg-native-metadata.json`, `--public-managed-proof`, direct `rg --json` 1GB/5GB route/correctness, the advanced many-pattern fair-baseline proof gate, `public_managed_promotion_ready = true`, and `public_gpu_proof = true`.

Until those are true, the public routing decision is explicit GPU search only.

## Supported semantics

The native GPU backend uses a **position-parallel brute-force byte-compare** kernel
(`gpu_text_search_positions` in `rust_core/src/gpu_native.rs`): each GPU thread owns a text
position and tests every fixed-string pattern at that position, with the pattern set staged in
shared memory. It is optimized for **fixed-string multi-pattern** search over large corpora — a
workload class targeted by the implementation. (A PFAC /
failureless-Aho-Corasick automaton is a *future* optimization, not what ships today.)

There are two independent GPU-adjacent lanes in this codebase, and they do **not** share one
support matrix — a semantic unsupported in one can be supported in the other. The two tables
below are intentionally separate so every row is unambiguous about which lane it describes:

1. The **native CUDA-kernel lane**, compiled only into the CUDA-feature release build
   (`cargo build --features cuda`) and gated by `gpu_native_fallback_reason` in
   `rust_core/src/main.rs`.
2. The **Python GPU sidecar lane**, the CuDF/Torch backends in
   `src/tensor_grep/core/pipeline.py`, reachable from any build — including the standard
   public binary — whenever a Python sidecar handles the search (directly, or as the native
   binary's own redirect target when a request falls outside lane 1).

### Native CUDA-kernel lane

`gpu_native_fallback_reason(&GpuSearchParams) -> Option<&'static str>` (`rust_core/src/main.rs`)
is the single source of truth for this lane: it returns `None` when the request reaches the
native CUDA kernel unmodified, or `Some(reason)` when the request must be redirected instead.
Every `Some(reason)` case falls through `handle_gpu_search` to `handle_gpu_sidecar_search` (the
Python GPU sidecar) first; if that sidecar is itself unavailable, CPU is the final fallback (see
the [Python GPU sidecar lane](#python-gpu-sidecar-lane) below).

| Semantic | Native CUDA kernel | Redirected to | Exact `gpu_native_fallback_reason` string |
| --- | --- | --- | --- |
| Fixed-string multi-pattern (`-F -e PAT1 -e PAT2 …`), or literal patterns without `-F` that contain no regex metacharacters | Supported (position-parallel byte-compare CUDA kernel) | — | — (`None`) |
| Count / counting mode (`-c`, `--count`) | Supported (native kernel emits counts directly) | — | — (`None`) |
| Hidden-file or no-ignore overrides (`--hidden`, `--no-ignore`) | Supported (`GpuSearchParams` carries `hidden`/`no_ignore`; the native file walk honors both) | — | — (`None`) |
| Case-insensitive or smart-case matching (`-i`, `-S`) | Not supported | Python GPU sidecar | `case-insensitive searches are not yet supported by native GPU routing` |
| Binary-as-text search (`--text`) | Not supported | Python GPU sidecar | `binary-as-text searches are not yet supported by native GPU routing` |
| Patterns containing a literal newline or carriage return | Not supported | CPU or Python GPU sidecar | `line-terminator patterns require CPU or sidecar routing` |
| Invert-match (`-v`) | Not supported | Python GPU sidecar | `invert-match searches are not yet supported by native GPU routing` |
| Context-line output (`-A`, `-B`, `-C`) | Not supported | Python GPU sidecar | `context line searches are not yet supported by native GPU routing` |
| Max-count (`-m`, `--max-count`) | Not supported | Python GPU sidecar | `max-count searches are not yet supported by native GPU routing` |
| Word-boundary matching (`-w`) | Not supported | Python GPU sidecar | `word-boundary searches are not yet supported by native GPU routing` |
| Regex patterns containing metacharacters, run without `-F` (`patterns_require_regex_engine`) | Not supported | Python GPU sidecar | `regex patterns still require the Python GPU sidecar` |
| `--replace` | Not supported | Python GPU sidecar | `--replace searches are not yet supported by native GPU routing` |
| `--only-matching` (`-o`) | Not supported | Python GPU sidecar | `--only-matching searches are not yet supported by native GPU routing` |
| `--max-filesize` | Not supported | Python GPU sidecar | `--max-filesize is not yet supported by native GPU routing` |
| `--color` | Not supported | Python GPU sidecar | `--color is not yet supported by native GPU routing` |
| `--no-ignore-vcs` | Not supported | Python GPU sidecar | `--no-ignore-vcs is not yet supported by native GPU routing` |

Multiline mode (`-U`, `--multiline`, `--multiline-dotall`) and exact-line matching (`-x`,
`--line-regexp`) never reach `GpuSearchParams` at all — they are forced to plain CPU/`rg`
passthrough further upstream regardless of `--gpu-device-ids`
(`SEARCH_PYTHON_PASSTHROUGH_FLAGS` / `search_requires_ripgrep_passthrough` in
`rust_core/src/main.rs`), so they are outside the `gpu_native_fallback_reason` contract above.

When the native GPU kernel is unavailable or the request falls outside this lane, `tg` emits a
`UserWarning` with a human-readable explanation. The `fallback_reason` attribute on the
`Pipeline` object captures the same text for programmatic inspection; it is surfaced in the JSON
route envelope via the `fallback_reason` field so callers can observe it without parsing log
output.

GPU routing is **explicit and opt-in** (`--gpu-device-ids`). Heuristic auto-routing
is disabled until the public managed binary passes the promotion proof gate described
in [Required Promotion Rule](#required-promotion-rule).

### Python GPU sidecar lane

The Python-layer pipeline (`Pipeline` in `src/tensor_grep/core/pipeline.py`) has its own,
independent support matrix for the CuDF/Torch GPU backends, reachable from any build (not only
the CUDA-feature release). It governs both the native binary's Python-sidecar redirect described
above and any direct Python-pipeline invocation. Unlike the native lane, an **explicit**
`--gpu-device-ids` request outside this lane's support does **not** silently fall back to CPU:
`Pipeline` raises a `ConfigurationError` and refuses the search (the Backend Fail-Closed
Contract), because a quiet CPU-fallback result here would otherwise look like GPU acceleration
proof without being any.

| Semantic | Sidecar (CuDF / Torch) | Explicit `--gpu-device-ids` behavior |
| --- | --- | --- |
| AST search (`--ast`) | Not supported | Fails closed: `ConfigurationError` ("AST search has no GPU backend") |
| Count mode (`-c`, `--count`) | Not supported | Fails closed: `ConfigurationError` ("count (-c) search has no GPU backend") |
| Fixed-string patterns (`-F`) | Not supported | Fails closed: `ConfigurationError` ("fixed-string (-F) search has no GPU backend"); served by the StringZilla SIMD CPU backend instead |
| Context, line-regexp, word-regexp, or LTL queries (`-A`/`-B`/`-C`, `-x`, `-w`, LTL) | Not supported | Fails closed: `ConfigurationError` ("context/line-regexp/word-regexp/LTL search has no GPU backend") |
| General regex (anything not listed above) | Supported | Routes to `CuDFBackend`, falling back to `TorchBackend` if CuDF is unavailable |

Fixed-string patterns (`-F`) in the Python pipeline are always served by the StringZilla SIMD
backend regardless of `--gpu-device-ids`; the fixed-string CUDA-kernel semantics in the
native-lane table above apply only to the CUDA-feature native route, never to this sidecar lane.
