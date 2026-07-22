---
name: tensor-grep-research-frontier
description: Use when scoping, pitching, planning, or judging a SOTA-advancing bet in tensor-grep (tg) — the OPEN research problems, not a bug fix or a shipped feature. Load when asked "where can tg beat the state of the art / what's the moat / is this worth building", or when touching the five frontier programs: the GPU PFAC many-pattern/resident wedge (Phase-0 shipped v1.75.0-v1.75.4, crossover still unproven), de-fragilizing the flat no-IDF ranking scorer (the IDF blast-radius), closing raw-grep parity via a native launcher/control-plane, the arXiv moat-deepeners (AST-node MCP read/write, graph-traversal tools, intent-aware blast-radius), or the parked `tg diff-docs` precision rebuild (naive absence-from-symbol-table doc-drift detection floods 20k+ false positives — the DocPrism trap). Everything here is candidate/experimental — to actually build or merge one, route to tensor-grep-change-control; for settled dead-ends, tensor-grep-failure-archaeology.
---

# tensor-grep research frontier

The map of where `tg` could plausibly advance the state of the art (SOTA), and — for each — why current SOTA falls short, the specific `tg` asset already in this repo, the first three concrete steps here, and a falsifiable "you have a result when" milestone.

**This skill is a compass, not a licence to build.** Every item below is labelled `open`, `candidate`, or `experimental`. Nothing here is a shipped win, and reading this skill does not authorise starting work. To actually attempt/merge any item you MUST go through `tensor-grep-change-control` (TDD-first, benchmark-hot-paths, no-speed-claim-without-measured-numbers, experimental-until-proven, draft-PR-only, one-merge-per-tick). No skill routes around change-control.

