---
name: tensor-grep-architecture-contract
description: Use when you need the load-bearing design of tensor-grep and WHY it holds before touching cli/bootstrap.py, rust_core/src/main.rs, backends/, routing, the agent capsule, or before reviewing/planning any change to the front door, command/flag registration, or backend contract. Explains the bootstrap intercept-before-Typer front door, native-vs-Python routing, the 4 command + 2 flag registration sites, the Backend Fail-Closed Contract, the agent-context moat, the invariants that must hold, and the known-weak points (flat no-IDF scorer, GPU not viable, rg parity gap, FFI not the dir-scan speed path). Read this to build the right mental model; use sibling skills for the how-to of changing, debugging, or benchmarking.
---

# tensor-grep architecture contract

**What this is.** A ground-truthed map of tensor-grep's load-bearing design: the invariants a change must not break, and the weak points you must not oversell. Read it to understand *why* the code is shaped this way before you touch it. It is not a how-to — for that, hand off to a sibling (routing table below).

**What tensor-grep is** (as of 2026-07-02, v1.17.25, `pyproject.toml:322`): a code-intelligence CLI named `tg`. A Rust core (`rust_core/` — both a PyO3 extension *and* a standalone `tg` binary) plus a Python CLI (`src/tensor_grep/`). Apache-2.0. Ships to PyPI (package `tensor-grep`), npm, Homebrew, winget. CONTRIBUTING.md calls it a "benchmark-governed, contract-heavy codebase" — that is the whole point: the contracts below are enforced by tests and a CI gate, not by convention.

## When to use this skill vs a sibling

| You are about to… | Use |
|---|---|
| Understand *why* the front door / routing / backend contract exists (this skill) | **you are here** |
| Add/rename a command or a search flag; ship a change safely | `tensor-grep-change-control` |
| Debug a live misroute, hang, wrong-result, or "no matches that should match" | `tensor-grep-debugging-playbook` |
| Study a settled past failure so you don't re-fight it | `tensor-grep-failure-archaeology` |
| Use `tg` as a *user* (search/orient/callers/agent flags) | `code-search-and-retrieval-reference`, or the `.claude/skills/tensor-grep/` usage skill |
| Set/override config or env axes | `tensor-grep-config-and-flags` |
| Build the Rust ext / set up the toolchain | `tensor-grep-build-and-env` |
| Run diagnostics (`doctor`, `dogfood`, readiness) | `tensor-grep-diagnostics-and-tooling` |
| Make or defend a speed/quality claim with numbers | `tensor-grep-benchmark-and-proof-toolkit` |
| Position the product / write release notes | `tensor-grep-release-and-positioning` |

**Do not use this skill to authorize a change.** It explains the design; it does not route around `tensor-grep-change-control` or the project's PR/council/dogfood discipline. Any code change still goes through change-control.

## Jargon (defined once)

- **Front door / bootstrap** — the process entry point `tensor_grep.cli.bootstrap:main_entry` that sees raw `argv` *before* the Typer app.
- **Typer app** — the Python click/Typer CLI in `src/tensor_grep/cli/main.py` (`@app.command` functions). It is the *inner* CLI, not the front door.
- **Native binary / native front door** — the standalone Rust `tg` binary built from `rust_core/`. Fast path for search routing.
- **Sidecar** — Python doing work the native binary bounces to it (`TG_SIDECAR_PYTHON`).
- **CliRunner** — Typer's in-process test harness. It calls the Typer app directly and **bypasses the bootstrap front door** — the single most important test-coverage caveat in this repo.
- **rg** — ripgrep. **ast-grep** — structural (AST) search. Both are baselines tg is measured against, not beaten.
- **Capsule** — the Actionable Context Capsule emitted by `tg agent` (`capsule_version = 1`).

## The front door: intercept before Typer

`tg` is not "a Typer app." The published entry point is `bootstrap.main_entry` (`src/tensor_grep/cli/bootstrap.py:820`). It parses `argv` itself and, for a **plain text search**, forwards to the native `tg` binary or to ripgrep *before Typer ever runs* (`bootstrap.py:852-910`). The Typer app is only reached for TG-only flags, help, or commands that require full CLI (`_requires_full_cli`, `bootstrap.py:296`).

Why this matters, concretely:

- **CliRunner cannot see routing bugs.** It invokes the Typer app directly, so any bug in `bootstrap` routing (a flag that leaks to `rg`, a fork-bomb delegation loop, a wrong native/Python choice) is **invisible** to CliRunner tests and green in CI while broken for real users. This is exactly how the `--rank` plain-text crash shipped (AGENTS.md "Dogfood the Real Binary, Not CliRunner", `bootstrap.py:200` history). **Rule: verify front-door behavior against the REAL published binary** via `scripts/dogfood/` (Dockerfile + `dogfood_features.py`), never CliRunner alone.
- **Two mutual-delegation fork-bomb hazards are guarded, not theoretical.** `TG_REEXEC_GUARD` (`bootstrap.py:872`) stops native→python→native search loops; `_json_aggregate_blocks_passthrough` (`bootstrap.py:359`) stops `--json` + a render-only flag (e.g. `-b`) from deadlocking the native front door; `_run_requires_ast_workflow` (`bootstrap.py:813`) keeps `tg run --selector/--strictness/--stdin/--globs` in Python so it does not ping-pong. If you touch delegation, you can re-arm a fork bomb — see `tensor-grep-failure-archaeology`.

## Native-vs-Python routing (the decision tree)

Search routing is a single shared decision in `rust_core/src/routing.rs::route_search(...)` (documented in `docs/routing_policy.md`). It returns a `RoutingDecision` carrying `selection`, `routing_backend`, `routing_reason`, `sidecar_used`, `allow_rg_fallback`. Priority order (routing_policy.md §"Unified `tg search` decision tree"):

1. `--index` → `TrigramIndex` (highest override)
2. `--gpu-device-ids` → `NativeGpuBackend` (overrides warm-index + size routing; **must fail loud if unhonorable**)
3. `--force-cpu`/`--cpu` with structured output or no usable `rg` → `NativeCpuBackend`
4. AST command → `AstBackend`
5. Warm non-stale compatible `.tg_index` → `TrigramIndex`
6. corpus > calibrated threshold **and** GPU available **and** calibration positive → `NativeGpuBackend`
7. else, `rg` available and no structured output → `RipgrepBackend`
8. else → `NativeCpuBackend`
9. native CPU route fails and `allow_rg_fallback` → `RipgrepBackend` final fallback

Load-bearing consequences:

- **`rg` is the normal cold-path backend when installed.** Native CPU is the default *only* for structured output (`--json`/`--ndjson`), explicit `--cpu`, warm index, AST, and GPU fallback. Do not "optimize" tg to beat rg on cold text — that is the parity tier (see Known-Weak §3).
- **Warm-index auto-routing is gated:** pattern ≥ 3 bytes, no `-v`, `-C`, `--max-count`, `-w`, `-g`, and the cache must exist + be non-stale + index-compatible (routing_policy.md notes). JSON/NDJSON no longer bypass a warm index.
- **Auto-GPU is conservative and effectively dormant** when rg is installed: no fresh positive calibration ⇒ stay CPU-side. GPU CPU-fallback emits `routing_gpu_device_ids = []` and must be called *CPU fallback*, never GPU acceleration (routing_policy.md §GPU).
- **AST routing is policy vs runtime-capability split:** `tg run` policy-routes to `AstBackend`, but real execution needs `AstBackend().is_available()`, which requires `torch_geometric` **and** `tree_sitter` **and a CUDA device** — `is_available()` returns `bool(torch.cuda.is_available())` (`ast_backend.py:492-504`). So on the common **non-GPU** box the native AST path never runs; `tg run` always falls back to the `ast-grep` CLI sidecar (also for string metavar queries like `def $F($$$ARGS)`) — visibly, per the fail-closed contract below. This matches `code-search-and-retrieval-reference` §2.

## The registration sites (miss one → silent misroute)

This is a **universal bug class**: "register in N places, miss one, fail *quietly*." The CI registration-completeness gate has been **BLOCKING since v1.17.1 / #282** (AGENTS.md:196), but you still author all sites by hand.

**A new top-level `tg COMMAND` needs four sites** (AGENTS.md:178-185):

| # | Site | File |
|---|---|---|
| 1 | `KNOWN_COMMANDS` set | `src/tensor_grep/cli/commands.py:9` |
| 2 | `Commands::X` variant + dispatch arm | `rust_core/src/main.rs:838` (enum) |
| 3 | `PUBLIC_TOP_LEVEL_COMMANDS` (parity test) | `tests/e2e/test_routing_parity.py:17` |
| 4 | `@app.command` function | `src/tensor_grep/cli/main.py` |

