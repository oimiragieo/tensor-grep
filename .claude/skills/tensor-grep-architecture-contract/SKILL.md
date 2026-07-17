---
name: tensor-grep-architecture-contract
description: Use when you need the load-bearing design of tensor-grep and WHY it holds before touching cli/bootstrap.py, rust_core/src/main.rs, backends/, core/result.py, cli/main.py's native-delegation gate, routing, the agent capsule, or before reviewing/planning any change to the front door, command/flag registration, or backend contract. Explains the bootstrap intercept-before-Typer front door, native-vs-Python routing, the 4 command + 2 flag registration sites, the Backend Fail-Closed Contract, the native-delegation forward-or-refuse contract (`_can_delegate_to_native_tg_search` + its field-coverage ratchet), the partial-results `result_incomplete`/`incomplete_reason` envelope, `MatchLine`'s frozen-but-hashable dataclass contract, the ASCII-only CLI output rule, the agent-context moat, the invariants that must hold, and the known-weak points (flat no-IDF scorer, GPU not viable, rg parity gap, FFI not the dir-scan speed path). Read this to build the right mental model; use sibling skills for the how-to of changing, debugging, or benchmarking.
---

# tensor-grep architecture contract

**What this is.** A ground-truthed map of tensor-grep's load-bearing design: the invariants a change must not break, and the weak points you must not oversell. Read it to understand *why* the code is shaped this way before you touch it. It is not a how-to — for that, hand off to a sibling (routing table below).

**What tensor-grep is** (as of 2026-07-16, v1.78.1, `pyproject.toml`): a code-intelligence CLI named `tg`. A Rust core (`rust_core/` — both a PyO3 extension *and* a standalone `tg` binary) plus a Python CLI (`src/tensor_grep/`). Apache-2.0. Ships to PyPI (package `tensor-grep`), npm, Homebrew, winget. CONTRIBUTING.md calls it a "benchmark-governed, contract-heavy codebase" — that is the whole point: the contracts below are enforced by tests and a CI gate, not by convention.

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

`tg` is not "a Typer app." The published entry point is `bootstrap.main_entry` (`src/tensor_grep/cli/bootstrap.py:926`). It parses `argv` itself and, for a **plain text search**, forwards to the native `tg` binary or to ripgrep *before Typer ever runs* (`bootstrap.py:959-984`). The Typer app is only reached for TG-only flags, help, or commands that require full CLI (`_requires_full_cli`, `bootstrap.py:298`).

Why this matters, concretely:

- **CliRunner cannot see routing bugs.** It invokes the Typer app directly, so any bug in `bootstrap` routing (a flag that leaks to `rg`, a fork-bomb delegation loop, a wrong native/Python choice) is **invisible** to CliRunner tests and green in CI while broken for real users. This is exactly how the `--rank` plain-text crash shipped (AGENTS.md:198-200 "Dogfood the Real Binary, Not CliRunner"). **Rule: verify front-door behavior against the REAL published binary** via `scripts/dogfood/` (Dockerfile + `dogfood_features.py`), never CliRunner alone.
- **Two mutual-delegation fork-bomb hazards are guarded, not theoretical.** `TG_REEXEC_GUARD` (checked at `bootstrap.py:979`) stops native→python→native search loops; `_json_aggregate_blocks_passthrough` (`bootstrap.py:374`) stops `--json` + a render-only flag (e.g. `-b`) from deadlocking the native front door; `_run_requires_ast_workflow` (`bootstrap.py:896`) keeps `tg run --selector/--strictness/--stdin/--globs` in Python so it does not ping-pong. If you touch delegation, you can re-arm a fork bomb — see `tensor-grep-failure-archaeology`.

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
- **AST routing is policy vs runtime-capability split:** `tg run` policy-routes to `AstBackend`, but real execution needs `AstBackend().is_available()`, which requires `torch_geometric` **and** `tree_sitter` **and a CUDA device** — `is_available()` returns `bool(torch.cuda.is_available())` (`ast_backend.py:508-521`). So on the common **non-GPU** box the native AST path never runs; `tg run` always falls back to the `ast-grep` CLI sidecar (also for string metavar queries like `def $F($$$ARGS)`) — visibly, per the fail-closed contract below. This matches `code-search-and-retrieval-reference` §2.

