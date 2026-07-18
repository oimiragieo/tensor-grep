# Native GPU Crossover Benchmark

## Current post-`v1.81.14` GPU dogfood Read

The post-`v1.81.14` dogfood keeps public GPU not promotion-ready. Single-pattern cold grep is still not a promotion story, and public managed many-pattern search is also not credible versus a single-invocation fair `rg -F -e ... -e ...` multi-pattern search for the declared workload class.

- Native CUDA release search passes 1GB and 5GB correctness on both RTX 4070 (`sm_89`) and RTX 5070 (`sm_120`).
- There is still no crossover for single-pattern literal search: GPU remains slower than `rg` and `tg_cpu` after CUDA startup, file I/O, H2D transfer, and output materialization are counted.
- Earlier local CUDA-native work measured a many fixed pattern win over sequential `rg`, but sequential `rg` is not the fair public baseline.
- The fair baseline is `rg -F -e ... -e ...`. In the `v1.11.5` public managed dogfood, 100 fixed no-match patterns over 1GB were `rg` multi-pattern: `0.169s`, `tg` CPU multi-pattern: `0.394s`, and `tg --gpu-device-ids 0`: `0.448s` via `NativeCpuBackend` CPU fallback. The mixed 100-pattern row was `rg` mixed multi-pattern: `0.105s` versus `tg` CPU mixed multi-pattern: `2.220s`, with the GPU-requested row also falling back to `NativeCpuBackend` (`2.211s`).
- Python GPU scale rows are unsupported for native CUDA promotion when they route through the Python/Torch sidecar instead of a CUDA-enabled native `tg` binary; sidecar-routed rows are unsupported for native CUDA promotion.
- The public managed binary currently reports GPU requests through `GpuSidecar`, not `NativeGpuBackend`; `NativeGpuBackend` rows in this document refer to a local CUDA-feature release build. That is not public GPU readiness until matching CUDA-native assets are shipped and verified.
- Native CUDA correctness and the local high-intensity multi-pattern lane remain implementation evidence, but GPU remains explicit/opt-in until public managed binaries produce qualifying `NativeGpuBackend`, `sidecar_used = false`, declared workload class, correctness, and speed artifacts.
- Public promotion additionally requires managed NVIDIA release provenance: the installed front door must include `tg-native-metadata.json`, and `benchmarks/run_gpu_native_benchmarks.py --public-managed-proof` must emit `public_managed_promotion_ready = true` and `public_gpu_proof = true`.
- Public managed proof must compare route/correctness directly with `rg --json`, not only with `tg --cpu`. Required 1GB and 5GB scale rows must pass match identity, file-set identity, `NativeGpuBackend`, and `sidecar_used = false`; public speed proof comes from the advanced many fixed-string proof gate against the fair single-invocation `rg -F -e ... -e ...` baseline.
- Current GPU artifacts expose `promotion_evidence_contract`, `fallback_or_sidecar_counts_as_gpu_proof`, `requires_independent_oracle`, `promotion_blockers`, `gpu_evidence_status`, `gpu_proof`, `native_gpu_unavailable`, `not_gpu_proof_reason`, and top-level `gpu_proof_summary` so sidecar routing, CPU fallback, missing correctness, missing speed proof, or failed public managed NVIDIA proof is machine-readable instead of buried in prose.

Native CUDA correctness passed locally, but public managed speed/promotion failed remains the current promotion summary. The public managed binary routes GPU requests through `GpuSidecar`, not `NativeGpuBackend`; that is sidecar/CPU fallback, not production GPU acceleration.

Current benchmark taxonomy:

| Surface | Meaning | Promotion status |
| --- | --- | --- |
| Python GPU scale (`run_gpu_benchmarks.py`) | Measures Python/Torch sidecar behavior and device availability. | Unsupported for native CUDA promotion unless `scale_gate_summary.native_cuda_scale_gate.status = SUPPORTED`. |
| Native CUDA scale (`run_gpu_native_benchmarks.py`) | Measures release-native `tg --gpu-device-ids ...` correctness and speed against `rg` and `tg_cpu`. | Requires 1GB and 5GB correctness plus a speed win over both baselines. |

