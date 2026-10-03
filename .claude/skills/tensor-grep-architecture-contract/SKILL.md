---
name: tensor-grep-architecture-contract
description: Use when you need the load-bearing design of tensor-grep and WHY it holds before touching cli/bootstrap.py, rust_core/src/main.rs, backends/, core/result.py, cli/main.py's native-delegation gate, routing, the agent capsule, or before reviewing/planning any change to the front door, command/flag registration, or backend contract. Explains the bootstrap intercept-before-Typer front door, native-vs-Python routing, the 4 command + 2 flag registration sites plus the MCP contract-version site, the Backend Fail-Closed Contract, the native-delegation forward-or-refuse contract (`_can_delegate_to_native_tg_search` + its field-coverage ratchet), the partial-results `result_incomplete`/`incomplete_reason` envelope, `MatchLine`'s frozen-but-hashable dataclass contract, the ASCII-only CLI output rule, the agent-context moat, the invariants that must hold, and the known-weak points (flat no-IDF scorer, GPU not viable, rg parity gap, FFI not the dir-scan speed path). Read this to build the right mental model; use sibling skills for the how-to of changing, debugging, or benchmarking.
---

# tensor-grep architecture contract

**What this is.** A ground-truthed map of tensor-grep's load-bearing design: the invariants a change must not break, and the weak points you must not oversell. Read it to understand *why* the code is shaped this way before you touch it. It is not a how-to — for that, hand off to a sibling (routing table below).

**What tensor-grep is:** a code-intelligence CLI named `tg`. A Rust core (`rust_core/` — both a PyO3 extension *and* a standalone `tg` binary) plus a Python CLI (`src/tensor_grep/`). Apache-2.0. Ships to PyPI (package `tensor-grep`) and as GitHub release binaries; npm, Homebrew and winget packages are not published (README). CONTRIBUTING.md calls it a "benchmark-governed, contract-heavy codebase" — that is the whole point: the contracts below are enforced by tests and a CI gate, not by convention.

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

`tg` is not "a Typer app." The published entry point is `bootstrap.main_entry` — `grep -n "^def main_entry" src/tensor_grep/cli/bootstrap.py`. It parses `argv` itself and, for a **plain text search**, forwards to the native `tg` binary or to ripgrep *before Typer ever runs* (re-grep `_run_rg_passthrough` — the dispatch runs from the `_normalize_search_invocation` call through the final `raise SystemExit(_run_rg_passthrough(...))`; traced live 2026-07-29 and confirmed a bare `tg search PAT` exits THERE, never reaching Typer). The Typer app is only reached for TG-only flags, help, or commands that require full CLI (`grep -n "^def _requires_full_cli" src/tensor_grep/cli/bootstrap.py`).

Why this matters, concretely:

- **CliRunner cannot see routing bugs.** It invokes the Typer app directly, so any bug in `bootstrap` routing (a flag that leaks to `rg`, a fork-bomb delegation loop, a wrong native/Python choice) is **invisible** to CliRunner tests and green in CI while broken for real users. This is exactly how the `--rank` plain-text crash shipped (`AGENTS.md` §"Dogfood the Real Binary, Not CliRunner" — `grep -n "^## Dogfood the Real Binary"`). **Rule: verify front-door behavior against the REAL published binary** via `scripts/dogfood/` (Dockerfile + `dogfood_features.py`), never CliRunner alone.
- **Two mutual-delegation fork-bomb hazards are guarded, not theoretical.** `TG_REEXEC_GUARD` (`grep -n 'os.environ.get("TG_REEXEC_GUARD")' src/tensor_grep/cli/bootstrap.py`) stops native→python→native search loops; `_json_aggregate_blocks_passthrough` (`grep -n "^def _json_aggregate_blocks_passthrough" src/tensor_grep/cli/bootstrap.py`) stops `--json` + a render-only flag (e.g. `-b`) from deadlocking the native front door; `_run_requires_ast_workflow` (`grep -n "^def _run_requires_ast_workflow" src/tensor_grep/cli/bootstrap.py`) keeps `tg run --selector/--strictness/--stdin/--globs` in Python so it does not ping-pong. If you touch delegation, you can re-arm a fork bomb — see `tensor-grep-failure-archaeology`.

## Native-vs-Python routing (the decision tree)

Search routing is a single shared decision in `rust_core/src/routing.rs::route_search(...)` (documented in `docs/routing_policy.md`). It returns a `RoutingDecision` carrying `selection`, `routing_backend`, `routing_reason`, `sidecar_used`, `allow_rg_fallback`. Priority order (routing_policy.md §"Unified `tg search` decision tree"):

1. `--index` → `TrigramIndex` (highest override)
2. `--gpu-device-ids` → `NativeGpuBackend` (overrides warm-index + size routing; **must fail loud if unhonorable**)
3. `--force-cpu`/`--cpu` with structured output or no usable `rg` → `NativeCpuBackend`
4. AST command → `AstBackend`
5. Warm non-stale compatible `.tg_index` → `TrigramIndex`
6. corpus > calibrated threshold **and** GPU available **and** calibration positive → `NativeGpuBackend`
7. else, plain-text request ADMITTED by `native_can_serve_plain_text` → `NativeCpuBackend` (`routing_reason = plain-text-native`; skips the `rg` spawn entirely — see the clause bullet below)
8. else, `rg` available and no structured output → `RipgrepBackend`
9. else → `NativeCpuBackend`
10. native CPU route fails and `allow_rg_fallback` → `RipgrepBackend` final fallback

Load-bearing consequences:

