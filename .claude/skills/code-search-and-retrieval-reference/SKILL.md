---
name: code-search-and-retrieval-reference
description: Use when you need the domain theory behind tensor-grep's search/retrieval behavior, not just the command syntax — ripgrep exit codes/PCRE2/binary-NUL-detection/-uuu/-- sentinels, ast-grep + tree-sitter routing, BM25 vs the flat no-IDF capsule scorer, PageRank vs in-degree centrality, the trigram index, PyO3 + the GIL, MCP argv surface, LSP 3.17 framing, and Model2Vec/potion-code (planned). Load before explaining WHY tg behaves a certain way, reasoning about the protocol/algorithm THEORY underneath a backend/router change (exit-code semantics, scoring math, wire framing), or writing docs that touch these subsystems — for the invariants a backend/router change must not break, use `tensor-grep-architecture-contract` instead (or in addition). Not a how-to-run or how-to-debug guide — see the sibling table below for those.
---

# Code Search & Retrieval Reference

The domain-theory pack a mid-level engineer (or a model working cold) usually lacks, narrowed to
**only the slice that governs tensor-grep's actual behavior**. Every claim below cites the tg file
that uses it — read that file before relying on the claim in a review or a fix, because code drifts
and this document does not update itself. Verified against the repo **as of 2026-07-02, v1.17.25**.

## When NOT to use this skill (use a sibling instead)

| You need... | Use instead |
|---|---|
| Command syntax / which flag to pass | `.claude/skills/tensor-grep/SKILL.md` + `REFERENCE.md` |
| The front-door/registration/fail-closed **contract** (the "must-hold" invariants) | `tensor-grep-architecture-contract` |
| How to change code safely (gates, one-merge-per-tick, TDD) | `tensor-grep-change-control` |
| A live bug you're actively debugging right now | `tensor-grep-debugging-playbook` |
| "Has this already been tried and lost?" | `tensor-grep-failure-archaeology` |
| Which benchmark script proves a speed/quality claim | `tensor-grep-benchmark-and-proof-toolkit` |
| Env var / flag default values | `tensor-grep-config-and-flags` |
| Build/toolchain setup | `tensor-grep-build-and-env` |
| Building/extending the approved BM25+dense+RRF semantic-search roadmap item | `tensor-grep-semantic-search-campaign` |
| Day-to-day `tg` invocation syntax (orient, search --rank, session, mcp, ...) | `tensor-grep-run-and-operate` |

This skill explains **why** a subsystem behaves the way it does; it does not tell you how to run,
fix, or ship it.

---

## 1. ripgrep internals — the cold-path baseline

`rg` is tg's raw-text-search comparator and, for most default `tg search` invocations, the actual
execution engine underneath (`RipgrepBackend`, `src/tensor_grep/backends/ripgrep_backend.py`; native
Rust passthrough in `rust_core/src/rg_passthrough.rs`). Getting rg's edge semantics wrong means
either a silent wrong-answer or a parity-test false green.

**Exit codes are not binary.** ripgrep uses three, and tg's contract is to *match rg's exit code
exactly*, including the non-obvious one:

| rg exit code | Meaning | tg's handling |
|---|---|---|
| `0` | at least one match found | pass through |
| `1` | search ran cleanly, zero matches | **not** an error — a `SearchResult` with 0 matches, never `BackendExecutionError` |
| `2` | a fatal error occurred (bad regex, unreadable path, etc.) | `RipgrepBackend.search()` raises `BackendExecutionError` whenever `result.returncode > 1` (`ripgrep_backend.py:88,164,199`) |

`tests/e2e/test_rg_parity_edges.py::test_rg_exit_code_edges_match` is parametrized exactly on this
boundary (`ids=["match", "no-match", "parse-error", "binary-skip"]`, line 149) and asserts
`tg.returncode == rg.returncode` for every case (`_assert_same_rg_behavior`, line 99) — a regex
`(` (unbalanced paren) must make tg exit `2`, same as rg.

