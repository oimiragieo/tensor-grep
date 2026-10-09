# Experimental features

As of 2026-10-06, this page describes optional features with narrower support or compatibility limits than core text search. They may need extra dependencies, project setup, or a specific build. Do not assume they are enabled by a basic install.

## `tg find`: experimental result search

`tg find` combines text retrieval with ranking options for finding potentially relevant files or passages. The command remains experimental, including when it uses BM25 ranking. **BM25** is a local ranking method that orders text results by how well their words match the query; it is not a generative model and does not establish that a result is correct.

Some `tg find` modes can use dense embeddings. A **dense embedding** is a numerical representation of text used to find passages with similar meaning, including when they do not share exact words. Dense mode is optional; without its dependencies, `tg find` reports that it is using BM25 only. To try dense matching, explicitly run:

```text
tg install-dense
```

This installs the `semantic` Python extra and fetches a checksum-pinned model of about 65 MB. The dense path uses CPU and NumPy; it does not require Torch or a GPU. The command needs network access, and nothing downloads automatically when you run `tg find`. For GPU-specific requirements, see the [GPU runbook](runbooks/gpu-troubleshooting.md).

Treat ranked results as suggestions to inspect. The ranking may vary with the chosen backend, model, and corpus. `rg` remains a useful baseline for exact text search, and `tg search` has a separate, documented search contract.

### Native cross-encoder (opt-in)

`tg install-dense --reranker` additionally installs the revision-pinned
`cross-encoder/ms-marco-MiniLM-L6-v2` ONNX model, tokenizer, and checksummed ONNX
Runtime 1.24.4 CPU libraries. Supported runtime assets cover Windows x64/ARM64,
Linux x64/ARM64 (glibc 2.27+), and macOS ARM64 (macOS 14+). This is an explicit
download of approximately 110 MB in addition to the dense model; queries never
download assets. `TG_CROSS_ENCODER_DIR` selects the local asset directory.
Windows requires the system Visual C++ runtime. Native loading restricts runtime
dependencies to System32 and refuses a missing system dependency rather than
searching the repository or PATH for DLLs.

New asset installations publish a verified staging directory atomically and
refuse to replace any destination entry created during installation, including
an empty directory. Publication uses Linux `renameat2(RENAME_NOREPLACE)`, macOS
`renamex_np(RENAME_EXCL)`, or Windows rename semantics. If the platform primitive
or filesystem support is unavailable, installation fails and cleans up its
private staging directory. Linux installation needs the libc `renameat2` symbol,
[introduced in glibc 2.28](https://man7.org/linux/man-pages/man2/rename.2.html).
An older libc can therefore refuse a new installation even though the CPU
runtime supports glibc 2.27; already-installed verified assets can still be used
when their runtime requirements are satisfied.

`tg find QUERY PATH --rerank cross-encoder` reorders the existing first twenty
results using bounded pair tokenization (256 tokens) and a serialized reusable
Rust ONNX session over the already-collected full chunk context. Reciprocal rank
fusion retains retrieval evidence alongside model scores: equal weights for lexical
retrieval, and a 16:1 retrieval prior when dense retrieval is active. The latter
allows only adjacent disagreements within the twenty-candidate bound.
It keeps candidate membership and stable ties. Python
orchestrates retrieval; `rank_fusion.cross_encoder.scoring: Rust-ONNX-CPU`
identifies native model scoring. `tg agent --rerank cross-encoder` orders existing
context snippets as advisory evidence and preserves target selection and line maps.

The default is `--rerank off`. `auto` discloses missing assets and keeps the
existing order; `cross-encoder` refuses missing assets. Corrupt assets and
inference failures are execution errors in both modes. A generic relevance model
does not establish code correctness or API compatibility; latency and retrieval
quality depend on the candidate texts and corpus.

## Focused context excerpts

Use `tg context-render src --query "invoice validation" --render-profile focused --json`
to request shorter excerpts from selected declarations. The profile retains signatures,
docstrings, and matching complete statements, and labels elided ranges. Selection uses
query terms and AST boundaries; it does not trace every dependency needed to edit the code.
Use the returned full-source read before making changes. Without a supported grammar or
a useful body match, the profile returns the full source with an explicit fallback reason.
Savings depend on the query and source; no fixed reduction is guaranteed. See the
[response contract](harness_api.md) for supported languages and line-map behavior.

## GPU execution and calibration

GPU support is opt-in and experimental. A CPU-only installation does not need GPU libraries. GPU execution requires a compatible build, device, drivers, and supported route. A GPU-related package or CI matrix entry by itself does not show that a request ran on the GPU. Inspect route evidence described in [GPU troubleshooting](runbooks/gpu-troubleshooting.md) when validating a specific request.

`tg calibrate` measures the CPU/GPU crossover for a supported GPU build. It helps the router decide which workload sizes may justify GPU execution. It does **not** create, rebuild, or warm `.tg_index`, and CPU-only builds cannot perform GPU calibration. See [routing policy](routing_policy.md) for index and backend selection.

The optional BM25-only behavior can run locally without an API key or GPU. Dense dependencies and GPU components are separate opt-ins; do not install them just to follow the [first-search guide](getting-started.md).

## Resident AST worker

The hidden `tg worker` command can keep AST project data available in a resident process for specialized repeated workflows. It is enabled with `TG_RESIDENT_AST=1` and is not started by core search. The workflow is experimental, uses local interprocess communication, and may change or be removed in a minor release. It is not a general recommendation for accelerating every scan.

See the [resident worker runbook](runbooks/resident-worker.md) for its operational details. Review [contracts](CONTRACTS.md) before relying on it in a production workflow.

## Support boundary

These features are outside the stable compatibility guarantees. They may fall back, refuse a request, or require a different dependency set from core text search. Keep a tested text-search path available, inspect structured route and completeness fields where offered, and check the [support matrix](SUPPORT_MATRIX.md) before selecting a deployment target.

## Runtime switches for testing and diagnosis

These environment variables change routing behavior and are intended for testing or diagnosis. Leave them unset for ordinary use unless you are following a specific troubleshooting procedure:

- `TG_FORCE_CPU=1` requests CPU routing for search commands; it does not enable GPU execution.
- `TG_RUST_EARLY_POSITIONAL_RG=1` enables an early `rg` passthrough attempt for eligible bare positional searches. It has an effect only when `rg` is available and the invocation fits the supported passthrough surface.
- `TG_RESIDENT_AST=1` enables the experimental resident AST worker workflow described above; it does not start a worker by itself.
