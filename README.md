<div align="center">
  <img src="docs/assets/logo.jpg" alt="tensor-grep logo" width="800"/>
</div>

# tensor-grep (tg)

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![PyPI](https://img.shields.io/pypi/v/tensor-grep)](https://pypi.org/project/tensor-grep/)
[![CI](https://github.com/oimiragieo/tensor-grep/actions/workflows/ci.yml/badge.svg)](https://github.com/oimiragieo/tensor-grep/actions/workflows/ci.yml)

**Fast text, AST, indexed, and GPU-aware search CLI.** One binary for ripgrep-compatible text search, native AST search and rewrite, indexed acceleration for repeated queries, machine-readable context for AI agents, symbol call-graph analysis, security and compliance rule packs, and an embedded MCP server.

```bash
pip install tensor-grep        # or: uvx tensor-grep

tg PATTERN [PATH]                                    # ripgrep-compatible text search
tg agent src/ "add invoice tax field" --json         # AI-agent context capsule
tg blast-radius-render src/ create_invoice           # blast-radius for a symbol
tg scan --config sgconfig.yml                        # AST structural search/rewrite
tg mcp                                               # start the built-in MCP server
```

---

## What it is

`tensor-grep` is an agent-native search and code-intelligence layer. It covers the full workflow from finding text to understanding what a change will affect: a ripgrep-compatible text engine, native AST structural search and rewrite via ast-grep rules, in-process caches and a session daemon for sub-second repeated queries, machine-readable AI-agent context capsules, symbol call-graph analysis, security/compliance rule packs with signed audit manifests, and a built-in MCP server and LSP.

It ships as a native CLI on Windows, macOS, and Linux — no server required for local use, no subprocess overhead.

---

## Features

### Text search
- **ripgrep-compatible subset** — supports the common `rg` flags (pattern, path, `-t`/`--type`, `--count-matches`, `--no-ignore`, `--sort path`, `--format rg`, `--json`, `--ndjson`). This is a validated compatible subset, not a full ripgrep replacement. Use `--format rg` for deterministic ripgrep-shaped stdout; `--format rg --json` for rg JSON Lines output.
- Root-level shortcuts: `tg PATTERN [PATH]`, `tg -t js PATTERN PATH`, `tg --count-matches PATTERN PATH` all behave as `tg search ...`.
- Chunk-parallel native CPU engine for large files.

### AST search & rewrite
- **`tg scan --config sgconfig.yml`** — run ast-grep class structural search/rewrite rules against a codebase.
- **`tg test`** — validate AST rules against fixtures.
- **`tg run`** — apply a validated AST rule slice. (`tg run` is a useful slice of ast-grep, not a full replacement.)
- **`tg new`** — scaffold a new rule, test, or project. `tg new project NAME` creates a named AST project; `tg new` initializes the current directory.

### AI-agent context
- **`tg agent PATH "query" --json`** — Actionable Context Capsule: primary files/functions, alternative targets, snippets with line maps, validation commands, rollback/checkpoint metadata, confidence, and an ask-before-editing recommendation. Mixed-language queries report `validation_alignment` instead of silently pairing mismatched targets and validators.
- **`tg map`** — machine-readable file/symbol map of a codebase.
- **`tg context PATH "query"`** — semantic context capsule for a natural-language question.
- **`tg context-render`** / **`tg edit-plan`** — rendered context and structured edit plans with daemon response caching for sub-second warm calls.

### Symbol intelligence
- **`tg defs`** — find definitions.
- **`tg source`** — show source for a symbol.
- **`tg refs`** — find references.
- **`tg callers`** — who calls a function.
- **`tg impact`** — what a symbol affects.
- **`tg blast-radius`** / **`tg blast-radius-render`** / **`tg blast-radius-plan`** — ranked impact graph with rendered and plan-ready output.

### Security & compliance
- **`tg rulesets`** — built-in security and compliance AST rule packs.
- **`tg audit`** — audit-verify / audit-history / audit-diff with signed manifest digests and semantic diff.
- **`tg classify`** — log and code classification. Default path is local deterministic heuristics; set `TENSOR_GREP_CLASSIFY_PROVIDER=cybert` to opt into the CyBERT/Triton path.
- **`tg review-bundle`** — produce enterprise review bundles.

### Edit safety & audit
- **`tg checkpoint create/list/undo`** — create, list, and undo edit checkpoints before applying rewrites.
- Signed audit manifests for every run.
- Bounded agent-loop memory: session and daemon response caches report byte usage; search and repo-context caches have environment-overridable entry caps.

### Integrations: MCP + LSP
- **`tg mcp`** — built-in MCP server. Exposes `tg_search` and related tools with `query`, `max_results`, `max_files`, and `structured_json` bounds. Machine-readable contracts in [docs/harness_api.md](docs/harness_api.md).
- **`tg lsp`** / **`tg lsp-setup`** — structural search language server for editor integration.

### Indexed / persisted acceleration
- In-process literal/string/AST/repo-context caches accelerate repeated queries.
- **`tg session`** — start a cached edit loop session.
- Daemon mode keeps caches warm across invocations; `tg context-render` and `tg edit-plan` reach sub-second latency on warm daemon calls.

### GPU routing (experimental)
- GPU support is **opt-in and experimental**. Default classification is local/deterministic unless you opt in.
- **`tg calibrate`** — benchmark local CPU vs GPU crossover for your workload.
- **`tg devices`** — list available GPU devices.
- **`--gpu-device-ids`** — select specific GPUs for a run.
- Native CUDA via `cudarc` with NVRTC JIT, CUDA streams, pinned memory, and CUDA graphs. Public managed GPU is not promotion-ready; `tg dogfood` reports `world_class_readiness.status = "not_claimed"` for GPU until public managed binaries produce verified end-to-end route/correctness proof.

### Diagnostics & ops
- **`tg doctor`** — system, GPU, cache, AST, and daemon diagnostics.
- **`tg dogfood`** — agent-readiness gate; emits structured JSON with limitation surfaces.
- **`tg upgrade`** / **`tg update`** — self-upgrade.
- **`tg repair-launcher`** — fix native vs Python launcher conflicts on Windows.

---

## Install

```bash
pip install tensor-grep
# or run without installing:
uvx tensor-grep
```

Supported on Windows, macOS, and Linux. See [docs/installation.md](docs/installation.md) for Homebrew, winget, and source build options.

---

## Quick start

```bash
# Text search — ripgrep-compatible subset
tg "TODO" src/

# Search with type filter
tg -t py "class.*Service" api/

# Deterministic ripgrep-shaped output (for automation)
tg --format rg --sort path "import" src/

# AI-agent context capsule — structured JSON for agent workflows
tg agent src/ "add invoice tax field" --json

# Blast radius for a symbol
tg blast-radius-render src/ create_invoice

# Who calls a function?
tg callers src/ authenticate_user

# AST structural search/rewrite
tg scan --config sgconfig.yml

# Start the built-in MCP server
tg mcp

# Check system and cache health
tg doctor

# Verify agent-readiness
tg dogfood
```

---

## Canonical docs

| Doc | Purpose |
|-----|---------|
| [docs/harness_api.md](docs/harness_api.md) | Machine-readable CLI and MCP contract shapes |
| [docs/harness_cookbook.md](docs/harness_cookbook.md) | End-to-end harness workflows |
| [docs/benchmarks.md](docs/benchmarks.md) | Benchmark matrix, artifact naming, regression rules |
| [docs/tool_comparison.md](docs/tool_comparison.md) | Comparison against `rg`, `git grep`, `ast-grep` |
| [docs/gpu_crossover.md](docs/gpu_crossover.md) | GPU crossover story and current limits |
| [docs/routing_policy.md](docs/routing_policy.md) | CPU/GPU/index/AST routing behavior |
| [docs/installation.md](docs/installation.md) | Supported install paths |
| [docs/EXPERIMENTAL.md](docs/EXPERIMENTAL.md) | Opt-in and hidden features outside the stable surface |
| [docs/CONTRACTS.md](docs/CONTRACTS.md) | Compatibility guarantees for configs, caches, and outputs |
| [docs/CI_PIPELINE.md](docs/CI_PIPELINE.md) | CI gates, the release pipeline, and version flow |
| [SECURITY.md](SECURITY.md) | Vulnerability reporting |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Contribution and release-intent rules |

---

## Issues & support

- [Report a bug](https://github.com/oimiragieo/tensor-grep/issues/new?template=bug_report.yml)
- [Request a feature](https://github.com/oimiragieo/tensor-grep/issues/new?template=feature_request.yml)
- [Ask a question](https://github.com/oimiragieo/tensor-grep/issues/new?template=question.yml)
- [Report a security vulnerability privately](https://github.com/oimiragieo/tensor-grep/security/advisories/new)

---

## gotcontext.ai

`tensor-grep` runs locally on your machine. [gotcontext.ai](https://gotcontext.ai) is the hosted version: an MCP gateway that uses tensor-grep for code intelligence and layers on semantic compression, Knowledge Hub RAG, and team management, so any AI tool gets compressed code context from one API key.

---

## License

Apache-2.0. See [LICENSE](LICENSE).