**A new search flag needs two front doors** (AGENTS.md:187-190) or it leaks to ripgrep and crashes with `rg: unrecognized flag` for anyone on the published binary:

| # | Site | File |
|---|---|---|
| 1 | `SEARCH_PYTHON_PASSTHROUGH_FLAGS` (native allowlist) | `rust_core/src/main.rs:160` |
| 2 | `bootstrap._TG_ONLY_SEARCH_FLAGS` (Python front-door allowlist) | `src/tensor_grep/cli/bootstrap.py:23` |

**Blind spot to internalize:** `tg callers <fn>` finds *callable* registration sites in ~1s, but the call graph **cannot see set/list/decorator registrations** — `_TG_ONLY_SEARCH_FLAGS` is a set, `@app.command` is a decorator, the Rust dispatch is a match arm. Those are the sites most often missed (`--rank` lived in a *set*). So `tg callers` for the reachable ones **and** grep / `tg scan` for the declarative ones, then confirm your entry appears in *all* sites. (The actual add-a-thing procedure lives in `tensor-grep-change-control`; this skill only explains why the sites exist.)

## Backend Fail-Closed Contract

The single most important correctness invariant. `src/tensor_grep/backends/base.py` defines it: every `ComputeBackend` **MUST raise `BackendExecutionError` on a real failure** — never return a clean empty / `0-match` `SearchResult`, and never silently swap to an engine that cannot preserve the requested semantics.

Why a context tool cannot afford to violate it: a swallowed backend failure reaches a coding agent as a trustworthy "no matches." That is the one lie a search tool must never tell — the agent then edits on the belief that the symbol does not exist.

Rules when a path *can* fall back (AGENTS.md "Backend Fail-Closed Contract", `backends/base.py:7`):

