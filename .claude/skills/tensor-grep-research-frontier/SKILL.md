---
name: tensor-grep-research-frontier
description: Use when scoping, pitching, planning, or judging a SOTA-advancing bet in tensor-grep (tg) — the OPEN research problems, not a bug fix or a shipped feature. Load when asked "where can tg beat the state of the art / what's the moat / is this worth building", or when touching the four frontier programs: the paused GPU PFAC many-pattern/resident wedge (#319), de-fragilizing the flat no-IDF ranking scorer (the IDF blast-radius), closing raw-grep parity via a native launcher/control-plane, or the arXiv moat-deepeners (AST-node MCP read/write, graph-traversal tools, intent-aware blast-radius). Everything here is candidate/experimental — to actually build or merge one, route to tensor-grep-change-control; for settled dead-ends, tensor-grep-failure-archaeology.
---

# tensor-grep research frontier

The map of where `tg` could plausibly advance the state of the art (SOTA), and — for each — why current SOTA falls short, the specific `tg` asset already in this repo, the first three concrete steps here, and a falsifiable "you have a result when" milestone.

**This skill is a compass, not a licence to build.** Every item below is labelled `open`, `candidate`, or `experimental`. Nothing here is a shipped win, and reading this skill does not authorise starting work. To actually attempt/merge any item you MUST go through `tensor-grep-change-control` (TDD-first, benchmark-hot-paths, no-speed-claim-without-measured-numbers, experimental-until-proven, draft-PR-only, one-merge-per-tick). No skill routes around change-control.

Date-stamped **2026-07-02, v1.17.25**. Re-verify volatile facts with the commands in **Provenance and maintenance** before you cite them.

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

## Problem 1 — GPU PFAC many-pattern / resident wedge (PAUSED, #319)

**PFAC** = Parallel Failureless Aho-Corasick, the fixed-string multi-pattern CUDA kernel `tg` targets. Fixed-string multi-pattern search over a large corpus is *the only* workload class where GPU could beat `rg` (`docs/gpu_crossover.md` "Supported semantics").

**Why current SOTA fails.** Naive GPU grep loses. Single-pattern cold grep is dominated by CUDA startup + PCIe H2D transfer + output materialization: measured `rg = 73.8ms` vs `tg GPU = 1093.8ms` at 1GB, and 29–35x slower than `rg` at 5GB on RTX 4070 / RTX 5070 (`docs/gpu_crossover.md` "Current native evidence"). Against the *fair* single-invocation `rg -F -e … -e …` multi-pattern baseline (never sequential `rg`, which is a strawman), the public managed lane still falls back to `NativeCpuBackend` and loses. The only *candidate* wins are (a) **many** fixed strings resident over a large corpus, and (b) an **amortized resident** mode (compiled PFAC plan + corpus kept resident across queries) — and (b) is explicitly `candidate/not-measured until a benchmark exists` (`docs/CONTRACTS.md`).

**Status:** PAUSED at the already-shipped **P0** harness. The roadmap holds P1 (the kernel) and beyond until **three CPU-only every-install wins ship first** (AGENTS.md "Roadmap Sequencing (2026-07-02)"): (1) local hybrid semantic search, (2) `tg registration-check` productized, (3) a Bloom-filter n-gram prefilter. Do NOT advance P1 while paused.

**The tg asset (already in-repo).** The P0 harness is real: the correctness taxonomy + loud non-promotional CPU fallback with `fallback_reason`; `doctor`/proof fields; `benchmarks/run_gpu_native_benchmarks.py --public-managed-proof`; the fair `rg -F -e … -e …` baseline already wired; `tg-native-metadata.json` provenance; an NVRTC PTX disk cache keyed by arch + kernel-hash; pinned-buffer staging with host-read / preprocess / H2D / kernel / wall timings separated. Local CUDA correctness already passes 1GB and 5GB match/file-set identity on RTX 4070 (`sm_89`) + RTX 5070 (`sm_120`).

**First three steps in THIS repo** (only *after* the pause is lifted):
1. Confirm the gate is open: `grep -n "Roadmap Sequencing" -A 15 AGENTS.md` — verify the three CPU-only wins shipped. If not, stop; the frontier is the CPU moat, not the kernel.
2. Read the promotion contract end-to-end: `docs/gpu_crossover.md` "Required Promotion Rule" + the "resident repeated-query claims remain candidate/not-measured" line in `docs/CONTRACTS.md`. Design the benchmark first.
3. Add a **resident/many-pattern** benchmark row to `benchmarks/run_gpu_native_benchmarks.py` that keeps the compiled PFAC plan + corpus resident across queries and compares against fair `rg -F -e … -e …`; and extend the adversarial oracle corpus (CRLF / UTF-8 / binary / multiline) so an overflow degrades to fallback, never a silent truncation (the CUDA-grep steal-list enhancement).

**You have a result when (falsifiable):** `run_gpu_native_benchmarks.py --public-managed-proof` emits **both** `public_managed_promotion_ready = true` and `public_gpu_proof = true` from a `NativeGpuBackend` route with `sidecar_used = false`, beating the fair single-invocation `rg -F -e … -e …` at 1GB and 5GB with exact match/file-set identity on **both** RTX 4070 and RTX 5070. Sidecar or CPU-fallback rows never count (`gpu_evidence_status = unsupported`). Until then GPU stays explicit `--gpu-device-ids` opt-in and must fail loud when it cannot be honored. A legitimate negative result — "resident mode measured, still no crossover" — is also a publishable outcome; record it, do not bury it.

---

## Problem 2 — De-fragilize the flat no-IDF ranking scorer (the IDF blast-radius)

**Read `tensor-grep-failure-archaeology` Battle 7 first — do not re-derive the mechanism.** It carries the full symptom/root-cause/evidence/status record, including the disproved "just add IDF" guess and the exact #302 receipt. In one sentence: `tg`'s ranking-dependent surfaces — the agent capsule, `tg search --rank`, local semantic search — score with `_score_text_terms` (`src/tensor_grep/cli/repo_map.py`), a **flat presence count with NO IDF**, and combined with a hard top-N candidate cap and an alphabetical tie-break, a corpus change can silently **flip** the primary target and degrade a safety behavior **invisible to the call graph**. What already shipped in **v1.17.13 (#302)** is a **degrade-to-ask safety floor**; **the flat scorer itself remains** — that is the open frontier this section targets.

**The tg asset.** `_score_text_terms` / `_symbol_rank_key` / `_score_file_path` in `repo_map.py`; the deterministic controlled-corpus fixture; the degrade-to-ask safety-floor pattern in `agent_capsule.py`.

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

Fold in the adjacent **round-4 open correctness items** (distinct from speed): `rust_core/src/rg_passthrough.rs` forwards PATHS with **no `--` sentinel**, so a directory literally named `-l` flips `rg` to files-with-matches (dogfood-confirmed); plus rg-parsing edge cases rg#3364 (`--multiline --pcre2 --json` emits one match with two submatches), rg#3131 (`rg -c` omits NUL-byte files), and BOM-in-`.gitignore`. Verify against the real binary — `tg search PATTERN -- <path>` vs `tg search PATTERN <path>`.

**You have a result when (falsifiable):** a native-control-plane experiment produces an accepted cold-path win — `benchmarks/check_regression.py` reports **no `tg` regression** against the frozen Windows baseline on the plain-search rows with a claim-quality launcher (native-exe route, clean `tg_binary_version_status`) — OR you re-confirm and record (a legitimate negative) that a larger native rewrite is still required. Parity sub-result: `tg search PATTERN <dir-named--l>` returns matches, not files-with-matches.

---

## Problem 4 — Moat-deepeners (arXiv-driven, structurally out of reach for ast-grep's MCP)

ast-grep ships its own MCP server — the one direct competitive threat. These candidates are things a pure structural matcher **cannot** offer. Each is a **new, default-OFF capability** needing its own design → council → benchmark. Provenance-label every edge honestly (`parser-backed` / `graph-derived` / `heuristic` / `LSP-confirmed`; AGENTS.md).

### 4a. AST-node-addressed MCP read/write (`readSymbol` / `proposeEdit`)
Reference: CodeStruct (arXiv:2604.05407). **Why SOTA fails:** agents read/write by line ranges or whole files; line-addressed edits are brittle under benign edits and token-wasteful — there is no stable node-addressed handle. **tg asset:** the native Rust AST backend (`backend_ast.rs`, tree-sitter + `ast-grep-core`), the MCP surface (`mcp_server.py`, `tg_mcp_capabilities` tiers), the rewrite plan/apply path. **First three steps:** (1) confirm the MCP capability tiers (`python-local` / `embedded-safe` / `native-required`); (2) design a node-address scheme stable across benign edits; (3) TDD a `readSymbol`/`proposeEdit` tool over the native AST path, argv-injection-safe (insert the `--` sentinel; MCP-276 / CWE-88 CVE class — AGENTS.md "Native-argv flag injection"). **Result when:** an agent reads + edits a symbol by node address through MCP, benchmarked to touch fewer tokens / apply more reliably than a line-range edit on the 12-scenario patch pack, with no correctness regression.

### 4b. Graph-traversal MCP tools (`IMPORTS` / `INHERITS` / `INSTANTIATES`)
Reference: CodeCompass (arXiv:2602.20048, +23.2 pts hidden-dependency). **Why SOTA fails:** `tg`'s callers/blast-radius are **heuristic**, not compiler-grade; hidden cross-file deps are missed. **tg asset:** the `repo_map` symbol graph, blast-radius, `defs`/`refs`/`callers`. **First three steps:** (1) inventory which typed edges `repo_map` already computes; (2) design typed-edge MCP tools with honest provenance labels; (3) benchmark hidden-dependency recall on a repo-backed fixture. **Caveat:** the market research flags Stack-graphs/SCIP as the compiler-grade resolution `tg` lacks — be explicit that these edges stay `heuristic` until SCIP-grade resolution lands; do not market compiler-grade cross-file resolution. **Result when:** a graph-traversal tool measurably lifts hidden-dependency recall on a fixture vs the current heuristic blast-radius, provenance-labelled.

### 4c. Intent-aware blast-radius (git evolutionary-coupling + ranking perturbation)
Reference: Ripple (ICSE 2026). **Why SOTA fails:** call-graph blast-radius sees only caller/callee edges — it misses (i) files that historically **co-change** in git (evolutionary coupling) and (ii) **ranking-dependent surfaces** an edit silently perturbs (Problem 2's IDF blast-radius). **tg asset:** existing blast-radius, git history, the IDF-fragility insight. **First three steps:** (1) read the `idf-ranking-fragility` memory (the beyond-call-graph product idea); (2) add a git co-change signal (files that historically change together); (3) add a ranking-perturbation warning (flag when an edit shifts query-adjacent term frequencies that could flip a ranking-dependent safety behavior). **Result when:** blast-radius surfaces a co-changed / ranking-perturbed file the pure call graph misses, on a fixture where that file is the real impact site.

Further candidates from the same research (lower priority, same discipline): data-flow slicing `tg slice <var>` (ARISE, arXiv:2605.03117) and goal-conditioned capsule line-pruning `--goal` (SWE-Pruner).

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

- **Version / date stamp** (`v1.17.25`, 2026-07-02): `grep -n '^version = ' pyproject.toml` and `grep -n 'release_docs_current_tag' AGENTS.md`.
- **GPU pause + the 3 gating CPU wins:** `grep -n "Roadmap Sequencing" -A 15 AGENTS.md`; `#319` in `CHANGELOG.md`. Promotion rule: `docs/gpu_crossover.md` "Required Promotion Rule" + "Supported semantics" (PFAC).
- **Flat no-IDF scorer + fragility:** `grep -n "_score_text_terms\|_symbol_rank_key\|_score_file_path" src/tensor_grep/cli/repo_map.py`; safety floor `grep -n "_primary_target_is_unrequested_marker_helper\|_prefer_implementation_over_marker_helper\|_alternative_targets" src/tensor_grep/cli/agent_capsule.py`; AGENTS.md "BM25/IDF-ranked surfaces … sensitive to corpus changes". Disproof-of-IDF-as-fix: `tensor-grep-failure-archaeology`.
- **Raw-grep parity / native control-plane closed outcomes:** `docs/world_class_plan.md` Roadmap C + "Roadmap 1: Native Control Plane". Round-4 open passthrough item: `rust_core/src/rg_passthrough.rs` (confirm no `--` sentinel before user paths).
- **Moat-deepener references (arXiv ids + which competitor gap):** the `tensor-grep-market-research-2026-06-25` memory. MCP surface + capability tiers: `grep -n "tg_mcp_capabilities" src/tensor_grep/**/mcp_server.py`.
- **Fused-thesis anchors:** `docs/world_class_plan.md` "Definition Of Done"; patch bakeoff `1.0/1.0` line in `docs/PAPER.md` / `world_class_plan.md`; harness-evolution thesis in the workspace `CLAUDE.md`.
- **Benchmark scripts referenced:** `benchmarks/run_gpu_native_benchmarks.py`, `benchmarks/run_benchmarks.py`, `benchmarks/check_regression.py` (`ls benchmarks/` to confirm). How to read them: `tensor-grep-benchmark-and-proof-toolkit`.
