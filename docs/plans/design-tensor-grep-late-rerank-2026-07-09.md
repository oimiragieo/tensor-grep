# Design — Late-Interaction (MaxSim) Rerank Stage for `tg search --semantic`

**Verdict: GO-WITH-CHANGES.** Fable design audit 2026-07-09 (agent ad65cf18), verified against real code. Every seam cited `file:line`. Governed by `.claude/skills/tensor-grep-semantic-search-campaign/SKILL.md`. Ships **default-OFF**; graduates only on the golden-set gate (§5).

## Idea
Add a 3rd stage to `tg search --semantic` (BM25+dense→RRF today): a tiny ColBERT-style code model MaxSim-reranks the top-K of the RRF-fused pool. Rerank-the-pool, NOT a PLAID DB (PLAID-repro insight). Direct competitive response to ColGrep/LightOn (−15.7% tokens / 70% wins vs grep).

## Corrections to the naive proposal (real code)
1. Pipeline entry = `src/tensor_grep/cli/main.py` — `_apply_semantic_rerank` at `main.py:3551`, invoked `main.py:6803` under `if config.semantic_rank:` (`main.py:6800`).
2. Registration is **8 sites across 2 layers**, not 2 (see §6) — incl. the missed `_can_delegate_to_native_tg_search` `unsupported_flags` at `bootstrap.py:403-418`.
3. `tg index --fetch-model` is a **GHOST** — only hit is the error string at `retrieval_dense.py:95`; no `index` command exists. Must BUILD the fetch path (T4) + fix the ghost message same PR.
4. `benchmarks/run_repo_retrieval_benchmarks.py` REPLAYS canned rankings (`:48-56`) — can't gate this. `eval_bm25_quality.py` corpus is saturated (`SKILL.md:247-253`). Must build a live-pipeline golden harness (T8).

## The seam
Slot inside `rerank_hybrid` (`src/tensor_grep/core/reranker.py:101`) at the `fused_order` variable (`reranker.py:157`), BEFORE the positional-proxy freeze (`reranker.py:162`). Add `late_reranker: LateReranker | None = None` (mirrors `dense_index` at `:110`). `head = fused_order[:pool_k]` → `late_reranker.rerank(...)` → `fused_order = head + fused_order[pool_k:]`. **Order-only over chunk indices** — same matches, same membership, same JSON shape (preserves the reorder-overlay contract `SKILL.md:207`). Candidate pool = the RRF-fused ranking, NOT a new RRF leg (a 3rd leg rank-flattens MaxSim's precision).

## Model + LICENSE (blocker CLEARED)
**`lightonai/LateOn-Code-edge` (17M) — Apache-2.0** (verified: HF README frontmatter). Ships `model_int8.onnx` (17.2MB) + tokenizer + `onnx_config.json` pre-exported. ColGrep runs this exact model int8-ONNX on CPU (competitor-proven CPU-viable). Fallback: `mxbai-edge-colbert-v0-32m` (Apache-2.0, general-domain, needs own export).
**Bundling: license-safe but DON'T bundle — download-on-demand** (keep wheel slim; mirror dense leg's `~/.tensor-grep/models/` fetch, `retrieval_dense.py:70-79`). T4 builds a checksum-pinned (SHA-256, fail-closed), byte-capped+timeout fetch of the 3 files from a pinned HF revision. Never auto-download at query time (`SKILL.md:205`). NOTICE attribution entry required.

## Inference
Runtime deps: `onnxruntime` (MIT, CPU-only, never `-gpu`) + `tokenizers` (Apache-2.0) + numpy. **NO torch/transformers/PyLate at runtime** (governance test pins this, `test_pyproject_dependencies.py:39-45`). New optional extra `rerank = ["tensor-grep[semantic]", "onnxruntime>=1.20", "tokenizers>=0.21"]` (no upper caps). MaxSim = per-token L2-normalize + numpy matmul + rowwise-max + sum. 512-token truncation guard. Latency budget `TG_RERANK_BUDGET_MS` (default 2000) enforced in-code incl. cold ONNX init; else degrade-with-signal. Ties break ascending chunk index.

## Fail-closed contract (mirrors dense leg `retrieval_dense.py:9-19`)
New `src/tensor_grep/core/retrieval_late.py` + `LateRerankUnavailableError(RuntimeError)`.
- Extra not installed / model not fetched / budget exceeded / shape-mismatch → **recoverable**: degrade to RRF order, set `SearchResult.rank_fallback_reason` (`result.py:55-60`) + `tg:` stderr. Never silent-skip.
- ONNX/tokenizer load fails / encode raises → **`BackendExecutionError`** (`base.py:7-14`): propagate uncaught to the CLI boundary `main.py:6804-6813` → exit 2.
- Upstream degrade paths (`main.py:3595/3607/3638`) append `"; late rerank skipped"` to their existing reason.
- **Invariant (bidirectional test T6):** `--rerank` requested ⇒ EITHER order provably changed (reason untouched) XOR `rank_fallback_reason` non-None. No third state. Reuse `rank_fallback_reason` (a `rerank_status` field is a JSON_OUTPUT_VERSION bump, deferred).