- **Fail closed** for any flag/contract the fallback cannot preserve. `--pcre2` through a non-PCRE2 engine ⇒ raise, do not swap (that produces *wrong results*, not just slower ones).
- **A legitimate degraded fallback must be VISIBLE:** set `fallback_reason` (and a distinct `routing_reason`) on the result so JSON/CLI consumers can tell degraded output from real output. Never label heuristic output as model output.
- **Validate an untrusted response shape before indexing** (e.g. a model's class count vs a fixed label list) so a mismatch degrades gracefully instead of raising an `IndexError` a broad `except` then swallows.

**The recurring anti-pattern:** a bare `except Exception:` that returns empty or falls through to a different engine. This has been fixed *repeatedly* across audits — the Rust/PCRE2 bridge, the ast-grep OOM mask, the tree-sitter query swallow, CyBERT classify. When you review/write any backend or router that can change engines, this is the first thing to check. The structural fix (a `SafeBackendMixin` + a fault-injection conformance CI gate) is planned but **not yet shipped**, so the discipline is still per-file. The same rule extends to routers: an explicit `--gpu` request silently routed to CPU must raise/emit a diagnostic, not swap silently.

## The moat: agent-native context, not faster grep

Positioning is a design constraint, not marketing. **tg is not a faster grep.** ripgrep is the raw-text parity baseline; ast-grep is the structural-search baseline. The moat is the **agent-native code-intelligence layer**: `orient`, `callers`, `blast-radius`, `defs`, `refs`, `source`, `agent` (the capsule), `session`. Peers to know: Aider repo-map (tree-sitter + NetworkX PageRank, `--map-tokens`), Sourcegraph Cody (SCIP + BM25 + embeddings → rerank), Cursor (index-first embeddings + Merkle change detection).

Engineering-capacity consequence (AGENTS.md "Roadmap Sequencing 2026-07-02"): CPU-only, every-install moat work is funded *first* — local hybrid semantic search (BM25 + CPU dense embeddings + RRF, no API key), `tg registration-check` as a first-class command, a Bloom-filter n-gram chunk prefilter — before advancing the GPU program. Never make a change that implies "tg beats rg for cold exact-text search."

## Invariants that must hold (agent contract)

These are enforced by the capsule/context contract (`docs/CONTRACTS.md` §3, "Context and edit-planning contracts") and the agent-readiness gate. A change that breaks one is a contract regression even if tests are green.

- **`context_consistency`** — `edit_plan_seed.primary_file`, `navigation_pack.primary_target.file`, the rendered source sections, and follow-up read commands must not contradict each other. The payload reports whether the primary file is included, whether rendered context matches the target, whether confidence was downgraded, and why a primary file was omitted (CONTRACTS.md:89-90).
- **Ambiguity hard-stop** — when equal-confidence alternatives are unresolved, cap `confidence.overall` and `primary_target.confidence` below the edit threshold, set `ask_user_before_editing.required = true`, and mirror it in top-level `ambiguity.status = "tie_requires_confirmation"`. A validation-resolved tie records `ambiguity.status = "tie_resolved"`, `resolved_by = "targeted-validation"` with concrete `resolution_evidence`; an LSP-resolved tie needs explicit provider-response proof (CONTRACTS.md:102). This is the safety floor added in #302 — do not weaken it.
- **Validation provenance** — validation hints use `validation_plan[].detection ∈ {detected, heuristic, generic}` and must align with the primary target language: a TS target must not silently get pytest, a Python target must not silently get `npm test`; `validation_alignment` records filtering. JS commands require `package.json` evidence, Python commands require test/marker/layout evidence, and commands are omitted entirely when no runner evidence exists — **never invented** (CONTRACTS.md:92-93).
- **Evidence labeling** — routing/claim evidence is labeled `parser-backed | rg-backed | graph-derived | heuristic | LSP-confirmed | stale/uncertain`; when signals disagree, downgrade confidence and surface the contradiction rather than hiding it behind one ranked file (CONTRACTS.md:103). LSP availability is **not** semantic proof: a row counts as `lsp_proof` only with an explicit `lsp_provider_response = true` (CONTRACTS.md:104).

## Known-weak points (state plainly, never oversell)

Encode these honestly; the dogfood report itself emits `world_class_readiness.status = "not_claimed"` (CONTRACTS.md:85). Everything unproven stays labeled candidate/experimental. Experimental, default-OFF: GPU, LSP, semantic, CyBERT/provider paths.

1. **Flat, no-IDF ranking scorer.** Repo-map scoring uses flat integer term counts (`_score_text_terms`/`_score_file_path`/`_score_symbol` in `src/tensor_grep/cli/repo_map.py:4781+`), not IDF/term-rarity weighting. Ranking surfaces (`search --rank`, the agent capsule, semantic) can silently **flip** on a corpus change, and the blast radius of a ranking change is **invisible to the call graph**. A degrade-to-ask safety floor was added in #302; the flat scorer itself remains open debt. Treat any ranking-affecting change as high-risk and benchmark it.
2. **GPU is slower than CPU with no promotion-ready path.** The P1 CUDA-PFAC kernel is **paused** (#319; roadmap funds the CPU moat first). The only *candidate* CUDA wedge is many-fixed-strings resident over a large corpus — **not** single-pattern cold grep, where PCIe/setup cost loses. Explicit `--gpu-device-ids` stays supported and must fail loud when unhonorable; sidecar-routed GPU output is compatibility evidence, never GPU-acceleration proof (CONTRACTS.md:79-81, 100-101).
3. **The raw-grep parity gap is control-plane latency, not backend cleverness.** When tg trails rg on cold text it is launcher/dispatch overhead; the likely fix is a more native launcher path, not Python micro-tuning. Benchmark artifacts must record `tg_launcher_mode` + `tg_launcher_command_kind` and refuse stale in-tree binaries by default (CONTRACTS.md:82) so you never mix a `.cmd`-shim timing into a speed claim.
4. **FFI is not the directory-scan speed path.** PyO3 FFI overhead for directory walking was measured too high and reverted to native CPython directory scanning — a settled battle. Do not re-propose "just move the dir walk into the Rust extension" without new measurements. Full story + the mock-FFI-passed-while-the-real-bridge-was-dead lesson: `tensor-grep-failure-archaeology`.
5. **rg-parsing edge cases (round-4, open):** `rg#3364` (`--multiline --pcre2 --json` emits one match with two submatches), `rg#3131` (`rg -c` omits NUL-byte files), BOM-in-`.gitignore`. And an **open native-argv gap**: `rust_core/src/rg_passthrough.rs` forwards user *patterns* safely via `-e` (`:391`) but forwards *paths* raw with **no `--` sentinel** (`:395-397`), so a directory literally named `-l` flips rg to files-with-matches (CWE-88 flag-injection class). Tracked; do not assume it is fixed.

## Domain background a mid-level engineer may lack

- **ripgrep internals:** default regex engine matches invalid UTF-8; PCRE2 requires valid UTF-8 and transcodes (hence `--pcre2` cannot be silently swapped); binary detection via NUL byte; exit **2 = non-fatal error**, exit 1 = clean no-match, exit 0 = match; `--` ends options; `-e` supplies patterns; `-uuu` = `--no-ignore --hidden --binary`.
- **BM25 / IDF:** IDF weights rare terms higher; the flat scorer above skips this, which is why a common term can dominate ranking after a corpus grows.
- **PyO3 + the GIL:** release the GIL (`py.detach`/`allow_threads`) for CPU-bound or subprocess work; mock-based FFI tests can pass while the real extension is dead — verify FFI against the **real** built extension (`maturin develop`), not mocks.
- **MCP argv surface:** MCP tool handlers forward LLM-controlled params into `tg`/`rg`/`git` argv — a flag-injection surface. List-argv (`shell=False`) stops *shell* injection but not *flag* injection; a `--` sentinel before user positionals is required. Security detail lives in `tensor-grep-failure-archaeology` and the round-3 hardening notes in AGENTS.md.

## Fast self-check before you trust a claim about this design

```powershell
# Front door + version identity
uv run tg --version                                  # expect: tensor-grep 1.17.25 (or current)
# The published entry point (must be bootstrap.main_entry, not a Typer callback)
uv run python -c "import tensor_grep.cli.bootstrap as b; print(b.main_entry)"
# Routing / launcher observability
uv run tg doctor --json | python -c "import sys,json;d=json.load(sys.stdin);print(d.get('search_acceleration_backend'), d.get('path_tg_first_launcher_kind'))"
# Fast readiness gate (context_consistency, parity edges, registration, capsule invariants)
uv run python scripts/agent_readiness.py --output artifacts/agent_readiness.json
```

Never claim a speedup, a fixed weak point, or "tests pass" from a model self-report. Confirm against external state: an exit code, a real-binary dogfood, a `file:line` that resolves. A subagent's "green" is a hypothesis until then.

## Provenance and maintenance

All facts verified against the live repo on 2026-07-02 at v1.17.25. Re-verify anything volatile before relying on it:

- **Version:** `grep '^version = ' pyproject.toml` (was `1.17.25`, `:322`).
- **Front-door entry point:** read `src/tensor_grep/cli/bootstrap.py:820` (`def main_entry`) and confirm `pyproject.toml`/`packaging` still points `tg` at `tensor_grep.cli.bootstrap:main_entry`.
- **Command registration sites (4):** `commands.py:9` (`KNOWN_COMMANDS`), `rust_core/src/main.rs:838` (`enum Commands`), `tests/e2e/test_routing_parity.py:17` (`PUBLIC_TOP_LEVEL_COMMANDS`), `@app.command` in `src/tensor_grep/cli/main.py`.
- **Flag front doors (2):** `rust_core/src/main.rs:160` (`SEARCH_PYTHON_PASSTHROUGH_FLAGS`), `bootstrap.py:23` (`_TG_ONLY_SEARCH_FLAGS`).
- **Fail-closed contract:** `src/tensor_grep/backends/base.py` (`BackendExecutionError`, `ComputeBackend`).
- **Routing tree:** `docs/routing_policy.md` + `rust_core/src/routing.rs::route_search`.
- **Capsule/context invariants:** `docs/CONTRACTS.md` §3 (search for `context_consistency`, `ambiguity.status`, `validation_alignment`).
- **rg timeout default:** `src/tensor_grep/cli/subprocess_policy.py:44` (`TG_RG_TIMEOUT_SECONDS`, default `60.0`).
- **Open rg-passthrough `--` gap:** `rust_core/src/rg_passthrough.rs:389-397` (patterns via `-e`, paths raw). If a `--` sentinel now precedes the path loop, mark weak-point §5 resolved.
- **Registration CI gate BLOCKING:** confirm in `AGENTS.md` §"Adding a Command or Flag" (as of #282/v1.17.1).

If a re-verify disagrees with this skill, fix the skill — a wrong runbook is worse than none — and route any code change through `tensor-grep-change-control`.