## The registration sites (miss one → silent misroute)

This is a **universal bug class**: "register in N places, miss one, fail *quietly*." The CI registration-completeness gate has been **BLOCKING since v1.17.1 / #282** (AGENTS.md:215), but you still author all sites by hand.

**A new top-level `tg COMMAND` needs four sites** (AGENTS.md "Adding a Command or Flag", starting at line 197; re-derive by grepping the header, not the line number, since AGENTS.md's line numbers shift as sections are added above it):

| # | Site | File |
|---|---|---|
| 1 | `KNOWN_COMMANDS` set | `src/tensor_grep/cli/commands.py:9` |
| 2 | `Commands::X` variant + dispatch arm | `rust_core/src/main.rs:860` (enum) |
| 3 | `PUBLIC_TOP_LEVEL_COMMANDS` (parity test) | `tests/e2e/test_routing_parity.py:17` |
| 4 | `@app.command` function | `src/tensor_grep/cli/main.py` |

**A new search flag needs two front doors** (AGENTS.md, same section, "two front doors") or it leaks to ripgrep and crashes with `rg: unrecognized flag` for anyone on the published binary:

| # | Site | File |
|---|---|---|
| 1 | `SEARCH_PYTHON_PASSTHROUGH_FLAGS` (native allowlist) | `rust_core/src/main.rs:170` |
| 2 | `bootstrap._TG_ONLY_SEARCH_FLAGS` (Python front-door allowlist) | `src/tensor_grep/cli/bootstrap.py:24` |

**Blind spot to internalize:** `tg callers <fn>` finds *callable* registration sites in ~1s, but the call graph **cannot see set/list/decorator registrations** — `_TG_ONLY_SEARCH_FLAGS` is a set, `@app.command` is a decorator, the Rust dispatch is a match arm. Those are the sites most often missed (`--rank` lived in a *set*). So `tg callers` for the reachable ones **and** grep / `tg scan` for the declarative ones, then confirm your entry appears in *all* sites. (The actual add-a-thing procedure lives in `tensor-grep-change-control`; this skill only explains why the sites exist.)

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

Companion invariant to the Backend Fail-Closed Contract above, shipped in round-4 slice 3 (#341, commit `f11ce28`, v1.18.x). `SearchResult` (`src/tensor_grep/core/result.py:21+`, fields at `:54-55`) carries `result_incomplete: bool = False` and `incomplete_reason: str | None = None`, deliberately **not** overloaded onto `fallback_reason` — `fallback_reason` means "the execution engine was swapped"; `result_incomplete` means "this engine ran, but a soft per-item error suppressed part of the output." Conflating them would emit a false "we fell back" signal to `doctor`/JSON consumers.

The trigger: rg exit code **2** is a *soft* per-file error (e.g. one unreadable/missing path among many) and rg still emits matches for every readable file. Before #341, tg's parser raised unconditionally on `exit > 1`, **discarding those partial matches** — and even if it hadn't, tg would have silently exited 0 while rg exits 2 (a parity break an agent scripting around exit codes would never see).

**And the exit-code side of this contract has since been made STRICTER, not looser — do not describe it as "empty partial -> exit 2, non-empty partial -> exit 0."** #398 first made ANY truncated partial exit 2; #399 briefly walked that back to exit-2-only-when-empty; **#401 reverted #399** after a unanimous design council — the current, final contract is: any `result_incomplete`/`partial` result exits **2 regardless of whether matches were found**, because a truncated match/caller/blast-radius list must never be silently trusted as exhaustive. See `tensor-grep-large-repo-scale-campaign` §5 for the full exit-code table and `docs/CONTRACTS.md:109`.

The 5-site fix, cite `file:line`:

- **Parse-first-then-branch** — `backends/ripgrep_backend.py` (`search`, `_search_files_with_matches`, `_search_counts`): exit 2 with a non-empty parse *keeps* the results, sets `result_incomplete=True` + a stderr-derived `incomplete_reason` (`ripgrep_backend.py:123-128,297,413`); exit >2, or exit 2 with nothing parsed, raises `BackendExecutionError` (**RESOLVED #79/#10/#14, commit `a7c9431`**: every `RipgrepBackend` fatal path, including the rg-missing guard at `:505`, now raises `BackendExecutionError` instead of a bare `RuntimeError`, so `cli/main.py`'s per-file `except BackendExecutionError` CPU-fallback retry (`:7481`) catches it instead of falling into the broad `except Exception` and crashing the whole search — see `code-search-and-retrieval-reference` §1 for the exit-code table).
- **Monotonic merge** — `merge_runtime_routing` (`core/result.py:67+`) OR-merges `result_incomplete` across sub-results (`aggregate.result_incomplete or result.result_incomplete`), so the CLI/MCP/sidecar aggregate inherits uniformly — any incomplete sub-result taints the whole.
- **Exit-code parity** — `cli/main.py:6918-6953`: the terminal exits now read `sys.exit(2 if all_results.result_incomplete else …)` across the files-with/without-matches, `is_empty`, quiet, and post-format branches, closing the "tg exits 0 while rg exits 2" gap.
- **JSON/NDJSON envelope** — `cli/formatters/json_fmt.py:126-127,189-190`: `result_incomplete`/`incomplete_reason` are emitted **only when incomplete**, so a complete result's JSON shape stays byte-identical to before #341.
- **MCP** — `cli/mcp_server.py:1645,3117,3174,3202,3261` (multiple call sites, not one): the structured `tg_search`/graph-command responses carry both fields top-level — suppression must be visible to an agent, not buried in a log line.

**Rule for any new path that can drop some results due to a soft/partial failure:** set `result_incomplete` + `incomplete_reason`. Do not (a) raise and lose the good results, or (b) silently return only the good results as if they were the complete answer — that is the same "suppression reads as absence" lie the Backend Fail-Closed Contract forbids, just at the partial-result layer instead of the total-failure layer. Tests: `tests/unit/test_rg_exit2_partial.py`.

## Native-delegation forward-or-refuse contract (`_can_delegate_to_native_tg_search`)

`cli/main.py:3311+` gates whether a Python-side `tg search` hands the **entire** search to the native `tg` subprocess (`_build_native_tg_search_command`, `cli/main.py:3333+`) and then `sys.exit()`s on its result — a delegation that runs *before* the Python-side BM25 rerank (`--rank`) and the in-backend sort (`--sort-files`) ever execute.

**The invariant:** delegation is permitted only when native execution is byte-equivalent to the Python path for the requested config. The gate enforces this mechanically, not by convention — it loops every field name in `_NATIVE_TG_DELEGATION_DEFAULT_REQUIRED_FIELDS` (`cli/main.py:1783-1886`) and **refuses** delegation (falls through to the Python/backend path) if *any* of those fields differs from a fresh `SearchConfig()`'s default. Every `SearchConfig` field must land in exactly one bucket:

1. **Forwarded** — read by `_build_native_tg_search_command` and translated into native argv.
2. **Refused** — listed in `_NATIVE_TG_DELEGATION_DEFAULT_REQUIRED_FIELDS`, so a non-default value forces the gate closed.
3. **Gate-handled** — read off explicit keyword args at the call site (`files_with_matches`, `files_without_match`), not the config object.
4. **KNOWN_GAP** — explicitly documented pre-existing tech debt, tracked rather than silently dropped.

This is enforced by a governance **ratchet**, `tests/unit/test_native_delegation_field_coverage.py` (round-4 #25, shipped as #342, commit `5e6f780`): it AST-derives the "forwarded" set straight from `_build_native_tg_search_command`'s source (`ast.walk` over every `config.<attr>` read), so that list can never silently drift from the real code, then asserts `all_fields - (forwarded | required | gate_handled | known_gap) == set()`. Add a new `SearchConfig` field and forget to classify it → this test goes red immediately.

**The bug this closes (#342):** `rank_bm25` and `sort_files` were neither forwarded to native argv nor in the refuse-tuple, so `tg search --rank --cpu` silently delegated to the native binary — which has no BM25 of its own — and `sys.exit()`d *before* the Python rerank/sort ever ran, returning unranked/unsorted output that looked like a normal, correct result (suppression indistinguishable from absence, same class the partial-results contract above targets). This is the **same flag-drop bug class** as the `-u`/`-uu` no-op fixed in #336 (round-4 PR-A slice 1): a flag parses successfully but never reaches the engine that must honor it.

**Landmine already hit once — do not re-propose it:** the tempting "just gate on any field differing from defaults" fix is wrong. `query_pattern` is auto-set to the search pattern on *every* search, so a differs-from-default check would always see a difference and refuse delegation on every call, killing the fast path entirely (the exact failure mode from the 2026-06-30 #1 audit finding — see `tensor-grep-failure-archaeology`). The fix has to be per-field, not "any field changed."

**Rule when adding a new `SearchConfig` field that affects search output:** decide immediately whether native delegation can reproduce it byte-for-byte. If not, add the field name to `_NATIVE_TG_DELEGATION_DEFAULT_REQUIRED_FIELDS`. The ratchet test refuses to let you skip this decision silently — it is a hard gate, not a lint suggestion.

## `MatchLine` is a frozen, HASHABLE dataclass

`src/tensor_grep/core/result.py:4-17` (`@dataclass(frozen=True) class MatchLine`). `submatches` (`tuple[dict[str, object], ...] | None`, added by #340 to carry rg's per-occurrence byte offsets for `--vimgrep`/`--column`) is a tuple-of-dicts — dicts are unhashable, so a populated `submatches` would break `hash(MatchLine(...))` the moment a frozen dataclass's default hash implementation (derived from its `==`-participating fields) tried to hash it.

The fix (#344, commit `80de0b4`): `submatches: tuple[dict[str, object], ...] | None = field(default=None, compare=False)`. `compare=False` excludes the field from both `__eq__` and the derived `__hash__`, so `MatchLine` stays hashable even when `submatches` is populated. Excluding it from `==` is intentionally correct, not a shortcut: the offsets are a pure function of `text` + `line_number`, so two matches equal on those fields are equal regardless of any incidental difference in their submatch tuples.

No caller hashes `MatchLine` today — this was caught as a **latent landmine** before any set/dedup consumer existed, not a live crash. Treat it as the standing precedent: this codebase keeps its frozen dataclasses hashable on purpose. **Any new field added to a frozen dataclass here that is itself unhashable (a `list`, `dict`, or other mutable/unhashable container) must be marked `field(..., compare=False)`** — or, if it genuinely must participate in equality, the dataclass needs a deliberate `eq=False`/custom `__hash__` redesign, not a silent break.

Adjacent, same commit: the per-match submatch stash is now built only behind `config.vimgrep or config.column` in `RipgrepBackend.search` — only those two formatters consume the offsets, so building the tuple on every default-format match was wasted allocation (found via the blast-radius-regression profile that produced #345, not a separate bug hunt). Output stays byte-identical; `--vimgrep`/`--column` still emit one row per rg occurrence.

## ASCII-only CLI output contract

`tg` does not reconfigure `stdout` to UTF-8, and Windows consoles commonly default to the `cp1252` codepage. `typer.echo` (used throughout the CLI) **raises `UnicodeEncodeError`** on any character outside that codepage — a hard crash, not mojibake. #346 (commit `6b7b518`) found `render_inventory_text` in `src/tensor_grep/cli/inventory.py` emitting a literal `⚠` (U+26A0 WARNING SIGN) on the truncation-notice path (repo > `max_files`); on a stock Windows terminal, `tg inventory` on a large repo crashed instead of printing a warning.

**Rule: no non-ASCII characters in any `tg`-CLI-rendered text output** (Typer `echo`/`print` call sites — this governs strings *tg itself* prints, not file contents being searched). Use bracketed ASCII markers instead — the fix replaced `⚠` with the literal string `[!]`. Before adding a new CLI-rendered string (a warning glyph, a checkmark, box-drawing table characters, an arrow), check it is `str.isascii()`-clean; if in doubt, dogfood on a real `cp1252` Windows console, not just a UTF-8-default terminal or CI runner — **CI's UTF-8 locale will not catch this class of bug**, it is Windows-console-only and was found by dogfooding a large real repo locally (`tensor-grep-dogfood-real-corpus-before-shipping-precision-2026-07-03` memory), not by the fixture test suite.

## The moat: agent-native context, not faster grep

Positioning is a design constraint, not marketing. **tg is not a faster grep.** ripgrep is the raw-text parity baseline; ast-grep is the structural-search baseline. The moat is the **agent-native code-intelligence layer**: `orient`, `callers`, `blast-radius`, `defs`, `refs`, `source`, `agent` (the capsule), `session`, `find` (whole-repo hybrid NL search, v1.77.0, #189). Peers to know: Aider repo-map (tree-sitter + NetworkX PageRank, `--map-tokens`), Sourcegraph Cody (SCIP + BM25 + embeddings → rerank), Cursor (index-first embeddings + Merkle change detection).

Engineering-capacity consequence (AGENTS.md "Roadmap Sequencing 2026-07-02"): CPU-only, every-install moat work is funded *first* — local hybrid semantic search (BM25 + CPU dense embeddings + RRF, no API key), `tg registration-check` as a first-class command, a Bloom-filter n-gram chunk prefilter — before advancing the GPU program. Never make a change that implies "tg beats rg for cold exact-text search."

## Invariants that must hold (agent contract)

These are enforced by the capsule/context contract (`docs/CONTRACTS.md` §3, "Context and edit-planning contracts") and the agent-readiness gate. A change that breaks one is a contract regression even if tests are green.

- **`context_consistency`** — `edit_plan_seed.primary_file`, `navigation_pack.primary_target.file`, the rendered source sections, and follow-up read commands must not contradict each other. The payload reports whether the primary file is included, whether rendered context matches the target, whether confidence was downgraded, and why a primary file was omitted (CONTRACTS.md:89-90).
- **Ambiguity hard-stop** — when equal-confidence alternatives are unresolved, cap `confidence.overall` and `primary_target.confidence` below the edit threshold, set `ask_user_before_editing.required = true`, and mirror it in top-level `ambiguity.status = "tie_requires_confirmation"`. A validation-resolved tie records `ambiguity.status = "tie_resolved"`, `resolved_by = "targeted-validation"` with concrete `resolution_evidence`; an LSP-resolved tie needs explicit provider-response proof (CONTRACTS.md:102). This is the safety floor added in #302 — do not weaken it.
- **Validation provenance** — validation hints use `validation_plan[].detection ∈ {detected, heuristic, generic}` and must align with the primary target language: a TS target must not silently get pytest, a Python target must not silently get `npm test`; `validation_alignment` records filtering. JS commands require `package.json` evidence, Python commands require test/marker/layout evidence, and commands are omitted entirely when no runner evidence exists — **never invented** (CONTRACTS.md:92-93).
- **Evidence labeling** — routing/claim evidence is labeled `parser-backed | rg-backed | graph-derived | heuristic | LSP-confirmed | stale/uncertain`; when signals disagree, downgrade confidence and surface the contradiction rather than hiding it behind one ranked file (CONTRACTS.md:103). LSP availability is **not** semantic proof: a row counts as `lsp_proof` only with an explicit `lsp_provider_response = true` (CONTRACTS.md:104).

## Known-weak points (state plainly, never oversell)

Encode these honestly; the dogfood report itself emits `world_class_readiness.status = "not_claimed"` (CONTRACTS.md:85). Everything unproven stays labeled candidate/experimental. Experimental, default-OFF: GPU, LSP, semantic, CyBERT/provider paths.

1. **Flat, no-IDF ranking scorer.** Repo-map scoring uses flat integer term counts (`_score_text_terms`/`_score_file_path`/`_score_symbol` in `src/tensor_grep/cli/repo_map.py:5819,5896,5923`), not IDF/term-rarity weighting. Ranking surfaces (`search --rank`, the agent capsule, semantic) can silently **flip** on a corpus change, and the blast radius of a ranking change is **invisible to the call graph**. A degrade-to-ask safety floor was added in #302; the flat scorer itself remains open debt. Treat any ranking-affecting change as high-risk and benchmark it.
2. **GPU Phase-0 SHIPPED (v1.75.0-v1.75.4, PRs #593-#597) but no speed crossover is proven.** NVIDIA native assets are built and locally correctness-proven (RTX 4070 `sm_89` / RTX 5070 `sm_120`, 1GB/5GB match+file-set correctness -- `docs/gpu_crossover.md`), but gated OFF the public release by the CI Actions var `TENSOR_GREP_RELEASE_NATIVE_ASSET_PROFILE` (default `native-frontdoor`, CPU-only; GPU asset publishing needs the non-default `native-frontdoor-gpu`). Phase 1 (publishing those already-built assets) is now a **reversible flag-flip**, not a multi-week rebuild -- but flipping the var publishes assets only: it does **not** promote GPU, does **not** change the CPU-default auto-recommendation, and does **not** prove a speed crossover. Keep the honesty floor verbatim: no speed crossover is proven vs `rg`/`tg_cpu` (GPU remains slower after CUDA startup/H2D transfer/output materialization for the only *candidate* wedge, many-fixed-strings resident over a large corpus — **not** single-pattern cold grep, where PCIe/setup cost loses), GPU auto-recommendation stays `false`, and the reviewer-gated `public-gpu-proof.yml` speed-crossover gate remains unmet (CONTRACTS.md:80-82). Explicit `--gpu-device-ids` stays supported and must fail loud when unhonorable; sidecar-routed GPU output is compatibility evidence, never GPU-acceleration proof.
3. **The raw-grep parity gap is control-plane latency, not backend cleverness.** When tg trails rg on cold text it is launcher/dispatch overhead; the likely fix is a more native launcher path, not Python micro-tuning. Benchmark artifacts must record `tg_launcher_mode` + `tg_launcher_command_kind` and refuse stale in-tree binaries by default (CONTRACTS.md:82) so you never mix a `.cmd`-shim timing into a speed claim.
4. **FFI is not the directory-scan speed path.** PyO3 FFI overhead for directory walking was measured too high and reverted to native CPython directory scanning — a settled battle. Do not re-propose "just move the dir walk into the Rust extension" without new measurements. Full story + the mock-FFI-passed-while-the-real-bridge-was-dead lesson: `tensor-grep-failure-archaeology`.
5. **rg-parsing edge cases (round-4, open, narrowed):** `rg#3364` (`--multiline --pcre2 --json` emits one match with two submatches), `rg#3131` (`rg -c` omits NUL-byte files), BOM-in-`.gitignore` remain open/unverified against this repo. **The native-argv `--` sentinel gap this point used to describe is now FIXED, not open:** `rust_core/src/rg_passthrough.rs`'s `ripgrep_operand_args` (`:397-422`) forwards patterns safely via `-e` and inserts a `--` sentinel before any user paths (`:415-419`), closing the "a directory literally named `-l` flips rg to files-with-matches" CWE-88 gap; 3 unit tests pin it (`:609-648`). Do not cite this as open. See `code-search-and-retrieval-reference` §7 for the full detail.
6. **Scoped file-dependency primitive — C14 RESOLVED (#74, v1.54.x).** `tg imports FILE` (forward, O(1)) and `tg importers FILE [ROOT]` (bounded reverse) ship as the cheap alternative to whole-repo `tg map`/`tg orient` for single-file dependency questions. Dogfood: `tg imports` on `main.py` returned 31 edges in ~1.8s; `tg importers` found 1 confirmed importer in ~12s on tensor-grep. Recommend `imports`/`importers` first; reserve `map`/`orient` for whole-repo architecture.
7. **B9 — ReDoS gate on `-w`/`-x`/`-C`/`--ltl`/UTF-8-fallback/native-failure/`--pcre2` — RESOLVED (audit #6/#16/#111, closed 2026-07-10).** `cpu_backend.py`'s linear-time Rust-regex routing now covers every path that can reach Python's backtracking `re`: `-w`/`-x`/`-C`/`-A`/`-B` route through `_search_word_line_context_via_rust` (`cpu_backend.py:800`, match-set via the linear-time Rust engine, context assembled in pure Python); `--ltl` routes through `_search_ltl` (`:931`) via the same helper; and the "simple pattern" route's three residual paths — UTF-8-fallback (`_RustUtf8DecodeMismatch` at `:438`), `--pcre2` (`:485`), and a non-syntax native runtime fault (`:512`) — all gate on `_fallback_pattern_is_provably_linear` (`:280`), failing closed with `BackendExecutionError` unless the pattern is `fixed_strings`. Audit #111 found the UTF-8-fallback + native-failure bypasses (the code assumed "Rust ran it in O(n), so it's ReDoS-safe" and fell open to unbounded Python `re`). **The first fix attempt — a static "no `*+?{` quantifier char" allow-list — was BLOCKED by the adversarial Opus security gate as PROVABLY UNSOUND:** catastrophic backtracking has a second source besides repetition, variable-length ALTERNATION `(a|aa)...(a|aa)b` (`"(a|aa)"*k + "b"`) backtracks 2^k with no quantifier char (measured k=24 → 6.19s), attacker-dialable by pattern length on a tiny file. The shipped gate admits ONLY `fixed_strings` (re.escape'd → literal automaton → provably linear regardless of raw pattern text); everything else fails closed. This deliberately fails closed a legit non-ASCII regex on a non-UTF-8 file — the endorsed security-over-availability trade (use `--fixed-strings` or ripgrep). **Durable guidance (two rules): (a) "Rust accepted/ran this pattern" is NEVER evidence Python's backtracking `re` can run it safely — Rust has no catastrophic-backtracking failure mode for any pattern it accepts; (b) NO static pattern-char analysis is a sound gate — only the structural `fixed_strings` guarantee is. Any new fallback path must admit only `fixed_strings` or fail closed.**

## Domain background a mid-level engineer may lack

- **ripgrep internals:** default regex engine matches invalid UTF-8; PCRE2 requires valid UTF-8 and transcodes (hence `--pcre2` cannot be silently swapped); binary detection via NUL byte; exit **2 = non-fatal error**, exit 1 = clean no-match, exit 0 = match; `--` ends options; `-e` supplies patterns; `-uuu` = `--no-ignore --hidden --binary`.
- **BM25 / IDF:** IDF weights rare terms higher; the flat scorer above skips this, which is why a common term can dominate ranking after a corpus grows.
- **PyO3 + the GIL:** release the GIL (`py.detach`/`allow_threads`) for CPU-bound or subprocess work; mock-based FFI tests can pass while the real extension is dead — verify FFI against the **real** built extension (`maturin develop`), not mocks.
- **MCP argv surface:** MCP tool handlers forward LLM-controlled params into `tg`/`rg`/`git` argv — a flag-injection surface. List-argv (`shell=False`) stops *shell* injection but not *flag* injection; a `--` sentinel before user positionals is required. Security detail lives in `tensor-grep-failure-archaeology` and the round-3 hardening notes in AGENTS.md.

## Fast self-check before you trust a claim about this design

```powershell
# Front door + version identity
uv run tg --version                                  # expect: tensor-grep 1.78.1 (or current)
# The published entry point (must be bootstrap.main_entry, not a Typer callback)
uv run python -c "import tensor_grep.cli.bootstrap as b; print(b.main_entry)"
# Routing / launcher observability
uv run tg doctor --json | python -c "import sys,json;d=json.load(sys.stdin);print(d.get('search_acceleration_backend'), d.get('path_tg_first_launcher_kind'))"
# Fast readiness gate (context_consistency, parity edges, registration, capsule invariants)
uv run python scripts/agent_readiness.py --output artifacts/agent_readiness.json
```

Never claim a speedup, a fixed weak point, or "tests pass" from a model self-report. Confirm against external state: an exit code, a real-binary dogfood, a `file:line` that resolves. A subagent's "green" is a hypothesis until then.

## Provenance and maintenance

All facts verified against the live repo on 2026-07-08 at v1.49.3; the `tg find` fail-closed-boundary
paragraph and moat-command-list addition were verified 2026-07-16 at v1.78.1 (the rest was not
re-walked in this pass). Re-verify anything volatile before relying on it:

- **Version:** `grep '^version' pyproject.toml` (was `1.78.1`).
- **Front-door entry point:** read `src/tensor_grep/cli/bootstrap.py:926` (`def main_entry`) and confirm `pyproject.toml`/`packaging` still points `tg` at `tensor_grep.cli.bootstrap:main_entry`.
- **Command registration sites (4):** `commands.py:9` (`KNOWN_COMMANDS`), `rust_core/src/main.rs:860` (`enum Commands`), `tests/e2e/test_routing_parity.py:17` (`PUBLIC_TOP_LEVEL_COMMANDS`), `@app.command` in `src/tensor_grep/cli/main.py`.
- **Flag front doors (2):** `rust_core/src/main.rs:170` (`SEARCH_PYTHON_PASSTHROUGH_FLAGS`), `bootstrap.py:24` (`_TG_ONLY_SEARCH_FLAGS`).
- **Fail-closed contract:** `src/tensor_grep/backends/base.py` (`BackendExecutionError`, `ComputeBackend`).
- **Routing tree:** `docs/routing_policy.md` + `rust_core/src/routing.rs::route_search`.
- **Capsule/context invariants:** `docs/CONTRACTS.md` §3 (search for `context_consistency`, `ambiguity.status`, `validation_alignment`).
- **rg timeout default:** `src/tensor_grep/cli/subprocess_policy.py:44` (`TG_RG_TIMEOUT_SECONDS`, default `60.0`).
- **rg-passthrough `--` gap: RESOLVED, not open.** `rust_core/src/rg_passthrough.rs:397-422` (`ripgrep_operand_args`) inserts `--` before paths; 3 tests at `:609-648`. Do not re-open weak-point §5 as unresolved.
- **Registration CI gate BLOCKING:** confirm in `AGENTS.md` §"Adding a Command or Flag" (starts at line 197 as of this writing; as of #282/v1.17.1 for when it went blocking) — grep the header text, the line number shifts as sections are added above it.
- **Partial-results contract (added 2026-07-03, #341/`f11ce28`; exit-code side made stricter by #398/#399/#401):** `src/tensor_grep/core/result.py:54-55` (`SearchResult.result_incomplete`/`incomplete_reason`) + `:67+` (`merge_runtime_routing` OR-merge); exit-code wiring `src/tensor_grep/cli/main.py:6918-6953` (exit 2 fires regardless of found, per #401 — `docs/CONTRACTS.md:109`); envelope `src/tensor_grep/cli/formatters/json_fmt.py:126-127,189-190`; MCP `src/tensor_grep/cli/mcp_server.py:1645,3117,3174,3202,3261` (multiple sites). Re-verify: `tests/unit/test_rg_exit2_partial.py` green.
- **C14 (scoped file-dep primitive):** RESOLVED — `tg imports` / `tg importers` are in `KNOWN_COMMANDS` as of v1.54+; do not re-open as a gap. Re-verify with `tg imports --help` / `tg importers --help` before citing.
- **B9 (ReDoS gate, RESOLVED audit #6/#16/#111):** `grep -n "_fallback_pattern_is_provably_linear\|needs_word_or_context_rust_routing" src/tensor_grep/backends/cpu_backend.py` should find the fixed_strings-only gate (`:280`) plus its call sites in BOTH the `_RustUtf8DecodeMismatch` handler AND the generic native-failure `except Exception` handler; the gate must admit ONLY `fixed_strings` (a static pattern-char allow-list was proven unsound by the Opus gate — the `(a|aa)...b` alternation bomb). Re-check before citing closed — a future new fallback path could reopen the class the way #111 did after #6/#16.
- **Native-delegation forward-or-refuse contract (added 2026-07-03, #342/`5e6f780`):** gate `src/tensor_grep/cli/main.py:3263-3282` (`_can_delegate_to_native_tg_search`), refuse-tuple `:1755-1855` (`_NATIVE_TG_DELEGATION_DEFAULT_REQUIRED_FIELDS`). Re-verify: run `tests/unit/test_native_delegation_field_coverage.py` after touching `SearchConfig` — a new unclassified field turns it red immediately, which is the point.
- **`MatchLine` hashability (added 2026-07-03, #344/`80de0b4`):** `src/tensor_grep/core/result.py:4-17` (`submatches` field uses `compare=False`). Re-verify: `python -c "from tensor_grep.core.result import MatchLine; hash(MatchLine(1,'x','f.py',submatches=({'start':0,'end':1},)))"` must not raise.
- **ASCII-only CLI output (added 2026-07-03, #346/`6b7b518`):** fixed call site `src/tensor_grep/cli/inventory.py`. Re-verify: grep new CLI-rendered string literals for non-ASCII before shipping; no automated gate enforces this yet (a governance test is a reasonable follow-up, not yet shipped).

If a re-verify disagrees with this skill, fix the skill — a wrong runbook is worse than none — and route any code change through `tensor-grep-change-control`.