Current native evidence:

| Workload | Device / route | Evidence | Read |
| --- | --- | ---: | --- |
| Single no-match fixed string, 1GB | RTX 4070 local CUDA native | `rg = 73.838ms`, `tg GPU = 1093.778ms` | no crossover |
| Three real fixed strings, 1GB | RTX 4070 local CUDA native stats | `rg = 277.661ms`, `tg GPU = 2398.238ms` | no crossover after output materialization |
| 100 no-match fixed strings, 1GB | Public managed `tg search -F --gpu-device-ids 0 --json -e ...` | `rg -F -e ... = 0.169s`, `tg CPU = 0.394s`, GPU request fell back to `NativeCpuBackend` at `0.448s` | not promotion-ready |
| 100 mixed fixed strings with 2665 emitted matches, 1GB | Public managed `tg search -F --gpu-device-ids 0 --json -e ...` | `rg -F -e ... = 0.105s`, `tg CPU = 2.220s`, GPU request fell back to `NativeCpuBackend` at `2.211s` | not promotion-ready |
| Prior 5GB single-pattern scale | RTX 4070 / RTX 5070 local CUDA native | `35.46x` / `29.91x` slower than `rg` in latest `v1.9.11` dogfood read | superseded as a single-pattern caution |

The latest user dogfood also reported the native harness as `passed = false` because the speed target and error-test expectations did not pass. That is the intended decision: correctness evidence is necessary, but it is not enough to enable or market GPU auto-routing.

## 2026-07-14 GPU Phase-0 Hardening Wave (Audit #171, v1.75.1-v1.75.4)

Four PRs closed audit #171's P0-1 through P0-5 findings against the GPU doctor/agent probe surface and
the installer/calibrate remediation paths, each gated by the mandatory Opus adversarial review (SHIP /
SHIP-WITH-NIT verdicts, 8/8 probes clean). This wave hardens *evidence quality and operator messaging*
around the existing GPU surface; it does not change the promotion decision above -- native CUDA
correctness remains locally proven, and public managed promotion remains unmet.

- `#594` (v1.75.1): fixed a WSL path-domain mismatch in the doctor and agent GPU probes. On WSL,
  `resolve_native_tg_binary()` can return a Windows-target binary; both probes were writing a GPU-route
  sentinel under a Linux `TemporaryDirectory` and passing that `/tmp/...` path as argv to a Windows PE,
  which cannot resolve it, so a path-domain mismatch previously read as "no GPU support" rather than its
  real cause. Fixed by detecting genuine WSL cross-domain execution, translating the sentinel path via
  `wslpath -w`, and failing closed to a distinct `path_domain_mismatch` status when translation is
  unavailable. Also added a `cargo check --features cuda` anti-bit-rot CI gate (runs on every PR, no CUDA
  toolkit required) so the `cuda` Cargo feature -- normally compiled only by release legs gated on the
  `TENSOR_GREP_RELEASE_NATIVE_ASSET_PROFILE` repository variable equalling `native-frontdoor-gpu` --
  cannot rot silently between releases.
- `#595` (v1.75.2): replaced the doctor's opaque GPU-probe `status="failed"` (which collapsed every
  nonzero exit code from the native binary into one undifferentiated status) with a structured
  `native_error_kind` taxonomy (`failed_path_bridging` / `failed_input` / `failed_gpu_unavailable` /
  `failed_other`), and added an honest pre-flight warning when a requested `--gpu-device-ids` value is
  out of range for the local device inventory instead of an indistinguishable silent CPU fallback.
- `#596` (v1.75.3): added a `calibrate` remediation message on both native bail arms, plus a loud
  nvidia-requested/cpu-delivered installer downgrade warning (guarded so it never fires on a host with no
  NVIDIA candidate at all, which would be a false claim rather than a true one).