**PCRE2 is a different engine, not a flag.** rg's default matcher is the Rust `regex` crate — linear
time, but **no lookaround, no backreferences**. `--pcre2`/`-P` switches to libpcre2, which supports
both but requires the *pattern* to be valid UTF-8 (it transcodes). tg detects PCRE2 support with a
real smoke test — build help output contains `--pcre2`/`PCRE2`, then actually run
`rg -P "a(?=b)" -V` and check `returncode == 0` (`ripgrep_backend.py:27-51`, `supports_pcre2()`).
This matters because of the **fail-closed contract**: `--pcre2` routed through an engine that cannot
honor PCRE2 semantics must raise, never silently execute as a plain-regex search that returns wrong
(or merely different) matches (`src/tensor_grep/backends/base.py:7-14`, `BackendExecutionError`;
AGENTS.md "Fail closed" bullet, line 220). A prior incident shipped exactly this bug — a broad
`except Exception: pass` around the Rust passthrough silently ran `--pcre2` through the non-PCRE2
Python-regex engine — fixed in v1.17.17/18 (see `tensor-grep-change-control`, Part 4 — Backend
fail-closed contract, lines 125-134).

**Binary detection is NUL-byte sniffing, and it changes exit codes.** rg's default binary heuristic
scans early file bytes for a `\0`; on a hit, the file is treated as binary and searched under the
binary-skip policy unless `-a`/`--text` is passed (`rg_contract.py` row `"text"`, `public_flags:
("-a", "--text")`). The parity fixture builds exactly this case —
`binary_path.write_bytes(b"needle\0binary tail\n")` (`test_rg_parity_edges.py:41-43`) — and the
`"binary-skip"` parametrization (line 147) asserts tg's exit code matches rg's on a NUL-containing
file, not just its stdout.

**`-u`/`-uu`/`-uuu` are not blind passthrough here.** Upstream, each additional `-u` widens scope
(`-u` = `--no-ignore`, `-uu` = `--no-ignore --hidden`, `-uuu` = `--no-ignore --hidden --binary`). tg's
Python front door specifically *detects* any `-u*` flag (or `--unrestricted`, or an explicit
no-ignore/hidden flag) as a request for unrestricted scanning and routes it through a broad-root
safety guard (`bootstrap.py:443-452`, `_search_args_request_unrestricted_generated_scan`). This
exists because of a real v1.13.1 incident: an unguarded broad-root unrestricted scan could recurse
into `node_modules`/`.git`/multi-project workspace roots. If you're adding a new flag that widens
scan scope, check whether it needs to join this guard's flag set — a missed case is a silent safety
regression, not just a slow query.

