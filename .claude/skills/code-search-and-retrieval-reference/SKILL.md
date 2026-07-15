---
name: code-search-and-retrieval-reference
description: Use when you need the domain theory behind tensor-grep's search/retrieval behavior, not just the command syntax — ripgrep exit codes (including the exit-2-but-kept partial-results contract)/PCRE2/binary-NUL-detection/-uuu/-- sentinels, ast-grep + tree-sitter routing, BM25 vs the flat no-IDF capsule scorer, PageRank vs in-degree centrality, the trigram index, PyO3 + the GIL, MCP argv surface, LSP 3.17 framing, and Model2Vec/potion-code (SHIPPED as `tg search --semantic`). Load before explaining WHY tg behaves a certain way, reasoning about the protocol/algorithm THEORY underneath a backend/router change (exit-code semantics, scoring math, wire framing), or writing docs that touch these subsystems — for the invariants a backend/router change must not break, use `tensor-grep-architecture-contract` instead (or in addition). Not a how-to-run or how-to-debug guide — see the sibling table below for those.
---

# Code Search & Retrieval Reference

The domain-theory pack a mid-level engineer (or a model working cold) usually lacks, narrowed to
**only the slice that governs tensor-grep's actual behavior**. Every claim below cites the tg file
that uses it — read that file before relying on the claim in a review or a fix, because code drifts
and this document does not update itself. Verified against the repo **as of 2026-07-08, v1.49.3**.

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
exactly*, including the non-obvious one — and exit `2` itself is not simply pass-or-raise, because
of the partial-results contract (see `tensor-grep-architecture-contract` for the full
`SearchResult.result_incomplete` story, added #341):

| rg exit code | Meaning | tg's handling |
|---|---|---|
| `0` | at least one match found | pass through |
| `1` | search ran cleanly, zero matches | **not** an error — a `SearchResult` with 0 matches, never raised as a failure |
| `2`, matches parsed (soft per-file error, e.g. one unreadable path among many) | rg still emitted matches for the readable files | tg **keeps** those matches, sets `result_incomplete=True` + an `incomplete_reason`, and does **not** raise — `partial = result.returncode == 2 and total_matches > 0` (`ripgrep_backend.py:123`, mirrored in `_search_files_with_matches`/`_search_counts`) |
| `2`, nothing parsed, or any `> 2` | a genuine fatal failure (bad regex, unreadable path with no other matches, etc.) | `RipgrepBackend.search()` raises `BackendExecutionError` whenever `result.returncode > 1 and not partial` (`ripgrep_backend.py:124-128`; the two sibling methods raise the same way at `:297` and `:413`; the rg-missing guard at `:505` too). **RESOLVED #79/#10/#14 (commit `a7c9431`)** -- every `RipgrepBackend` fatal path used to raise a bare `RuntimeError`, deliberately not `BackendExecutionError`, so it would not get caught by `cli/main.py`'s `except BackendExecutionError:` per-file CPU-fallback retry (`:7481`); the fix flipped all of them to `BackendExecutionError` so that retry now catches rg failures the same way it does every other backend, per the Backend Fail-Closed Contract's normal convention. |

**Do not describe exit-2 as unconditionally "raise `BackendExecutionError`"** — that was true before
#341 (round-4 slice 3) and is no longer true; a partial exit-2 with real matches is now a **kept**
result, not a failure. `tests/e2e/test_rg_parity_edges.py::test_rg_exit_code_edges_match` is
parametrized exactly on the fatal-failure boundary (`ids=["match", "no-match", "parse-error",
"binary-skip"]`, line 149) and asserts `tg.returncode == rg.returncode` for every case
(`_assert_same_rg_behavior`, line 81) — a regex `(` (unbalanced paren, nothing parsed) must still
make tg exit `2`, same as rg. Partial-parse-with-matches is covered separately by
`tests/unit/test_rg_exit2_partial.py`.

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
safety guard (`bootstrap.py:459-468`, `_search_args_request_unrestricted_generated_scan`). This
exists because of a real v1.13.1 incident: an unguarded broad-root unrestricted scan could recurse
into `node_modules`/`.git`/multi-project workspace roots. If you're adding a new flag that widens
scan scope, check whether it needs to join this guard's flag set — a missed case is a silent safety
regression, not just a slow query.

**`--` and `-e` matter for argv safety, not just POSIX correctness.** `--` ends option parsing so a
user- or LLM-supplied pattern beginning with `-` cannot be reinterpreted as a flag (CWE-88 / the
MCP-276 CVE class). tg's MCP tool handlers build subprocess argv with an explicit `--` sentinel
before positionals for exactly this reason: `command.extend(["--", pattern, path])`
(`src/tensor_grep/cli/mcp_server.py:837`, comment two lines above: "round-3 security: end options
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
   (alias `--bm25`; `main.py:6036-6037`) via `reranker.py::rerank_by_bm25`, which chunks matched
   files and re-sorts matches by the BM25 score of the chunk containing each match
   (`reranker.py:62-96`) — a stable sort, so ties keep original grep order.
2. **`_score_text_terms`** (`src/tensor_grep/cli/repo_map.py:5819`) — a **flat presence count, no
   IDF at all** — used by `tg orient`'s symbol ranking and the `tg agent` capsule's target
   selection. This is the ranker behind `_symbol_rank_key` (`repo_map.py:5840`) and the top-N
   candidate cap `ranked_symbols[: max(max_symbols, 8)]` (`repo_map.py:10797,10964`).

**Why this is a known weak point, not just a style difference:** because scorer #2 has no IDF, ties
are common, and the tie-break falls through to alphabetical file-path string
(`_symbol_rank_key`'s final field). A corpus change with **zero call-graph edge** to the query — an
unrelated file added elsewhere in the repo — can shift which candidate wins a tie and flip the
capsule's primary target, including flipping "ask before editing" (`ambiguity=tie_requires_confirmation,
ask_user=True`) to "confidently pick a target" (`ask_user=False`) with no code-level connection an
agent could see via `tg callers`. This actually happened (PR #302, receipt in project memory
`tensor-grep-idf-ranking-fragility-2026-06-29`) and is now covered by a **degrade-to-ask safety
floor**, not a ranking fix: if the post-tie primary target is still an unrequested "marker" helper,
`agent_capsule.py`'s `_primary_target_is_unrequested_marker_helper` (line 197) forces `ask_user=True`
rather than silently auto-picking it. The flat no-IDF scorer itself remains deferred debt — do not
assume it has been fixed just because the unsafe *consequence* was mitigated.

If you are reviewing a PR that touches ranking-feature tests: an edit that reddens a live-repo
ranking assertion is not automatically a "brittle test" to relax — inspect whether the actual
tie/ask/confidence behavior degraded before deciding. See `tensor-grep-idf-ranking-fragility-2026-06-29`
in project memory for the full incident writeup, and `tensor-grep-change-control` for the review gate.

---

## 4. PageRank / centrality — and why `tg orient` deliberately does NOT use it

tg has a real, hand-rolled **personalized PageRank** implementation over the reverse-import graph:
`_personalized_reverse_import_pagerank` (`src/tensor_grep/cli/repo_map.py:6455+`) — damping
factor `alpha=0.85` (the standard Google PageRank default), `12` power-iteration steps, a
personalization vector seeded uniformly over up to `_GRAPH_PAGERANK_SEED_FILE_LIMIT = 64` query-
relevant files (`repo_map.py:309`), teleporting back to those seeds rather than to a uniform distribution.
This feeds descriptive-query file ranking (`graph-centrality` reason) inside `repo_map`/capsule/edit-
plan retrieval — pure Python, no `networkx` dependency (unlike Aider's repo-map, which uses
`networkx`'s PageRank over the full import graph — an external comparison, not yet documented in this
repo's `docs/tool_comparison.md`, which currently makes no Aider/networkx/PageRank claim).

**`tg orient`'s "central files" list is explicitly NOT PageRank — it's a composite of import
in-degree plus symbol density, both capped** (`src/tensor_grep/cli/orient_capsule.py:69-70`,
`_central_files_from_map`; docstring: *"Rank source files by import in-degree (foundational =
imported-by-many); top-N with symbols"*). The rationale for avoiding raw reverse-import PageRank
here — that a personalized PageRank seeded by all files ranks IMPORTERS above the imported, which
is backwards for "show me the core files" — is no longer stated as a verbatim code comment at this
location; do not quote it as a literal in-repo string without re-finding it. The underlying design
choice is still real and still worth citing conceptually: **personalized PageRank answers "what's
relevant to this specific seed set", while in-degree answers "what does the whole repo depend on"**
— different questions, and `orient` wants the second (foundational files a newcomer should read
first). The current implementation additionally caps fan-in (`_CENTRAL_FAN_IN_CAP = 12`) and symbol
density (`_CENTRAL_SYMBOL_DENSITY_CAP = 25`, both `orient_capsule.py:44-45`) so a single
widely-imported data-sink file or one giant file can't dominate the ranking on its own — a
refinement on top of plain in-degree, not a switch to PageRank. If you're adding a new "show me the
important files" feature, pick deliberately between personalized-PageRank and in-degree-based
centrality; don't default to whichever is already imported in the module you're editing.

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
(`src/tensor_grep/cli/mcp_server.py:19,80`), a ~4500-line module (`mcp_server.py`).

**The domain risk that matters here:** an MCP tool handler takes LLM-supplied parameters (a search
`pattern`, a file `path`, a rewrite `replacement`) and forwards them into a subprocess argv to invoke
the native `tg` binary. If a parameter value happens to start with `-`, and the argv builder doesn't
end option-parsing first, the "data" is reinterpreted as a flag — this is CWE-88 (argument
injection), the same class as the MCP-276 CVE. **List-argv subprocess calls (no `shell=True`) already
block shell injection; they do NOT block flag injection** — that needs an explicit `--` sentinel
before user-controlled positionals. tg's rewrite/index-search command builders do this:
`command.extend(["--", pattern, path])` (`mcp_server.py:837`) and the parallel index-search builder,
`_build_index_search_command` (`mcp_server.py:840-850`). If you add a new MCP tool that shells out
with user-controlled string values, this is the pattern to copy — and the gap to check for if you
don't see it.

**The native rg-passthrough path-sentinel gap is now FIXED, not open.** A prior round-4 sweep flagged
that `rust_core/src/rg_passthrough.rs` forwarded user **paths** with no `--` sentinel of its own — a
directory literally named `-l` could silently flip rg into files-with-matches mode at the native
layer (CWE-88). This is closed: `ripgrep_operand_args` (`rg_passthrough.rs:397-422`) builds patterns
flag-safely via `-e` and then, whenever there is at least one user path, inserts a `--` sentinel
before the path loop (`:415-419`; the sentinel is intentionally omitted only when there are no paths
at all, so as not to change the piped-stdin invocation shape). Three unit tests pin this:
`operand_args_insert_end_of_options_sentinel_before_paths`,
`operand_args_no_sentinel_when_no_paths`, `operand_args_files_mode_omits_patterns_but_keeps_sentinel`
(`rg_passthrough.rs:609-648`). Don't cite this as an open gap anymore; do still check any *new*
argv builder you touch for the same pattern — the class of bug recurs.

---

## 8. LSP — Language Server Protocol, experimental provider mode

**LSP** is a JSON-RPC-over-stdio protocol (Microsoft-originated, editor-agnostic) for
definitions/references/symbols; tg's external-provider client speaks it to talk to a real language
server (e.g. Pyright, rust-analyzer) instead of relying on tg's own native/AST navigation. Two
concrete wire-level facts worth knowing before touching this code:

- **Framing** is `Content-Length: N\r\n\r\n` followed by exactly `N` bytes of JSON
  (`src/tensor_grep/cli/lsp_external_provider.py:131`) — this is LSP's transport framing, not a tg
  invention. tg **caps** the declared `Content-Length` at `_MAX_LSP_MESSAGE_BYTES = 64 * 1024 * 1024`
  (line 91) specifically so "a malicious or buggy external LSP provider cannot declare a huge
  Content-Length and force an unbounded read/allocation" (line 90) — a DoS hardening measure on
  the *client* side of an LSP session, since tg here is the client trusting an external server
  process.
- **Lifecycle**: `initialize` → `initialized` handshake before any real request
  (`lsp_external_provider.py:480,508`), then per-document `textDocument/didOpen` /
  `didChange` / `didSave` / `didClose` notifications (around lines 700-757) and
  `textDocument/documentSymbol` requests (line 1289). Session docs anchor this to the official
  **LSP 3.17** lifecycle spec (`docs/SESSION_HANDOFF.md:110`, "official Language Server Protocol
  3.17 lifecycle docs for initialize/initialized sequencing").

LSP is an **explicit provider mode** (`--provider lsp` / `--provider hybrid`), not the default —
native/AST lookup is the default path, and provider modes are reserved for cases where native lookup
is ambiguous or incomplete (see `.claude/skills/tensor-grep/SKILL.md` "Provider Modes"). Treat it as
experimental infrastructure: it exists and is tested, but is not promoted as tg's default navigation
path.

---

## 9. Model2Vec / potion-code — the semantic-search reference architecture (SHIPPED)

**Model2Vec** is a technique for distilling a full sentence-transformer into a small, fast,
**static** (non-contextual — one fixed vector per token/word, no attention pass at inference time)
embedding model that runs on CPU with no GPU and no API key. `potion-code-16M` is MinishLab's
code-specialized Model2Vec model, the reference architecture AGENTS.md named as the target for tg's
semantic-search build (roadmap #27): tree-sitter chunking + `potion-code-16M` Model2Vec + BM25 +
RRF, CPU-only, MIT. **RRF** (Reciprocal Rank Fusion, `k=60` is the standard constant from the
original paper) is the fusion method for combining two independently ranked lists (here: BM25
lexical ranking + dense embedding ranking) into one merged ranking without needing comparable raw
scores — each item's fused score is `Σ weight_i / (k + rank_i)` across the lists it appears in.

**Current shipped status: BOTH legs are real code, not a plan.** This section previously described
the dense+RRF leg as plan-only (`docs/superpowers/plans/2026-06-26-hybrid-semantic-search.md`,
`embed_backend.py`/`retrieval_hybrid.py`) — those exact module names never shipped; the feature
landed under different, real module names in `src/tensor_grep/core/`:

| Module | Role |
|---|---|
| `retrieval_bm25.py` | the real Okapi BM25 index (section 3 above) — the lexical leg |
| `retrieval_dense.py` | the dense-embedding leg: `model2vec` (pure-numpy, no torch/GPU) paired with `minishlab/potion-code-16M` (256-dim, code-distilled, ~64MB F32), fetched once to a local dir then fully offline; raises the recoverable `DenseUnavailableError` when the `[semantic]` extra isn't installed or the model hasn't been fetched, which callers **must** catch and degrade visibly to BM25-only (Backend Fail-Closed Contract) |
| `retrieval_fusion.py` | RRF over the two legs' independent chunk rankings — fuses on rank only, never compares raw BM25 vs cosine scores directly |
| `retrieval_chunker.py` | chunking: `chunk_file` (default, always-on line-window chunking, MAX_CHUNKS-guarded) and `chunk_file_structural` (opt-in cAST — arxiv 2506.15655 — tree-sitter split-then-merge structural chunking, wired in transparently as an alternative to `chunk_file`) |
| `retrieval_lexical.py` | tokenization helper (camelCase/snake_case boundary splitting) shared by the lexical scoring path |
| `retrieval_scoring.py` | retrieval-quality eval metrics (e.g. `recall_at_k`) used to gate promotion, not shipped in the search hot path |

**The naming discrepancy this section used to flag is resolved**: the shipped model IS
`potion-code-16M` (`retrieval_dense.py:5,34,73`; `pyproject.toml`'s `[semantic]` extra:
`model2vec>=0.5`, `numpy>=1.26`), matching the AGENTS.md roadmap directive — the smaller
`potion-base-8M` from the old plan doc's draft snippet was never shipped.

**The user-facing surface is a flag, not a new command:** `tg search PATTERN PATH --semantic`
(`main.py:6045` registers `--semantic`; `config.semantic_rank` drives `_apply_semantic_rerank` at
`main.py:6800`). There is no separate `tg index` command for the dense leg. A genuine dense-backend
fault (e.g. a corrupt model directory) surfaces as a `BackendExecutionError` and exits the CLI
cleanly with a `tg:`-prefixed message (never a raw traceback) — `main.py:6800-6812`.

This feature remains **EXPERIMENTAL / default-off-by-flag** per project discipline (it is shipped
code, not a marketed default) — `--semantic` must be explicitly passed. If you are actually building
or extending this campaign (not just reading the theory), switch to
`tensor-grep-semantic-search-campaign` — it has the phase-gated build plan, promotion gates, and
fenced-off wrong paths; re-verify its own status note against this section before trusting either in
isolation, since this is still the fastest-moving area in the repo.

---

## Quick-reference table

| Concept | tg file | One-line gotcha |
|---|---|---|
| rg exit code 2 (kept, not raised) | `ripgrep_backend.py:123-128,297,413` | matches parsed -> `result_incomplete=True`, kept; nothing parsed / `>2` -> `BackendExecutionError` (RESOLVED #79, `a7c9431`) |
| PCRE2 detection | `ripgrep_backend.py:53` (`supports_pcre2`) | real smoke test (`-P "a(?=b)" -V`), not a version-string guess |
| Binary NUL detection | `test_rg_parity_edges.py:41-43,147` | exit code must match rg's, not just stdout |
| `-u`/`-uu`/`-uuu` | `bootstrap.py:459` (`_search_args_request_unrestricted_generated_scan`) | intercepted by the broad-root safety guard, not blind passthrough |
| `--` argv sentinel (MCP) | `mcp_server.py:837`, `:840-850` | blocks flag injection; list-argv alone only blocks shell injection |
| `--` argv sentinel (native rg passthrough) | `rg_passthrough.rs:397-422` | FIXED — `ripgrep_operand_args` inserts `--` before paths; 3 tests at `:609-648` |
| AST native vs sidecar | `ast_backend.py:508` (`is_available`), `routing_policy.md:70-82` | native path requires a CUDA GPU to even report available |
| BM25 (real IDF) | `retrieval_bm25.py`, `reranker.py` | backs `tg search --rank`/`--bm25` only |
| Flat scorer (no IDF) | `repo_map.py:5819` (`_score_text_terms`) | backs `tg orient`/`tg agent` symbol ranking — known weak point |
| Personalized PageRank | `repo_map.py:6455` (`_personalized_reverse_import_pagerank`) | alpha=0.85, seeded, answers "relevant to this query" |
| Central-files centrality | `orient_capsule.py:69-70` (`_central_files_from_map`) | composite in-degree + fan-in/symbol-density caps — `tg orient`'s deliberate choice over PageRank, answers "what's foundational" |
| Trigram index | `rust_core/src/index.rs:137,972` | falls back to full scan when no literal is extractable (never drops matches) |
| GIL release | `rust_core/src/lib.rs:32,55` | `py.detach` (formerly `allow_threads`) around the mmap/scan closure |
| `gil_used` pin | `rust_core/src/lib.rs:342` | pinned `true`; free-threading needs a full green Linux CI run to re-attempt |
| LSP framing | `lsp_external_provider.py:91,131` | `Content-Length` capped at 64MB against a malicious/buggy server |
| Model2Vec/potion (SHIPPED) | `retrieval_dense.py`, `retrieval_fusion.py` | `tg search --semantic`; `potion-code-16M`; default-off-by-flag, not marketed default |
| ReDoS gate (`-w`/`-x`/`-C`/`--ltl`/UTF-8-fallback/native-failure/`--pcre2`) | `cpu_backend.py:355` (`config.ltl`), `:377-384` (word/context routing), `:438` (UTF-8-fallback gate) + `:485` (`--pcre2`) + `:512` (native-failure) all calling `_fallback_pattern_is_provably_linear` (`:280`) | CLOSED (audit #6/#16/#111) — every path that could reach Python's backtracking `re` either routes through the linear-time Rust engine first or fails closed with `BackendExecutionError` unless the pattern is `fixed_strings` (the only provably-linear shape); see §1a below |

### 1a. ReDoS-gate bypass on `-w`/`-x`/`-C`/`--ltl`/UTF-8-fallback/native-failure — CLOSED (audit #6/#16/#111)

`cpu_backend.py`'s ReDoS protection (comment above `def search`: *"Instead of using Python's
standard `re` module ... we route complex pure-python CPU requests to the native Rust `regex`
crate"*) now covers every path that can reach this backend:

- **`-w`/`-x`/`-C`/`-A`/`-B` (word/line/context flags)**: `needs_word_or_context_rust_routing`
  (`cpu_backend.py:395-402`) routes these to `_search_word_line_context_via_rust` (`:797`), which
  resolves the match-SET via the linear-time Rust engine (`_rust_match_set`, `:760`) and
  assembles context windows in pure Python — no backtracking regex ever runs on this path. On any
  Rust failure it raises `BackendExecutionError` (fail closed) instead of falling back to Python
  `re`.
- **`--ltl`**: `_search_ltl` (`:928`) resolves both LTL sub-expressions via the same
  `_rust_match_set` helper and fails closed identically.
- **`--pcre2`**: the generic Rust-exception handler's `pcre2` branch (`:497`) fails closed
  unconditionally (`BackendExecutionError`) regardless of *why* Rust could not service the
  request — CPUBackend has no real PCRE2 engine, only Python `re` as a backtracking
  approximation, so it never silently swaps to that engine.
- **UTF-8-fallback + native-runtime-failure (audit #111, closed 2026-07-10)**: two residual paths
  of the "simple pattern" route used to fall through to unbounded Python `re` — (1) the empty-Rust-
  result-on-a-non-UTF-8-file retry (`_RustUtf8DecodeMismatch`, handled at `:438`), on the premise
  "Rust already ran the pattern in O(n), so it's ReDoS-safe"; and (2) the generic `except Exception`
  branch on a non-syntax Rust runtime fault (native panic / IO / version skew), which fell open
  "for robustness" (`:512`). Both premises are the SAME one already refuted for `--pcre2` — a
  pattern Rust runs in guaranteed linear time can still catastrophically backtrack under Python's
  backtracking engine; Rust accepting/running a pattern proves nothing about Python-`re` safety
  (Rust has no catastrophic-backtracking failure mode for ANY pattern it accepts). Reproduced
  empirically: `(a+)+$` on a non-UTF-8 file pegged a CPU core (158+ CPU-seconds, forced kill).
  **The first fix attempt (a static "no quantifier metachar `*+?{`" allow-list) was itself BLOCKED
  by the adversarial Opus security gate as PROVABLY UNSOUND** — catastrophic backtracking has a
  SECOND source besides repetition: variable-length ALTERNATION. `(a|aa)(a|aa)...(a|aa)b`
  (`"(a|aa)"*k + "b"`) contains no quantifier char yet backtracks 2^k (measured pure-Python `re`:
  k=24 → 6.19s, clean 2^k), and the attacker dials severity via pattern length on a tiny file. **No
  static pattern analysis can be the gate.** The shipped fix gates ALL these paths on
  `CPUBackend._fallback_pattern_is_provably_linear` (`:280`), which admits ONLY `fixed_strings`
  (re.escape'd → a literal automaton → provably linear regardless of the raw pattern text); EVERY
  other pattern fails closed with `BackendExecutionError`. This deliberately fails closed a legit
  non-ASCII regex on a non-UTF-8 file (e.g. `caf\xe9\d+` on latin-1) — the endorsed
  security-over-availability trade; such users pass `--fixed-strings` or use ripgrep (a genuine
  byte-safe engine). Regression tests in `tests/unit/test_cpu_backend.py`:
  `test_should_fail_closed_for_nested_quantifier_bomb_on_non_utf8_file`,
  `test_should_fail_closed_for_alternation_bomb_on_non_utf8_file` (the Opus counterexample),
  `test_fixed_strings_nonascii_literal_matches_via_python_fallback_but_regex_fails_closed`,
  `test_should_fail_closed_on_nonsyntax_rust_runtime_failure_for_regex`. Note for anyone writing a
  regression test against this class: Python's `_sre` engine holds the GIL for the entire match
  attempt, so even an in-process `Thread.join(timeout=...)` watchdog cannot reliably bound it — a
  sibling thread stuck in catastrophic backtracking can prevent the *main* thread's own timeout
  wait from waking up too; only an OS-level process kill (`subprocess.run(timeout=...)`) reliably
  bounds it.

The durable lesson (keep citing this even after further refactors): (1) "Rust ran this pattern
successfully" is NEVER sufficient evidence that Python's backtracking `re` can safely run the SAME
pattern — asking Rust again is not a valid gate, because Rust runs a catastrophic-backtracking-
shaped pattern and a benign one in the same guaranteed linear time. (2) NO static analysis of the
raw pattern is a sound gate either — catastrophic backtracking arises from BOTH nested quantifiers
(`(a+)+$`) AND quantifier-free variable-length alternation (`(a|aa)...b`), so any character/shape
allow-list is a bypass waiting to be dialed. The ONLY sound gate is the structural `fixed_strings`
guarantee; any new code path that could fall back to Python `re` must admit only `fixed_strings`
or fail closed.

## Provenance and maintenance

Facts here were verified by reading the cited files on **2026-07-08, tensor-grep v1.49.3**. Code
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

# Confirm the semantic-search dense+RRF leg is still present under its real module names
# (embed_backend.py / retrieval_hybrid.py never existed -- don't grep for those)
test -f C:/dev/projects/tensor-grep/src/tensor_grep/core/retrieval_dense.py -a -f C:/dev/projects/tensor-grep/src/tensor_grep/core/retrieval_fusion.py && echo "SHIPPED (section 9 current)" || echo "MISSING -- section 9 needs re-audit"

# Re-check the ReDoS gate is still closed (audit #6/#16/#111) — confirm the fixed_strings-only
# gate still exists and BOTH the UTF-8-fallback and native-failure branches call it before any
# Python `re` retry. It must admit ONLY fixed_strings; a static pattern-char allow-list is unsound.
grep -n "_fallback_pattern_is_provably_linear\|needs_word_or_context_rust_routing" C:/dev/projects/tensor-grep/src/tensor_grep/backends/cpu_backend.py

# Re-run the AST comparison benchmark before citing its numbers
cd C:/dev/projects/tensor-grep && uv run python benchmarks/run_ast_benchmarks.py
```

Open uncertainties (do not cite as fact without independent verification):

- The exact upstream ripgrep issue numbers for the `--multiline --pcre2 --json` double-submatch and
  `-c` NUL-omission edge cases (section 1) — no in-repo test fixture or citation exists for either as
  of this writing.
- The ReDoS gate on `-w`/`-x`/`-C`/`--ltl`/UTF-8-fallback/`--pcre2` (§1a) is CLOSED as of audit
  #111 (2026-07-10) — but re-verify before citing on a fresh session anyway: any NEW code path
  added to `cpu_backend.py` that could fall back to Python `re` must independently prove pattern
  safety or fail closed (see the durable lesson at the end of §1a); a future change could
  reintroduce a bypass the same way audit #111 found a third one after #6/#16 closed the first two.