## Golden-set gate (promotion; ships default-OFF regardless)
New `benchmarks/eval_late_rerank_quality.py` (T8): LIVE pipeline, 4 arms (BM25 / dense / RRF / RRF+MaxSim) on the SAME corpus+queries. Metrics from `core/retrieval_scoring.py` (recall@k/ndcg@k/mrr — don't invent). Corpus = pinned real repo + ~40 **vocabulary-mismatch** golden queries (query "verify login" → chunk defines `authenticate`); reuse express@4.21.1 where possible. Bidirectional oracle validation before any scored batch. **Thresholds (all hold, same run):** (1) nDCG@5 RRF+MaxSim > RRF by ≥+0.03 abs AND beyond 3-run noise; (2) recall@5 no regression; (3) latency p50 ≤ 2000ms @ pool_k=50 warm+cold, flag-off byte-identical; (4) tokens-per-correct on Sverklo P1/P2 no worse than `--semantic`. Gate fail = keep env-gated experimental OR retire with the negative recorded in `docs/PAPER.md`.

## Registration (§6 — 8 sites, 2 layers) — ONLY after T8 evidence
**Argv (3 + CI config):** (1) `rust_core/src/main.rs:170` SEARCH_PYTHON_PASSTHROUGH_FLAGS; (2) `bootstrap.py:24` _TG_ONLY_SEARCH_FLAGS; (3) `bootstrap.py:403-418` _can_delegate_to_native_tg_search unsupported_flags (the missed one); (4) `.tg-registration.toml` search-flag-front-doors + add site 3 as a declared member.
**SearchConfig (4):** (5) `config.py:184-188` new `late_rerank: bool = False`, built `main.py:6273`; (6) `main.py:1783` _NATIVE_TG_DELEGATION_DEFAULT_REQUIRED_FIELDS (field-coverage ratchet test will fail until classified); (7) `main.py:4059` _can_passthrough_rg `and not config.late_rerank`; (8) `main.py:6207-6216` multi-pattern-source conflict list.
Plus the Typer option (`main.py:6043`). `--rerank` REQUIRES `--semantic` (bare `--rerank` exits 2 — one char from `--rank`, must fail loud). No new command.

## TDD tasks (each = 1 PR-able testable deliverable; gates: `uv run --no-sync` pytest + ruff check + ruff format --check --preview + mypy src)
- **T0** — `rerank` extra + governance test (no torch/onnxruntime-gpu/pylate; mirror `test_pyproject_dependencies.py:39-45`).
- **T1** — `retrieval_late.py::maxsim_scores` pure numpy, hand-computed test + ties + empty.
- **T2** — `LateReranker.rerank()` order-only permutation, stub encoder; returns-permutation-never-drops + orders-by-maxsim.
- **T3** — ONNX encoder behind the extra: `late_available()` probe + `load_late_model()` recoverable/unrecoverable split + onnx_config prefixes + 512-truncation. Real-model smoke `skipif(not fetched)`.
- **T4** — checksum-pinned `python -m tensor_grep.core.retrieval_late --fetch` (SHA-256, byte-cap, timeout, atomic rename); fix the ghost message `retrieval_dense.py:95`. Tests: checksum-mismatch-fail-closed, atomic-on-partial-fail.
- **T5** — the seam: `rerank_hybrid` gains `late_reranker`, head/tail splice, `TG_LATE_RERANK=1` env gate (mirror `TG_RRF_CHANNELS`). Tests: flag-off byte-identical, reorders-head-only-tail-stable, same-match-membership.
- **T6** — fail-closed wiring + budget: the contract table end-to-end + the bidirectional invariant test.
- **T7** — real-model integration + latency receipt (cold+warm); dogfood the REAL binary via `scripts/dogfood/`.
- **T8** — golden harness `eval_late_rerank_quality.py` + `datasets/late_rerank_golden.jsonl` (~40 vocab-mismatch, oracle-validated) → the 4-row table + noise band.
- **T9** — `--rerank` registration (all 8 sites) + requires-`--semantic` test + registration-check green — ONLY after T8 evidence.
- **T10** — docs + NOTICE (Apache-2.0 attribution) + skill STATUS note.

## Biggest risk
CPU latency of encoding ~50 chunks/query + cold ONNX init blowing the 2000ms budget. If p50 is multi-second → the stage fails its own gate → honest documented no-ship. Mitigations in-spec: pool_k cap, budget-degrade-with-signal, int8, competitor's CPU-viable proof.

## Build order
T0→T1→T2 (foundation, no model) → T3→T4 (encoder + fetch) → T5→T6 (seam + fail-closed) → T7 (real-model receipt) → T8 (golden gate — ship/no-ship decision) → T9→T10 (registration + docs, only if T8 passes). Each PR drains one-per-publish.