Date-stamped **2026-07-22, v1.93.2** (originally authored 2026-07-02 at v1.17.25; spot-checked 2026-07-08
at v1.49.3; spot-checked 2026-07-16 at v1.78.1 for Problem 4b/Problem 1 GPU-funding). This pass rewrote
Problem 4d to SHIPPED (`tg ledger`, #673/#675, hardened by #701/#706), hardened Problem 2 with the
`_score_symbol`/#699 progress note + the H1 dead-end, added the B-warm-session retirement note to
Problem 3, and added the B-GPU publish-HOLD one-liner to Problem 1 — the rest was not re-walked
line-by-line. Re-verify volatile facts with the commands in **Provenance and maintenance** before you
cite them.

## When to use this skill — and when a sibling is the right door

| Your situation | Use instead |
|---|---|
| You want the mental model / invariants of the code before touching it | `tensor-grep-architecture-contract` |
| You are about to build/review/merge/release ANY of these bets | `tensor-grep-change-control` (the gates) |
| "Has this idea already been tried and lost?" | `tensor-grep-failure-archaeology` |
| A live run fails / hangs / red CI / release didn't publish | `tensor-grep-debugging-playbook` |
| Which benchmark to run + how to read `check_regression.py` / fair-baseline / launcher attribution | `tensor-grep-benchmark-and-proof-toolkit` |
| Building the already-approved CPU-only hybrid semantic search | `tensor-grep-semantic-search-campaign` |
| HOW to run the frontier research itself (Exa fan-out, adversarial claim-verification, cite `file:line`) | `tensor-grep-research-methodology` |
| BM25 / IDF / PageRank / trigram / PFAC domain theory | `code-search-and-retrieval-reference` |
| Flags, build/env, run/operate, diagnostics, validation/QA, docs, release mechanics | the matching `tensor-grep-*` operational sibling |

Some sibling skills in this list are authored in the same batch and may not exist yet; reference them by name.

## Ground rules for frontier work (do not skip)

1. **The moat is the agent-native context layer, not faster grep.** `rg` is the raw cold-text **parity baseline**; `ast-grep` is the structural-search baseline. The product wedge is `orient` / `callers` / blast-radius / `defs` / `refs` / the Actionable Context Capsule (`tg agent`) / `session` (AGENTS.md "The product wedge is not 'faster grep'"). Weigh every bet by whether it deepens that moat or merely narrows a parity gap.
2. **No speed/quality claim without a measured line vs the accepted baseline.** A clean prototype that does not move a real benchmark is rejected (AGENTS.md Operating Rules 4–5).
3. **Experimental-until-proven, default-OFF.** GPU / LSP / semantic / provider paths stay opt-in and labelled experimental until correctness AND speed AND UX are all proven (AGENTS.md; `docs/EXPERIMENTAL.md`).
4. **Oracle before kernel.** Build the deterministic benchmark that would detect the win BEFORE building the thing — and bidirectionally validate it (a correct answer passes, a wrong/empty answer fails). A broken oracle reads as a capability gap.
5. **Never trust a self-report.** A subagent's "it works / tests pass" is a hypothesis until an exit code, a real-binary dogfood, or a `file:line` that resolves confirms it.

---

## Problem 1 — GPU PFAC many-pattern / resident wedge (Phase-0 SHIPPED, crossover still open)

**PFAC** = Parallel Failureless Aho-Corasick, the fixed-string multi-pattern CUDA kernel `tg` ultimately targets. Fixed-string multi-pattern search over a large corpus is *the only* workload class where GPU could beat `rg` (`docs/gpu_crossover.md` "Supported semantics"). What ships **today** is a simpler **position-parallel byte-compare** kernel (`gpu_text_search_positions`, `rust_core/src/gpu_native.rs`) -- the full PFAC/failureless-Aho-Corasick automaton remains a *future* optimization on top of it, not what ships today.

**Why current SOTA fails.** Naive GPU grep loses. Single-pattern cold grep is dominated by CUDA startup + PCIe H2D transfer + output materialization: measured `rg = 73.8ms` vs `tg GPU = 1093.8ms` at 1GB, and 29–35x slower than `rg` at 5GB on RTX 4070 / RTX 5070 (`docs/gpu_crossover.md` "Current native evidence"). Against the *fair* single-invocation `rg -F -e … -e …` multi-pattern baseline (never sequential `rg`, which is a strawman), the public managed lane still falls back to `NativeCpuBackend` and loses. The only *candidate* wins are (a) **many** fixed strings resident over a large corpus, and (b) an **amortized resident** mode (compiled plan + corpus kept resident across queries) — and (b) is explicitly `candidate/not-measured until a benchmark exists` (`docs/CONTRACTS.md`).

**Status:** GPU Phase-0 SHIPPED (v1.75.0-v1.75.4, PRs #593-#597) and locally correctness-proven (RTX 4070 `sm_89` / RTX 5070 `sm_120`, 1GB/5GB match+file-set identity -- `docs/gpu_crossover.md`), gated OFF the public release by the CI Actions var `TENSOR_GREP_RELEASE_NATIVE_ASSET_PROFILE` (default `native-frontdoor`, CPU-only; GPU asset publishing needs the non-default `native-frontdoor-gpu`) -- Phase 1 (publishing those already-built assets) is now a **reversible flag-flip**, not a multi-week rebuild, so the old "roadmap holds all GPU work until three CPU-only wins ship first" gate no longer describes reality (five GPU PRs shipped through v1.75.4 without waiting on it). **The honesty floor is unchanged and does not move with the flag:** flipping the CI var publishes assets only -- it does not promote GPU, does not change the CPU-default auto-recommendation, and does not prove a speed crossover. No speed crossover is proven vs `rg`/`tg_cpu`, GPU auto-recommendation stays `false`, and the reviewer-gated `public-gpu-proof.yml` speed-crossover gate remains unmet (`docs/CONTRACTS.md:80-82`). The PFAC automaton itself, and the resident/many-pattern crossover this Problem targets, remain unbuilt/unproven -- that is still the open research question below, independent of the publish-flag decision.

**Funding note (2026-07-16, do not oversell):** `docs/BACKLOG.md`'s CEO desk now frames the CPU semantic
direction (`tg find` / `--semantic`, campaign #189, see `tensor-grep-semantic-search-campaign`) as the
funded engineering priority, repeatedly using the phrase "GPU retired-for-search (#169)". This is a
*resourcing* decision (where engineering capacity goes next), not a technical finding that PFAC/resident
GPU search is proven impossible — the crossover question above remains genuinely open on its own terms.
`#169`'s exact decided scope lives in the CLI task store, not git history; re-verify current phrasing in
`docs/BACKLOG.md`'s CEO desk line before citing it as more than a resourcing signal.

**One-liner (2026-07-21, B-GPU): public CUDA-asset publishing is on a deliberate HOLD**, per an
evidence-cited CEO decision package — not because the crossover question is settled dead, but because
every measured artifact contradicts a "beats CPU on WSL/Windows search" framing today. See
`tensor-grep-failure-archaeology` Battle 20 for the specific measured numbers.

**The tg asset (already in-repo).** The P0 harness is real: the correctness taxonomy + loud non-promotional CPU fallback with `fallback_reason`; `doctor`/proof fields (failure-taxonomy, honest device-id validation, native-error-kind, v1.75.2-v1.75.4); `benchmarks/run_gpu_native_benchmarks.py --public-managed-proof`; the fair `rg -F -e … -e …` baseline already wired; `tg-native-metadata.json` provenance; an NVRTC PTX disk cache keyed by arch + kernel-hash; pinned-buffer staging with host-read / preprocess / H2D / kernel / wall timings separated. Local CUDA correctness already passes 1GB and 5GB match/file-set identity on RTX 4070 (`sm_89`) + RTX 5070 (`sm_120`).

**First three steps in THIS repo:**
1. Read the promotion contract end-to-end: `docs/gpu_crossover.md` "Required Promotion Rule" + the "resident repeated-query claims remain candidate/not-measured" line in `docs/CONTRACTS.md`. Design the benchmark first.
2. Add a **resident/many-pattern** benchmark row to `benchmarks/run_gpu_native_benchmarks.py` that keeps a compiled plan + corpus resident across queries and compares against fair `rg -F -e … -e …`; and extend the adversarial oracle corpus (CRLF / UTF-8 / binary / multiline) so an overflow degrades to fallback, never a silent truncation (the CUDA-grep steal-list enhancement).
3. If pursuing the full PFAC automaton, confirm it is still unbuilt (`grep -n "PFAC" rust_core/src/gpu_native.rs docs/gpu_crossover.md`) before starting -- do not assume the byte-compare kernel already IS PFAC.

**You have a result when (falsifiable):** `run_gpu_native_benchmarks.py --public-managed-proof` emits **both** `public_managed_promotion_ready = true` and `public_gpu_proof = true` from a `NativeGpuBackend` route with `sidecar_used = false`, beating the fair single-invocation `rg -F -e … -e …` at 1GB and 5GB with exact match/file-set identity on **both** RTX 4070 and RTX 5070. Sidecar or CPU-fallback rows never count (`gpu_evidence_status = unsupported`). Until then GPU stays explicit `--gpu-device-ids` opt-in and must fail loud when it cannot be honored. A legitimate negative result — "resident mode measured, still no crossover" — is also a publishable outcome; record it, do not bury it.

---

## Problem 2 — De-fragilize the flat no-IDF ranking scorer (the IDF blast-radius)

**Read `tensor-grep-failure-archaeology` Battle 7 first — do not re-derive the mechanism.** It carries the full symptom/root-cause/evidence/status record, including the disproved "just add IDF" guess and the exact #302 receipt. In one sentence: `tg`'s ranking-dependent surfaces — the agent capsule, `tg search --rank`, local semantic search — score with `_score_text_terms` (`src/tensor_grep/cli/repo_map.py`), a **flat presence count with NO IDF**, and combined with a hard top-N candidate cap and an alphabetical tie-break, a corpus change can silently **flip** the primary target and degrade a safety behavior **invisible to the call graph**. What already shipped in **v1.17.13 (#302)** is a **degrade-to-ask safety floor**; **the flat scorer itself remains** — that is the open frontier this section targets.

**The tg asset.** `_score_text_terms` / `_symbol_rank_key` / `_score_file_path` in `repo_map.py`; the deterministic controlled-corpus fixture; the degrade-to-ask safety-floor pattern in `agent_capsule.py`.

**Progress since (v1.92.2/#699/A7) — a SIBLING scorer hardened, the primary flat scorer unchanged.**
`_score_symbol` (`repo_map.py:7266`) — a THIRD scorer, distinct from both `_score_text_terms` above and
the real IDF-weighted BM25 in `retrieval_bm25.py`, used only by the deadline-truncated best-effort-
primary fallback path — gained an exact word-boundary bonus (+1, capped, subordinate to match-tier
rank) and a test-file demotion (best-effort path only; the main path already drops test files
post-score). This is real, verified hardening, but it does **not** touch `_score_text_terms`/
`_symbol_rank_key`, the primary scorer this Problem targets — do not describe #699 as having closed
Problem 2; it hardened a subordinate tie-refiner used on a narrower fallback path. **A durable dead-end
also surfaced in the same investigation: "defs should outrank refs" (H1) has NO live insertion point**
— `payload["symbols"]` is already exclusively AST-derived definitions (refs are never mixed into that
list in the first place), so there is nothing to reorder. Do not re-propose a defs-over-refs scoring
fix; the premise is already true by construction.

**First three steps:**
1. Read the resolved incident before touching code (`tensor-grep-failure-archaeology` + the `idf-ranking-fragility` memory) so you inherit the disproof, not the wrong guess.
2. Build a **deterministic ranking-stability benchmark**: a controlled multi-query corpus where a *benign, unrelated* edit is applied and the metric reddens if the primary target flips. This is the missing oracle — inspect the ranked payload pre/post, don't assert one exact ranking a benign edit can move.
3. Prototype an IDF/BM25-weighted `_score_text_terms` behind a flag; benchmark flip-rate AND primary hit-rate on that corpus vs the flat scorer. Keep it only if it reduces flips without regressing hit-rate or the safety floor. This is a councilled + benchmarked `repo_map` PR — deferred, low-priority debt, not a quick swap.

**You have a result when (falsifiable):** a deterministic ranking-stability benchmark exists that FAILS on a benign-corpus-perturbation flip; a weighted scorer measurably lowers the flip rate AND holds or improves `mean_file_hit_rate` (1.0 on the current external pack) AND does not weaken the degrade-to-ask floor. The larger frontier result is a `tg`-class **intent-aware blast-radius that warns when an edit perturbs a ranking-dependent safety behavior** — which merges into Problem 4(c).

---

## Problem 3 — Raw-grep parity via a native launcher / control-plane

**Why current SOTA fails — and what the gap actually is.** The remaining cold-path gap to `rg` is **control-plane / launcher overhead, not backend cleverness and not Python micro-tuning**. The repo already recorded the honest outcome twice (`docs/world_class_plan.md` Roadmap C and Roadmap 1): forced `python_module_launcher` (mean `0.2526s`) even beats `explicit_binary` (`0.2823s`), yet **both still regress against the frozen Windows baseline** under `check_regression.py`. The recorded conclusion: **a larger native rewrite is required**; Python launcher micro-tuning is *exhausted*. Separately, PyO3/FFI for directory walking was measured *too high* and **reverted** — FFI is not the dir-scan speed path (`tensor-grep-failure-archaeology`). Do not reopen either dead end.

**Positioning caveat (no oversell).** `rg` remains the parity baseline; raw search speed is in the **parity tier, not the moat** (AGENTS.md; roadmap sequencing rationale). Closing this gap removes a credibility tax — it does not create the moat. Never market a raw-grep speed win.

**The tg asset.** The native Rust front door (`rust_core/src/main.rs`) + the bootstrap intercept-before-Typer front door (`src/tensor_grep/cli/bootstrap.py`); the `TG_NATIVE_TG_BINARY` override; `benchmarks/run_benchmarks.py` recording `tg_launcher_mode` + `tg_launcher_command_kind` + `tg_binary_version_status`; `benchmarks/check_regression.py`; the frozen Windows baseline.

**First three steps:**
1. Read the two closed roadmaps first so you do NOT restart a Python launcher tuning loop (settled → `tensor-grep-failure-archaeology`).
2. Baseline honestly with the attribution harness: run `benchmarks/run_benchmarks.py`, confirm `tg_binary_version_status` is clean (no stale in-tree binary — it blocks claim-quality runs by default), and separate native-exe vs `.cmd` shim vs `uv` vs Python-module timings. Confirm on your host that the gap is control-plane, not backend.
3. Prototype ONE native-control-plane experiment (e.g. a resident native front door that removes the per-invocation Python bounce on the plain-search hot path) and benchmark end-to-end vs the frozen baseline with `check_regression.py`.

**Retirement note (B-warm-session, 2026-07-21, #251) — "just serve search from the already-warm
session daemon" is a BIG REFACTOR, not step 3's quick resident-front-door win.** A candidate that
looks adjacent to the resident-front-door idea above — "the session daemon is already a long-lived
warm process, route repeated search through it" — was evaluated and found to rest on a false premise:
`session_daemon.py`'s cached state is a **symbol map** (the repo-map graph for `orient`/`callers`/
`blast-radius`/`agent`), not a search index, and there is **no PyO3 binding** for the Rust
`TrigramIndex` reachable from that process; the common `tg search` path is a raw `rg` passthrough that
never touches the daemon. Building genuine warm-session search means a new binding + a new serving
path, not a resident-front-door flag flip — do not conflate the two when scoping a "close the raw-grep
gap" experiment. The free win that DOES already exist: `tg mcp` is itself long-lived, so `CPUBackend`'s
in-process caches stay warm for the duration of one MCP session — a narrower, already-available
mechanism, not a substitute for this Problem's control-plane target. Full detail:
`tensor-grep-failure-archaeology` Battle 19.

**Round-4 argv-injection item — RESOLVED, do not reopen.** `rust_core/src/rg_passthrough.rs`
forwarding PATHS with no `--` sentinel (a directory literally named `-l` flipping `rg` to
files-with-matches) was fixed twice: `#326` (v1.17.26) first added the sentinel, a refactor silently
dropped it, then `#370` (v1.28.1) restored it as the extracted, unit-tested `ripgrep_operand_args`
helper (`rust_core/src/rg_passthrough.rs:401-421` — the sentinel is pushed unconditionally before
the path loop whenever `!args.paths.is_empty()`). Verify before citing: `grep -n "fn ripgrep_operand_args" -A 20 rust_core/src/rg_passthrough.rs`. The remaining adjacent
**round-4 open correctness items** (distinct from speed, still open): rg-parsing edge cases rg#3364
(`--multiline --pcre2 --json` emits one match with two submatches), rg#3131 (`rg -c` omits NUL-byte
files), and BOM-in-`.gitignore`. Verify against the real binary — `tg search PATTERN -- <path>` vs
`tg search PATTERN <path>`.

**You have a result when (falsifiable):** a native-control-plane experiment produces an accepted cold-path win — `benchmarks/check_regression.py` reports **no `tg` regression** against the frozen Windows baseline on the plain-search rows with a claim-quality launcher (native-exe route, clean `tg_binary_version_status`) — OR you re-confirm and record (a legitimate negative) that a larger native rewrite is still required. The `--` sentinel parity sub-result is already shipped (`#370`); it is no longer part of this milestone.

---

## Problem 4 — Moat-deepeners (arXiv-driven, structurally out of reach for ast-grep's MCP)

ast-grep ships its own MCP server — the one direct competitive threat. These candidates are things a pure structural matcher **cannot** offer. Each is a **new, default-OFF capability** needing its own design → council → benchmark. Provenance-label every edge honestly (`parser-backed` / `graph-derived` / `heuristic` / `LSP-confirmed`; AGENTS.md).

### 4a. AST-node-addressed MCP read/write (`readSymbol` / `proposeEdit`)
Reference: CodeStruct (arXiv:2604.05407). **Why SOTA fails:** agents read/write by line ranges or whole files; line-addressed edits are brittle under benign edits and token-wasteful — there is no stable node-addressed handle. **tg asset:** the native Rust AST backend (`backend_ast.rs`, tree-sitter + `ast-grep-core`), the MCP surface (`mcp_server.py`, `tg_mcp_capabilities` tiers), the rewrite plan/apply path. **First three steps:** (1) confirm the MCP capability tiers (`python-local` / `embedded-safe` / `native-required`); (2) design a node-address scheme stable across benign edits; (3) TDD a `readSymbol`/`proposeEdit` tool over the native AST path, argv-injection-safe (insert the `--` sentinel; MCP-276 / CWE-88 CVE class — AGENTS.md "Native-argv flag injection"). **Result when:** an agent reads + edits a symbol by node address through MCP, benchmarked to touch fewer tokens / apply more reliably than a line-range edit on the 12-scenario patch pack, with no correctness regression.

### 4b. Graph-traversal MCP tools (`IMPORTS` / `INHERITS` / `INSTANTIATES`)
Reference: CodeCompass (arXiv:2602.20048, +23.2 pts hidden-dependency). **Why SOTA fails:** `tg`'s callers/blast-radius are **heuristic**, not compiler-grade; hidden cross-file deps are missed. **tg asset:** the `repo_map` symbol graph, blast-radius, `defs`/`refs`/`callers`. **First three steps:** (1) inventory which typed edges `repo_map` already computes; (2) design typed-edge MCP tools with honest provenance labels; (3) benchmark hidden-dependency recall on a repo-backed fixture. **Caveat:** the market research flags Stack-graphs/SCIP as the compiler-grade resolution `tg` lacks — be explicit that these edges stay `heuristic` until SCIP-grade resolution lands; do not market compiler-grade cross-file resolution. **Result when:** a graph-traversal tool measurably lifts hidden-dependency recall on a fixture vs the current heuristic blast-radius, provenance-labelled.

**Falsifiable sub-item — `#74`, the scoped file-dependency primitive -- SHIPPED (PR #460, commit `05f49b8`, 2026-07-08).** The first real token-economy proof-point (Sverklo `bench:primitives`, oracle-validated tokens-per-correct-answer on `express@4.21.1`) found `tg` **7.5x better than grep on definition-lookup** but roughly an **order of magnitude worse on file-deps** ("what does file X import" / "who imports file X"), because `tg` had no scoped file-dependency primitive — an agent paid a whole-repo `tg map` to answer a single-file question (memory `tensor-grep-benchmark-proofpoint-2026-07-08`). The fix shipped exactly as designed: `tg imports FILE` (O(1) forward lookup, no repo scan/cap) and `tg importers FILE [ROOT]` (bounded reverse lookup, prefiltered via the alias-substring graph then confirmed per-edge against the real per-language matchers -- closes the Case-4 false-edge over-count bug) both registered at the normal 4 sites plus MCP tools (`tg_file_imports`, `tg_file_importers`, `tg_session_file_importers`) and a `tg session importers` zero-reparse daemon arm — ~1-2K tokens vs `tg map`'s ~53K. **Result -- RE-VERIFIED AND CLOSED (2026-07-16, `v1.76.12` #619, memory `tensor-grep-benchmark-proofpoint-2026-07-08` follow-up).** The Sverklo P4 file-deps task slice was re-run independently (deterministic, $0, `aggregate.py`): tokens-per-correct-answer went from **53,631 (whole-repo `tg map`, ~10x WORSE than rg) to 2,387 (scoped `tg imports`/`tg importers`, ~2.24x BETTER than rg)**, with F1 preserved and improved (0.542 -> 0.606), bidirectional-oracle-validated (25/25). The re-run also surfaced and fixed a genuine directory-index-import resolution gap in `tg importers` (`#619`) confined via `_reverse_importer_extra_aliases`, verified not to inflate `tg blast-radius`/PageRank. **The moat is now proven on both the definition-lookup (P1) and file-deps (P4) axes.** Publishing these numbers publicly is a separate, still-open decision (**CEO-gated #72**) — cite them as internally verified, not as a public claim.

### 4c. Intent-aware blast-radius (git evolutionary-coupling + ranking perturbation)
Reference: Ripple (ICSE 2026). **Why SOTA fails:** call-graph blast-radius sees only caller/callee edges — it misses (i) files that historically **co-change** in git (evolutionary coupling) and (ii) **ranking-dependent surfaces** an edit silently perturbs (Problem 2's IDF blast-radius). **tg asset:** existing blast-radius, git history, the IDF-fragility insight. **First three steps:** (1) read the `idf-ranking-fragility` memory (the beyond-call-graph product idea); (2) add a git co-change signal (files that historically change together); (3) add a ranking-perturbation warning (flag when an edit shifts query-adjacent term frequencies that could flip a ranking-dependent safety behavior). **Result when:** blast-radius surfaces a co-changed / ranking-perturbed file the pure call graph misses, on a fixture where that file is the real impact site.

### 4d. Local agent context-sharing / shared code-intelligence plane — SHIPPED end-to-end (`tg ledger`, mirrors Problem 4b's #74 treatment)
**Why current SOTA was crowded, not empty (2026-07-08 landscape review, thinktank-reviewed, memory
`tensor-grep-a2a-ledger-audit-2026-07-08`):** MCP (agent-tool) and A2A (cross-org agent-agent,
HTTP/OAuth) were already settled at the protocol layer, and the *local* agent-coordination layer
exploded in Q1 2026 and is harness-owned (Claude Code Agent Teams, Beads) — all of it trading in
**prose** task state. The one sliver still off everyone else's moat was **code-aware** coordination:
repo maps and blast radii that only a tool like `tg` actually computes.

**Falsifiable sub-item — the demand-gate MET, and the ledger shipped end-to-end (#673→v1.82.0,
#675→v1.83.0, hardened by #701/#706).** The step-0 demand-instrumentation patch (merged `#456`,
2026-07-08) earned its pre-stated 2-week gate, authorizing the real build: `tg ledger claim/release/
list` (Slice 1 — advisory code-scoped locks, always exit-0 + an `overlaps` report, TTL-prune, crash-safe
via TTL expiry) shipped as `#673`/v1.82.0; `tg ledger record/find` (Slice 2 — content-addressed finding
reuse with revision-freshness + integrity tamper-detect) shipped as `#675`/v1.83.0. Both compose ONLY
existing primitives (`atomic_write_json`/`_index_lock` RMW, cross-process `index_lock`, evidence
receipts, `_repo_revision_identity`) — no new crypto/transport/bus/task-queue, never a blocking lock —
and both were dogfood-verified on the published binary (agent-b sees agent-a's overlap in production;
record/find round-trip on the shipped wheel).

**Hardening since ship (do not describe the ledger as merely "shipped, unhardened"):** `#701`
(v1.92.2) killed a 2-release flaky lock-concurrency test by rewriting it to a scheduler-independent
Event-handshake contract (independence + converse mutual-exclusion), decoupling the ledger's
correctness proof from wall-clock thread-overlap assertions. `#706`/A13 (v1.93.0) fixed the real
dogfood-#1 footgun: claims were scoped to the LITERAL PATH argument (a physical bug — each command
resolved its store dir from PATH, so `claim core/hooks` + `list .` silently used two different
stores) — fixed via canonicalization to the nearest `.git` ancestor (worktree-aware) + a stored
`ClaimRecord.scope` field + subtree-rollup `list` + fail-closed bare-path release. **Slice 2
(`record`/`find`) did NOT get this fix — it remains literal-path-rooted, a known open footgun.**

**Result — CLOSED, not open-ended:** the operator/reference documentation for this surface now lives
in its own registered skill, `tensor-grep-ledger` (plus `tensor-grep-enterprise-review-bundle` for the
receipt/CI-gate-chain sibling) — this Problem 4d entry stays as the frontier-research provenance trail
(why it was built, what demand-gate justified it), not the day-to-day reference.

Data-flow slicing `tg slice <var>` (ARISE, arXiv:2605.03117) and goal-conditioned capsule line-pruning
`--goal` (SWE-Pruner) remain further, lower-priority candidates from the same research, unbuilt.

---

## Problem 5 — Doc-drift detection precision rebuild (`tg diff-docs`, PARKED, round-4 item H — added 2026-07-03)

**Read `tensor-grep-failure-archaeology` Battle 13 first — do not re-derive the mechanism.** It carries the retrospective (17/17 green fixture tests, then a real-corpus flood — the DocPrism trap realized). This section is the forward-looking rebuild: exactly why the absence signal fails, the parked foundation, and a ranked menu for making it shippable.

**Why current SOTA fails.** DocPrism (arXiv:2511.00215) measured naive code-doc drift detection at 0.62 precision / 98% flag-rate — `diff_docs.py`'s own module docstring cited this risk before the feature shipped anyway on green fixtures. `tg diff-docs`'s scope-gated MVP (flag a doc code-span identifier that does not resolve against `repo_map`'s symbol table) hits the same wall for the same structural reason: **absence from a flat, repo-wide name set is not evidence of drift**, because the set has no notion of *where* a name is expected to live. Re-verified today (2026-07-03) by running the parked branch's own `build_doc_drift()` against this repo's real `docs/` vs `src/`:

```
build_doc_drift("docs", code_path="src")  ->  20,072 findings / 2,727 "high confidence"
  (89 docs files scanned, 45 out-of-scope-language files)
```

— within noise of the `20,060 / 2,727` figure recorded the day the feature was deferred (the high-confidence count is exact; total drifted by ~12 findings from same-day doc edits), confirming the flood is reproducible, not a fixture-suite artifact. Four concrete false-positive classes, verified by inspecting the actual findings, each pointing at a different piece of the fix:

1. **Generic/self-referential low-confidence noise.** Path-fragment and vocabulary tokens dominate the low-confidence tail (`tests` 533x, `unit` 399x, `artifacts` 343x, `grep`/`tensor_grep` 340x each, `pytest` 205x, `json` 285x, …) — none are code-identifier drift; they are prose words and path examples that happen to sit inside a fence.
2. **Pytest fixtures / local example variables read as "high confidence."** `tmp_path` (149 hits), `file_path` (41), `session_id` (29), `max_tokens` (28), `chunk_size` (21), `typer.Option` (21), `CliRunner` (19), `entry_points` (14) — all qualify as "high" under the current heuristic (dotted-or-capitalized-or-underscored, length >= 6) purely because they look like identifiers inside a fenced code example; they were never meant to resolve against `repo_map`'s symbol table.
3. **Rust builtin types leak through the curated denylist.** `_CURATED_STDLIB` in `diff_docs.py` is Python-only (`Path`, `Optional`, `Enum`, `Any`, `Dict`, `List`, `Tuple`, `Set`) — no Rust builtins. `String` produces **9 false positives even when `code_path="."` includes `rust_core/` in the scan** (this is the literal receipt behind round-4 item H's "flagging String/Option/Vec language types" note). The flip side is worse: `Option`/`Result` do NOT false-positive in a whole-repo scan, but only by accident — both names collide with unrelated `class Option` / `class Result` definitions vendored under `benchmarks/external_repos/{click,commander.js}/…`, which a flat repo-wide name set cannot tell apart from a real project symbol. A flat set is unreliable in *both* directions, not just the flagged one.
4. **Historical-doc downgrade misses the biggest historical-ish source: plan docs.** `_is_historical_doc` only matches `paper.md` / `roadmap` / `changelog` / `history` in the path. `docs/superpowers/plans/` holds **30 dated implementation-plan documents** that routinely spell out helper names before (or instead of) they ship — e.g. `docs/superpowers/plans/2026-06-26-tg-session.md` documents `_append_retrieval_log_entry()` / `_query_retrieval_log()` in prose and pseudocode, and neither name exists anywhere under `src/` today (`grep -rn "_append_retrieval_log_entry" src/` returns nothing) — the plan was executed under different names or dropped. A plan doc is a proposal, not documentation of shipped behavior, yet it is scanned at full confidence like a reference doc.

**Status:** PARKED. Foundation lives on branch `wip/diff-docs-precision`, commit `90b7042` ("wip: tg diff-docs foundation (DEFERRED — precision inadequate, see task)") — `src/tensor_grep/cli/diff_docs.py` (303 lines) + `tests/unit/test_diff_docs.py` (123 lines, 17 `def test_...` functions, all green). **Never merged to `main`**; `docs/SESSION_HANDOFF.md` records the deferral. Do not resurrect by merging as-is — 17/17 green is not the bar (see milestone below).

**The tg asset (already on the branch, not main).** `build_doc_drift()` / `render_doc_drift_text()`: a markdown code-span extractor (`_iter_code_span_tokens`, a line-state-machine over fenced + inline code that gives free line numbers), a fence-language scope gate (`_FENCE_LANGUAGE` / `_IN_SCOPE_LANGUAGES` — only python/js/ts/rust fences are resolved against symbols; every other fence language is counted in `coverage.docs_files_out_of_scope`, never silently folded into "0 findings = clean"), and four precision denylists (length floor `_MIN_TOKEN_LEN=4`, `_TG_COMMAND_NAMES`, `_COMMON_WORD_STOPLIST`, `_CURATED_STDLIB` + `_LANGUAGE_KEYWORDS` + `builtins`). The historical-doc downgrade and the "unresolved, never removed" wording discipline (the docstring is explicit: "tg has no git history, so it cannot assert a symbol was removed") are both real, keep-worthy design decisions — the gap is precision of the core "does it resolve" signal, not the scaffolding around it.

**Ranked solution menu (highest-leverage first):**
1. **Qualified in-repo-module signal (the real fix).** `known_symbols` today is a FLAT `{name}` set with no notion of origin — `repo_map` symbols already carry a `file` field (`src/tensor_grep/cli/repo_map.py:1692`, e.g. `"file": str(file)`; re-verify with `grep -n '"file":' src/tensor_grep/cli/repo_map.py`) that is simply discarded when building the set. For a dotted reference like `tensor_grep.cli.session_store.foo`, resolve the module prefix to a real repo file path FIRST; only if that file exists among the scanned symbol files does an unresolved trailing segment count as a genuine finding, and only then raise its confidence. A bare, undotted `foo` with no resolvable module anchor should never reach "high." This alone kills false-positive class 2 (pytest fixtures / local variables are never expressed as `real_module.symbol` against an actual repo path) and closes the class-3 collision hole (a same-named symbol in an unrelated vendored file no longer masks a real Rust builtin, because the module path won't match).
2. **Git-history removed-detection.** `diff_docs.py` currently has zero git access by design. `git log --all -S<symbol> -- <code_path>` (or a cheaper `git log -1 --diff-filter=D -- '**/*<symbol>*'` sweep) distinguishes "this name existed in the code and was deleted" (real drift — report it) from "this name never existed" (typo, pseudocode, or a plan-doc proposal — suppress or heavily downweight). This directly fixes false-positive class 4 (plan docs) without a path-pattern denylist arms race, and lets the tool honestly say "removed" instead of only ever "unresolved."
3. **Curated per-language type denylists.** Cheapest, narrowest-scope fix: extend `_CURATED_STDLIB` with a `_RUST_BUILTINS` frozenset (`String`, `Vec`, `HashMap`, `HashSet`, `Box`, `Result`, `Option`, `Cow`, `Arc`, `Rc`, `str`, …) gated by `fence_language == "rust"`, mirroring the existing Python-only list. Kills false-positive class 3 outright, but a denylist is always one type behind — pair with #1, do not ship #3 alone and call it "the fix."
4. **Cheap, bounded denylist patch** (do alongside #2, not instead of it): add a `plans/` / `superpowers/plans/` path-fragment marker to `_is_historical_doc`. Fast, but a pure path-pattern patch regresses again the moment a new plan-doc subdirectory appears — treat it as a stopgap for class 4, not a substitute for #2.

**First three steps in THIS repo:**
1. `git show wip/diff-docs-precision:src/tensor_grep/cli/diff_docs.py > <scratch>.py` (or check out the branch into a worktree — do not merge it), then re-run `build_doc_drift("docs", code_path="src")` against current `docs/`/`src/` to get a fresh baseline before touching anything, and diff the finding set against a prior run so you are not chasing doc-drift-in-the-drift-detector.
2. Prototype solution #1 (qualified in-repo-module signal) first — it is the only menu item that fixes a false-positive *class*, not a finite token list. Build it as a second, stricter resolution pass: dotted references resolve module-then-member; bare references either drop to "low" unconditionally or require solution #2's git-history check to earn "high."
3. Build the missing oracle before touching the heuristic again: a stratified, hand-labelled sample of findings (mix of "high"/"low", fenced/inline) with a human-verified true/false-positive label, so the next iteration has a measured precision number to beat instead of "the fixture suite is green."

**You have a result when (falsifiable):** a real-corpus run of `tg diff-docs` on `docs/` vs `src/` (or `.` to include `rust_core/`) — NOT the 17-test fixture suite — yields a **reviewable finding count** (an agent could plausibly triage the list in one sitting, not tens of thousands) with **spot-check precision >= 0.9** on a stratified human-labelled sample. Fixture-green alone never reopens this milestone: fixture-green + unusable-on-the-real-corpus is precisely the state that got the feature parked in the first place. Record the measured precision number and the sample methodology alongside any future merge, not just "looks better now."

---

## Beyond-SOTA: the fused thesis (candidate, not proven)

No single wedge above is the beyond-SOTA claim. The claim is the **fusion of three moats into one model-agnostic harness**:

| Moat | What it is | In-repo status |
|---|---|---|
| **Context moat** | The agent-native layer (`orient` / `callers` / blast-radius / capsule / `session`): give the agent exactly what it needs before it edits — token-efficient, provenance-tagged, ask-before-edit. Raw speed is parity, not this. | Shipped + benchmarked; Problems 2 & 4 deepen it |
| **Correctness moat** | Deterministic oracles + the Backend Fail-Closed Contract + the patch-correctness bakeoff (measured *final-edit* outcomes, not planning quality). `claude-enhanced` = `1.0 / 1.0` on the 12-scenario pack. | Shipped; expand corpus (`world_class_plan.md`) |
| **Model-agnostic harness** | The harness-evolution thesis: world-best harness + a smaller/cheaper model ≈ frontier performance; the harness hill-climbs via CUJ → score → cluster → fix-wave. | Program, not a single feature |

**The fused, falsifiable end-state claim:** `tg`-enhanced agent flows beat generic agent baselines on **final task outcomes** (not just retrieval) across model tiers, and the advantage **widens as the model gets cheaper** — because the moat is the harness, not the model. This is not proven; it is the target. Its definition-of-done is `docs/world_class_plan.md` "Definition Of Done" (all 7 conditions) plus a defensible cross-model A/B where the `tg`-enhanced line wins the majority of real-repo task slices at more than one model tier, with caveats kept explicit when a comparator wins a class.

This thesis must not contradict the standing contracts: raw grep is parity, GPU is experimental, provider modes are opt-in. If a "beyond-SOTA" pitch requires bending one of those, it is over-claiming — send it back through `tensor-grep-change-control`.

---

## Provenance and maintenance

Re-verify anything below before you cite it; line numbers drift.

- **Version / date stamp** (`v1.93.2`, 2026-07-22): `grep -n '^version' pyproject.toml` and `grep -n 'release_docs_current_tag' AGENTS.md`.
- **GPU pause + the 3 gating CPU wins:** `grep -n "Roadmap Sequencing" -A 15 AGENTS.md`; `#319` in `CHANGELOG.md`. Promotion rule: `docs/gpu_crossover.md` "Required Promotion Rule" + "Supported semantics" (PFAC). **B-GPU publish=HOLD (2026-07-21):** `tensor-grep-failure-archaeology` Battle 20.
- **Flat no-IDF scorer + fragility:** `grep -n "_score_text_terms\|_symbol_rank_key\|_score_file_path" src/tensor_grep/cli/repo_map.py`; safety floor `grep -n "_primary_target_is_unrequested_marker_helper\|_prefer_implementation_over_marker_helper\|_alternative_targets" src/tensor_grep/cli/agent_capsule.py`; AGENTS.md "BM25/IDF-ranked surfaces … sensitive to corpus changes". Disproof-of-IDF-as-fix: `tensor-grep-failure-archaeology`. **`_score_symbol` hardening (A7/#699):** `grep -n "def _score_symbol" src/tensor_grep/cli/repo_map.py` (expect `~7266`); H1 dead-end: `grep -n '"symbols"' src/tensor_grep/cli/repo_map.py`.
- **Raw-grep parity / native control-plane closed outcomes:** `docs/world_class_plan.md` Roadmap C + "Roadmap 1: Native Control Plane". Round-4 argv-injection item is **RESOLVED** (`#326`/`#370`) — verify with `grep -n "fn ripgrep_operand_args" -A 20 rust_core/src/rg_passthrough.rs`, expect an unconditional `operands.push("--".to_string())` before the path loop. **B-warm-session retirement (2026-07-21):** `tensor-grep-failure-archaeology` Battle 19.
- **Moat-deepener references (arXiv ids + which competitor gap):** the `tensor-grep-market-research-2026-06-25` memory. MCP surface + capability tiers: `grep -n "tg_mcp_capabilities" src/tensor_grep/**/mcp_server.py`.
- **Problem 4d (`tg ledger`, SHIPPED) + Problem 4b sub-item (`#74` scoped file-deps):** design docs `tensor-grep-a2a-ledger-audit-2026-07-08` and `tensor-grep-benchmark-proofpoint-2026-07-08` memories; ship receipts `#673`(v1.82.0)/`#675`(v1.83.0), hardening `#701`/`#706`(v1.93.0) — `git log --oneline origin/main | grep -E "673|675|701|706"` to confirm. Day-to-day reference: `tensor-grep-ledger` skill.
- **`tg diff-docs` parked foundation + false-positive receipts (Problem 5):** confirm the branch and commit still exist: `git log wip/diff-docs-precision -1` (expect `90b7042`, not on `main` — `git merge-base --is-ancestor 90b7042 main` should fail). Re-pull the file: `git show wip/diff-docs-precision:src/tensor_grep/cli/diff_docs.py` (303 lines) and `git show wip/diff-docs-precision:tests/unit/test_diff_docs.py` (123 lines / 17 tests — `grep -c "def test_"`). Re-run the flood measurement (copy the file out via `git show`, `sys.path.insert(0, "src")`, call `build_doc_drift("docs", code_path="src")`) before citing exact finding counts — they drift with the doc corpus. Retrospective: `tensor-grep-failure-archaeology` Battle 13; deferral note: `grep -n -A2 "diff-docs" docs/SESSION_HANDOFF.md`.
- **Fused-thesis anchors:** `docs/world_class_plan.md` "Definition Of Done"; patch bakeoff `1.0/1.0` line in `docs/PAPER.md` / `world_class_plan.md`; harness-evolution thesis in the workspace `CLAUDE.md`.
- **Benchmark scripts referenced:** `benchmarks/run_gpu_native_benchmarks.py`, `benchmarks/run_benchmarks.py`, `benchmarks/check_regression.py` (`ls benchmarks/` to confirm). How to read them: `tensor-grep-benchmark-and-proof-toolkit`.
