---
name: tensor-grep-semantic-search-campaign
description: >
  Use when building, extending, or reviewing tensor-grep's APPROVED local hybrid
  semantic search — BM25 + CPU dense embeddings fused with Reciprocal Rank Fusion
  (RRF), no API key, no GPU (roadmap item #1). Load before adding a dense/embedding
  leg, RRF fusion, a `tg index` command, or changing `tg search --rank` / `--bm25`.
  Covers the decision-gated build phases with exact commands + expected gate numbers,
  the ranked solution menu (Semble / ripvec / BM25-only) with derivation obligations,
  the retrieval-quality + editor-plane + token-economy promotion gates, the Backend
  Fail-Closed Contract for the dense leg, fenced-off wrong paths (no API-key
  embeddings, no GPU dependency, do not break `--format rg` / `--json` / `--ndjson`
  semantics), and routing promotion through change-control. STATUS as of 2026-07-08,
  v1.49.3: the dense leg + RRF fusion described here as the target architecture
  (Candidate 1 / the Semble pattern) SHIPPED as `tg search --semantic`
  (`retrieval_dense.py` + `retrieval_fusion.py`, default-OFF, gated on the `semantic`
  extra) — this skill's Phases 0-3 are now historical/reference for HOW it was built;
  see the STATUS note below before assuming any "not built yet" claim in Sections 1-2.
---

# tensor-grep — Local Hybrid Semantic Search Campaign

A decision-gated runbook for building the **APPROVED** local hybrid semantic search
layer: **BM25 (lexical) + a CPU dense-embedding leg, fused with Reciprocal Rank
Fusion (RRF), 100% local, no API key, no GPU.** This is roadmap item #1
(`AGENTS.md:232`) — the #1 validated user ask and the biggest competitive gap.

This skill is the campaign map. It tells you what already exists, what you are
building, the exact commands + expected numbers at each gate, the wrong paths that
are fenced off, and how promotion routes through change-control. **You do not ship
anything user-visible from this skill without beating the gate and doing a conscious
flag-flip** (see Phase 5).

> **STATUS UPDATE (2026-07-08, v1.49.3): SHIPPED.** The dense leg + RRF fusion this
> skill specifies as the target architecture (Candidate 1, the Semble pattern) is now
> live: `src/tensor_grep/core/retrieval_dense.py` (model2vec + `potion-code-16M`,
> `DenseUnavailableError`/`BackendExecutionError` fail-closed contract exactly as §6
> specifies) and `src/tensor_grep/core/retrieval_fusion.py`
> (`reciprocal_rank_fusion(rankings, k=DEFAULT_K=60)` — matches §3/§5 exactly), wired
> as `tg search --semantic` (`main.py` typer option, default `False`; bootstrap front
> door at `bootstrap.py:44,413`) gated on the optional `semantic` extra
> (`pyproject.toml:467`, `model2vec>=0.5`+`numpy>=1.26`). **Sections 1-2 below still
> describe the PRE-BUILD state and are now WRONG on the "does not exist yet" claims —
> read them as historical design intent, not current fact.** No `tg index` command
> was added (the persisted-index building blocks in `semantic_index.py` remain
> unwired, per the original §1 note). **Not yet re-verified: whether Phase 4's
> promotion gate (RRF-hybrid beats BM25-only on a real corpus + editor-plane latency)
> was actually measured before shipping, or whether `--semantic` graduated past
> default-OFF** — re-run `grep -n "retrieval_dense\|retrieval_fusion\|--semantic"
> AGENTS.md docs/PAPER.md` and check for a promotion PR before treating this as fully
> closed-out; if you are extending it further (chunking, a `tg index` command, a
> default-flip), Phases 4-5 below are still the right runbook.
>
> **Two further opt-in refinements SHIPPED since (both default-OFF, additive):**
> `TG_CHUNKER=structural` (PR #443, `9015238`, shipped v1.47.0) -- cAST AST-shaped
> chunking (`retrieval_chunker.py`, `CHUNKER_MODE_ENV_VAR`) beside the fixed-window
> chunker, fail-open, chunk-shape-identical contract, index-version-bumped
> (`semantic_index.py` v2 folds the active chunker mode into its cache key). This
> retires the old "cAST is a candidate deepener, not a requirement for v1" framing in
> S3 Candidate 1 below -- it shipped as an explicit opt-in, not a requirement, but it is
> no longer merely a future candidate. `TG_RRF_CHANNELS=1` (PR #442, `a402f81`, shipped
> v1.46.0) -- channelized RRF (`reranker.py`, `_RRF_CHANNELS_ENV`): weighted per-channel
> fusion including a 1.5x path/filename channel, additive `weights` param, default-off
> and byte-identical when unset.
>
> (Superseded note, kept for history: as of 2026-07-05/v1.40.2 this was still unbuilt
> and `tensor-grep-large-repo-scale-campaign` was the live campaign instead — that is
> no longer the case for the dense/RRF leg specifically; re-check which campaign is
> "live" at the time you read this.)

> **STATUS UPDATE 2 (2026-07-16, v1.77.0-v1.78.1, campaign #189): the architecture
> GRADUATED into a standalone whole-repo command, `tg find`.** Where `--semantic`
> re-ranks an EXISTING regex match set, `tg find` walks and ranks the WHOLE repo (no
> pattern pre-filter), reusing the same `retrieval_dense.py`/`retrieval_fusion.py`
> core. It shipped its own golden harness, `benchmarks/eval_late_rerank_quality.py`
> (a 40-query NL vocab-mismatch golden set + literal/identifier golden slices,
> superseding this skill's toy `eval_bm25_quality.py` as the Phase-4-style
> discriminating gate for `tg find` specifically) — gate-run result: `rrf` beats
> `bm25` by **+0.195 ndcg@10 / +0.30 recall@10**, bidirectional-oracle-validated
> (internal; public numbers stay CEO-gated #72). Two further pieces are STILL
> evidence-gated, not shipped-as-default: (1) **optional MaxSim late rerank**
> (`TG_LATE_RERANK`) stays OFF — the gate-run shows it regressing vs plain BM25, but
> that is entangled with a known harness gap (`retrieval_late.py:328-333`'s doc-role
> encoder is not query/doc role-aware yet), NOT a verdict on MaxSim itself; (2) the
> `TG_FIND_DENSE_WEIGHT` query-adaptive knob (see `tensor-grep-config-and-flags`) is
> default-OFF (`1.0` = byte-identical no-op) with real evidence in hand (a 1:5
> bm25:dense weight lifts NL ndcg@10 by +0.14 with zero per-category regression) — the
> default-flip itself is a separate, still-open CEO checkpoint (product taste, not an
> engineering gate). **Receipt (real-corpus-dogfood-beats-fixture-green):** the query
> classifier that scopes `TG_FIND_DENSE_WEIGHT` to multi-word queries was originally a
> `split_terms()` morpheme-count floor (`> 2` morphemes = NL); it passed its synthetic
> literal-golden fixture but a real-repo dogfood on tensor-grep's own `src/` caught it
> mis-boosting 5 of 6 literal identifier queries (`_confine_mcp_path`, `getUserName`,
> `reciprocal_rank_fusion` all split into 3+ morphemes) — fixed by switching to a
> whitespace word-count gate (`len(query.split()) <= 1` stays literal), #191/#630.
> See `tensor-grep-run-and-operate` §1/§7/§11c for the CLI/MCP command surface and
> exit contract, and the dedicated operator skill **`tensor-grep-find-and-route`** for the
> day-to-day `tg find`/`tg route-test` CUJ (this skill stays the BUILD/campaign history; that one is
> the how-to-run doc).
>
> **STATUS UPDATE 3 (2026-07-21, research campaign #251) — cAST structural chunking REJECTED as
> default; do not re-propose it.** `TG_CHUNKER=structural` (shipped v1.47.0, mentioned above as an
> opt-in refinement) was evaluated as a candidate for the DEFAULT chunker on a real-corpus retrieval
> eval: the retrieval-quality delta was a net WASH, while cAST chunking ran **24.4x SLOWER** and
> produced **~38% LARGER** chunks than the shipped line-window `chunk_file`. The opt-in code remains
> shipped for experimentation; it is **not** promoted, and this is now a documented retirement in
> `docs/PAPER.md` §3.10 — do not re-run this experiment expecting a different verdict without new
> evidence. **Use the right harness when re-measuring anything chunker-sensitive:**
> `benchmarks/eval_late_rerank_quality.py` (live — imports and calls `chunk_file`/`chunk_file_structural`)
> is the correct instrument; `benchmarks/run_repo_retrieval_benchmarks.py` is a **static-fixture
> REPLAY that never calls `chunk_file` at all** — it cannot detect a chunker regression or improvement
> and was mistakenly cited as the Phase-4 discriminating gate earlier in this skill (§0/Phase 4 below
> now point at the correct script).
>
> **One-line caution (do not re-propose): dense-embedding compression (int8/binary/PCA) was
> evaluated and DEFERRED** — memory-only win (3.79x smaller on disk), but ~2x SLOWER in numpy (no
> int8 SIMD on the CPU-only path this campaign targets); a real speed win needs a native kernel
> (banked as a moat-investment option, not a quick follow-up here).
>
> **Sibling hardening, not a semantic-search change:** `#699`/A7 hardened the SIBLING flat lexical
> scorer (`_score_symbol` in `repo_map.py` — exact word-boundary bonus + test-file demotion), which
> is a DIFFERENT scorer from this campaign's BM25/dense/RRF stack (see
> `code-search-and-retrieval-reference` §3) — do not conflate the two when reading a "ranking fixed"
> claim.

---

## 0. When to use this skill — and when to use a sibling instead

Use this skill when the task is **the hybrid-retrieval build itself**: adding a dense
leg, RRF fusion, a persisted hybrid index, a `tg index` command, or measurably
improving `tg search --rank`.

| If you actually need to… | Use this sibling instead |
| --- | --- |
| Understand the front door / routing / registration sites / backend contract | `tensor-grep-architecture-contract` |
| Get merge/release/experimental-flag gates + the incidents behind them | `tensor-grep-change-control` |
| Register a new flag or command (the exact sites) | `tensor-grep-config-and-flags` (mechanics) + `tensor-grep-architecture-contract` |
| Run/read a benchmark so a number is claim-quality, not noise | `tensor-grep-benchmark-and-proof-toolkit` |
| BM25 / RRF / static-embedding / IDF theory reference | `code-search-and-retrieval-reference` |
| Research external prior art (Semble, ripvec, potion-code-16M) | `tensor-grep-research-frontier` + `tensor-grep-research-methodology` |
| Debug a broken build / test / import | `tensor-grep-debugging-playbook` |
| Learn a settled battle (FFI reverts, dep caps, mock-vs-real) so you don't re-fight it | `tensor-grep-failure-archaeology` |
| Build/run the toolchain (uv, maturin, cargo) | `tensor-grep-build-and-env` |
| The local validation gate + QA | `tensor-grep-validation-and-qa` |
| Update README/AGENTS/docs after shipping | `tensor-grep-docs-and-writing` |
| Position the feature externally (never "faster grep") | `tensor-grep-release-and-positioning` |

**This skill never routes around change-control.** Promotion is a `change-control`
decision (Phase 5); this skill only produces the *evidence* that decision needs.

---

## 1. What already exists (the BM25 lexical leg is SHIPPED)

> **2026-07-08 correction: this section (and §2 below) was written when only the BM25
> leg existed. The dense leg (`retrieval_dense.py`) and RRF fusion
> (`retrieval_fusion.py`) have SHIPPED since — see the STATUS box above. Treat every
> "does not exist yet" statement below as describing the pre-2026-07-0x state, not
> current fact; re-verify with the grep in the STATUS box before relying on it.**

Read these before writing a line. Every path below is verified against the repo as
of v1.17.25.

| File | What it does | Load-bearing facts |
| --- | --- | --- |
| `src/tensor_grep/core/retrieval_chunker.py` | Splits files into line-window chunks | `chunk_file(chunk_size=30, overlap=5)`; step = `max(1, chunk_size-overlap)` = 25; `MAX_CHUNKS=100_000` loud guard (raises, never silent OOM). Per-chunk granularity is what the design council settled on (not per-line, not per-file). |
| `src/tensor_grep/core/retrieval_lexical.py` | Tokenizer + bare overlap counter | `split_terms()` is camelCase/underscore/hyphen aware, lowercased. This is the shared tokenizer — the dense leg MUST tokenize identically or scores diverge. |
| `src/tensor_grep/core/retrieval_bm25.py` | Okapi BM25 over chunks | `Bm25Index`, `k1=1.5`, `b=0.75`, IDF with +1 smoothing (non-negative weights). Dedupes query terms so a repeated token isn't double-counted. Returns `[(chunk_index, score)]`, zero-score chunks excluded, ties break by chunk index (deterministic). |
| `src/tensor_grep/core/reranker.py` | The LIVE `tg search --rank` path | `rerank_by_bm25(result, query, file_paths)` re-orders matches by the best BM25 score of the chunk containing each match; stable sort (ties keep grep order); non-scoring matches sink. Builds the BM25 index **in memory every call** over just the matched files — no persisted index. |
| `src/tensor_grep/core/semantic_index.py` | Persisted chunk-BM25 index building blocks | `build_and_save` / `load_or_warn` under `.tg_semantic_index/` (env `TG_SEMANTIC_INDEX_DIR`), **SEPARATE** from the Rust TGI v3 `.tg_index` (trigram). `INDEX_VERSION=1`. Stale check = SHA-256 fingerprint over sorted paths + mtimes → on mismatch, warn to stderr + return `None` → in-memory fallback. **NOT wired to the CLI — there is no `tg index` command yet.** |
| `tg install-dense` (CLI command, v1.91.0) | One-shot dense-leg setup | Installs the `semantic` extra (`model2vec`+`numpy`, torch-free) via the same `uv tool → uv pip → pip` cascade `tg upgrade` uses, then fetches the checksum-pinned `potion-code-16M` model; fails closed (non-zero exit, no partial model directory) on any pip/network/checksum failure — never a silent half-installed state. Every dense-absent hint across the CLI (`tg search --semantic`, `tg find`'s `rank_fallback_reason`) now leads with `tg install-dense` (v1.93.0/#705) instead of a bare "pip install the extra" instruction. |
| `src/tensor_grep/core/retrieval_scoring.py` | Metrics | `recall_at_k`, `precision_at_k`, `mean_reciprocal_rank_at_k`, `ndcg_at_k`, `f1_score`, `RetrievalMetrics`. These are the promotion yardsticks — use them, don't invent new ones. |

**How `--rank` is wired (verify before changing):**
- Flag: `--rank` (alias `--bm25`), default OFF. `SearchConfig.rank_bm25 = False` (`config.py:181-183`). The dense leg is described there as "a separate gated flag."
- It is a **TG-only** search flag: `bootstrap.py::_TG_ONLY_SEARCH_FLAGS` (`--rank` line 42, `--bm25` line 43) — the bootstrap front door intercepts it and does NOT forward it to ripgrep. This is one of the two flag front doors; see `tensor-grep-config-and-flags`.
- Setting `--rank` **leaves the ripgrep passthrough fast-path**: the `_can_passthrough_rg()` condition includes `and not config.rank_bm25` (`src/tensor_grep/cli/main.py:3883`, re-verified 2026-07-05), so the request runs the tg engine and results are re-ordered right after match aggregation — the `if config.rank_bm25 and all_results.matches:` guard through the `rerank_by_bm25(...)` call at `main.py:6535-6538` (re-verified 2026-07-05).
- User docs: `README.md:38` and `README.md:136-137`.

**Bottom line:** the **lexical leg (BM25) and the persisted-index building blocks
already exist and ship default-OFF.** The campaign adds the **dense leg + RRF fusion
+ (optionally) a wired persisted hybrid index.**

---

## 2. What you are building (the approved architecture)

```
                 chunk_file()  ──►  chunks  ──►  ┌─ BM25Index.query() ──► ranking A
   query ──►  split_terms()                      │
                 (same tokenizer)                 └─ dense encode+cosine ──► ranking B
                                                          │
                                        RRF fuse(A, B, k=60)  ──►  final ranking
```

- **BM25 leg** — exists (`retrieval_bm25.py`).
- **Dense leg** — DOES NOT EXIST YET. A CPU static-embedding model produces a vector
  per chunk and per query; rank chunks by cosine similarity. Static means a per-token
  vector *lookup* (no transformer forward pass at query time) → fast on CPU, no GPU,
  no API key, no network at query time.
- **RRF fusion** — DOES NOT EXIST YET. Combine the two rankings without score
  normalization: `score(d) = Σ_r 1 / (k + rank_r(d))` over the rankers `r ∈ {bm25,
  dense}`, with **k = 60** (the value the reference architecture uses). A document
  absent from a ranker's list contributes 0 for that ranker. RRF is rank-based, so it
  is robust to the fact that BM25 scores and cosine scores are on incomparable scales.

**SUPERSEDED (was true through v1.40.2, 2026-07-05; false as of v1.49.3, 2026-07-08):**
~~no dense/embedding/RRF/Model2Vec/potion code exists in `src/` today~~ — this leg has
since shipped as `retrieval_dense.py` + `retrieval_fusion.py`; re-run
`grep -rin "model2vec|potion|reciprocal_rank_fusion|StaticModel" src/` yourself and
expect real hits, not just comments.

**The moat framing (do not lose it):** this is **not** "faster grep." ripgrep is the
raw-text parity baseline. The value is agent-native retrieval quality on
vocabulary-mismatch queries (find `authenticate` when the user typed "verify login").
Keep the positioning honest per `tensor-grep-release-and-positioning`.

---

## 3. Solution menu (RANKED) with derivation obligations

Pick in this order. **Each candidate carries a derivation obligation — a claim you
MUST verify (not assume) before you build on it.** "Derive" = confirm against a
primary source (the model card, the license file, a local import test), then record
the finding. Route the research through `tensor-grep-research-frontier` +
`tensor-grep-research-methodology`; never trust a self-report (change-control gate B).

### Candidate 1 (preferred): the Semble pattern
Tree-sitter chunking + **`potion-code-16M`** Model2Vec static embeddings + BM25 + RRF
(k=60). CPU-only, MIT. This is the reference architecture named in `AGENTS.md:230`.

Derivation obligations before you depend on it:
1. **License** — confirm `potion-code-16M` (and the `model2vec` runtime) are
   MIT/Apache-compatible with tensor-grep's Apache-2.0 and add the required NOTICE
   entries. Ideas are free; imported code/weights need their notices.
2. **Truly offline** — confirm the model loads from a bundled/cached file with **no
   network call and no API key** at query time. If it phones home or needs a token,
   it is DISQUALIFIED (see §4 fenced paths).
3. **CPU + footprint** — confirm it runs with no GPU, and record the on-disk model
   size and the added dependency weight. The model **must be an OPTIONAL extra**, not
   a hard install dependency (every-install must still work with BM25-only).
4. **Chunking choice -- RESOLVED, both ship.** Semble uses tree-sitter chunks;
   tensor-grep already had line-window `chunk_file` as the default, and now also ships
   `TG_CHUNKER=structural` (PR #443, v1.47.0) -- opt-in cAST AST-shaped chunking
   (`docs/PAPER.md`/arXiv:2506.15655 is the reference paper), fail-open and
   chunk-shape-identical to the fixed-window path when unset. This was a *candidate
   deepener* as of the original design; it is now a shipped, default-off refinement --
   do not describe it as unbuilt.

### Candidate 2: ripvec (pure-Rust)
A pure-Rust vector path. Derivation obligations: confirm license, maturity, and
whether it fits the existing PyO3 bridge without reintroducing the FFI overhead that
was already measured too high and reverted (`tensor-grep-failure-archaeology`: FFI is
not the dir-scan speed path). Only choose this if Candidate 1 fails a gate AND you
have measured that the Rust path clears the same promotion bar.

### Candidate 3: BM25-only (the honest null result)
Ship nothing new. **This is a legitimate, non-embarrassing outcome** if the dense leg
does not beat the BM25 baseline on both retrieval quality and editor-plane latency.
"No speed/quality claim without measured numbers vs the baseline" (change-control
gate C) cuts both ways: if the numbers aren't there, the correct move is to keep the
shipped `--rank` baseline and record the negative result. `README.md:194` states the
rule explicitly: extend BM25 re-ranking with semantic re-ranking **only when it
demonstrably beats the shipped `tg search --rank` baseline on both retrieval quality
and editor-plane benchmarks.**

---

## 4. Fenced-off wrong paths (do NOT do these)

| Forbidden | Why | If you're tempted |
| --- | --- | --- |
| **API-key / hosted embeddings** (OpenAI, Voyage, Cohere, any `*_API_KEY`) | Breaks "no API key, runs on every install, local-first." The whole point is $0, offline. | Static local model only. If a candidate needs a key or a network call at query time, it's disqualified. |
| **GPU / CUDA dependency for the dense leg** | GPU is EXPERIMENTAL, default-OFF, and currently *slower* than CPU with no promotion-ready path (P1 kernel paused, `AGENTS.md:228-234`). A GPU-gated ranking layer would not run on the common install. | CPU static embeddings. GPU may be an *optional* future accelerator, never a requirement. |
| **Breaking `--format rg` / `--json` / `--ndjson` semantics** | Those output contracts are the raw-grep parity surface. `--rank` is a **re-order overlay**: same matches, different order. When `--rank` is NOT set, the ripgrep passthrough fast-path (`main.py:3883`) must remain byte-for-byte. | Keep ranking strictly post-processing over an already-produced `SearchResult`. Never change match membership or the rg-shaped output when ranking is off. |
| **A hard new install dependency** | Every-install must keep working. | Make the dense model an optional extra; degrade to BM25-only when absent (see §6). |
| **Shipping user-visible before the gate** | Violates experimental-until-proven (change-control gate D). | Default-OFF flag + benchmark + conscious flag-flip (Phase 5). |
| **Eyeballing "it feels more relevant"** | Ranking surfaces silently FLIP on corpus change; the blast radius is invisible to the call graph (known weak point — flat scorer, incident #302). | Measure `recall@k` / `ndcg@k` on a real corpus. Numbers or it didn't happen. |
| **Routing around change-control** | Non-negotiable. | Produce evidence here; let `tensor-grep-change-control` gate the flip. |

---

## 5. The phased runbook (decision-gated)

Run phases in order. Each gate says the expected number and where to **branch** if
you see something else. All commands are copy-pasteable; PowerShell is the primary
shell on the dev box, but `uv run` is cross-platform.

> **`uv run` gotcha:** a bare `uv run ...` can re-sync and drop the `[dev]`
> tree-sitter/extras tree. For benchmark/import work use `uv run --no-sync ...` so the
> installed dev tree (and any editable install of the dense model) is not wiped. See
> `tensor-grep-build-and-env`.

### Phase 0 — Establish the baseline (NEVER skip)

You cannot claim an improvement without the number you improved on.

```powershell
uv run --no-sync python benchmarks/eval_bm25_quality.py --top-k 3
```

**Expected (verified 2026-07-02):**
```
BM25 baseline (top_k=3, n=10 queries):
  recall@k   = 1.000
  precision  = 0.333
  mrr@k      = 1.000
  ndcg@k     = 1.000
v2 gate (recall@k >= 0.6): PASS
```
- `benchmarks/eval_bm25_quality.py` defines `V2_GATE_RECALL = 0.60` and states in its
  own docstring: "the v2 dense+RRF leg must beat this before it ships user-visible."
- **GATE 0a:** recall@k must be `1.000` here. If it is lower, the BM25 leg itself
  regressed → STOP, do not build dense on a broken base → `tensor-grep-debugging-playbook`.
- **GATE 0b — read this or you'll waste weeks:** this toy corpus is
  keyword-discriminating, so **BM25 already saturates it at recall 1.0.** Passing the
  0.60 gate proves *nothing* about whether dense helps — it is a **floor / sanity
  gate, not the discriminating gate.** The real justification for a dense leg is
  **vocabulary-mismatch** queries (synonyms/paraphrase) where lexical BM25 misses.
  Your promotion evidence MUST come from a harder, realistic corpus (Phase 4), not
  this file.

### Phase 1 — Choose + derive the solution

Work the §3 menu top-down. Complete every derivation obligation for your chosen
candidate and **write the findings down** (license, offline-proof, CPU-proof,
footprint, optional-extra plan). Research via `tensor-grep-research-frontier` +
`tensor-grep-research-methodology`. Before writing code, verify the plan's seam
claims against the real files with `file:line` citations (verify-plan-against-code) —
an AI-drafted plan that says "add it in pipeline.py" is a hypothesis until you
confirm `--rank` is actually wired in `main.py`.

- **GATE 1:** if the preferred candidate fails a derivation obligation (needs a key,
  needs a GPU, incompatible license, cannot be an optional extra), do not "work around
  it" — drop to the next candidate. If all dense candidates fail, Candidate 3
  (BM25-only) is the correct answer; document the negative result and stop.

### Phase 2 — Build the dense leg behind a default-OFF experimental flag

- Add a **new** module (mirror the existing seam names, e.g.
  `core/retrieval_dense.py`) — do not bolt onto `retrieval_bm25.py`.
- Reuse `split_terms()` (or an explicitly justified tokenizer) so the two legs stay
  comparable.
- Wire it behind a **separate default-OFF flag** (the `config.py:182` note already
  anticipates "a separate gated flag" for the dense leg). Do NOT change the meaning of
  `--rank`/`--bm25` yet.
- Honor the **Backend Fail-Closed Contract** (§6).
- TDD: write the contract test first (`tests/unit/test_retrieval_dense.py`), then the
  smallest implementation. See existing tests `tests/unit/test_retrieval_bm25.py`,
  `tests/unit/test_reranker.py`, `tests/unit/test_semantic_index.py` for the pattern.
- **GATE 2:** the dense leg imports and runs with the model **absent** (degrades to
  BM25-only, visibly) AND with it present (produces a ranking). Prove the FFI/model
  path against the REAL runtime, not a mock — mock-green while the real bridge is dead
  is a documented trap (`tensor-grep-failure-archaeology`).

### Phase 3 — Build RRF fusion

- Implement `reciprocal_rank_fusion(rankings, k=60)` as a pure function taking each
  leg's ordered list of chunk indices and returning the fused order. Keep `k`
  configurable (default 60) and deterministic ties.
- Fuse **ranks**, not raw scores (BM25 score vs cosine are incomparable scales — this
  is the whole reason RRF is chosen).
- **GATE 3:** unit-test that fusing two identical rankings is a no-op, and that a
  document top-ranked by either leg surfaces near the top of the fused list.

### Phase 4 — Measure (the real gate)

Two measurements, both required (`README.md:194`):

1. **Retrieval quality on a realistic corpus** (not the toy). Use
   `benchmarks/eval_late_rerank_quality.py` — the LIVE, chunker/ranking-sensitive harness (it actually
   imports and calls `chunk_file`/`rank_chunks`, and computes `RetrievalMetrics`: recall/precision/
   mrr/ndcg on a real repo + the 40-query NL golden set). **Do not use
   `benchmarks/run_repo_retrieval_benchmarks.py`** for this — it is a static-fixture REPLAY that never
   calls `chunk_file` and cannot detect a chunker or dense/RRF-weighting change at all (this was the
   cAST-chunking evaluation's own harness-selection mistake before STATUS UPDATE 3 above corrected
   it). Produce three rows: **BM25-only**, **dense-only**, **RRF-hybrid**, on the SAME corpus + queries.

   ```powershell
   uv run --no-sync python benchmarks/eval_late_rerank_quality.py --output artifacts/bench_find_quality.json
   ```
   (Read its args first; see `tensor-grep-benchmark-and-proof-toolkit` §7 for the corpus-hardness and
   paired win/loss/tie reporting rigor this gate specifically needs.)

2. **Editor-plane latency** — the ranking overlay must not blow the interactive
   budget:
   ```powershell
   uv run --no-sync python benchmarks/run_editor_plane_benchmarks.py
   ```

Route interpretation through `tensor-grep-benchmark-and-proof-toolkit` (noise-floor
rule for sub-10ms rows, fair-baseline rule, launcher attribution). Also record the
**token-economy** delta if the surface feeds the agent capsule (`AGENTS.md` names
token economy as a focus).

**Promotion gate (all must hold, measured vs the SAME baseline run):**

| Metric | Requirement |
| --- | --- |
| `recall@k` (real corpus) | RRF-hybrid > BM25-only by a margin beyond the noise floor |
| `ndcg@k` (real corpus) | RRF-hybrid ≥ BM25-only (no ranking-quality regression) |
| Editor-plane latency | within the interactive budget; no material regression vs `--rank` |
| Token economy (if capsule-facing) | no worse than BM25-only |

- **GATE 4:** if RRF-hybrid does not beat BM25-only on retrieval quality AND hold the
  line on latency, **do not ship it.** Reject the regression even if the code is clean
  (change-control gate C). The honest outcomes are: (a) improve the dense leg/chunking
  and re-measure, or (b) record the negative result and keep BM25-only.

### Phase 5 — Promote through change-control (never here)

This skill produces evidence; **`tensor-grep-change-control` owns the flip.** The
graduation path is fixed:

1. Ship **experimental, default-OFF** (already true after Phase 2).
2. Attach the Phase 4 evidence (three-row quality table + editor-plane numbers +
   token economy) to the PR.
3. **Dry-run** on real data (dogfood the REAL binary via `scripts/dogfood/`; CliRunner
   bypasses the bootstrap front door and will not exercise routing).
4. **Conscious flag-flip** — a deliberate, reviewed default change, never auto-merged,
   never admin-merged. Autonomy is draft-PR-only.
5. Update docs (`README.md`, `AGENTS.md`, the usage skill) via
   `tensor-grep-docs-and-writing`; observe one-merge-per-tick + the push-race rules
   (`tensor-grep-release-and-positioning`).

If you add a **`tg index`** command (the natural home for a persisted hybrid index),
remember it needs the **4 command-registration sites** (miss one → silent misroute)
and a new flag needs the **2 flag front doors** — see `tensor-grep-config-and-flags`
and `tensor-grep-architecture-contract`.

---

## 6. Backend Fail-Closed Contract for the dense leg

The dense leg is a compute path; it is bound by `backends/base.py`
(`BackendExecutionError`) and the `AGENTS.md:216-224` contract. The recurring
anti-pattern to avoid: a bare `except Exception:` that silently returns empty or
swaps engines.

- **Model missing / not installed** → this is a **legitimate degraded fallback** to
  BM25-only, but it MUST be **VISIBLE**: set a `fallback_reason` on the `SearchResult`
  so JSON/CLI consumers can tell degraded output from full hybrid output. Never label
  BM25-only output as "semantic."
- **Model load/encode raises at runtime** (corrupt cache, OOM, version skew) → raise
  `BackendExecutionError`; do not return a clean empty result that reads as "no
  matches." A real failure reported as a no-match is the exact bug this contract
  exists to prevent.
- **Contract flag the fallback cannot honor** → fail closed (raise), do not swap. (For
  a *ranking* overlay a graceful visible degrade to BM25 is the norm; only fail-closed
  if a caller explicitly demanded semantic-only and you cannot deliver it.)
- Validate the model's output shape (vector dimensionality, chunk count) before you
  index, so a mismatch degrades gracefully instead of raising an `IndexError` that a
  broad `except` then swallows.

See `tensor-grep-architecture-contract` for the full contract and the
planned `SafeBackendMixin` conformance gate.

---

## 7. Common failure modes → branch

| Symptom | Likely cause | Branch to |
| --- | --- | --- |
| BM25 baseline recall < 1.0 in Phase 0 | BM25/tokenizer/chunker regression | `tensor-grep-debugging-playbook` |
| Dense leg "works" in tests but the real binary shows no effect | mock-green while the real path is dead; or `--rank` not actually re-routing | `tensor-grep-failure-archaeology`; verify against the real binary via `scripts/dogfood/` |
| `uv run` benchmark can't import the dense model | `uv run` re-synced away the extra | re-run with `uv run --no-sync`; `tensor-grep-build-and-env` |
| Numbers look great but flip on a different repo | ranking fragility / corpus-sensitive scorer (known weak point) | measure on multiple corpora; `tensor-grep-benchmark-and-proof-toolkit` |
| A speedup/quality claim disputed in review | no fair-baseline row, or sub-noise delta | `tensor-grep-benchmark-and-proof-toolkit` (noise-floor + fair-baseline rules) |
| `--json`/`--format rg` output changed shape | ranking leaked into match membership/output, not just order | revert to strictly post-processing; re-read §4 |
| Release didn't publish after the flip | push-race / one-merge-per-tick violation | `tensor-grep-release-and-positioning` |

---

## 8. Pre-flight checklist (before you open the PR)

- [ ] Phase 0 baseline recorded (`eval_bm25_quality.py`) — with the note that it's a floor, not the discriminating gate.
- [ ] Chosen candidate's derivation obligations all verified + written down (license, offline, CPU, optional-extra).
- [ ] Dense leg is a NEW module, behind a NEW default-OFF flag; `--rank`/`--bm25` semantics unchanged.
- [ ] Fail-closed contract honored: missing model → visible `fallback_reason`; runtime error → `BackendExecutionError`.
- [ ] RRF is rank-based, `k=60` default, deterministic; unit-tested (identity no-op + top-surfacing).
- [ ] Phase 4 evidence: three-row quality table (BM25 / dense / RRF) on a REAL corpus + editor-plane latency + token economy — RRF beats BM25-only on quality without a latency regression, OR the negative result is documented and you stop.
- [ ] `--json` / `--ndjson` / `--format rg` unchanged when ranking is off.
- [ ] Local validation green: `uv run ruff check .` · `uv run ruff format --check --preview .` · `uv run mypy src/tensor_grep` · `uv run pytest -q` (CI runs `ruff format --check --preview` — you MUST pass `--preview`).
- [ ] Real-binary dogfood, not just CliRunner (`scripts/dogfood/`).
- [ ] Promotion routed through `tensor-grep-change-control`; draft PR only; conscious flag-flip, never auto-merge.

---

## Provenance and maintenance

Everything below is verifiable from the repo. Re-run these when a claim may have
drifted; date-stamp any change.

- **Version / date:** facts originally verified `v1.17.25` (2026-07-02); re-verified
  UNCHANGED against released `v1.40.2` (origin/main `8829441`) on 2026-07-05; spot-checked
  again 2026-07-08 against `v1.49.3` and found the dense/RRF leg now SHIPPED (see STATUS
  UPDATE near the top); spot-checked again 2026-07-16 against `v1.78.1` and found the
  architecture graduated into `tg find` (see STATUS UPDATE 2 near the top); **spot-checked again
  2026-07-22 against `v1.93.2` and recorded the cAST-chunking rejection + dense-int8 deferral +
  install-dense row (see STATUS UPDATE 3 near the top — this was targeted at the research-campaign
  #251 retirements and the harness-selection correction, not a full re-walk of Phases 0-8 below).**
  Re-check: `grep -m1 release_docs_current_tag AGENTS.md` and `grep -m1 '"version"' npm/package.json`.
- **Dense leg + RRF now shipped:** `ls src/tensor_grep/core/retrieval_dense.py src/tensor_grep/core/retrieval_fusion.py`;
  `grep -n "\-\-semantic" src/tensor_grep/cli/main.py src/tensor_grep/cli/bootstrap.py`;
  `grep -n "semantic = " pyproject.toml` (the optional extra).
- **BM25 leg + defaults:** `Read src/tensor_grep/core/retrieval_bm25.py` (k1=1.5, b=0.75),
  `retrieval_chunker.py` (chunk_size=30, overlap=5, MAX_CHUNKS=100_000).
- **`--rank` wiring + default-OFF:** `grep -n "rank_bm25" src/tensor_grep/core/config.py`
  (default False), `grep -n "rerank_by_bm25\|not config.rank_bm25\|rank_bm25=rank" src/tensor_grep/cli/main.py`,
  `grep -n "\-\-rank\|\-\-bm25" src/tensor_grep/cli/bootstrap.py` (TG-only flag front door).
- **Dense/RRF genuinely unbuilt:** `grep -rin "model2vec\|potion\|reciprocal_rank_fusion\|StaticModel\|sentence_transformers" src/` → expect only comments/GPU words, no implementation.
- **The gate:** `grep -n "V2_GATE_RECALL\|must beat" benchmarks/eval_bm25_quality.py` (0.60), and run
  `uv run --no-sync python benchmarks/eval_bm25_quality.py --top-k 3` (expect recall 1.000 — the floor).
- **Governance:** roadmap item #1 `AGENTS.md` §"Roadmap Sequencing" (~line 230, Semble reference);
  the "only when it demonstrably beats the shipped baseline on both retrieval quality
  and editor-plane" rule `grep -n "editor-plane" README.md`; backend contract
  `AGENTS.md` §"Backend Fail-Closed Contract".
- **Benchmarks:** `ls benchmarks/eval_bm25_quality.py benchmarks/eval_late_rerank_quality.py benchmarks/run_editor_plane_benchmarks.py` — `run_repo_retrieval_benchmarks.py` still exists but is a static-fixture replay, not the live chunker-sensitive gate (see STATUS UPDATE 3 / Phase 4 above).
- **Persisted-index building blocks (unwired):** `Read src/tensor_grep/core/semantic_index.py`
  (env `TG_SEMANTIC_INDEX_DIR`, `.tg_semantic_index/`, INDEX_VERSION=1, no `tg index` command).

**Open / candidate (not proven — do not present as fact):**
- The exact `potion-code-16M` license, offline behavior, size, and dep footprint are
  **derivation obligations** (Phase 1), not settled facts — verify via
  `tensor-grep-research-frontier`.
- The `k=60` RRF constant and line-vs-AST chunking are **candidates**; the final
  values are whatever Phase 4 measurement supports.
- Whether the dense leg beats BM25-only at all on a real corpus is **unknown until
  measured** — Candidate 3 (ship nothing) remains a valid, honest outcome.