- **`rg` is the normal cold-path backend when installed.** Native CPU is the default *only* for structured output (`--json`/`--ndjson`), explicit `--cpu`, warm index, AST, and GPU fallback. Do not "optimize" tg to beat rg on cold text — that is the parity tier (see Known-Weak §3).
- **Warm-index auto-routing is gated:** pattern ≥ 3 bytes, no `-v`, `-C`, `--max-count`, `-w`, `-g`, and the cache must exist + be non-stale + index-compatible (routing_policy.md notes). JSON/NDJSON no longer bypass a warm index.
- **The plain-text native admission is a fail-closed SUBSET, not a flag (perf: skip the `rg` subprocess).** `native_can_serve_plain_text` (`grep -n "pub const fn native_can_serve_plain_text" rust_core/src/routing.rs`) is the single predicate deciding when the in-process native CPU engine may answer a plain-text search instead of spawning `rg`. Every clause is a refusal: cheap refusals first (`plain_text_native_cheap_checks_pass`: only `PLAIN_TEXT_NATIVE_ALLOWED_FLAGS` flags, no structured output, no explicit `--format`, stdout not a terminal, `$RIPGREP_CONFIG_PATH` not set-and-non-empty, PATH explicit, exactly one non-empty pattern, exactly one path, that path a regular file and not the `-` stdin sentinel), then the two expensive clauses evaluated last as a latency contract (pattern is native-renderable; the single path renders identically — a full-content probe: no `\r`, valid UTF-8, no NUL, ≤ 512 KiB, reported size == bytes read). The admitted route is `RoutingDecision::native_cpu_plain_text()` → `NativeCpuBackend` / `routing_reason = plain-text-native` with `allow_rg_fallback = true`, so a native failure still falls back to real `rg`; in `route_search` the predicate sits directly in front of the `rg` arm (`!config.native_plain_text` is the only thing standing between an admitted request and the `rg` subprocess). Anything outside the subset keeps spawning `rg`, unchanged. Full clause list + rationale: `docs/routing_policy.md` §"Admitted plain-text native subset".
- **Auto-GPU is conservative and effectively dormant** when rg is installed: no fresh positive calibration ⇒ stay CPU-side. GPU CPU-fallback emits `routing_gpu_device_ids = []` and must be called *CPU fallback*, never GPU acceleration (routing_policy.md §GPU).
- **AST routing is a DSL-preference split, not a GPU-capability gate.** `AstBackend.is_available()` (`grep -n "def is_available" src/tensor_grep/backends/ast_backend.py`) checks ONLY `importlib.util.find_spec("tree_sitter") is not None` — the earlier `torch_geometric`/CUDA requirement was dead GNN code (`_ast_to_graph`), audited as unreachable and deleted in #542; its own docstring now states plainly that gating a working CPU backend behind an unrelated GPU dependency was itself the bug. What actually routes `tg run`/`tg scan` to the `ast-grep` CLI sidecar (`AstGrepWrapperBackend`) on a typical box is a **deliberate DSL-consistency policy**, not hardware: the backend-selection block (`_select_ast_backend_for_pattern` — the single implementation lives in `src/tensor_grep/cli/ast_workflows.py` (`grep -n "^def _select_ast_backend_for_pattern" src/tensor_grep/cli/ast_workflows.py`); `main.py` re-exports the name from `cli/ast_scan.py`, whose copy is a thin forwarding shim onto it (`grep -rn "Thin forwarding shim onto" src/tensor_grep/cli/`) — its docstring records that the old hand-maintained duplicate drifted and silently dropped the `requires_ast_grep_wrapper` fail-closed guard. The wrapper-availability check is `_check_backend_available("AstGrepWrapperBackend")` in `ast_workflows.py`) prefers the wrapper whenever it is available, for BOTH pattern kinds, because native tree-sitter `AstBackend` speaks a different query DSL and would silently return different results if substituted; native `AstBackend` is reached only as the ast-grep-absent fallback for native-pattern queries (a code comment marks flipping this default as future task #141). Net practical effect on a typical box (ast-grep CLI installed) is unchanged from the old text — `tg run` still uses the ast-grep CLI sidecar (also for string metavar queries like `def $F($$$ARGS)`) — visibly, per the fail-closed contract below — but the REASON is DSL-safety, not CUDA-availability. This matches `code-search-and-retrieval-reference` §2.
- **`NativeCpuBackend` is not one engine — it is two distinct code paths, and a change proven for one is NOT automatically true for the other (A3, v1.91.3/#695).** `rust_core/src/native_search.rs` is the **default streaming** path: it is deliberately kept SERIAL, held to a tested **≥25ms first-match latency contract** — do not parallelize this path casually; its whole design point is fast first-byte-out, and parallelizing it risks regressing that contract even if aggregate throughput looks better in a microbenchmark. `rust_core/src/backend_cpu.rs` is the separate **PyO3/FFI fallback path** (reached only when the search doesn't route through the primary native front door) — this is where #695 shipped intra-file `rayon` parallel search, gated to files **≥50MiB**, byte-identical to the serial result. Before citing a `backend_cpu.rs` benchmark number as evidence for `native_search.rs` (or vice versa), confirm which file the change/measurement actually touched — these are two engines behind one routing label, not one engine with two code paths.

## The registration sites (miss one → silent misroute)

This is a **universal bug class**: "register in N places, miss one, fail *quietly*." The CI registration-completeness gate has been **BLOCKING since v1.17.1 / #282** (`AGENTS.md`, `grep -n "registration-completeness gate is BLOCKING" AGENTS.md`), but you still author all sites by hand.

**Since #977, PR CI is no longer a full routing/parity oracle for docs-only PRs.** A cheap `changes` job (`grep -n "^  changes:" .github/workflows/ci.yml`) detects whether the PR diff touches code (`src/`, `rust_core/`, `tests/`, `.github/workflows/`, `pyproject.toml`, `Cargo.toml`, `Cargo.lock`, `uv.lock`), and the expensive/cross-platform jobs carry `needs: [smoke, changes]` with `if: github.event_name != 'pull_request' || needs.changes.outputs.code == 'true'` (re-grep `needs: \[smoke, changes\]` for the current set). A skipped job counts as SUCCESS for branch protection, so a docs-only PR's green rollup proves nothing about code behavior; main pushes always run the full matrix (the job forces `CODE_FILES="main-push"` off-PR). Do not cite a docs-only PR's CI as routing evidence, and do not propose `paths-ignore` on required checks (branch protection would wait forever on a run that never starts).

**A new top-level `tg COMMAND` needs four sites** (AGENTS.md "Adding a Command or Flag" — `grep -n "^## Adding a Command or Flag" AGENTS.md`):

| # | Site | File |
|---|---|---|
| 1 | `KNOWN_COMMANDS` set | `src/tensor_grep/cli/commands.py:9` |
| 2 | `Commands::X` variant + dispatch arm | `rust_core/src/main.rs` (`grep -n "pub enum Commands" rust_core/src/main.rs`); e.g. `Commands::Prepare`/`Commands::Ledger` dispatch arms — `grep -n "Commands::Prepare\|Commands::Ledger" rust_core/src/main.rs` |
| 3 | `PUBLIC_TOP_LEVEL_COMMANDS` (parity test) | `tests/e2e/test_routing_parity.py:46` |
| 4 | `@app.command` function | `src/tensor_grep/cli/main.py` |

**A new search flag needs two front doors** (AGENTS.md, same section, "two front doors") or it leaks to ripgrep and crashes with `rg: unrecognized flag` for anyone on the published binary:

| # | Site | File |
|---|---|---|
| 1 | `SEARCH_PYTHON_PASSTHROUGH_FLAGS` (native allowlist) | `rust_core/src/search_flag_registry.rs` (`grep -rn "const SEARCH_PYTHON_PASSTHROUGH_FLAGS" rust_core/src`) |
| 2 | `bootstrap._TG_ONLY_SEARCH_FLAGS` (Python front-door allowlist) | `src/tensor_grep/cli/bootstrap.py:50` |

**A new MCP tool (or a request/response shape change to an existing one) is a FIFTH registration site** — distinct from the four command sites above (AGENTS.md "Adding a Command or Flag", 5th-registration-site note). Every MCP tool's JSON envelope embeds `mcp_contract_version` from the SINGLE constant `_TG_MCP_SERVER_CONTRACT_VERSION` (`grep -n "_TG_MCP_SERVER_CONTRACT_VERSION = " src/tensor_grep/cli/mcp_server.py` — read the current value from source; do not cite it here); `_inject_mcp_contract_fields` (`grep -n "^def _inject_mcp_contract_fields" src/tensor_grep/cli/mcp_server.py`) HARD-assigns it into every serialized tool envelope (a stale per-tool literal can never win — the M14 retirement of `setdefault`), and the same constant sets `server._mcp_server.version`. **Bump the constant whenever any tool's request/response shape changes** — the `tg_find` MCP PR (#627) shipped with an un-bumped contract version and only the mandatory adversarial Opus gate caught it, not tests or CI.

**Blind spot to internalize:** `tg callers <fn>` finds *callable* registration sites in ~1s, but the call graph **cannot see set/list/decorator registrations** — `_TG_ONLY_SEARCH_FLAGS` is a set, `@app.command` is a decorator, the Rust dispatch is a match arm. Those are the sites most often missed (`--rank` lived in a *set*). So `tg callers` for the reachable ones **and** grep / `tg scan` for the declarative ones, then confirm your entry appears in *all* sites. (The actual add-a-thing procedure lives in `tensor-grep-change-control`; this skill only explains why the sites exist.)

**Unknown and reserved top-level commands fail closed on BOTH front doors (A90).** `commands.py` carries `RESERVED_TOP_LEVEL_COMMANDS` (`grep -n "RESERVED_TOP_LEVEL_COMMANDS = " src/tensor_grep/cli/commands.py`, with the A90 lifecycle comment above it): roadmap command names that DO NOT EXIST yet, kept disjoint from `KNOWN_COMMANDS` by a test-pinned `RESERVED ∩ KNOWN == ∅` invariant — realizing a reserved name means removing it from the reserved set in the same change. A genuinely unknown command is never forwarded to search; both doors refuse it with **exit 2, diagnostic on stderr, stdout EMPTY, and a did-you-mean suggestion**: the Python bootstrap via `_emit_unknown_command_human` / `_emit_unknown_command_json` (`grep -n "_emit_unknown_command" src/tensor_grep/cli/bootstrap.py`, then `raise SystemExit(2)`) and the native binary via `top_level_unknown_command_refusal` (`grep -n "top_level_unknown_command_refusal" rust_core/src/main.rs`, `std::process::exit(2)`; human text when `--help`/`-h` is present, otherwise a single `{"error": {"code": "unknown_command", ...}}` JSON object on stderr).

## Backend Fail-Closed Contract

The single most important correctness invariant. `src/tensor_grep/backends/base.py` defines it: every `ComputeBackend` **MUST raise `BackendExecutionError` on a real failure** — never return a clean empty / `0-match` `SearchResult`, and never silently swap to an engine that cannot preserve the requested semantics.

Why a context tool cannot afford to violate it: a swallowed backend failure reaches a coding agent as a trustworthy "no matches." That is the one lie a search tool must never tell — the agent then edits on the belief that the symbol does not exist.

Rules when a path *can* fall back (AGENTS.md "Backend Fail-Closed Contract", `backends/base.py:7`):

- **Fail closed** for any flag/contract the fallback cannot preserve. `--pcre2` through a non-PCRE2 engine ⇒ raise, do not swap (that produces *wrong results*, not just slower ones).
- **A legitimate degraded fallback must be VISIBLE:** set `fallback_reason` (and a distinct `routing_reason`) on the result so JSON/CLI consumers can tell degraded output from real output. Never label heuristic output as model output.
- **Validate an untrusted response shape before indexing** (e.g. a model's class count vs a fixed label list) so a mismatch degrades gracefully instead of raising an `IndexError` a broad `except` then swallows.

**The recurring anti-pattern:** a bare `except Exception:` that returns empty or falls through to a different engine. This has been fixed *repeatedly* across audits — the Rust/PCRE2 bridge, the ast-grep OOM mask, the tree-sitter query swallow, CyBERT classify. When you review/write any backend or router that can change engines, this is the first thing to check. The structural fix (a `SafeBackendMixin` + a fault-injection conformance CI gate) is planned but **not yet shipped**, so the discipline is still per-file. The same rule extends to routers: an explicit `--gpu` request silently routed to CPU must raise/emit a diagnostic, not swap silently.

**A new command does not inherit a sibling's fail-closed boundary-catch automatically — prove it, don't assume it (`tg find`, v1.77.0, #189).** `tg find` and `tg search --semantic` share the same dense-embedding core (`retrieval_dense.py`/`retrieval_fusion.py`), but their fail-closed SHAPE differs because their corpora differ: `--semantic` re-ranks an already regex-prefiltered match set, so a degrade to BM25-only is always cheap and benign; `tg find` walks and ranks the WHOLE repo with no prefilter, so a query-time model fault reachable mid-walk is a materially different risk surface. The first `tg find` build wave shipped WITHOUT a command-boundary catch for `DenseUnavailableError` — it would have propagated as an uncaught crash instead of a visible BM25-degrade — caught only by the mandatory adversarial Opus gate, not by the (green) unit tests, and fixed in the same PR (`045fadc`). **Rule:** when a new command reuses an existing backend/compute path, verify its OWN command-boundary exception handling explicitly; do not assume "the underlying module already has a fail-closed contract" is sufficient — the CALLER must also catch and degrade/exit correctly at ITS boundary. See `tensor-grep-run-and-operate` §11c for `tg find`'s full exit-code contract (`BackendExecutionError`→exit-2; empty+`result_incomplete`→exit-2 else exit-1; found+`result_incomplete`→print then exit-2).

## Partial-results contract: suppression != absence (`SearchResult.result_incomplete`)

Companion invariant to the Backend Fail-Closed Contract above, shipped in round-4 slice 3 (#341, commit `f11ce28`, v1.18.x). `SearchResult` (`grep -n "^class SearchResult" src/tensor_grep/core/result.py`; fields — `grep -n "result_incomplete: bool\|incomplete_reason: str" src/tensor_grep/core/result.py`) carries `result_incomplete: bool = False` and `incomplete_reason: str | None = None`, deliberately **not** overloaded onto `fallback_reason` — `fallback_reason` means "the execution engine was swapped"; `result_incomplete` means "this engine ran, but a soft per-item error suppressed part of the output." Conflating them would emit a false "we fell back" signal to `doctor`/JSON consumers.

The trigger: rg exit code **2** is a *soft* per-file error (e.g. one unreadable/missing path among many) and rg still emits matches for every readable file. Before #341, tg's parser raised unconditionally on `exit > 1`, **discarding those partial matches** — and even if it hadn't, tg would have silently exited 0 while rg exits 2 (a parity break an agent scripting around exit codes would never see).

**And the exit-code side of this contract has since been made STRICTER, not looser — do not describe it as "empty partial -> exit 2, non-empty partial -> exit 0."** #398 first made ANY truncated partial exit 2; #399 briefly walked that back to exit-2-only-when-empty; **#401 reverted #399** after a unanimous design council — the current, final contract is: any `result_incomplete`/`partial` result exits **2 regardless of whether matches were found**, because a truncated match/caller/blast-radius list must never be silently trusted as exhaustive. See `tensor-grep-large-repo-scale-campaign` §5 for the full exit-code table and `docs/CONTRACTS.md` — `grep -n "This mirrors \`tg search\`'s \`2 = result_incomplete\`"` for the symbol-command three-state exit-code contract.

The 5-site fix, cite `file:line`:

- **Parse-first-then-branch** — `backends/ripgrep_backend.py` (`search`, `_search_files_with_matches`, `_search_counts`): exit 2 with a non-empty parse *keeps* the results, sets `result_incomplete=True` + a stderr-derived `incomplete_reason` (`grep -n "result_incomplete = True" src/tensor_grep/backends/ripgrep_backend.py`); exit >2, or exit 2 with nothing parsed, raises `BackendExecutionError` (**RESOLVED #79/#10/#14, commit `a7c9431`**: every `RipgrepBackend` fatal path, including the rg-missing guard — `grep -n "requires the 'rg' binary" src/tensor_grep/backends/ripgrep_backend.py` — now raises `BackendExecutionError` instead of a bare `RuntimeError`, so `cli/main.py`'s per-file `except BackendExecutionError` CPU-fallback retry — `grep -n "except BackendExecutionError" src/tensor_grep/cli/main.py` (several call sites; confirm which one is the per-file retry before citing it) — catches it instead of falling into the broad `except Exception` and crashing the whole search — see `code-search-and-retrieval-reference` §1 for the exit-code table).
- **Monotonic merge** — `merge_runtime_routing` (`grep -n "^def merge_runtime_routing" src/tensor_grep/core/result.py`) OR-merges `result_incomplete` across sub-results (`aggregate.result_incomplete or result.result_incomplete`), so the CLI/MCP/sidecar aggregate inherits uniformly — any incomplete sub-result taints the whole.
- **Exit-code parity** — `grep -n "if .*result_incomplete else\|exit_incomplete else" src/tensor_grep/cli/main.py` (scattered, not one contiguous block): the terminal exits read `sys.exit(2 if … result_incomplete/exit_incomplete else …)` across the files-with/without-matches, `is_empty`, quiet, and post-format branches, closing the "tg exits 0 while rg exits 2" gap.
- **JSON/NDJSON envelope** — `cli/formatters/json_fmt.py` (`grep -n "result_incomplete" src/tensor_grep/cli/formatters/json_fmt.py`): `result_incomplete`/`incomplete_reason` are emitted **only when incomplete**, so a complete result's JSON shape stays byte-identical to before #341.
- **MCP** — `grep -n '"result_incomplete"' src/tensor_grep/cli/mcp_server.py`: the structured `tg_search`/graph-command responses carry both fields top-level — suppression must be visible to an agent, not buried in a log line.

**Rule for any new path that can drop some results due to a soft/partial failure:** set `result_incomplete` + `incomplete_reason`. Do not (a) raise and lose the good results, or (b) silently return only the good results as if they were the complete answer — that is the same "suppression reads as absence" lie the Backend Fail-Closed Contract forbids, just at the partial-result layer instead of the total-failure layer. Tests: `tests/unit/test_rg_exit2_partial.py`.

## Native-delegation forward-or-refuse contract (`_can_delegate_to_native_tg_search`)

`_can_delegate_to_native_tg_search` — `grep -n "^def _can_delegate_to_native_tg_search" src/tensor_grep/cli/main.py` — gates whether a Python-side `tg search` hands the **entire** search to the native `tg` subprocess (`_build_native_tg_search_command` — `grep -n "^def _build_native_tg_search_command" src/tensor_grep/cli/main.py`) and then `sys.exit()`s on its result — a delegation that runs *before* the Python-side BM25 rerank (`--rank`) and the in-backend sort (`--sort-files`) ever execute.

**The invariant:** delegation is permitted only when native execution is byte-equivalent to the Python path for the requested config. The gate enforces this mechanically, not by convention — it loops every field name in `_NATIVE_TG_DELEGATION_DEFAULT_REQUIRED_FIELDS` (`grep -n "_NATIVE_TG_DELEGATION_DEFAULT_REQUIRED_FIELDS = " src/tensor_grep/cli/main.py`) and **refuses** delegation (falls through to the Python/backend path) if *any* of those fields differs from a fresh `SearchConfig()`'s default. Every `SearchConfig` field must land in exactly one bucket:

1. **Forwarded** — read by `_build_native_tg_search_command` and translated into native argv.
2. **Refused** — listed in `_NATIVE_TG_DELEGATION_DEFAULT_REQUIRED_FIELDS`, so a non-default value forces the gate closed.
3. **Gate-handled** — read off explicit keyword args at the call site (`files_with_matches`, `files_without_match`), not the config object.
4. **KNOWN_GAP** — explicitly documented pre-existing tech debt, tracked rather than silently dropped.

This is enforced by a governance **ratchet**, `tests/unit/test_native_delegation_field_coverage.py` (round-4 #25, shipped as #342, commit `5e6f780`): it AST-derives the "forwarded" set straight from `_build_native_tg_search_command`'s source (`ast.walk` over every `config.<attr>` read), so that list can never silently drift from the real code, then asserts `all_fields - (forwarded | required | gate_handled | known_gap) == set()`. Add a new `SearchConfig` field and forget to classify it → this test goes red immediately.

**The bug this closes (#342):** `rank_bm25` and `sort_files` were neither forwarded to native argv nor in the refuse-tuple, so `tg search --rank --cpu` silently delegated to the native binary — which has no BM25 of its own — and `sys.exit()`d *before* the Python rerank/sort ever ran, returning unranked/unsorted output that looked like a normal, correct result (suppression indistinguishable from absence, same class the partial-results contract above targets). This is the **same flag-drop bug class** as the `-u`/`-uu` no-op fixed in #336 (round-4 PR-A slice 1): a flag parses successfully but never reaches the engine that must honor it.

**Landmine already hit once — do not re-propose it:** the tempting "just gate on any field differing from defaults" fix is wrong. `query_pattern` is auto-set to the search pattern on *every* search, so a differs-from-default check would always see a difference and refuse delegation on every call, killing the fast path entirely (the exact failure mode from the 2026-06-30 #1 audit finding — see `tensor-grep-failure-archaeology`). The fix has to be per-field, not "any field changed."

**Rule when adding a new `SearchConfig` field that affects search output:** decide immediately whether native delegation can reproduce it byte-for-byte. If not, add the field name to `_NATIVE_TG_DELEGATION_DEFAULT_REQUIRED_FIELDS`. The ratchet test refuses to let you skip this decision silently — it is a hard gate, not a lint suggestion.

## A THIRD rg-passthrough door lives INSIDE `cli/main.py::search_command` — gated on `rg` availability, not a platform flag (task #24, 2026-07-30)

This is a **third**, independent rg-passthrough decision, distinct from both the bootstrap front door (`bootstrap._run_rg_passthrough`, "The front door" above) and the Rust `routing.rs` tree ("Native-vs-Python routing" above). It lives entirely inside the Python Typer app, fires only for invocations that a `_TG_ONLY_SEARCH_FLAGS` flag has already forced past the bootstrap front door (e.g. `--stats`, `--ast`, `--rank`, `--semantic`), and is easy to miss because nothing about it is platform-conditional — yet it produced a real Windows-vs-Linux CI divergence (`docs/BACKLOG.md` "STILL OPEN — `tg search --stats` routing DIVERGES BY PLATFORM").

- `can_passthrough_rg` (`grep -n "can_passthrough_rg = (" src/tensor_grep/cli/main.py`; built by `_can_passthrough_rg` — `grep -n "^def _can_passthrough_rg" src/tensor_grep/cli/main.py`) categorically excludes `--ast`/`--rank`/`--semantic` (`not config.ast` / `not config.rank_bm25` / `not config.semantic_rank` — `grep -n "not config.rank_bm25" src/tensor_grep/cli/main.py`) but has **no equivalent categorical exclusion for `--stats`** — its only stats-specific veto, `not (rg_json_passthrough and stats_mode)` (`grep -n "rg_json_passthrough and stats_mode" src/tensor_grep/cli/main.py`), fires solely for the `--format rg --json` combo. A plain-text `tg search PAT --stats` sails through.
- When `stats=True`, `search_command` has its own branch for it (`grep -n "_selected_route_supports_rg_passthrough(" src/tensor_grep/cli/main.py`):
  ```python
  if can_passthrough_rg and stats and _selected_route_supports_rg_passthrough(...):
      exit_code = rg_backend.search_passthrough(passthrough_paths, pattern, config=config)
      sys.exit(exit_code)
  ```
  This hands the **entire** search to a live `rg` subprocess — `RipgrepBackend._build_cmd` appends rg's own `--stats` flag when `config.stats` is set (`grep -n 'cmd.append("--stats")' src/tensor_grep/backends/ripgrep_backend.py`) — and exits on rg's own exit code, entirely bypassing everything `search_command` would otherwise do downstream: its own `_emit_stats()` `[stats] backend=... reason=...` line (`grep -n "def _emit_stats" src/tensor_grep/cli/main.py`), the `--debug` routing echo (`grep -n "routing.backend=" src/tensor_grep/cli/main.py`), and — the reported symptom — the `is_empty` branch's defaulted-scope note (`grep -n "_write_defaulted_scope_note" src/tensor_grep/cli/main.py`).
- `_selected_route_supports_rg_passthrough` (`grep -n "^def _selected_route_supports_rg_passthrough" src/tensor_grep/cli/main.py`) requires `Pipeline.selected_backend_name == "RipgrepBackend"`, which `core/pipeline.py`'s backend-selection `__init__` picks via `elif rg_available: self.backend = rg_backend; selected_backend_reason = "rg_default_fast_path"` (`grep -n "rg_default_fast_path" src/tensor_grep/core/pipeline.py`) whenever `rg_backend.is_available()` is `True`. That in turn is `RipgrepBackend.is_available()` (`grep -n "def is_available" src/tensor_grep/backends/ripgrep_backend.py`) → `resolve_ripgrep_binary()` (`grep -n "^def resolve_ripgrep_binary" src/tensor_grep/cli/runtime_paths.py`) — a pure **environment** probe: `TG_RG_PATH` env var, then `shutil.which("rg"/"rg.exe")` on `PATH`, then an in-tree fallback directory that is **gitignored** (`ripgrep-*/` in `.gitignore`) and absent from a fresh checkout on any platform (only the zip `benchmarks/rg.zip` is committed, and only a separate *test* helper — `tests/helpers/rg_parity.py::resolve_pinned_rg_binary` — knows how to unpack it; production code never touches the zip).
- **There is no `sys.platform`/`os.name` conditional anywhere in this chain.** The Windows-vs-Linux CI split is caused entirely by whether a real `rg`/`rg.exe` happens to be resolvable on `PATH` inside each OS leg of the `test-python` CI job (`grep -n "^  test-python:" .github/workflows/ci.yml`), which installs **no** ripgrep package on any OS — contrast the `native-build-smoke`/`smoke`/`benchmark` jobs, which explicitly `apt-get install ripgrep` / `brew install ripgrep` (`grep -n "install ripgrep" .github/workflows/ci.yml`).
- **Paired proof (same source tree, one variable flipped, 2026-07-30, Windows):** with a real `rg.exe` resolvable on `PATH`, `tg search NO_MATCH_ZZZ --stats` (no PATH argument) prints rg's own `0 matches / 0 matched lines / ...` stats block, no scope note, exit 1. With `PATH` stripped so `resolve_ripgrep_binary()` returns `None`, the identical invocation instead prints tg's own `[stats] backend=CPUBackend reason=cpu_python_regex_prefilter` line **and** the `is_empty` branch's `note: no PATH was given...`, still exit 1. One boolean (`rg_available`) is the entire mechanism.
- **Why only `--stats` XPASSed, not `--ast`/`--rank`/`--semantic`:** those three are hard-excluded from `_can_passthrough_rg` regardless of `rg` availability (see the exclusions above); `--stats` is the one flag in that family with no such categorical veto.
- **Ruled out:** the native Rust `tg` binary (`resolve_native_tg_binary`) is never reachable for `--stats` on either platform — `_can_delegate_to_native_tg_search`'s `unsupported_flags` explicitly lists `--stats` (`grep -n '"--stats"' src/tensor_grep/cli/bootstrap.py`), and the `TG_RUST_FIRST_SEARCH` OR-branch (`grep -n "_prefer_rust_first_search() and not _requires_full_cli" src/tensor_grep/cli/bootstrap.py`) requires `not _requires_full_cli(...)`, which is always `False` for `--stats` (`_TG_ONLY_SEARCH_FLAGS`, `bootstrap.py:50`). `cli/mcp_server.py` has no `can_passthrough_rg`/`search_passthrough` shortcut at all — this divergence is CLI-only.
- **Downstream blast radius found while sweeping — CLOSED in this tree; the "still open / zero quiet mentions" text this bullet carried was FALSE.** The same `can_passthrough_rg` fork also gates the plain, non-stats internal passthrough (`grep -n "if can_passthrough_rg:" src/tensor_grep/cli/main.py`), and BOTH passthrough call sites — the plain (`if not stats:`) branch and the `--stats` branch, `grep -n "search_passthrough" src/tensor_grep/cli/main.py` — funnel through the one method that now honors `--quiet`: `RipgrepBackend.search_passthrough` (`grep -n "def search_passthrough" src/tensor_grep/backends/ripgrep_backend.py` — `:478`) appends rg's `-q` at `:511` when `config.quiet` is set. It is deliberately NOT inside the shared `_build_cmd` (`:531`): `_build_cmd` has FOUR consumers — `search()` (`:80`, parses `--json`), `_search_files_with_matches()` (`:287`, parses `-l`), `_search_counts()` (`:406`, parses `--count`) — and only `search_passthrough` streams rg's output; `-q` makes rg print NOTHING, so a parsing consumer would report a false zero-match on a matching file plus an exit-code violation (the in-code hazard comment at `:492-510` records the measured rg behavior). Placement is pinned by `tests/unit/test_quiet_survives_rg_passthrough.py`: the fix arm asserts `-q` reaches the streaming route's argv, the control arms assert the three parsing consumers never receive `-q` and that a non-quiet search is never silenced. Do not re-propose moving `-q` into `_build_cmd`. (Honesty note: no test combines `--stats` AND `--quiet` specifically; coverage is at the `search_passthrough` choke point both branches share.)
- **Confirm on CI in one line**, run inside the `test-python` job matrix on each OS (no test framework needed):
  ```
  python -c "from tensor_grep.cli.runtime_paths import resolve_ripgrep_binary as r; print(r())"
  ```
  A real path on one OS and `None` on the other reproduces the whole divergence directly.
- **SUPERSEDED (2026-09-23): the missing scope note is fixed.** The `--stats` branch now writes
  the defaulted-scope note on rg's zero-match exit under the same three gates as `is_empty`
  (`grep -n "Task #24" src/tensor_grep/cli/main.py`; shared gate `_scope_filtered`). The
  routing divergence described above still exists -- only its user-visible symptom is closed;
  tg's own `[stats]` line and `--debug` echo are still skipped on this route.

## The walk-ceiling fast-refuse: 3 doors, 2 constants, 1 value (A9, v1.92.3/#702)

Before #702, the plain flag-less `bootstrap._run_rg_passthrough` path (`grep -n "^def _run_rg_passthrough" src/tensor_grep/cli/bootstrap.py` — the front
door a bare `tg search PATTERN` with no scoping flags hits, *before* `main.py`'s Typer app is ever
reached) had **no walk ceiling at all**. `main.py`'s three vendored/workspace/large-root refusal guards
never ran for this path, so an unscoped search on a large defaulted-path root silently walked unbounded
until it hit the 60s `TG_RG_TIMEOUT_SECONDS` subprocess backstop — natively reproduced, not a WSL
filesystem artifact.

The fix is one constant, enforced coherently across **3 doors**, not three independent numbers that can
drift apart:

- **The single constant**: `IMPLICIT_SEARCH_WALK_FILE_CEILING = 1500`
  (`src/tensor_grep/io/scan_limits.py:106` — **moved here from `io/directory_scanner.py` since the
  v1.93.2 pass**; `directory_scanner.py:34` now only re-imports/re-exports it, so a grep of the old
  file still finds a hit but not the definition).
- **Door 1 — Python bootstrap probe**: `bootstrap._search_paths_include_oversized_implicit_root`
  (`grep -n "^def _search_paths_include_oversized_implicit_root" src/tensor_grep/cli/bootstrap.py`), gated on `paths_defaulted` (fires only when no explicit PATH was given, not on
  every search).
- **Door 2 — Python Typer app**: `main.py`'s `_LARGE_ROOT_SCAN_FILE_CEILING = IMPLICIT_SEARCH_WALK_FILE_CEILING`
  (`grep -n "_LARGE_ROOT_SCAN_FILE_CEILING = IMPLICIT_SEARCH_WALK_FILE_CEILING" src/tensor_grep/cli/main.py`), the alias that keeps the Typer-app-side ceiling from silently drifting from the
  bootstrap door's value.
- **Door 3 — Rust native front door**: `rust_core/src/rg_passthrough.rs` keeps its own copy of the same
  numeral (`pub const IMPLICIT_SEARCH_WALK_FILE_CEILING: usize = 1500;`, `rg_passthrough.rs:153`),
  synced by convention (not a shared cross-language build constant) — a future change to the
  Python-side value needs a matching edit here or the two front doors will disagree on where the
  ceiling sits.

**Escape hatches**: an explicit PATH, `--max-depth`, or `--allow-broad-generated-scan` — `--glob`/
`--type` alone do **not** bypass the ceiling when the path itself was defaulted. Result: an over-ceiling
implicit root now refuses in ~1.7s (exit 2) instead of silently walking for up to 60s.

## The `dynamic_unresolved` honesty marker — every downstream consumer must re-check it, not inherit it (A10/A15, v1.93.0/#703 + v1.93.2/#709)

`tg imports`/`tg importers`/`tg blast-radius` mark a **relative** dynamic import
(`import_module(".x", package=...)`, `__import__(..., level>=1)`) as `dynamic_unresolved` rather than
resolving it to a guessed target — the literal text is preserved in `unresolved`, and it is **never**
silently pointed at a same-named decoy top-level file (both the forward `tg imports` direction and the
reverse `tg importers` direction). Absolute-literal dynamic imports (`import_module("pkg.mod")`) still
resolve normally (`"dynamic": true`) — only the genuinely ambiguous relative/computed form degrades to
the honesty marker. Rule: **a wrong edge is worse than a missing one.**

**The #709 lesson is the reason this gets its own subsection instead of living as a one-line note next
to #703:** shipping the honesty marker at the import-graph layer (#703) was NOT sufficient by itself —
`tg blast-radius`'s reverse **scoring prefilter** had its own, separate code path that fuzzy-matched
`dynamic_unresolved` literals against real symbol names, so a same-named decoy could still leak into
`affected_files`/`dependent_files` through the scoring layer even though the import-resolution layer
correctly refused to link it. #709 fixed the prefilter to exclude `dynamic_unresolved` literals too,
with a pinned ranking test proving zero legitimate reorder. **Generalize this:** when a marker like
`dynamic_unresolved` is introduced at one layer (import resolution), audit every OTHER layer that reads
import/symbol data for its own independent path that could re-introduce the same class of false edge
(a scoring prefilter, a cache, a graph-traversal shortcut) — do not assume a single fix point closes the
whole surface.

## Cross-domain native-binary detection (A11, v1.93.0/#704)

`is_cross_domain_native_binary` (`grep -n "def is_cross_domain_native_binary" src/tensor_grep/cli/runtime_paths.py`) decides whether a resolved `tg` binary lives in
a different OS/filesystem domain than the current process (the concrete case: a WSL Linux process
resolving a Windows-built `tg.exe` via a translated `/mnt/c/...` path). Before #704, cross-domain
detection was **`.exe`-suffix-only** — but the managed installer also ships a bare-named POSIX shim
`tg` that wraps `tg.exe`, and that shim has no `.exe` suffix to detect. The bare shim was misclassified
as same-domain, so its sentinel probe used an **untranslated** `/tmp/...` path against the Windows
binary and failed with a confusing `path_not_found`/`failed_probe_path` — a probe bug, not a genuine
GPU unavailability signal. The fix adds two more signals: a sibling `tg-native-metadata.json` file, and
a co-located `<name>.exe` file next to the bare-named shim — both checks are **fail-closed-only**
(reading the metadata file is capped at 1MiB and guarded against `OSError`/`ValueError`; a read failure
never *promotes* a binary to cross-domain, it only affects whether the extra signal is available) and
**non-WSL hosts never run these checks at all**, so the fix cannot introduce a false-positive on a
plain Windows or Linux box. Post-fix, the same WSL bare-shim probe reports an honest
`status=unsupported, routing_backend=NativeCpuBackend, routing_reason=gpu-auto-fallback-cpu, exit 0`
instead of the misleading path error.

## `MatchLine` is a frozen, HASHABLE dataclass

`grep -n "^class MatchLine" src/tensor_grep/core/result.py` (`@dataclass(frozen=True) class MatchLine`). `submatches` (`tuple[dict[str, object], ...] | None`, added by #340 to carry rg's per-occurrence byte offsets for `--vimgrep`/`--column`) is a tuple-of-dicts — dicts are unhashable, so a populated `submatches` would break `hash(MatchLine(...))` the moment a frozen dataclass's default hash implementation (derived from its `==`-participating fields) tried to hash it.

The fix (#344, commit `80de0b4`): `submatches: tuple[dict[str, object], ...] | None = field(default=None, compare=False)`. `compare=False` excludes the field from both `__eq__` and the derived `__hash__`, so `MatchLine` stays hashable even when `submatches` is populated. Excluding it from `==` is intentionally correct, not a shortcut: the offsets are a pure function of `text` + `line_number`, so two matches equal on those fields are equal regardless of any incidental difference in their submatch tuples.

No caller hashes `MatchLine` today — this was caught as a **latent landmine** before any set/dedup consumer existed, not a live crash. Treat it as the standing precedent: this codebase keeps its frozen dataclasses hashable on purpose. **Any new field added to a frozen dataclass here that is itself unhashable (a `list`, `dict`, or other mutable/unhashable container) must be marked `field(..., compare=False)`** — or, if it genuinely must participate in equality, the dataclass needs a deliberate `eq=False`/custom `__hash__` redesign, not a silent break.

The same PR tried gating the per-match submatch stash behind `config.vimgrep or config.column` and reverted it: `tests/unit/test_submatches_output_shaping.py` requires `submatches` populated under a default config (see `tensor-grep-failure-archaeology` Battle 11). Do not re-add the gate.

## ASCII-only CLI output contract

`tg` does not reconfigure `stdout` to UTF-8, and Windows consoles commonly default to the `cp1252` codepage. `typer.echo` (used throughout the CLI) **raises `UnicodeEncodeError`** on any character outside that codepage — a hard crash, not mojibake. #346 (commit `6b7b518`) found `render_inventory_text` in `src/tensor_grep/cli/inventory.py` emitting a literal `⚠` (U+26A0 WARNING SIGN) on the truncation-notice path (repo > `max_files`); on a stock Windows terminal, `tg inventory` on a large repo crashed instead of printing a warning.

**Rule: no non-ASCII characters in any `tg`-CLI-rendered text output** (Typer `echo`/`print` call sites — this governs strings *tg itself* prints, not file contents being searched). Use bracketed ASCII markers instead — the fix replaced `⚠` with the literal string `[!]`. Before adding a new CLI-rendered string (a warning glyph, a checkmark, box-drawing table characters, an arrow), check it is `str.isascii()`-clean; if in doubt, dogfood on a real `cp1252` Windows console, not just a UTF-8-default terminal or CI runner — **CI's UTF-8 locale will not catch this class of bug**, it is Windows-console-only and was found by dogfooding a large real repo locally (`tensor-grep-dogfood-real-corpus-before-shipping-precision-2026-07-03` memory), not by the fixture test suite.

## The moat: agent-native context, not faster grep

Positioning is a design constraint, not marketing. **tg is not a faster grep.** ripgrep is the raw-text parity baseline; ast-grep is the structural-search baseline. The moat is the **agent-native code-intelligence layer**: `orient`, `callers`, `blast-radius`, `defs`, `refs`, `source`, `agent` (the capsule), `session`, `find` (whole-repo hybrid NL search, v1.77.0, #189). Peers to know: Aider repo-map (tree-sitter + NetworkX PageRank, `--map-tokens`), Sourcegraph Cody (SCIP + BM25 + embeddings → rerank), Cursor (index-first embeddings + Merkle change detection).

Engineering-capacity consequence (AGENTS.md "Roadmap Sequencing 2026-07-02"): CPU-only, every-install moat work is funded *first* — local hybrid semantic search (BM25 + CPU dense embeddings + RRF, no API key), `tg registration-check` as a first-class command, a Bloom-filter n-gram chunk prefilter — before advancing the GPU program. Never make a change that implies "tg beats rg for cold exact-text search."

## Invariants that must hold (agent contract)

These are enforced by the capsule/context contract (`docs/CONTRACTS.md` §3, "Context and edit-planning contracts") and the agent-readiness gate. A change that breaks one is a contract regression even if tests are green.

- **`context_consistency`** — `edit_plan_seed.primary_file`, `navigation_pack.primary_target.file`, the rendered source sections, and follow-up read commands must not contradict each other. The payload reports whether the primary file is included, whether rendered context matches the target, whether confidence was downgraded, and why a primary file was omitted (`docs/CONTRACTS.md`, `grep -n "reports whether the primary file is included"`).
- **Ambiguity hard-stop** — when equal-confidence alternatives are unresolved, cap `confidence.overall` and `primary_target.confidence` below the edit threshold, set `ask_user_before_editing.required = true`, and mirror it in top-level `ambiguity.status = "tie_requires_confirmation"`. A validation-resolved tie records `ambiguity.status = "tie_resolved"`, `resolved_by = "targeted-validation"` with concrete `resolution_evidence`; an LSP-resolved tie needs explicit provider-response proof (`docs/CONTRACTS.md`, `grep -n "tie_requires_confirmation"`). This is the safety floor added in #302 — do not weaken it.
- **Validation provenance** — validation hints use `validation_plan[].detection ∈ {detected, heuristic, generic}` and must align with the primary target language: a TS target must not silently get pytest, a Python target must not silently get `npm test`; `validation_alignment` records filtering. JS commands require `package.json` evidence, Python commands require test/marker/layout evidence, and commands are omitted entirely when no runner evidence exists — **never invented** (`docs/CONTRACTS.md`, `grep -n "validation_alignment.*records whether"`).
- **Evidence labeling** — routing/claim evidence is labeled `parser-backed | rg-backed | graph-derived | heuristic | LSP-confirmed | stale/uncertain`; when signals disagree, downgrade confidence and surface the contradiction rather than hiding it behind one ranked file (`docs/CONTRACTS.md`, `grep -n "parser-backed"`). LSP availability is **not** semantic proof: a row counts as `lsp_proof` only with an explicit `lsp_provider_response = true` (`docs/CONTRACTS.md`, `grep -n "lsp_provider_response = true"`).

## Known-weak points (state plainly, never oversell)

Encode these honestly; the dogfood report itself emits `world_class_readiness.status = "not_claimed"` (`docs/CONTRACTS.md`, `grep -n "world_class_readiness"`). Everything unproven stays labeled candidate/experimental. Experimental, default-OFF: GPU, LSP, semantic, CyBERT/provider paths.

1. **Flat, no-IDF ranking scorer.** Repo-map scoring uses flat integer term counts (`_score_text_terms`/`_score_file_path`/`_score_symbol` in `src/tensor_grep/cli/repo_map.py` -- `grep -n 'def _score_' src/tensor_grep/cli/repo_map.py`), not IDF/term-rarity weighting. Ranking surfaces (`search --rank`, the agent capsule, semantic) can silently **flip** on a corpus change, and the blast radius of a ranking change is **invisible to the call graph**. A degrade-to-ask safety floor was added in #302; the flat scorer itself remains open debt. Treat any ranking-affecting change as high-risk and benchmark it.
2. **GPU Phase-0 SHIPPED (v1.75.0-v1.75.4, PRs #593-#597) but no speed crossover is proven, and the shipped kernel is NOT what the roadmap language implies.** NVIDIA native assets are built and locally correctness-proven (RTX 4070 `sm_89` / RTX 5070 `sm_120`, 1GB/5GB match+file-set correctness -- `docs/gpu_crossover.md`), but gated OFF the public release by the CI Actions var `TENSOR_GREP_RELEASE_NATIVE_ASSET_PROFILE` (default `native-frontdoor`, CPU-only; GPU asset publishing needs the non-default `native-frontdoor-gpu`). Phase 1 (publishing those already-built assets) is now a **reversible flag-flip**, not a multi-week rebuild -- but flipping the var publishes assets only: it does **not** promote GPU, does **not** change the CPU-default auto-recommendation, and does **not** prove a speed crossover. **The shipped kernel (`gpu_text_search_positions`) is a position-parallel brute-force byte-compare, NOT a PFAC/Aho-Corasick automaton** (`docs/gpu_crossover.md:133-138` — PFAC remains documented future work, not what runs today). No crossover is proven at ANY scale, including the best-case many-fixed-pattern lane (100 patterns over 1GB: fair-baseline `rg -F -e ... -e ...` at 0.169s vs the GPU-requested path at 0.448s, itself a CPU-fallback measurement, not even a real GPU number); historical worst case at 5GB is ~30-35x slower than `rg`. Keep the honesty floor verbatim: no speed crossover is proven vs `rg`/`tg_cpu`, GPU auto-recommendation stays `false`, and the reviewer-gated `public-gpu-proof.yml` speed-crossover gate remains unmet (`docs/CONTRACTS.md`, `grep -n "gpu_evidence_status.*gpu_proof.*native_gpu_unavailable"`). **Public CUDA-asset publishing is on a deliberate HOLD** (CEO decision package, task-store #169 — not a GitHub issue, re-verify with `gh issue list`); release checksums currently ship 3 CPU-only rows. Explicit `--gpu-device-ids` stays supported and must fail loud when unhonorable; sidecar-routed GPU output is compatibility evidence, never GPU-acceleration proof.
3. **The raw-grep parity gap is control-plane latency, not backend cleverness.** When tg trails rg on cold text it is launcher/dispatch overhead; the likely fix is a more native launcher path, not Python micro-tuning. Benchmark artifacts must record `tg_launcher_mode` + `tg_launcher_command_kind` and refuse stale in-tree binaries by default (`docs/CONTRACTS.md`, `grep -n "environment.tg_launcher_mode"`) so you never mix a `.cmd`-shim timing into a speed claim.
4. **FFI is not the directory-scan speed path.** PyO3 FFI overhead for directory walking was measured too high and reverted to native CPython directory scanning — a settled battle. Do not re-propose "just move the dir walk into the Rust extension" without new measurements. Full story + the mock-FFI-passed-while-the-real-bridge-was-dead lesson: `tensor-grep-failure-archaeology`.
5. **rg-parsing edge cases (round-4, open, narrowed):** `rg#3364` (`--multiline --pcre2 --json` emits one match with two submatches) is open upstream and `rg#3131` (`rg -c` omits NUL-byte files) is closed upstream; neither has a tg fixture, and BOM-in-`.gitignore` is likewise unverified against this repo. **The native-argv `--` sentinel gap this point used to describe is now FIXED, not open:** `rust_core/src/rg_passthrough.rs`'s `ripgrep_operand_args` (`grep -n "^fn ripgrep_operand_args" rust_core/src/rg_passthrough.rs`) forwards patterns safely via `-e` and inserts a `--` sentinel before any user paths, closing the "a directory literally named `-l` flips rg to files-with-matches" CWE-88 gap; 3 unit tests pin it (`grep -n "^    fn operand_args_" rust_core/src/rg_passthrough.rs`). Do not cite this as open. See `code-search-and-retrieval-reference` §7 for the full detail.
6. **Scoped file-dependency primitive — C14 RESOLVED (#74, v1.54.x).** `tg imports FILE` (forward, O(1)) and `tg importers FILE [ROOT]` (bounded reverse) ship as the cheap alternative to whole-repo `tg map`/`tg orient` for single-file dependency questions. Dogfood: `tg imports` on `main.py` returned 31 edges in ~1.8s; `tg importers` found 1 confirmed importer in ~12s on tensor-grep. Recommend `imports`/`importers` first; reserve `map`/`orient` for whole-repo architecture.
7. **B9 — ReDoS gate on `-w`/`-x`/`-C`/`--ltl`/UTF-8-fallback/native-failure/`--pcre2` — RESOLVED (audit #6/#16/#111, closed 2026-07-10).** `cpu_backend.py`'s linear-time Rust-regex routing now covers every path that can reach Python's backtracking `re`: `-w`/`-x`/`-C`/`-A`/`-B` route through `_search_word_line_context_via_rust` (`grep -n "def _search_word_line_context_via_rust" src/tensor_grep/backends/cpu_backend.py`, match-set via the linear-time Rust engine, context assembled in pure Python); `--ltl` routes through `_search_ltl` (`grep -n "def _search_ltl" src/tensor_grep/backends/cpu_backend.py`) via the same helper; and the "simple pattern" route's three residual paths — UTF-8-fallback (`_RustUtf8DecodeMismatch`, `grep -n "class _RustUtf8DecodeMismatch" src/tensor_grep/backends/cpu_backend.py`), `--pcre2` (its own unconditional raise, `grep -n "pcre2.* approximation" src/tensor_grep/backends/cpu_backend.py`), and a non-syntax native runtime fault (the final `except Exception` branch) — all gate on `_fallback_pattern_is_provably_linear` (`grep -n "_fallback_pattern_is_provably_linear" src/tensor_grep/backends/cpu_backend.py` for the def and its call sites), failing closed with `BackendExecutionError` unless the pattern is `fixed_strings`. Audit #111 found the UTF-8-fallback + native-failure bypasses (the code assumed "Rust ran it in O(n), so it's ReDoS-safe" and fell open to unbounded Python `re`). **The first fix attempt — a static "no `*+?{` quantifier char" allow-list — was BLOCKED by the adversarial Opus security gate as PROVABLY UNSOUND:** catastrophic backtracking has a second source besides repetition, variable-length ALTERNATION `(a|aa)...(a|aa)b` (`"(a|aa)"*k + "b"`) backtracks 2^k with no quantifier char (measured k=24 → 6.19s), attacker-dialable by pattern length on a tiny file. The shipped gate admits ONLY `fixed_strings` (re.escape'd → literal automaton → provably linear regardless of raw pattern text); everything else fails closed. This deliberately fails closed a legit non-ASCII regex on a non-UTF-8 file — the endorsed security-over-availability trade (use `--fixed-strings` or ripgrep). **Durable guidance (two rules): (a) "Rust accepted/ran this pattern" is NEVER evidence Python's backtracking `re` can run it safely — Rust has no catastrophic-backtracking failure mode for any pattern it accepts; (b) NO static pattern-char analysis is a sound gate — only the structural `fixed_strings` guarantee is. Any new fallback path must admit only `fixed_strings` or fail closed.**

## Domain background

For the protocol/algorithm background behind this design (rg exit codes including the exit-2 partial-results contract above, BM25/IDF, PyO3 + the GIL, the MCP argv flag-injection surface) see `code-search-and-retrieval-reference`; for the mock-FFI-passed-while-the-real-bridge-was-dead lesson see `tensor-grep-failure-archaeology`.

## Fast self-check before you trust a claim about this design

```powershell
# Front door + version identity
uv run tg --version                                  # the version must match: grep '^version' pyproject.toml
# The published entry point (must be bootstrap.main_entry, not a Typer callback)
uv run python -c "import tensor_grep.cli.bootstrap as b; print(b.main_entry)"
# Routing / launcher observability
uv run tg doctor --json | python -c "import sys,json;d=json.load(sys.stdin);print(d.get('search_acceleration_backend'), d.get('path_tg_first_launcher_kind'))"
# Fast readiness gate (context_consistency, parity edges, registration, capsule invariants)
uv run python scripts/agent_readiness.py --output artifacts/agent_readiness.json
```

Never claim a speedup, a fixed weak point, or "tests pass" from a model self-report. Confirm against external state: an exit code, a real-binary dogfood, a `file:line` that resolves. A subagent's "green" is a hypothesis until then.

## Provenance and maintenance

Citations in this file are symbol greps, not line numbers: line numbers in `main.py`, `repo_map.py`, `bootstrap.py`, and `mcp_server.py` drift every release. Before relying on a claim, run its grep; if the result disagrees, fix this file in the same PR.

Re-verify anything volatile before relying on it:

- **Version:** `grep '^version' pyproject.toml`.
- **Front-door entry point:** `grep -n "^def main_entry" src/tensor_grep/cli/bootstrap.py`, and confirm `pyproject.toml` still points `tg` at `tensor_grep.cli.bootstrap:main_entry`.
- **Command registration sites (4):** `grep -n "^KNOWN_COMMANDS" src/tensor_grep/cli/commands.py`; `grep -n "pub enum Commands\|Commands::Prepare\|Commands::Ledger" rust_core/src/main.rs`; `grep -n "PUBLIC_TOP_LEVEL_COMMANDS =" tests/e2e/test_routing_parity.py`; `@app.command` in `src/tensor_grep/cli/main.py`.
- **Flag front doors (2):** `grep -rn "const SEARCH_PYTHON_PASSTHROUGH_FLAGS" rust_core/src`; `grep -n "^_TG_ONLY_SEARCH_FLAGS" src/tensor_grep/cli/bootstrap.py`.
- **MCP contract version (5th site):** `grep -n "_TG_MCP_SERVER_CONTRACT_VERSION = \|^def _inject_mcp_contract_fields" src/tensor_grep/cli/mcp_server.py`.
- **Native-delegation forward-or-refuse contract:** `grep -n "^def _can_delegate_to_native_tg_search\|^def _build_native_tg_search_command\|_NATIVE_TG_DELEGATION_DEFAULT_REQUIRED_FIELDS = " src/tensor_grep/cli/main.py`; run `tests/unit/test_native_delegation_field_coverage.py` after touching `SearchConfig` -- a new unclassified field turns it red immediately, which is the point.
- **Partial-results contract:** `grep -n "result_incomplete: bool\|incomplete_reason: str\|^def merge_runtime_routing" src/tensor_grep/core/result.py`; exit-code wiring `grep -n "result_incomplete else\|exit_incomplete else" src/tensor_grep/cli/main.py` (exit 2 regardless of found, per #401); envelope `grep -n "result_incomplete" src/tensor_grep/cli/formatters/json_fmt.py`; MCP `grep -n '"result_incomplete"' src/tensor_grep/cli/mcp_server.py`. Re-verify: `tests/unit/test_rg_exit2_partial.py` green.
- **AST routing (DSL preference, no GPU gate):** `grep -n "def is_available" -A 12 src/tensor_grep/backends/ast_backend.py` must show no `torch`/`cuda` reference; `grep -n "^def _select_ast_backend_for_pattern\|_check_backend_available(\"AstGrepWrapperBackend\")" src/tensor_grep/cli/ast_workflows.py` for the wrapper-preference block.
- **A9 walk-ceiling:** `grep -n IMPLICIT_SEARCH_WALK_FILE_CEILING src/tensor_grep/io/scan_limits.py rust_core/src/rg_passthrough.rs`; `grep -n _search_paths_include_oversized_implicit_root src/tensor_grep/cli/bootstrap.py`.
- **A10/A15 dynamic_unresolved:** `grep -n dynamic_unresolved src/tensor_grep/cli/repo_map.py`.
- **A11 cross-domain detection:** `grep -n is_cross_domain_native_binary src/tensor_grep/cli/runtime_paths.py`.
- **A3 native_search.rs vs backend_cpu.rs:** `ls rust_core/src/native_search.rs rust_core/src/backend_cpu.rs`.
- **Fail-closed contract:** `src/tensor_grep/backends/base.py` (`BackendExecutionError`, `ComputeBackend`).
- **Routing tree:** `docs/routing_policy.md` + `grep -n "pub const fn route_search\|pub const fn native_can_serve_plain_text" rust_core/src/routing.rs`.
- **Capsule/context invariants:** `docs/CONTRACTS.md` §3 (search for `context_consistency`, `ambiguity.status`, `validation_alignment`).
- **rg timeout default:** `grep -n TG_RG_TIMEOUT_SECONDS src/tensor_grep/cli/subprocess_policy.py` (default `60.0`).
- **rg-passthrough `--` sentinel (RESOLVED, not open):** `grep -n "^fn ripgrep_operand_args\|^    fn operand_args_" rust_core/src/rg_passthrough.rs`.
- **Registration CI gate BLOCKING:** `grep -n "registration-completeness gate is BLOCKING" AGENTS.md`.
- **C14 (scoped file-dep primitive):** RESOLVED -- `tg imports` / `tg importers` are in `KNOWN_COMMANDS`; re-verify with `tg imports --help` / `tg importers --help` before citing.
- **B9 (ReDoS gate, RESOLVED audit #6/#16/#111):** `grep -n "_fallback_pattern_is_provably_linear\|needs_word_or_context_rust_routing" src/tensor_grep/backends/cpu_backend.py` should find the fixed_strings-only gate plus its call sites in BOTH the `_RustUtf8DecodeMismatch` handler AND the generic native-failure `except Exception` handler; the gate must admit ONLY `fixed_strings`. A future new fallback path could reopen the class the way #111 did after #6/#16.
- **`MatchLine` hashability (#344/`80de0b4`):** `grep -n "^class MatchLine" src/tensor_grep/core/result.py`. Re-verify: `python -c "from tensor_grep.core.result import MatchLine; hash(MatchLine(1,'x','f.py',submatches=({'start':0,'end':1},)))"` must not raise.
- **ASCII-only CLI output (#346/`6b7b518`):** fixed call site `src/tensor_grep/cli/inventory.py`. Re-verify: grep new CLI-rendered string literals for non-ASCII before shipping; no automated gate enforces this yet (a governance test is a reasonable follow-up, not yet shipped).

If a re-verify disagrees with this skill, fix the skill — a wrong runbook is worse than none — and route any code change through `tensor-grep-change-control`.