- `#597` (v1.75.4): closed 5 gate-nits from the Opus review of the prior three PRs, including re-gating
  `GpuRouteFailureKind` / `sanitize_cuda_detail` / `classify_gpu_route_failure` in `rust_core/src/main.rs`
  from `#[cfg(feature = "cuda")]` to `#[cfg(any(feature = "cuda", test))]` so a default `cargo test` (no
  `--features cuda`) actually compiles and runs their unit tests instead of silently skipping them -- see
  `AGENTS.md`'s CI/Release Rules section for this pattern generalized as a standing rule.

Separately, `#593` (v1.75.0) shipped an unrelated `tg orient` / `tg agent` improvement (broadened
`suggested_ignore` vendor/skill-tree detection) that landed in the same version range by coincidence of
publish order; it is not part of this GPU wave and is not summarized here.

## 2026-06-29 Wave-2 Promotion-Schema Audit

A structured audit of the promotion-gate schema and `public-gpu-proof.yml` workflow was performed as part of the wave-2 hardening cycle.

**Audit findings (all conforming):**

- All promotion fields (`public_gpu_proof`, `promotion_evidence`, `public_managed_promotion_ready`, `gpu_proof`, `gpu_evidence_status`) default to `false` / `EXPERIMENTAL` / `UNSUPPORTED` on every missing-hardware path.  The `build_public_managed_gpu_proof_gate(requested=False)` path returns `public_gpu_proof = false`; `_gpu_proof_status_from_native_summary({})` returns `gpu_proof = false, gpu_evidence_status = "unsupported", native_gpu_unavailable = true`.  No path silently promotes.
- `public-gpu-proof.yml` correctly gates on the Python script exit code (via `set -euo pipefail`).  The script exits `1` when `public_managed_gpu_proof_gate.status != "PASS"`, so the workflow fails when proof is absent.  Artifacts are uploaded with `if: always()` so failed runs still produce inspectable output.
- `FAIR_RG_MULTI_PATTERN_BASELINE = "rg -F -e ... -e ..."` (single-invocation multi-pattern) already exists in `run_gpu_benchmarks.py` and is wired into `_promotion_evidence_contract` and `build_many_pattern_proof_gate`.  The fair-bench does not need to be rebuilt.
- The `_promotion_evidence_contract` schema was extended with `requires_independent_oracle: True` (wave-2 addition).  The C1 agent will wire `oracle_status` into the `correctness_gate` output once the independent CPU oracle is implemented; this field makes the requirement machine-readable in the contract before that ships.

**Current promotion status (as of 2026-06-29):** unchanged from the read above.  The public managed binary still routes GPU requests through `GpuSidecar` / `NativeCpuBackend`, not `NativeGpuBackend`.  Sidecar and CPU fallback are not GPU acceleration proof.  No public managed `public_gpu_proof = true` artifact exists.  GPU remains EXPERIMENTAL / explicit-opt-in only.

## 2026-05-11 Route And CPU-Staging Audit

The latest local route audit found that the public managed Windows front door is not a clean native CUDA timing source for `--gpu-device-ids`: a direct JSON probe reports `routing_backend = "GpuSidecar"` and `sidecar_used = true`. An in-tree debug binary without the CUDA feature also falls through the Python sidecar and can time out there. Treat any artifact without explicit `NativeGpuBackend` / `sidecar_used = false` route metadata as sidecar-contaminated and unsupported for native CUDA speed proof. Benchmark rows and JSON envelopes should say this directly through `gpu_evidence_status`, `gpu_proof = false`, `native_gpu_unavailable`, and `not_gpu_proof_reason`.

The accepted remediation is now threefold:

1. The native GPU benchmark must probe the runtime backend before timing GPU rows and must not time or promote sidecar-routed rows.
2. The CUDA ingest path must make CPU staging measurable. Native JSON/verbose output now exposes host file-read time, host preprocess time, host-to-pinned copy time, CPU staging bytes, pageable-host staging bytes, H2D transfer time, kernel time, and wall time.
3. GPU performance claims must name the workload class. Single-pattern cold grep is not the win; many fixed-string patterns over a large corpus is the current CUDA crossover lane.

The native ingest implementation now applies the same data-movement principles used by CUDA and RAPIDS guidance:

- read file chunks into reusable pageable staging memory, run CPU-side binary and line classification there, then copy accepted text into pinned host buffers for DMA. This avoids CPU preprocessing over pinned pages while preserving fast H2D transfer;
- cache NVRTC-generated PTX on disk by architecture and kernel hash so repeated CUDA CLI invocations do not pay the full compile cost;
- reuse line descriptors collected during CUDA dispatch setup when materializing output so matched lines are not discovered by rescanning every file buffer;
- keep chunking explicit so H2D transfer and kernel work can overlap through existing streams and double buffering;
- keep sidecar, CPU fallback, H2D transfer, and kernel execution visible as separate metrics instead of collapsing them into a single "GPU" timing;
- reserve future GPUDirect Storage work for platforms where direct storage-to-GPU DMA is available, because that is the correct next step to remove the remaining host I/O bounce;
- reserve NVLink/P2P work for multi-GPU systems whose topology actually supports peer access, instead of assuming PCIe-attached developer GPUs have that path.

Local CUDA-feature release measurements on 2026-05-12 show the host-tail improvement clearly. On the 1GB corpus, host preprocessing dropped from about `15195.926ms` to `71.510ms`; on the 5GB corpus it dropped from about `77161.298ms` to `359.224ms`. A warm PTX cache reduced an isolated 100MB native CUDA CLI run from about `1149.117ms` cold to `672.116ms` warm. These are implementation evidence for the local CUDA-native route, not proof that the public `v1.10.8` managed binary should be promoted.

Agent workflow GPU use follows the same rule. `tg agent --gpu-device-ids ... --json` may run a batched fixed-string evidence scan through the selected native GPU route, records the result in `gpu_acceleration`, and only marks the evidence as used when the route reports `NativeGpuBackend` with `sidecar_used = false`. Sidecar-routed output remains unsupported compatibility evidence and does not change the no-crossover positioning.

Reference principles:

- CUDA Best Practices, host/device transfer guidance: <https://docs.nvidia.com/cuda/cuda-c-best-practices-guide/index.html#data-transfer-between-host-and-device>
- CUDA Programming Guide, peer-to-peer memory access: <https://docs.nvidia.com/cuda/cuda-c-programming-guide/index.html#peer-to-peer-memory-access>
- GPUDirect Storage cuFile API guidance: <https://docs.nvidia.com/gpudirect-storage/api-reference-guide/index.html>
- RAPIDS cuDF CSV chunking/byte-range API: <https://docs.rapids.ai/api/cudf/stable/user_guide/api_docs/api/cudf.read_csv/>

## Required Promotion Rule

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
shared memory. It is optimized for **fixed-string multi-pattern** search over large corpora — the
only workload class where GPU can produce a credible speed win over `rg`. (A PFAC /
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

## Historical v1.7 Artifact (Superseded)

Earlier `v1.7.0` native GPU crossover work used:

```powershell
uv run python benchmarks/run_gpu_native_benchmarks.py --output artifacts/bench_run_gpu_native_benchmarks_post_v170_audit.json
```

That artifact covered device `0` (`NVIDIA GeForce RTX 4070`, `sm_89`) on `10MB`, `100MB`, `500MB`, and `1GB` synthetic log corpora. It found no crossover: GPU completed the small rows slower than `rg` and timed out on larger rows. Device `1` (`NVIDIA GeForce RTX 5070`, `sm_120`) was detected but blocked by the then-current CUDA/PyTorch sidecar stack.

Historical per-size data:

| Corpus size | `rg` median | `tg --cpu` median | `tg --gpu-device-ids 0` median | GPU/rg ratio | Result |
| --- | ---: | ---: | ---: | ---: | --- |
| 10MB | 0.104s | 0.113s | 0.409s | 3.9499x | no crossover |
| 100MB | 0.110s | 0.116s | 1.033s | 9.4159x | no crossover |
| 500MB | 0.126s | 0.131s | timeout | n/a | FAIL |
| 1GB | 0.144s | 0.150s | timeout | n/a | FAIL |

The historical artifact remains useful optimization history, but the decision above is the current contract.
