# Experimental features

As of 2026-10-06, this page describes optional features with narrower support or compatibility limits than core text search. They may need extra dependencies, project setup, or a specific build. Do not assume they are enabled by a basic install.

## `tg find`: experimental result search

`tg find` combines text retrieval with ranking options for finding potentially relevant files or passages. The command remains experimental, including when it uses BM25 ranking. **BM25** is a local ranking method that orders text results by how well their words match the query; it is not a generative model and does not establish that a result is correct.

Some `tg find` modes can use dense embeddings. A **dense embedding** is a numerical representation of text used to find passages with similar meaning, including when they do not share exact words. Dense mode is optional and may require installing a model and additional CPU or GPU dependencies. Follow the command's setup guidance and [GPU runbook](runbooks/gpu-troubleshooting.md); core search does not download a model automatically.

Treat ranked results as suggestions to inspect. The ranking may vary with the chosen backend, model, and corpus. `rg` remains a useful baseline for exact text search, and `tg search` has a separate, documented search contract.

## GPU execution and calibration

GPU support is opt-in and experimental. A CPU-only installation does not need GPU libraries. GPU execution requires a compatible build, device, drivers, and supported route. A GPU-related package or CI matrix entry by itself does not show that a request ran on the GPU. Inspect route evidence described in [GPU troubleshooting](runbooks/gpu-troubleshooting.md) when validating a specific request.

`tg calibrate` measures the CPU/GPU crossover for a supported GPU build. It helps the router decide which workload sizes may justify GPU execution. It does **not** create, rebuild, or warm `.tg_index`, and CPU-only builds cannot perform GPU calibration. See [routing policy](routing_policy.md) for index and backend selection.

The optional BM25-only behavior can run locally without an API key or GPU. Dense dependencies and GPU components are separate opt-ins; do not install them just to follow the [first-search guide](getting-started.md).

## Resident AST worker

The hidden `tg worker` command can keep AST project data available in a resident process for specialized repeated workflows. It is enabled with `TG_RESIDENT_AST=1` and is not started by core search. The workflow is experimental, uses local interprocess communication, and may change or be removed in a minor release. It is not a general recommendation for accelerating every scan.

See the [resident worker runbook](runbooks/resident-worker.md) for its operational details. Review [contracts](CONTRACTS.md) before relying on it in a production workflow.

## Support boundary

These features are outside the stable compatibility guarantees. They may fall back, refuse a request, or require a different dependency set from core text search. Keep a tested text-search path available, inspect structured route and completeness fields where offered, and check the [support matrix](SUPPORT_MATRIX.md) before selecting a deployment target.