**`--` and `-e` matter for argv safety, not just POSIX correctness.** `--` ends option parsing so a
user- or LLM-supplied pattern beginning with `-` cannot be reinterpreted as a flag (CWE-88 / the
MCP-276 CVE class). tg's MCP tool handlers build subprocess argv with an explicit `--` sentinel
before positionals for exactly this reason: `command.extend(["--", pattern, path])`
(`src/tensor_grep/cli/mcp_server.py:767`, comment at line 765-766: "round-3 security: end options
before the user-controlled positionals so a pattern beginning with `-` cannot be parsed by the
native binary as a flag"). Note the narrower scope of this fix: it blocks *flag* injection via a
missing `--`, not shell injection (list-argv subprocess calls already block shell injection). See
`tensor-grep-change-control` before touching any subprocess argv builder.

**BOM handling is a real, previously-broken seam.** UTF-8 BOM bytes at the start of a file/scenario
JSON broke PowerShell-generated fixtures until scenario loading switched to `utf-8-sig`
(`docs/PAPER.md:823`). AST rewrite's batch-apply path explicitly preserves BOM/CRLF through
atomic writes (`docs/harness_api.md:632`, "batch apply reuses the same atomic-write, BOM/CRLF
preservation, binary-skip, and stale-file protections as single rewrites").

**Open / candidate — verify before relying on these, no in-repo citation exists yet.** Two upstream
ripgrep quirks worth probing against tg's passthrough the next time someone does a round-4-style edge
sweep: (a) `--multiline --pcre2 --json` reportedly emits a single match with two submatches rather
than two matches (upstream ripgrep issue tracker; exact issue number unverified against this repo —
re-check before citing a number); (b) `rg -c` (count mode) reportedly omits files whose content
contains a NUL byte from the count entirely, rather than counting them normally. Neither has a
tensor-grep test fixture as of this writing — treat as **candidate**, not fact, until one exists.

---

## 2. ast-grep + tree-sitter — structural search

Two backends implement structural (AST-aware, not text-regex) search and rewrite, and the router
picks between them per query:

| Backend | File | Availability gate |
|---|---|---|
| `AstBackend` (native) | `src/tensor_grep/backends/ast_backend.py:137` | `is_available()` requires **both** `torch_geometric` and `tree_sitter` importable **and** `torch.cuda.is_available()` — i.e. it is GPU-gated even though AST parsing itself is CPU work (`ast_backend.py:492-506`) |
| `AstGrepWrapperBackend` (sidecar) | `src/tensor_grep/backends/ast_wrapper_backend.py:79` | shells out to an installed `ast-grep`/`sg`/`sg.exe`/`ast-grep.exe` binary via `shutil.which` (lines 92-99) |

`tree-sitter` parses source into a concrete syntax tree; a **metavariable** like `$FUNC` or the
"capture the rest" form `$$$ARGS` is ast-grep/tg's pattern-matching primitive over that tree (e.g.
`def $FUNC($$$ARGS):` matches any Python function definition, binding `$FUNC` and `$$$ARGS`). The
router (`docs/routing_policy.md`, "AST commands" section) sends `tg run` to `AstBackend` by default
(`routing_reason = "ast-native"`), but **falls back to `AstGrepWrapperBackend` whenever
`AstBackend().is_available()` is false, or when a string-based metavariable query cannot be natively
parsed as an S-expression** — this fallback is deliberate, not a bug, and applies to AST search,
`--rewrite` planning, `--apply`, `--diff`, and batch rewrite flows alike
(`docs/routing_policy.md` lines 70-82).

**Practical corollary:** on a machine with no CUDA device (most CI runners, most laptops without an
NVIDIA GPU), every `tg run`/AST call silently uses the `ast-grep` CLI sidecar, not the native Rust
path — this is why an earlier "AST probe" bug in CI traced back to `AstBackend` being GPU-gated (see
`tensor-grep-docs-and-writing` Part 6, and project memory
`tensor-grep-readme-release-blocker-2026-06-25`, for the incident). Don't assume native AST speed numbers apply
unless you've confirmed `is_available()` is true on the box you're measuring.

Measured (not marketing) ratios, `benchmarks/run_ast_benchmarks.py` /
`run_ast_multilang_benchmarks.py` (`docs/benchmarks.md` "ast-grep vs tensor-grep AST mode"): single-
query `tg 0.116s` vs `sg 0.151s` (`0.770x`); multi-language ratios Python `0.722x`, JavaScript
`0.800x`, TypeScript `0.726x`, Rust `0.715x` — `tg` ahead of `sg` on all four when the native path is
actually reachable (i.e. on a CUDA box — see the AST availability gate above; off-GPU `tg run` uses the
ast-grep sidecar and is not faster). **Positioning caveat (do not drop):** ast-grep is the structural-search
BASELINE and `tg run` is "a useful validated AST slice, not a blanket ast-grep replacement" (`AGENTS.md`;
the docs-governance tests ban an "ast-grep replacement" framing). Never let these ratios feed a "tg beats
ast-grep" narrative. Re-run the script before citing a fresher number; these drift.

---

## 3. BM25 / IDF — two different scorers in this repo, only one has IDF

**Term rarity weighting in one sentence:** IDF (inverse document frequency) down-weights terms that
appear in most documents (useless for discriminating relevance) and up-weights terms that appear in
few (highly discriminating) — BM25 combines that with term-frequency saturation (diminishing returns
for repeating a term) and document-length normalization.

tg has **two independent ranking surfaces**, and only one of them actually implements IDF:

1. **`retrieval_bm25.py`** (`src/tensor_grep/core/retrieval_bm25.py`) — a real Okapi BM25 index
   (`k1=1.5`, `b=0.75`, standard defaults; `DEFAULT_K1`/`DEFAULT_B`, lines 18-19), full IDF term
   `math.log(1.0 + (n - freq + 0.5) / (freq + 0.5))` (line 44). This backs `tg search --rank`
   (alias `--bm25`; `main.py:5682-5683`) via `reranker.py::rerank_by_bm25`, which chunks matched
   files and re-sorts matches by the BM25 score of the chunk containing each match
   (`reranker.py:19-55`) — a stable sort, so ties keep original grep order.
2. **`_score_text_terms`** (`src/tensor_grep/cli/repo_map.py:4781`) — a **flat presence count, no
   IDF at all** — used by `tg orient`'s symbol ranking and the `tg agent` capsule's target
   selection. This is the ranker behind `_symbol_rank_key` (`repo_map.py:4802`) and the top-N
   candidate cap `ranked_symbols[: max(max_symbols, 8)]` (`repo_map.py:9408,9565`).

**Why this is a known weak point, not just a style difference:** because scorer #2 has no IDF, ties
are common, and the tie-break falls through to alphabetical file-path string
(`_symbol_rank_key`'s final field). A corpus change with **zero call-graph edge** to the query — an
unrelated file added elsewhere in the repo — can shift which candidate wins a tie and flip the
capsule's primary target, including flipping "ask before editing" (`ambiguity=tie_requires_confirmation,
ask_user=True`) to "confidently pick a target" (`ask_user=False`) with no code-level connection an
agent could see via `tg callers`. This actually happened (PR #302, receipt in project memory
`tensor-grep-idf-ranking-fragility-2026-06-29`) and is now covered by a **degrade-to-ask safety
floor**, not a ranking fix: if the post-tie primary target is still an unrequested "marker" helper,
`agent_capsule.py`'s `_primary_target_is_unrequested_marker_helper` (line 125) forces `ask_user=True`
rather than silently auto-picking it. The flat no-IDF scorer itself remains deferred debt — do not
assume it has been fixed just because the unsafe *consequence* was mitigated.

If you are reviewing a PR that touches ranking-feature tests: an edit that reddens a live-repo
ranking assertion is not automatically a "brittle test" to relax — inspect whether the actual
tie/ask/confidence behavior degraded before deciding. See `tensor-grep-idf-ranking-fragility-2026-06-29`
in project memory for the full incident writeup, and `tensor-grep-change-control` for the review gate.

---

## 4. PageRank / centrality — and why `tg orient` deliberately does NOT use it

tg has a real, hand-rolled **personalized PageRank** implementation over the reverse-import graph:
`_personalized_reverse_import_pagerank` (`src/tensor_grep/cli/repo_map.py:5271-5316`) — damping
factor `alpha=0.85` (the standard Google PageRank default), `12` power-iteration steps, a
personalization vector seeded uniformly over up to `_GRAPH_PAGERANK_SEED_FILE_LIMIT = 64` query-
relevant files (line 215), teleporting back to those seeds rather than to a uniform distribution.
This feeds descriptive-query file ranking (`graph-centrality` reason) inside `repo_map`/capsule/edit-
plan retrieval — pure Python, no `networkx` dependency (unlike Aider's repo-map, which uses
`networkx`'s PageRank over the full import graph — an external comparison, not yet documented in this
repo's `docs/tool_comparison.md`, which currently makes no Aider/networkx/PageRank claim).

**`tg orient`'s "central files" list is explicitly NOT PageRank — it's raw import in-degree**
(`src/tensor_grep/cli/orient_capsule.py:34-74`, `_central_files_from_map`). The code comment states
the reason directly: *"the reused reverse-import PageRank, seeded by all files, ranks IMPORTERS
above the imported — backwards for 'show me the core files' — so we rank by import in-degree
directly"* (lines 57-59). This is a genuinely useful piece of domain theory: **personalized PageRank
answers "what's relevant to this specific seed set", while in-degree answers "what does the whole
repo depend on"** — they are different questions, and orient wants the second one (foundational
files a newcomer should read first), not the first. If you're adding a new "show me the important
files" feature, pick deliberately between these two, don't default to whichever is already imported
in the module you're editing.

---

## 5. Trigram index — `--index` / warm-cache acceleration

A trigram index maps every 3-byte substring ("trigram") appearing in the corpus to the list of files
containing it (a postings list); a query first extracts the trigrams it must contain, intersects
their postings lists to get a small file candidate set, then only regex-scans those files instead of
every file in the corpus. tg's implementation: `TrigramIndex` struct, `rust_core/src/index.rs:137`,
3-byte keys (`FileTrigramHits = Vec<([u8; 3], u32)>`, line 21), binary bincode
serialize/deserialize (lines 251-394).

**Safety property:** when a pattern has no extractable required literal (e.g. `.*` or an alternation
with no common substring), the index cannot safely prefilter — the code falls back to a full scan
"so the index never introduces false negatives" (`index.rs:972`). A trigram index is a *prefilter*,
never a source of truth by itself; getting this fallback wrong would mean silently missing real
matches, which is strictly worse than being slow.

**Compatibility gate for warm-index auto-routing** (`docs/routing_policy.md` line 52): the router
only auto-routes to `TrigramIndex` on a warm, non-stale cache when the query is index-compatible —
`pattern >= 3 bytes`, and none of `-v`, `-C`, `--max-count`, `-w`, `-g` are present. Below 3 bytes
there's no trigram to extract; the other flags change result *shape* in ways the index path doesn't
(yet) replicate. `--index` (explicit) is priority 1 in the router's decision tree regardless of
staleness (`routing_policy.md` lines 36, 48).

---

## 6. PyO3 + the GIL

The **GIL** (Global Interpreter Lock) serializes Python bytecode execution to one thread at a time.
When Rust code called via PyO3 (the Rust↔Python FFI binding library tg uses for its native
extension) does CPU-bound work with no Python API calls in the loop, holding the GIL for that whole
stretch blocks *every other Python thread* pointlessly — the fix is to explicitly release it around
the pure-Rust portion.

tg does this correctly at the mmap/newline-scan boundary: `py.detach(|| { ... })` wraps the call to
`create_arrow_string_array_from_mmap` in both `read_mmap_to_arrow` and its chunked sibling
(`rust_core/src/lib.rs:32,55`) — comment: *"Release the GIL while we map the file and scan for
newlines"* (line 31). `py.detach` is PyO3's current API name for what older PyO3 code (and most
tutorials/training data) calls `py.allow_threads` — same mechanism, releases the GIL for the closure
and reacquires it after.

**The module pin is a live scar, not a style choice.** `#[pymodule(gil_used = true)]`
(`rust_core/src/lib.rs:342`) intentionally opts back into the classic (non-free-threaded) GIL model.
The comment explains why: a prior attempt to ship `gil_used = false` (free-threaded Python, #266)
broke Linux `agent-readiness` in CI, and because that PR's CI run was cancelled by a force-push, it
merged without ever going green — re-enabling free-threading requires a **full green CI run on
Linux extension load** first, not just a local pass (`lib.rs:339-341`).

**Settled battle, don't re-propose:** FFI (moving directory-walk/file-scanning work into the PyO3
extension "for speed") was tried and reverted — the FFI call overhead measured higher than native
CPython directory scanning for that workload. See `tensor-grep-failure-archaeology` before proposing
a PyO3 rewrite of a hot path; benchmark first (`tensor-grep-benchmark-and-proof-toolkit`).

---

## 7. MCP — the protocol, and its argv-injection surface

**MCP (Model Context Protocol)** is the tool-calling protocol that lets an LLM agent invoke
structured "tools" (typed functions with a JSON schema) exposed by a server process. tg's MCP server
is built on the official Python `mcp` SDK's `FastMCP` class:
`from mcp.server.fastmcp import FastMCP` / `mcp = FastMCP("tensor-grep")`
(`src/tensor_grep/cli/mcp_server.py:18,64`), a ~3700-line module (`mcp_server.py`).

**The domain risk that matters here:** an MCP tool handler takes LLM-supplied parameters (a search
`pattern`, a file `path`, a rewrite `replacement`) and forwards them into a subprocess argv to invoke
the native `tg` binary. If a parameter value happens to start with `-`, and the argv builder doesn't
end option-parsing first, the "data" is reinterpreted as a flag — this is CWE-88 (argument
injection), the same class as the MCP-276 CVE. **List-argv subprocess calls (no `shell=True`) already
block shell injection; they do NOT block flag injection** — that needs an explicit `--` sentinel
before user-controlled positionals. tg's rewrite/index-search command builders do this:
`command.extend(["--", pattern, path])` (`mcp_server.py:767`) and the parallel index-search builder
(`mcp_server.py:772-782`). If you add a new MCP tool that shells out with user-controlled string
values, this is the pattern to copy — and the gap to check for if you don't see it.

This is a **security topic with an open item**, not fully closed: per project memory, a round-4 sweep
flagged that `rust_core/src/rg_passthrough.rs` forwards **paths** with no `--` sentinel of its own —
a directory literally named `-l` could silently flip rg into files-with-matches mode at the native
layer. Don't assume every argv boundary in the codebase has this fix; check the specific one you're
touching. For the fix-review process, use `tensor-grep-change-control`; this skill is domain theory
only.

---

## 8. LSP — Language Server Protocol, experimental provider mode

**LSP** is a JSON-RPC-over-stdio protocol (Microsoft-originated, editor-agnostic) for
definitions/references/symbols; tg's external-provider client speaks it to talk to a real language
server (e.g. Pyright, rust-analyzer) instead of relying on tg's own native/AST navigation. Two
concrete wire-level facts worth knowing before touching this code:

- **Framing** is `Content-Length: N\r\n\r\n` followed by exactly `N` bytes of JSON
  (`src/tensor_grep/cli/lsp_external_provider.py:130`) — this is LSP's transport framing, not a tg
  invention. tg **caps** the declared `Content-Length` at `_MAX_LSP_MESSAGE_BYTES = 64 * 1024 * 1024`
  (line 90) specifically so "a malicious or buggy external LSP provider cannot declare a huge
  Content-Length and force an unbounded read/allocation" (line 88-89) — a DoS hardening measure on
  the *client* side of an LSP session, since tg here is the client trusting an external server
  process.
- **Lifecycle**: `initialize` → `initialized` handshake before any real request
  (`lsp_external_provider.py:437,465-466`), then per-document `textDocument/didOpen` /
  `didChange` / `didSave` / `didClose` notifications and `textDocument/documentSymbol` requests
  (lines 657-1150). Session docs anchor this to the official **LSP 3.17** lifecycle spec
  (`docs/SESSION_HANDOFF.md:106`, "official Language Server Protocol 3.17 lifecycle docs for
  initialize/initialized sequencing").

LSP is an **explicit provider mode** (`--provider lsp` / `--provider hybrid`), not the default —
native/AST lookup is the default path, and provider modes are reserved for cases where native lookup
is ambiguous or incomplete (see `.claude/skills/tensor-grep/SKILL.md` "Provider Modes"). Treat it as
experimental infrastructure: it exists and is tested, but is not promoted as tg's default navigation
path.

---

## 9. Model2Vec / potion-code — the semantic-search reference architecture (PLANNED, not shipped)

**Model2Vec** is a technique for distilling a full sentence-transformer into a small, fast,
**static** (non-contextual — one fixed vector per token/word, no attention pass at inference time)
embedding model that runs on CPU with no GPU and no API key. `potion-code-16M` is MinishLab's
code-specialized Model2Vec model, referenced in AGENTS.md as the target for tg's semantic-search
build: *"Reference architecture: MinishLab `Semble` (tree-sitter chunking + `potion-code-16M`
Model2Vec + BM25 + RRF, CPU-only, MIT)"* (`AGENTS.md:230`). **RRF** (Reciprocal Rank Fusion, `k=60` is
the standard constant from the original paper) is the fusion method for combining two independently
ranked lists (here: BM25 lexical ranking + dense embedding ranking) into one merged ranking without
needing comparable raw scores — each item's fused score is `Σ weight_i / (k + rank_i)` across the
lists it appears in.

**Current shipped status (verify before citing further — this is the fastest-moving area in the
repo):** only the **lexical (BM25) leg** is implemented and shipped — `retrieval_bm25.py` /
`reranker.py`, section 3 above, backing `tg search --rank`. The **dense-embedding + RRF leg is a
plan, not code** — `docs/superpowers/plans/2026-06-26-hybrid-semantic-search.md` describes a v2
`EmbedBackend` (`src/tensor_grep/backends/embed_backend.py`, does not yet exist as of this writing)
gated behind proof that dense+RRF beats BM25-alone on a built-in eval, and an optional
`[semantic]` extra (`model2vec>=0.3`, `numpy>=1.26.0`). **Naming discrepancy to be aware of:** the
plan's draft code snippet loads `StaticModel.from_pretrained("minishlab/potion-base-8M")` — a
smaller, general-purpose model, *not* `potion-code-16M` as named in the AGENTS.md roadmap directive.
Confirm the actual model id in `pyproject.toml`'s `[semantic]` extra / `embed_backend.py` once this
ships (task is tracked as CEO-approved but pending); do not assume either name is final.

This whole feature is **EXPERIMENTAL / default-off** per project discipline — do not market it, and
do not assume `--semantic`/`--hybrid` flags exist until you've confirmed `embed_backend.py` and
`retrieval_hybrid.py` are present in `src/tensor_grep/`. If you are actually building or extending
this campaign (not just reading the theory), switch to `tensor-grep-semantic-search-campaign` — it
has the phase-gated build plan, promotion gates, and fenced-off wrong paths.

---

## Quick-reference table

| Concept | tg file | One-line gotcha |
|---|---|---|
| rg exit code 2 | `ripgrep_backend.py:88,164,199` | `> 1` → `BackendExecutionError`, not a clean empty result |
| PCRE2 detection | `ripgrep_backend.py:27-51` | real smoke test (`-P "a(?=b)" -V`), not a version-string guess |
| Binary NUL detection | `test_rg_parity_edges.py:41-43,147` | exit code must match rg's, not just stdout |
| `-u`/`-uu`/`-uuu` | `bootstrap.py:443-452` | intercepted by the broad-root safety guard, not blind passthrough |
| `--` argv sentinel | `mcp_server.py:765-767,778-782` | blocks flag injection; list-argv alone only blocks shell injection |
| AST native vs sidecar | `ast_backend.py:492-506`, `routing_policy.md:70-82` | native path requires a CUDA GPU to even report available |
| BM25 (real IDF) | `retrieval_bm25.py`, `reranker.py` | backs `tg search --rank`/`--bm25` only |
| Flat scorer (no IDF) | `repo_map.py:4781` (`_score_text_terms`) | backs `tg orient`/`tg agent` symbol ranking — known weak point |
| Personalized PageRank | `repo_map.py:5271-5316` | alpha=0.85, seeded, answers "relevant to this query" |
| In-degree centrality | `orient_capsule.py:34-74` | `tg orient`'s deliberate choice over PageRank — answers "what's foundational" |
| Trigram index | `rust_core/src/index.rs:137,972` | falls back to full scan when no literal is extractable (never drops matches) |
| GIL release | `rust_core/src/lib.rs:32,55` | `py.detach` (formerly `allow_threads`) around the mmap/scan closure |
| `gil_used` pin | `rust_core/src/lib.rs:342` | pinned `true`; free-threading needs a full green Linux CI run to re-attempt |
| LSP framing | `lsp_external_provider.py:90,130` | `Content-Length` capped at 64MB against a malicious/buggy server |
| Model2Vec/potion | `AGENTS.md:230`, hybrid-search plan doc | v1 (BM25) shipped; v2 (dense+RRF) is plan-only, not code |

## Provenance and maintenance

Facts here were verified by reading the cited files on **2026-07-02, tensor-grep v1.17.25**. Code
drifts; re-verify before treating a citation as current, especially line numbers.

Re-verification commands:

```bash
# Confirm the version this doc was written against
git -C C:/dev/projects/tensor-grep log -1 --format=%H -- pyproject.toml
grep -n '^version' C:/dev/projects/tensor-grep/pyproject.toml

# Re-check the cited line numbers haven't drifted (grep, don't trust the number blindly)
grep -n "def supports_pcre2" C:/dev/projects/tensor-grep/src/tensor_grep/backends/ripgrep_backend.py
grep -n "_score_text_terms\|_symbol_rank_key" C:/dev/projects/tensor-grep/src/tensor_grep/cli/repo_map.py
grep -n "_personalized_reverse_import_pagerank\|_GRAPH_PAGERANK_SEED_FILE_LIMIT" C:/dev/projects/tensor-grep/src/tensor_grep/cli/repo_map.py
grep -n "_central_files_from_map" C:/dev/projects/tensor-grep/src/tensor_grep/cli/orient_capsule.py
grep -n "py.detach\|gil_used" C:/dev/projects/tensor-grep/rust_core/src/lib.rs
grep -n "Content-Length\|_MAX_LSP_MESSAGE_BYTES" C:/dev/projects/tensor-grep/src/tensor_grep/cli/lsp_external_provider.py

# Confirm whether the semantic-search v2 (dense+RRF) leg has shipped yet
test -f C:/dev/projects/tensor-grep/src/tensor_grep/backends/embed_backend.py && echo "SHIPPED — rewrite section 9" || echo "still plan-only"

# Re-run the AST comparison benchmark before citing its numbers
cd C:/dev/projects/tensor-grep && uv run python benchmarks/run_ast_benchmarks.py
```

Open uncertainties (do not cite as fact without independent verification):

- The exact upstream ripgrep issue numbers for the `--multiline --pcre2 --json` double-submatch and
  `-c` NUL-omission edge cases (section 1) — no in-repo test fixture or citation exists for either as
  of this writing.
- The final embedding model id for the semantic-search v2 leg (`potion-code-16M` per AGENTS.md vs
  `potion-base-8M` per the plan doc's draft snippet) — unresolved until `embed_backend.py` ships.
- Whether the round-4 `rg_passthrough.rs` path-sentinel gap (section 7) has been fixed by the time
  you read this — it was open per project memory as of 2026-07-01.
