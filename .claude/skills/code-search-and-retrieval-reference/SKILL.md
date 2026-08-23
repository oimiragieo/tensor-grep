---
name: code-search-and-retrieval-reference
description: Use when you need the domain theory behind tensor-grep's search/retrieval behavior, not just the command syntax — ripgrep exit codes (including the exit-2-but-kept partial-results contract)/PCRE2/binary-NUL-detection/-uuu/-- sentinels, ast-grep + tree-sitter routing, the multi-language symbol-graph registry's fail-closed grammar-missing contract, BM25 vs the flat no-IDF capsule scorer, PageRank vs in-degree centrality, the trigram index, PyO3 + the GIL, MCP argv surface, LSP 3.17 framing, and Model2Vec/potion-code (SHIPPED as `tg search --semantic`). Load before explaining WHY tg behaves a certain way, reasoning about the protocol/algorithm THEORY underneath a backend/router change (exit-code semantics, scoring math, wire framing), or writing docs that touch these subsystems — for the invariants a backend/router change must not break, use `tensor-grep-architecture-contract` instead (or in addition). Not a how-to-run or how-to-debug guide — see the sibling table below for those.
---

# Code Search & Retrieval Reference

The domain-theory pack a mid-level engineer (or a model working cold) usually lacks, narrowed to
**only the slice that governs tensor-grep's actual behavior**. Every claim below cites the tg file
that uses it — read that file before relying on the claim in a review or a fix, because code drifts
and this document does not update itself. Verified against the repo **as of 2026-07-08, v1.49.3**;
§9's `tg find` addition and the new §10 (query-shape classification) verified **2026-07-16, v1.78.1**;
§3's `_score_symbol`/`_symbol_rank_key` breakdown re-verified and corrected **2026-07-22, v1.93.2**.
**Every `file:line` citation in the document re-grepped against `origin/main`, §2's AST-routing
description corrected, and new §2a (the `lang_registry` symbol-graph tier) added, 2026-07-23,
v1.95.0** — see the dated note at the end of "Provenance and maintenance" for what changed and why.
**2026-08-01, v1.101.27: §2a's language-tier claim corrected (all 10 top-10 languages are
registered, not 8; there is no unregistered-C/C++ gap) and every drifted `file:line` citation in
the document converted to a `grep <symbol>` instruction carrying a `was -> now` receipt** — see the
final dated note in "Provenance and maintenance" for the full list and for why a "verified as of
DATE" tag on a line number does not stop it from rotting.

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
| `2`, matches parsed (soft per-file error, e.g. one unreadable path among many) | rg still emitted matches for the readable files | tg **keeps** those matches, sets `result_incomplete=True` + an `incomplete_reason`, and does **not** raise — `partial = result.returncode == 2 and total_matches > 0` (grep that exact expression in `ripgrep_backend.py` -- was `:123`, now `:124`, mirrored in `_search_files_with_matches`/`_search_counts`) |
| `2`, nothing parsed, or any `> 2` | a genuine fatal failure (bad regex, unreadable path with no other matches, etc.) | `RipgrepBackend.search()` raises `BackendExecutionError` whenever `result.returncode > 1 and not partial` (grep `result.returncode > 1 and not partial` in `ripgrep_backend.py` -- was `:124-128`, now `:125-129`; the two sibling methods raise the same way -- was `:297` and `:413`, now `:308` and `:426`; the rg-missing guard -- was `:505`, now `:541` -- too). **RESOLVED #79/#10/#14 (commit `a7c9431`)** -- every `RipgrepBackend` fatal path used to raise a bare `RuntimeError`, deliberately not `BackendExecutionError`, so it would not get caught by `cli/main.py`'s `except BackendExecutionError:` per-file CPU-fallback retry; the fix flipped all of them to `BackendExecutionError` so that retry now catches rg failures the same way it does every other backend, per the Backend Fail-Closed Contract's normal convention. |

**Do not describe exit-2 as unconditionally "raise `BackendExecutionError`"** — that was true before
#341 (round-4 slice 3) and is no longer true; a partial exit-2 with real matches is now a **kept**
result, not a failure. `tests/e2e/test_rg_parity_edges.py::test_rg_exit_code_edges_match` is
parametrized exactly on the fatal-failure boundary (grep `ids=\["match", "no-match", "parse-error",`
in the same file -- was line `149`, now `169`) and asserts `tg.returncode == rg.returncode` for
every case (grep `def _assert_same_rg_behavior` -- was line `81`, now `96`) — a regex `(` (unbalanced paren, nothing parsed) must still
make tg exit `2`, same as rg. Partial-parse-with-matches is covered separately by
`tests/unit/test_rg_exit2_partial.py`.

**PCRE2 is a different engine, not a flag.** rg's default matcher is the Rust `regex` crate — linear
time, but **no lookaround, no backreferences**. `--pcre2`/`-P` switches to libpcre2, which supports
both but requires the *pattern* to be valid UTF-8 (it transcodes). tg detects PCRE2 support with a
real smoke test — build help output contains `--pcre2`/`PCRE2`, then actually run
`rg -P "a(?=b)" -V` and check `returncode == 0` (grep `def supports_pcre2` in `ripgrep_backend.py`
-- was `:27-51`, now `:54-77`).
This matters because of the **fail-closed contract**: `--pcre2` routed through an engine that cannot
honor PCRE2 semantics must raise, never silently execute as a plain-regex search that returns wrong
(or merely different) matches (`src/tensor_grep/backends/base.py:7-14`, `BackendExecutionError`;
grep `**Fail closed**` in `AGENTS.md` -- was line `444`, now `:2096`). A prior incident shipped
exactly this bug — a broad `except Exception: pass` around the Rust passthrough silently ran
`--pcre2` through the non-PCRE2 Python-regex engine — fixed in v1.17.17/18 (see
`tensor-grep-change-control`, grep `## Part 4` in that skill's `SKILL.md` -- was lines `125-134`,
now `:287-311` -- Backend fail-closed contract).

**Binary detection is NUL-byte sniffing, and it changes exit codes.** rg's default binary heuristic
scans early file bytes for a `\0`; on a hit, the file is treated as binary and searched under the
binary-skip policy unless `-a`/`--text` is passed (`rg_contract.py` row `"text"`, `public_flags:
("-a", "--text")`). The parity fixture builds exactly this case —
`binary_path.write_bytes(b"needle\0binary tail\n")` (grep that exact line in `test_rg_parity_edges.py`
-- was `:41-43`, now `:44`) — and the
`"binary-skip"` parametrization (grep `ids=\["match", "no-match", "parse-error",` -- was line `149`,
now `169`) asserts tg's exit code matches rg's on a NUL-containing file, not just its stdout.

**`-u`/`-uu`/`-uuu` are not blind passthrough here.** Upstream, each additional `-u` widens scope
(`-u` = `--no-ignore`, `-uu` = `--no-ignore --hidden`, `-uuu` = `--no-ignore --hidden --binary`). tg's
Python front door specifically *detects* any `-u*` flag (or `--unrestricted`, or an explicit
no-ignore/hidden flag) as a request for unrestricted scanning and routes it through a broad-root
safety guard (grep `def _search_args_request_unrestricted_generated_scan` in `bootstrap.py` --
was `:581-590`, now `:726-732`). This
exists because of a real v1.13.1 incident: an unguarded broad-root unrestricted scan could recurse
into `node_modules`/`.git`/multi-project workspace roots. If you're adding a new flag that widens
scan scope, check whether it needs to join this guard's flag set — a missed case is a silent safety
regression, not just a slow query.

**`--` and `-e` matter for argv safety, not just POSIX correctness.** `--` ends option parsing so a
user- or LLM-supplied pattern beginning with `-` cannot be reinterpreted as a flag (CWE-88 / the
MCP-276 CVE class). tg's MCP tool handlers build subprocess argv with an explicit `--` sentinel
before positionals for exactly this reason: `command.extend(["--", pattern, path])` (grep that
exact call in `src/tensor_grep/cli/mcp_server.py` -- was `:1306`, now `:1375`, comment two lines
above: "round-3 security: end options before the user-controlled positionals so a pattern
beginning with `-` cannot be parsed by the native binary as a flag"). Note the narrower scope of
this fix: it blocks *flag* injection via a
missing `--`, not shell injection (list-argv subprocess calls already block shell injection). See
`tensor-grep-change-control` before touching any subprocess argv builder.

**BOM handling is a real, previously-broken seam.** UTF-8 BOM bytes at the start of a file/scenario
JSON broke PowerShell-generated fixtures until scenario loading switched to `utf-8-sig`
(`docs/PAPER.md:840`). AST rewrite's batch-apply path explicitly preserves BOM/CRLF through
atomic writes (`docs/harness_api.md:700`, "batch apply reuses the same atomic-write, BOM/CRLF
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
| `AstBackend` (native) | `grep -n "^class AstBackend" src/tensor_grep/backends/ast_backend.py` -- `:142` as of 2026-08-14 (was `:133`) | `is_available()` checks **only** whether `tree_sitter` is importable (`grep -n "def is_available" src/tensor_grep/backends/ast_backend.py` -- `:608` as of 2026-08-14, was `:505-519`) — **no GPU/CUDA/`torch_geometric` gate anymore.** That gate was real once (see the "Corrected" note below) but was deleted in #542 (v1.65.0, 2026-07-12); the current docstring says outright: *"AstBackend.search() is pure tree-sitter query matching -- it never touches torch, CUDA, or any graph-learning library ... gating a fully-functional CPU backend behind an unrelated GPU dependency was itself the bug."* |
| `AstGrepWrapperBackend` (sidecar) | `src/tensor_grep/backends/ast_wrapper_backend.py:85` | shells out to an installed `ast-grep`/`sg`/`sg.exe`/`ast-grep.exe` binary via `shutil.which` (lines 111-123) |

`tree-sitter` parses source into a concrete syntax tree; a **metavariable** like `$FUNC` or the
"capture the rest" form `$$$ARGS` is ast-grep/tg's pattern-matching primitive over that tree (e.g.
`def $FUNC($$$ARGS):` matches any Python function definition, binding `$FUNC` and `$$$ARGS`).

**Corrected — the routing default is the OPPOSITE of what this section previously said.** The real
routing decision lives in `_select_ast_backend_for_pattern` in `src/tensor_grep/cli/ast_workflows.py`
(grep `def _select_ast_backend_for_pattern` in `ast_workflows.py` -- the `main.py` copy this
instruction used to point at (was `:6737`, then `:6915`) is now a THIN FORWARDING SHIM onto the
ast_workflows implementation; `grep "Thin forwarding shim onto" src/tensor_grep/cli/main.py` finds
it, and its docstring records the drift that forced the collapse -- the old hand-maintained
duplicate silently dropped the `requires_ast_grep_wrapper` fail-closed guard. A dated hedge does not
stop a line number from rotting, it just makes the rot look supervised, so no citation in this
document carries a date anymore), and its own comment is unambiguous: *"Prefer the ast-grep wrapper
whenever it is available: it is the stable, results-defining backend for BOTH pattern kinds. The
native tree-sitter AstBackend uses a DIFFERENT DSL and returns DIFFERENT results, so it must not be
silently preferred ... Native-as-CPU-default is task #141. Native is reached ONLY as the
ast-grep-absent fallback for native patterns."* (grep "Prefer the ast-grep wrapper" in
`ast_workflows.py` -- was `:6692-6697` in `main.py`, then `:6952-6957`; now `ast_workflows.py:1231-1232` (grep "Prefer the ast-grep wrapper")). Concretely: the wrapper
availability check runs **first** (`if _check_backend_available("AstGrepWrapperBackend"): backend =
_get_cached_backend("AstGrepWrapperBackend")` -- grep `_check_backend_available("AstGrepWrapperBackend")`
in `ast_workflows.py`; the old `if ast_wrapper.is_available(): backend = ast_wrapper` shape this
entry used to cite in `main.py`, was `:6698`, then `:6958`, is gone),
unconditionally, regardless of GPU/CUDA or pattern shape; native `AstBackend` is reached only when
the wrapper is unavailable AND the pattern qualifies as "native" (`base_config.ast_prefer_native`
and `is_native_ast_language(base_config.lang)` — the native-capable language set is narrower than
the wrapper's, `_NATIVE_AST_LANGUAGES = ("python", "javascript", "typescript", "tsx", "rust")`,
`ast_backend.py:104`). This applies to AST search, `--rewrite` planning, `--apply`, `--diff`, and
batch rewrite flows alike. **`docs/routing_policy.md`'s own "AST commands" section is itself stale
here** (grep `## AST commands` in that doc -- was `:107-113`, now `:156`) — it still describes a
`torch-geometric`/CUDA-style gate — trust the code cited above, not that doc's prose, until it is
refreshed.

**Practical corollary (updated):** on almost any real dev or CI box, `ast-grep`/`sg` being installed
is now the thing that decides the backend, not GPU presence — with ast-grep installed (the common
case, since it's how most people actually got `tg run` working), `tg run`/AST calls use the CLI
sidecar regardless of CUDA. This *used* to be a CUDA story: an earlier "AST probe" bug in CI traced
back to `AstBackend` being GPU-gated (see `tensor-grep-docs-and-writing` Part 6, and project memory
`tensor-grep-readme-release-blocker-2026-06-25`, for that incident) — but the GPU gate itself is gone
(#542 above), so don't cite CUDA absence as the reason native is skipped anymore; cite wrapper
availability instead. Don't assume native AST speed numbers apply unless you've confirmed the
*wrapper* is unavailable (or `--lang`/pattern shape forced native) on the box you're measuring.

Measured (not marketing) ratios, `benchmarks/run_ast_benchmarks.py` /
`run_ast_multilang_benchmarks.py` (`docs/benchmarks.md` "ast-grep vs tensor-grep AST mode"): single-
query `tg 0.116s` vs `sg 0.151s` (`0.770x`); multi-language ratios Python `0.722x`, JavaScript
`0.800x`, TypeScript `0.726x`, Rust `0.715x` — `tg` ahead of `sg` on all four **when the native path
is actually reachable**. Given the routing correction above, treat these specific numbers as
unverified-current: `run_ast_benchmarks.py` invokes the real `tg` binary end-to-end
(`build_tg_ast_benchmark_cmd`, `benchmarks/run_ast_benchmarks.py:115`) on a machine that also has a
resolvable `sg`/`ast-grep` binary (it benchmarks against it) — on such a machine, `tg run` now
prefers the wrapper by default, so a fresh run may be timing "tg shelling out to sg" against "sg
directly" rather than native-tree-sitter vs sg. **Positioning caveat (do not drop):** ast-grep is the
structural-search BASELINE and `tg run` is "a useful validated AST slice, not a blanket ast-grep
replacement" (`AGENTS.md`; the docs-governance tests ban an "ast-grep replacement" framing). Never
let these ratios feed a "tg beats ast-grep" narrative. Re-run the script before citing a fresher
number — cold-process, single-pass, and confirm which backend actually served the request (the
`profile-guided-byte-identical-optimization` skill's warm-vs-cold measurement discipline applies
here too: a cached/warm run of either binary understates its true per-invocation cost).

### 2a. The deep symbol-graph tier — `lang_registry` (added 2026-07-23)

The two backends above answer "match/rewrite an AST pattern" (`tg run`/`tg scan`). A separate,
third tier answers a different question — "what are this repo's symbols, and how do they
import/call each other" (`tg orient`/`tg defs`/`tg source`/`tg imports`/`tg callers`/
`tg blast-radius`/the `tg agent` capsule) — and is unrelated code: no shared availability gate and
no shared routing function with `AstBackend`/`AstGrepWrapperBackend` above.

This tier's single source of truth is `lang_registry.py`
(`src/tensor_grep/cli/lang_registry.py`): a frozen `LanguageSpec` dataclass registry answering
"which languages does the symbol graph support, and which callable implements each extraction
stage for each" (module docstring). `repo_map.py` calls
`lang_registry.register_language(LanguageSpec(...))` once per language.

**Do not hand-derive this count or its tier split — this repo's memory records the split being
stated WRONG three separate times** (a hand-count landing on 4/10 with go demoted, a grep for a
string that happened to also match `=None` landing on 8/10, and an invented third "unresolved" tier
built from a field's absence). The authoritative source is the product's own descriptor function,
run it rather than trust any number written here:

```bash
python -c "import sys;sys.path.insert(0,'src');from tensor_grep.cli import repo_map as r;print(r._symbol_navigation_descriptor())"
# -> parser-backed-refs-callers:c-cpp-csharp-go-java-javascript-php-python-rust-typescript
#    +foundational-defs-imports-only:
# (as of Task 10E, the final wave of the top-10 language-support campaign: every registered
# language is parser-backed, the foundational-defs-imports-only segment is EMPTY -- it is still
# always emitted, never omitted, so the descriptor's shape never changes)
```

cross-checked against `grep -c "lang_registry.register_language(" src/tensor_grep/cli/repo_map.py`,
which returns **10**. **All 10 of the top-10 languages** by TIOBE-Jul-2026/Stack-Overflow-2025/
GitHub-Octoverse-2025 consensus ranking (Python, JavaScript, TypeScript, Java, C#, C++, C, Go,
Rust, PHP) are registered — there is no unregistered language on this list and nothing deliberately
deferred. **There is also no third tier.** The 10 have been evenly parser-backed since the campaign's final
waves -- PR #927 (Task 10A), the Task 10B C# wave, the Task 10C PHP wave, the Task 10D C wave, and
the Task 10E C++ wave: **all ten parser-backed** languages
with refs/callers support (c, cpp, csharp, go, java, javascript, php, python, rust, typescript;
every one is in-file only -- cross-file caller confirmation still falls back to the text
prefilter pending a package/source-root resolver) and **zero foundational** languages
(defs+imports-only tier EMPTY). The descriptor's own docstring says this outright: *"As of
Task 10E (C++, the final wave of the top-10 language-support campaign) this tier is EMPTY -- every
registered language carries a real `references_and_calls` extractor"* (`repo_map.py:590-597`; the
2026-08-04 "C++ joins the symbol graph as a FOUNDATIONAL-TIER language" registration comment is
pre-wave history). The real open backlog item here is therefore **cross-file caller confirmation
for all ten languages** -- not registering or upgrading any language; the tier structure is final
at 10/0. A language's callables live either as older helpers
defined directly in `repo_map.py` (python needs no external grammar at all -- it parses with the
stdlib `ast` module; rust's `_rust_*` helpers predate/mirror that inline style; java's `_java_*`
helpers still hold its defs/imports extraction inline, but its references-and-calls extraction
moved to `lang_java.py`, mirroring the module-shaped languages below) or in a newer, self-contained
per-language module mirroring `lang_go.py` (`lang_go.py`, `lang_php.py`, `lang_csharp.py`,
`lang_c.py`, `lang_cpp.py`, `lang_java.py` -- each importing nothing from `repo_map.py`, to avoid an
import cycle).
`LanguageSpec` does not care which shape a language's callables take, only that they exist, so both
are equally contract-consistent — do not assume "inline" means "old" or "module" means "new" from
the shape alone; check the registration date.

**The fail-closed default matters more here than in section 2 above.** `LanguageSpec.
provenance_when_missing` defaults to `"regex-heuristic"` (`lang_registry.py:89`) — the original
four languages fall back differently when their grammar is missing: python's
`provenance_when_missing="python-ast"` (grep that exact string in `repo_map.py` -- was `:6011`, now
`:6294`, no external grammar to miss at all); javascript/typescript keep the inherited
`"regex-heuristic"` default (grep each `register_language(` call and read the block — there is no
separate literal string to grep for on those two, since it is the field's own default), while rust
now sets it EXPLICITLY (grep `provenance_when_missing="regex-heuristic"` in `repo_map.py`). Every
language added since the original four (go/java/php/csharp, and both c/cpp -- foundational-tier at
registration, since promoted in Tasks 10D/10E) instead sets
`provenance_when_missing="grammar-missing"` explicitly on its own `register_language(...)` call
(count with `grep -c "^\s*provenance_when_missing=" src/tensor_grep/cli/repo_map.py` — **8 field
settings** as of 2026-08-12 (was 6 as of 2026-08-09): the six post-registry `grammar-missing`
languages plus python's `python-ast` plus rust's now-explicit `regex-heuristic`; the literal also
appears in explanatory comments, so match the field shape `^\s*provenance_when_missing=` to count
settings rather than raw hits) and ships **no regex fallback at all**: a
grammar-absent file for one of these six returns `([], [])` from `_imports_and_symbols_for_path`
(grep `def _imports_and_symbols_for_path` in `repo_map.py` -- was `:6626`, now `:6627`) rather than
silently degrading. That flag is consumed by `_language_coverage_gaps_for_universe` (grep `def
_language_coverage_gaps_for_universe` in `repo_map.py` -- was `:8461`, now `:8478`; the check
itself is the literal `if spec.provenance_when_missing not in {"regex-heuristic", "heuristic"}:` --
grep that string rather than trust a sub-line offset, was `:8019`, now `:8515`), which turns it
into an honest, labeled `resolution_gaps` entry instead of a silent empty result — the Backend
Fail-Closed Contract's "treat a zero as UNKNOWN, never as a silently proven zero" rule, applied at
the language-registration layer instead of the backend layer. This is a deliberate per-language
precision/recall tradeoff, not an oversight; see `tensor-grep-change-control` if you are adding an
11th language and need the full seam checklist rather than the theory.

**A concrete consequence of this design worth knowing (ties back to §3/§4's ranking theme below):**
`_target_language_for_path` (grep `def _target_language_for_path` in `repo_map.py` -- was `:7850`,
now `:7867`) feeds the `tg agent` capsule's query-language-vs-target-language confidence cap
(`agent_capsule.py`). Its own in-repo comment calls each new-language branch the "MOST-FORGOTTEN
seam" — miss it, and the capsule never learns the new language exists as a candidate target, so it
can silently misfire (e.g. reporting "no target language" for a C# file instead of
`primary_target_language == "csharp"`) with no error, just a quietly wrong answer. Same failure
shape as the ranking weak points below: a missing registration doesn't crash, it degrades a
downstream signal invisibly.

**Positioning (ties back to the ast-grep discussion above):** **text search = any language** (`rg`
passthrough, no tg-side language awareness at all); **structural scan/rewrite = 26 languages**
(`tg run`/`tg scan`, via the ast-grep CLI this section describes — `_SUPPORTED_AST_LANGUAGES`,
`ast_backend.py:76-103`, `get_supported_languages()` at `:128`); **deep symbol-graph = 10
languages, split across two tiers** (this subsection -- 10 parser-backed refs/callers + 0
foundational defs/imports-only: the foundational tier is EMPTY since the Task 10E C++ wave, per
the `_symbol_navigation_descriptor()` derivation above). tg is
`rg` (text) + ast-grep (structural) + a symbol/retrieval/capsule LAYER on top of that — not "a
faster grep," and the three tiers do NOT share a language-support number, so check which tier a
coverage claim is actually about before citing it.

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
   (alias `--bm25`; grep `"--bm25"` in `main.py` -- was `:7135`, now `:7395`) via
   `reranker.py::rerank_by_bm25`, which chunks matched
   files and re-sorts matches by the BM25 score of the chunk containing each match
   (`reranker.py:162-214`, stable sort at line 203) — a stable sort, so ties keep original grep order.
2. **The `tg orient` / capsule symbol-ranking family** (`src/tensor_grep/cli/repo_map.py`) — **a flat
   presence-count stack, no IDF anywhere in it.** Three layered pieces, not one function — do not
   conflate them:
   - `_score_text_terms` (grep `def _score_text_terms` in `repo_map.py` -- was `:7912`, now
     `:8189`) — the primitive: counts term hits in a haystack, no rarity weighting.
   - `_score_symbol` (grep `def _score_symbol` in `repo_map.py` -- was `:8177`, now `:8194`) — the
     actual per-symbol composite scorer, and the thing that produces `symbol["score"]`: name-match
     (`_score_text_terms` on the symbol name, `x3` weight) + kind-match + file-path score
     (`_score_file_path`, grep `def _score_file_path` in `repo_map.py` -- was `:8100`, now
     `:8117`), plus two additive heuristics shipped for task #254 (the CEO deep-research #251 steal
     / A7): a **+1 word-boundary bonus** (`_symbol_name_exact_boundary_bonus`, grep `def
     _symbol_name_exact_boundary_bonus` in `repo_map.py` -- was `:8159`, now `:8176`; fires when a
     query term longer than 3 chars matches a clean token in `split_terms(symbol_name)` rather than
     only a raw substring) and a **`_TEST_SHADOW_PENALTY = 2`** demotion (grep
     `_TEST_SHADOW_PENALTY` in `repo_map.py` -- was `:7661`, now `:8157`, floored at 0 in
     `_score_symbol`) that sinks a test-file hit below a same-named non-test definition instead of
     letting it compete on equal footing. Both are additive refinements to *order among
     already-matching candidates* — neither changes *which* symbols match, and neither adds IDF.

     (The two "as of 2026-07-27" hedges this bullet list used to carry had already drifted a
     further 17 lines by the very next pass — a dated hedge does not stop a line number from
     rotting, it just makes the rot look supervised, so this document tags no citation with a date
     anymore; see the note beside the AST-routing citation in §2 for the same lesson applied there.)
   - `_symbol_rank_key` (grep `def _symbol_rank_key` in `repo_map.py` -- was `:8044`, now `:8061`)
     — the final sort key, called as `scored_symbols.sort(key=_symbol_rank_key)` (grep that exact
     call in `repo_map.py` -- was `:8685`, now `:9181`). Its 7-tuple is
     `(query_match_rank, -score, kind-is-function?, -span_length, file, line, name)`. The **first**
     field, `query_match_rank`, is a query-relevance bucket (0 = `exact_query_match`, 1 =
     `bridge_query_match`, 2 = `covered_query_match`, 3 = none) evaluated **before** the flat
     `_score_symbol` score — so a query-name-match bucket dominates the flat count, it doesn't lose
     to it. The **final** tie-break field is `str(symbol.name)`, **not** a file-path string.

   This whole stack feeds `tg orient`'s symbol ranking and the `tg agent` capsule's target selection;
   the top-N candidate cap is `ranked_symbols[: max(max_symbols, 8)]` (grep that exact expression in
   `repo_map.py` -- was `:13187,13364`, now `:13683,13860`).

**Why this is still a known weak point, just a narrower one than it used to be:** `_score_symbol`
still has no IDF, so two symbols in the same `query_match_rank` bucket can still tie on the flat
`score` — but `query_match_rank` being evaluated first, plus four more tie-break fields (kind, span
length, file, line) sitting ahead of `name`, make an *unrelated* corpus change flipping the final pick
considerably less likely — and less exactly reproducible — than it was when this was first found. The
original incident (receipt in project memory `tensor-grep-idf-ranking-fragility-2026-06-29`): a corpus
change with **zero call-graph edge** to the query — an unrelated file added elsewhere in the repo —
shifted which candidate won a flat-score tie and flipped the capsule's primary target, including
flipping "ask before editing" (`ambiguity=tie_requires_confirmation, ask_user=True`) to "confidently
pick a target" (`ask_user=False`) with no code-level connection an agent could see via `tg callers`
(PR #302). That incident predates both the `query_match_rank` first-field and the `_score_symbol`
heuristics documented above, so do not assume today's tuple shape reproduces it step-for-step on a
fresh repro attempt — but the underlying hazard (a flat, no-IDF score can tie, and a tie still falls
through several non-relevance fields before `name`) is real and unresolved. It is covered by a
**degrade-to-ask safety floor**, not a ranking fix: if the post-tie primary target is still an
unrequested "marker" helper, `agent_capsule.py`'s `_primary_target_is_unrequested_marker_helper`
(now `agent_capsule_targets.py` -- find it: `grep -rn "^def _primary_target_is_unrequested_marker_helper" src/tensor_grep/cli/`) forces `ask_user=True` rather than silently auto-picking it. The flat no-IDF
scorer family itself remains deferred debt — do not assume it has been fixed just because the unsafe
*consequence* was mitigated, and do not mistake the `query_match_rank` bucketing or the #254/A7
heuristics for an IDF fix: they are relevance refinements layered on the same rarity-blind foundation,
not term-rarity weighting.

If you are reviewing a PR that touches ranking-feature tests: an edit that reddens a live-repo
ranking assertion is not automatically a "brittle test" to relax — inspect whether the actual
tie/ask/confidence behavior degraded before deciding. See `tensor-grep-idf-ranking-fragility-2026-06-29`
in project memory for the full incident writeup, and `tensor-grep-change-control` for the review gate.

---

## 4. PageRank / centrality — and why `tg orient` deliberately does NOT use it

tg has a real, hand-rolled **personalized PageRank** implementation over the reverse-import graph:
`_personalized_reverse_import_pagerank` (`src/tensor_grep/cli/repo_map.py:9174` — re-derive with: grep -n '_personalized_reverse_import_pagerank' src/tensor_grep/cli/repo_map.py) — damping
factor `alpha=0.85` (the standard Google PageRank default), `12` power-iteration steps, a
personalization vector seeded uniformly over up to `_GRAPH_PAGERANK_SEED_FILE_LIMIT = 64` query-
relevant files (grep `_GRAPH_PAGERANK_SEED_FILE_LIMIT` in `repo_map.py` -- was `:319`, now `:335`),
teleporting back to those seeds rather than to a uniform distribution.
This feeds descriptive-query file ranking (`graph-centrality` reason) inside `repo_map`/capsule/edit-
plan retrieval — pure Python, no `networkx` dependency (unlike Aider's repo-map, which uses
`networkx`'s PageRank over the full import graph — an external comparison, not yet documented in this
repo's `docs/tool_comparison.md`, which currently makes no Aider/networkx/PageRank claim).

**`tg orient`'s "central files" list is explicitly NOT PageRank — it's a composite of import
in-degree plus symbol density, both capped** (`src/tensor_grep/cli/orient_capsule.py:694`,
`_central_files_from_map`; docstring: *"Rank source files by import in-degree (foundational =
imported-by-many); top-N with symbols"*). The rationale for avoiding raw reverse-import PageRank
here — that a personalized PageRank seeded by all files ranks IMPORTERS above the imported, which
is backwards for "show me the core files" — is no longer stated as a verbatim code comment at this
location; do not quote it as a literal in-repo string without re-finding it. The underlying design
choice is still real and still worth citing conceptually: **personalized PageRank answers "what's
relevant to this specific seed set", while in-degree answers "what does the whole repo depend on"**
— different questions, and `orient` wants the second (foundational files a newcomer should read
first). The current implementation additionally caps fan-in (`_CENTRAL_FAN_IN_CAP = 12`) and symbol
density (`_CENTRAL_SYMBOL_DENSITY_CAP = 25`, both `orient_capsule.py:45-46`) so a single
widely-imported data-sink file or one giant file can't dominate the ranking on its own — a
refinement on top of plain in-degree, not a switch to PageRank. If you're adding a new "show me the
important files" feature, pick deliberately between personalized-PageRank and in-degree-based
centrality; don't default to whichever is already imported in the module you're editing.

---

## 5. Trigram index — `--index` / warm-cache acceleration

A trigram index maps every 3-byte substring ("trigram") appearing in the corpus to the list of files
containing it (a postings list); a query first extracts the trigrams it must contain, intersects
their postings lists to get a small file candidate set, then only regex-scans those files instead of
every file in the corpus. tg's implementation: `TrigramIndex` struct, `rust_core/src/index.rs:151`,
3-byte keys (`FileTrigramHits = Vec<([u8; 3], u32)>`, line 30), binary bincode
serialize/deserialize.

**Safety property:** when a pattern has no extractable required literal (e.g. `.*` or an alternation
with no common substring), the index cannot safely prefilter — the code falls back to a full scan
"so the index never introduces false negatives" (`index.rs:1609`). A trigram index is a *prefilter*,
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
(`rust_core/src/lib.rs:353`) intentionally opts back into the classic (non-free-threaded) GIL model.
The comment explains why: a prior attempt to ship `gil_used = false` (free-threaded Python, #266)
broke Linux `agent-readiness` in CI, and because that PR's CI run was cancelled by a force-push, it
merged without ever going green — re-enabling free-threading requires a **full green CI run on
Linux extension load** first, not just a local pass (`lib.rs:350-352`).

**Settled battle, don't re-propose:** FFI (moving directory-walk/file-scanning work into the PyO3
extension "for speed") was tried and reverted — the FFI call overhead measured higher than native
CPython directory scanning for that workload. See `tensor-grep-failure-archaeology` before proposing
a PyO3 rewrite of a hot path; benchmark first (`tensor-grep-benchmark-and-proof-toolkit`).

---

## 7. MCP — the protocol, and its argv-injection surface

**MCP (Model Context Protocol)** is the tool-calling protocol that lets an LLM agent invoke
structured "tools" (typed functions with a JSON schema) exposed by a server process. tg's MCP server
is built on the official Python `mcp` SDK's `FastMCP` class:
`from mcp.server.fastmcp import FastMCP` (`src/tensor_grep/cli/mcp_server.py:20`, still current) /
`mcp = FastMCP("tensor-grep")` (grep that exact call in the same file -- was `:120`, now `:189`), a
~7700-line module (`mcp_server.py`) — it has grown
substantially (from ~4500 lines) on unrelated feature work since this doc's baseline; re-check the
line count before citing it as a "small module" argument in a review.

**The domain risk that matters here:** an MCP tool handler takes LLM-supplied parameters (a search
`pattern`, a file `path`, a rewrite `replacement`) and forwards them into a subprocess argv to invoke
the native `tg` binary. If a parameter value happens to start with `-`, and the argv builder doesn't
end option-parsing first, the "data" is reinterpreted as a flag — this is CWE-88 (argument
injection), the same class as the MCP-276 CVE. **List-argv subprocess calls (no `shell=True`) already
block shell injection; they do NOT block flag injection** — that needs an explicit `--` sentinel
before user-controlled positionals. tg's rewrite/index-search command builders do this:
`command.extend(["--", pattern, path])` (grep that exact call in `mcp_server.py` -- was `:1306`,
now `:1375`) and the parallel index-search builder, `_build_index_search_command` (grep `def
_build_index_search_command` in `mcp_server.py` -- was `:1362-1372`, now `:1379-1390`). If you add a new MCP tool that shells out
with user-controlled string values, this is the pattern to copy — and the gap to check for if you
don't see it.

**The native rg-passthrough path-sentinel gap is now FIXED, not open.** A prior round-4 sweep flagged
that `rust_core/src/rg_passthrough.rs` forwarded user **paths** with no `--` sentinel of its own — a
directory literally named `-l` could silently flip rg into files-with-matches mode at the native
layer (CWE-88). This is closed: `ripgrep_operand_args` (grep `fn ripgrep_operand_args` in
`rg_passthrough.rs` -- was `:581-600`, now `:584-603`) builds patterns flag-safely via `-e` and
then, whenever there is at least one user path, inserts a `--` sentinel before the path loop (same
function body; the sentinel is intentionally omitted only when there are no paths at all, so as not
to change the piped-stdin invocation shape). Three unit tests pin this, no longer contiguous as a
range — grep each name: `operand_args_insert_end_of_options_sentinel_before_paths` (was in a
`:788-826` block, now `:1034`), `operand_args_no_sentinel_when_no_paths` (now `:1056`),
`operand_args_files_mode_omits_patterns_but_keeps_sentinel` (now `:1065`). Don't cite this as an open gap anymore; do still check any *new*
argv builder you touch for the same pattern — the class of bug recurs.

---

## 8. LSP — Language Server Protocol, experimental provider mode

**LSP** is a JSON-RPC-over-stdio protocol (Microsoft-originated, editor-agnostic) for
definitions/references/symbols; tg's external-provider client speaks it to talk to a real language
server (e.g. Pyright, rust-analyzer) instead of relying on tg's own native/AST navigation. Two
concrete wire-level facts worth knowing before touching this code:

- **Framing** is `Content-Length: N\r\n\r\n` followed by exactly `N` bytes of JSON
  (`src/tensor_grep/cli/lsp_external_provider.py:173`) — this is LSP's transport framing, not a tg
  invention. tg **caps** the declared `Content-Length` at `_MAX_LSP_MESSAGE_BYTES = 64 * 1024 * 1024`
  (line 91) specifically so "a malicious or buggy external LSP provider cannot declare a huge
  Content-Length and force an unbounded read/allocation" (line 90) — a DoS hardening measure on
  the *client* side of an LSP session, since tg here is the client trusting an external server
  process.
- **Lifecycle**: `initialize` → `initialized` handshake before any real request
  (`lsp_external_provider.py:522,550`), then per-document `textDocument/didOpen` /
  `didChange` / `didSave` / `didClose` notifications (around lines 742-799) and
  `textDocument/documentSymbol` requests (line 1331). Session docs anchor this to the official
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
**Shipped default is `combine="max"`, not the sum:** `retrieval_fusion.py`'s
`reciprocal_rank_fusion(..., combine: Literal["sum", "max"] = "max")` ships best-rank-wins as the
DEFAULT (accuracy-leg campaign: ndcg@10 0.3047 (sum) -> 0.4953 (max), +62.6%, on the frozen golden
harness); the sum formula above is the ORIGINAL Cormack/Clarke/Buettcher formulation, kept as
`combine="sum"` for byte-identity with pre-max-flip behavior (module docstring; verify with
`grep -n "combine: Literal" src/tensor_grep/core/retrieval_fusion.py`).

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
`potion-code-16M` (`retrieval_dense.py:5,49,93`; `pyproject.toml`'s `[semantic]` extra:
`model2vec>=0.5`, `numpy>=1.26`), matching the AGENTS.md roadmap directive — the smaller
`potion-base-8M` from the old plan doc's draft snippet was never shipped.

**The user-facing surface is a flag, not a new command:** `tg search PATTERN PATH --semantic`
(grep `"--semantic"` in `main.py` -- was `:7141`, now `:7403`, registers the flag; `config.semantic_rank`
drives `_apply_semantic_rerank` at the `if config.semantic_rank:` call site -- was `:8048-8051`,
now `:8392-8395`). There is no separate `tg index` command for the dense leg. A genuine dense-backend
fault (e.g. a corrupt model directory) surfaces as a `BackendExecutionError` and exits the CLI
cleanly with a `tg:`-prefixed message (never a raw traceback) — grep `f"tg: {exc}"` in that same
function -- was `:8051-8060`, now `:8396-8405`.

This feature remains **EXPERIMENTAL / default-off-by-flag** per project discipline (it is shipped
code, not a marketed default) — `--semantic` must be explicitly passed. If you are actually building
or extending this campaign (not just reading the theory), switch to
`tensor-grep-semantic-search-campaign` — it has the phase-gated build plan, promotion gates, and
fenced-off wrong paths; re-verify its own status note against this section before trusting either in
isolation, since this is still the fastest-moving area in the repo.

**A 2nd consumer joined in v1.77.0 (#189): `tg find`.** Where `--semantic` above re-ranks an EXISTING
regex match set, `tg find` walks and ranks the WHOLE repo (no pattern pre-filter) through the SAME
`retrieval_dense.py`/`retrieval_fusion.py` core, plus two further pieces: an optional MaxSim
late-rerank stage (`TG_LATE_RERANK`, currently HELD/evidence-gated -- regresses vs plain BM25 in the
gate-run, entangled with a non-role-aware doc encoder, not a verdict on MaxSim itself) and the
query-adaptive `TG_FIND_DENSE_WEIGHT` fusion-weight knob (default-OFF, `1.0` = today's byte-identical
equal weighting) gated by the query-shape classifier in §10 below. See `tensor-grep-run-and-operate`
§1/§7/§11c for the command surface and `tensor-grep-semantic-search-campaign` STATUS UPDATE 2 for the
build history.

**SUPERSEDED (append-only, do not rewrite the paragraph above) — 2026-08-05, task F10: the
`TG_LATE_RERANK` "HELD/evidence-gated, not a verdict on MaxSim" framing is RETIRED.**
`src/tensor_grep/core/retrieval_late.py:4-16` (the module-header RETIRED block) records MaxSim
late-rerank as a VALIDATED DEAD END, not a paused build: decisive negative on the golden set AFTER
the role-aware encoder fix already landed (ndcg@10 0.068 vs plain RRF 0.305), root cause = model
capacity (the 17M-param int8 `LateOn-Code-edge` model's raw MaxSim ranking is statistically
indistinguishable from random on in-repo code), NOT the encoder — so re-flipping the same encoder
cannot change the verdict, and the old "entangled with a non-role-aware doc encoder" hedge no
longer applies. Reopen only on BOTH a real `tg`-command install path AND a different encoder
clearing the design doc's T8 golden-set thresholds. Do not re-run the old experiment.

**SUPERSEDED (append-only, do not rewrite the paragraph above) — 2026-08-19, enterprise audit: the
`TG_FIND_DENSE_WEIGHT` "default-OFF, `1.0` = today's byte-identical equal weighting" framing is
RETIRED.** The flip SHIPPED (#191/#634, first released v1.79.0). Unset now resolves to an ADAPTIVE
weight, not to `1.0`: `5.0` (the ledger-swept 1:5 bm25:dense ratio) for a genuinely multi-word
query, and `1.0` for a single whitespace-free token — the literal/identifier case, which stays
pinned at `1.0` regardless of the env value. A malformed or non-finite value is treated as unset
and falls to the adaptive `5.0` rather than being clamped to `1.0`, so a typo cannot silently opt
an operator out of the improved default. Explicit `TG_FIND_DENSE_WEIGHT=1.0` is the opt-out back
to equal-weight fusion.

Ask the code, not this paragraph:
`grep -n '_FIND_DENSE_WEIGHT_DEFAULT = \|_FIND_DENSE_WEIGHT_ADAPTIVE_DEFAULT = ' src/tensor_grep/cli/main.py`

*Why this note exists, and why it is a pointer rather than a fuller copy:* the stale claim survived
here for months while `tensor-grep-config-and-flags` carried the corrected one, so the library held
a fact AND its refutation in two files at once — the failure mode the 2026-08-19 audit found six
instances of. A 2026-08-12 retention audit had already flagged this exact row as DRIFT_FOUND. The
constants are deliberately code-resident; duplicating tuned values into prose is what produced this
drift in the first place, so this note states the shape and hands over the grep.

---

## 10. Query-shape classification — a tokenizer is not a word-splitter (added 2026-07-16)

Any hybrid lexical+semantic router eventually needs to answer a COARSE routing question — "is this
query a literal/identifier lookup or a natural-language phrase?" — separately from the FINE-GRAINED
tokenization question BM25 and the dense leg both need ("what are this string's index terms?"). tg
learned the hard way that reusing the fine-grained tokenizer to answer the coarse question is a bug
class, not a shortcut: `tg find`'s `TG_FIND_DENSE_WEIGHT` classifier originally gated on
`split_terms(query) > 2` (the same camelCase/snake_case-aware subword splitter `retrieval_lexical.py`
uses for BM25 indexing, §3 above) — but `split_terms` splits a descriptive SINGLE-token identifier
into 3+ MORPHEMES (`reciprocal_rank_fusion`, `_confine_mcp_path`, `BackendExecutionError` all split
into 3+ pieces), so a literal identifier lookup misclassified as multi-word NL and leaked into the
dense-boost branch. A real-repo dogfood caught it mis-boosting 5 of 6 literal-identifier golden
queries. The fix: **whitespace word-count, not morpheme count** (`len(query.split()) <= 1` -> literal;
#191, commit `173e093`/#630) — see `tensor-grep-config-and-flags` for the shipped mechanics and
`tensor-grep-validation-and-qa` Part 1 pt 4 for the fixture-green-vs-real-corpus receipt.

**The generalizable gotcha:** do NOT reuse a fine-grained identifier/subword tokenizer (a
camelCase/snake_case splitter shared with a BM25/indexing leg) to answer a coarse literal-vs-NL
ROUTING question. Use a cheap, coarse, structural signal instead — raw whitespace word-count is one;
production systems converge on the same family of signal (backtick-quoting, CamelCase/snake_case
SHAPE without splitting it into morphemes, path separators, leading question words) rather than a
learned classifier or a shared subword tokenizer. This generalizes to any hybrid lexical+semantic
router, not just `tg find`.

**External grounding (verified 2026-07-16, not taken on faith):**
- Broder, *A Taxonomy of Web Search* (ACM SIGIR Forum, 2002; DOI `10.1145/792550.792552`, ~1,900
  citations) — the foundational "classify query intent before choosing how to serve it" framing that
  this whole class of router descends from (navigational/informational/transactional for web search;
  the same "classify first, retrieve second" shape reapplied to code search).
- `Dicklesworthstone/frankensearch`, `crates/frankensearch-core/src/query_class.rs` — a real, shipped,
  zero-ML `Empty`/`Identifier`/`ShortKeyword`/`NaturalLanguage` classifier for hybrid lexical+semantic
  code retrieval: identifier detection is SHAPE-based (path separators, `::`, dots-without-spaces,
  camelCase/PascalCase/snake_case, issue-ID patterns) with a raw `split_whitespace().count()` word-count
  threshold for the ShortKeyword/NaturalLanguage split — the same whitespace-not-morphemes shape tg
  converged on independently, a near-sibling design confirming the fix direction, not just a coincidence.
- jgravelle, *You Don't Need an LLM to Route Agent Context: Regex Beats Classifiers by 45 Points*
  (dev.to, 2026-07-08) — a ~40-line regex heuristic classifier scored **94.3%** accuracy on an
  agent-context-routing task vs **48.6%** for a TF-IDF+logistic-regression learned classifier and
  **47.9%** for a TF-IDF-centroid classifier; the article's thesis is exactly this section's lesson
  generalized: "intent usually lives in the SHAPE of the request" (camelCase/snake_case tokens, leading
  question words, quoted literals), and bag-of-words/subword tokenization throws that shape away.
- A third production example surfaced independently during this same research pass (Mnemex, a code-search
  RAG router): a `symbol_lookup` vs `structural`/`semantic_search`/`exploratory` regex classifier keys
  on backtick-quoting and CamelCase/snake_case SHAPE (not a shared subword tokenizer) and bypasses vector
  search entirely for `symbol_lookup` — the same "route on shape, not on the indexing tokenizer" pattern.

---

## Quick-reference table

| Concept | tg file | One-line gotcha |
|---|---|---|
| rg exit code 2 (kept, not raised) | `ripgrep_backend.py`, grep `partial = result.returncode == 2` (was `:123-128,297,413`, now `:124-129,308,426`) | matches parsed -> `result_incomplete=True`, kept; nothing parsed / `>2` -> `BackendExecutionError` (RESOLVED #79, `a7c9431`) |
| PCRE2 detection | `ripgrep_backend.py`, grep `def supports_pcre2` (was `:53`, now `:54`) | real smoke test (`-P "a(?=b)" -V`), not a version-string guess |
| Binary NUL detection | `test_rg_parity_edges.py`, grep `ids=\["match", "no-match", "parse-error",` (was `:41-43,149`, now `:44,169`) | exit code must match rg's, not just stdout |
| `-u`/`-uu`/`-uuu` | `bootstrap.py`, grep `def _search_args_request_unrestricted_generated_scan` (was `:581`, now `:726`) | intercepted by the broad-root safety guard, not blind passthrough |
| `--` argv sentinel (MCP) | `mcp_server.py`, grep `command.extend\(\["--", pattern, path\]\)` (was `:1306`, now `:1375`) | blocks flag injection; list-argv alone only blocks shell injection |
| `--` argv sentinel (native rg passthrough) | `rg_passthrough.rs`, grep `fn ripgrep_operand_args` (was `:581-600`, now `:584-603`) | FIXED — `ripgrep_operand_args` inserts `--` before paths; 3 tests, no longer contiguous — grep `operand_args_insert_end_of_options_sentinel_before_paths` / `operand_args_no_sentinel_when_no_paths` / `operand_args_files_mode_omits_patterns_but_keeps_sentinel` (was `:788-826`, now `:1034`/`:1056`/`:1065`) |
| AST native vs sidecar | `ast_workflows.py`, grep `def _select_ast_backend_for_pattern` (was `:6655` in `main.py`, then hedged `:6737`, then `:6915`; `main.py`'s copy is now a forwarding shim onto `ast_workflows.py`); `ast_backend.py:505` (`is_available`, still current) | ast-grep WRAPPER is preferred whenever installed; native tree-sitter is a fallback-only path with no GPU gate anymore |
| Symbol-graph language registry | `lang_registry.py`, `repo_map.py` -- `grep -c "lang_registry.register_language(" src/tensor_grep/cli/repo_map.py` (was `:6004-6222` claiming 8 calls, now returns **10**) | 10/10 top-10 languages, all 10 parser-backed refs/callers + 0 foundational defs/imports-only (tier EMPTY) as of the Task 10E C++ final wave -- the "6 parser-backed + 4 foundational as of PR #927" split this row used to quote was the pre-campaign state (`_symbol_navigation_descriptor()` -- re-run it, do not trust this number); grammar-missing fails closed to `resolution_gaps`, never a silent empty result -- see section 2a |
| BM25 (real IDF) | `retrieval_bm25.py`, `reranker.py` | backs `tg search --rank`/`--bm25` only |
| Flat scorer (no IDF) | `repo_map.py`, grep `def _score_text_terms` (was `:7433`, now `:8189`) | backs `tg orient`/`tg agent` symbol ranking — known weak point |
| Personalized PageRank | `repo_map.py`, grep `def _personalized_reverse_import_pagerank` (was `:8418`, now `:9174` — re-derive with: grep -n '_personalized_reverse_import_pagerank' src/tensor_grep/cli/repo_map.py) | alpha=0.85, seeded, answers "relevant to this query" |
| Central-files centrality | `orient_capsule.py:694` (`_central_files_from_map`, still current) | composite in-degree + fan-in/symbol-density caps — `tg orient`'s deliberate choice over PageRank, answers "what's foundational" |
| Trigram index | `rust_core/src/index.rs:151,1609` (still current) | falls back to full scan when no literal is extractable (never drops matches) |
| GIL release | `rust_core/src/lib.rs:32,55` (still current) | `py.detach` (formerly `allow_threads`) around the mmap/scan closure |
| `gil_used` pin | `rust_core/src/lib.rs:353` (still current) | pinned `true`; free-threading needs a full green Linux CI run to re-attempt |
| LSP framing | `lsp_external_provider.py:91,173` (still current) | `Content-Length` capped at 64MB against a malicious/buggy server |
| Model2Vec/potion (SHIPPED) | `retrieval_dense.py`, `retrieval_fusion.py` | `tg search --semantic`; `potion-code-16M`; default-off-by-flag, not marketed default |
| ReDoS gate (`-w`/`-x`/`-C`/`--ltl`/UTF-8-fallback/native-failure/`--pcre2`) | `cpu_backend.py`, grep each: `if config.ltl:` (was `:355`, now `:391`), `needs_word_or_context_rust_routing` (was `:377-384`, now `:413-420`), UTF-8-fallback check (was `:438`, now `:490`), `--pcre2` check (was `:485`, now `:521`), native-failure check (was `:513`, now `:549`), all calling `def _fallback_pattern_is_provably_linear` (was `:280`, now `:316`) | CLOSED (audit #6/#16/#111) — every path that could reach Python's backtracking `re` either routes through the linear-time Rust engine first or fails closed with `BackendExecutionError` unless the pattern is `fixed_strings` (the only provably-linear shape); see §1a below |

### 1a. ReDoS-gate bypass on `-w`/`-x`/`-C`/`--ltl`/UTF-8-fallback/native-failure — CLOSED (audit #6/#16/#111)

`cpu_backend.py`'s ReDoS protection (comment above `def search`: *"Instead of using Python's
standard `re` module ... we route complex pure-python CPU requests to the native Rust `regex`
crate"*) now covers every path that can reach this backend:

- **`-w`/`-x`/`-C`/`-A`/`-B` (word/line/context flags)**: `needs_word_or_context_rust_routing`
  (grep that name in `cpu_backend.py` -- was `:377-384`, now `:413-420`) routes these to
  `_search_word_line_context_via_rust` (grep `def _search_word_line_context_via_rust` -- was
  `:800`, now `:852`), which resolves the match-SET via the linear-time Rust engine
  (`_rust_match_set`, grep `def _rust_match_set` -- was `:763`, now `:815`) and assembles context
  windows in pure Python — no backtracking regex ever runs on this path. On any Rust failure it
  raises `BackendExecutionError` (fail closed) instead of falling back to Python `re`.
- **`--ltl`**: `_search_ltl` (grep `def _search_ltl` -- was `:931`, now `:983`) resolves both LTL
  sub-expressions via the same `_rust_match_set` helper and fails closed identically.
- **`--pcre2`**: the generic Rust-exception handler's `pcre2` branch (grep `if getattr(config,
  "pcre2", False):` -- was `:485`, now `:521`) fails closed unconditionally
  (`BackendExecutionError`) regardless of *why* Rust could not service the request — CPUBackend
  has no real PCRE2 engine, only Python `re` as a backtracking approximation, so it never silently
  swaps to that engine.
- **UTF-8-fallback + native-runtime-failure (audit #111, closed 2026-07-10)**: two residual paths
  of the "simple pattern" route used to fall through to unbounded Python `re` — (1) the empty-Rust-
  result-on-a-non-UTF-8-file retry (`_RustUtf8DecodeMismatch`, handled at `except
  _RustUtf8DecodeMismatch as exc:` -- was `:438`, now `:474`), on the premise "Rust already ran the
  pattern in O(n), so it's ReDoS-safe"; and (2) the generic `except Exception` branch on a
  non-syntax Rust runtime fault (native panic / IO / version skew), which fell open "for
  robustness" (grep `except Exception as exc:` in the same method -- was `:513`, now `:508`). Both
  premises are the SAME one already refuted for `--pcre2` — a
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
  `CPUBackend._fallback_pattern_is_provably_linear` (grep `def
  _fallback_pattern_is_provably_linear` -- was `:280`, now `:316`), which admits ONLY `fixed_strings`
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

Facts here were verified by reading the cited files on **2026-07-08, tensor-grep v1.49.3**; §9's `tg
find` paragraph and §10 (query-shape classification, with its external citations re-verified live via
Exa on 2026-07-16) were added and verified against **v1.78.1**. **2026-07-22, v1.93.2**: re-grepped §3
by symbol against `repo_map.py` — every cited line number had drifted (file grew past 7000 lines) and
one claim was substantively wrong for the current code: `_symbol_rank_key`'s tuple no longer leads
with the flat score (it now leads with a `query_match_rank` bucket) and no longer tie-breaks on a
file-path string (it now tie-breaks on `symbol.name`). Also documented `_score_symbol` as a named
third scorer (it wasn't called out by name before) including its task #254/A7 word-boundary-bonus and
test-shadow-penalty heuristics, and softened the PR #302 incident framing to note it predates both.
`agent_capsule.py`'s `_primary_target_is_unrequested_marker_helper` citation also moved (was line 197,
now line 294). Code drifts; re-verify before treating a citation as current, especially line numbers.

**2026-07-23, v1.95.0 — full-document citation sweep, not just a date bump.** Every `file:line`
citation in this document was re-grepped against `origin/main`, not carried forward from the prior
pass, because the ~46 intervening releases (v1.49.3 → v1.95.0) had shifted nearly all of them: some
by 40-50 lines (the multi-language symbol-graph work — Go, Java, PHP, then C# — all landed inside
`repo_map.py` in that window, each insertion pushing everything below it down), one file by
hundreds (`mcp_server.py` grew from ~4500 to ~7700 lines on unrelated feature work), one by over
200 (`AGENTS.md`'s cited "Fail closed" bullet moved from line 220 to line 444). §3's own quick-
reference-table citation for `_score_text_terms` (`repo_map.py:7912`) had silently disagreed with
its own body-text citation for the same symbol (`repo_map.py:7001`) since at least the prior pass —
both are now the same, correct, current line. **SUPERSEDED (2026-08-14 retention pass):** `_score_text_terms` has drifted again since that pass -- `grep -n "^def _score_text_terms" src/tensor_grep/cli/repo_map.py` now yields `:8189`, not `:7912`; the quick-reference table row above already carries the current number. One correction was substantive, not just a line
number: §2's AST-routing description was **backwards**. `AstBackend.is_available()` no longer gates
on `torch_geometric`/CUDA — that gate was deleted in #542 (v1.65.0, 2026-07-12, 11 days before this
doc's own original "current" baseline was first written) — and the router has PREFERRED
`AstGrepWrapperBackend` whenever it's available ever since, using native `AstBackend` only as a
fallback (`main.py:6593`, `_select_ast_backend_for_pattern`); this document said the opposite. New
§2a documents the separate, third `lang_registry` symbol-graph tier (an unrelated code path to the
AstBackend/AstGrepWrapperBackend routing above), now covering 8 of the top-10 languages. One
process note for future re-verifiers doing a from-scratch sweep like this one: `origin/main` itself
moved mid-session during this pass (C# support, PR #726, merged while this refresh was already in
progress, confirmed via `git reflog show refs/remotes/origin/main`) — on an actively-drained
campaign repo, pin a specific commit SHA for the duration of a citation sweep rather than re-reading
the floating branch tip on every grep, or your own citations can end up internally inconsistent
with each other.

**2026-08-01, v1.101.27 — repaired a substantive error and stopped re-stamping line numbers.**
§2a's core claim ("only C and C++ remain unregistered, both deliberately deferred") was **FALSE**:
`grep -c "lang_registry.register_language(" repo_map.py` returns **10**, and
`_symbol_navigation_descriptor()` confirms all 10 of the top-10 languages are registered, split
exactly **5 parser-backed** (go, javascript, python, rust, typescript) + **5 foundational**
defs/imports-only (c, cpp, csharp, java, php) — there is no unregistered language and no third
tier. This repo's own memory records the tier split being stated wrong three separate times before
this one (a hand-count, a string grep that also matched `=None`, and an invented "unresolved"
tier), so §2a now derives the count from the product's own descriptor function rather than stating
a number by hand. Separately, and independent of that content error: nearly every `file:line`
citation touching `repo_map.py`, `mcp_server.py`, `cpu_backend.py`, `main.py`, and
`rg_passthrough.rs` (the fastest-growing files in the repo) had drifted since the 2026-07-23 sweep
above — including two citations that a still-more-recent partial touch-up had tagged "as of
2026-07-27" and which had ALREADY drifted a further 17 lines by this pass. That is the proof, not
just the theory, behind AGENTS.md's "re-stamping is not a fix; it is the defect on a slower clock"
rule: a dated hedge on a line number does not slow its rot, it only makes the rot look supervised.
Every citation this pass touched was converted from a bare line number to a `grep <symbol>`
instruction with a `was -> now` drift receipt kept beside it (matching the style
`tensor-grep-change-control` already uses), and no new "confirmed as of DATE" line-number claim was
introduced anywhere in this document. A handful of small, stable files (`base.py`, `lang_registry.py`,
`ast_backend.py`, `ast_wrapper_backend.py`, `orient_capsule.py`, `reranker.py`, `agent_capsule.py`,
`index.rs`, `lib.rs`, `lsp_external_provider.py`, `retrieval_dense.py`) were independently re-grepped
and found still accurate (within a line or two, well inside normal review noise) — those were left
as plain citations rather than churned into grep form for no reason, per the same "don't re-stamp
for its own sake" discipline applied in the other direction.

**SUPERSEDED (append-only, do not edit the paragraph above) -- 2026-08-04, Tasks 10B/10C.** Two
more languages moved from foundational to parser-backed since the v1.101.27 pass above: C# (Task
10B) then PHP (Task 10C), both in-file refs/callers only, same shape as Java's Task 10A landing.
`_symbol_navigation_descriptor()` now returns **8 parser-backed** (csharp, go, java, javascript,
php, python, rust, typescript) + **2 foundational** defs/imports-only (c, cpp) -- re-run the
one-liner below rather than trust either this line or the "5 parser-backed + 5 foundational" line
above; both are dated snapshots.

**SUPERSEDED AGAIN (append-only, do not edit either paragraph above) -- 2026-08-04, Task 10D.** C
moved from foundational to parser-backed (in-file refs/callers only, same shape as Java/C#/PHP's
own Task 10A/10B/10C landings). `_symbol_navigation_descriptor()` now returns **9 parser-backed**
(c, csharp, go, java, javascript, php, python, rust, typescript) + **1 foundational**
defs/imports-only (cpp) -- re-run the one-liner below rather than trust any of the three dated
snapshots above.

**SUPERSEDED FINAL (append-only, do not edit the paragraphs above) -- 2026-08-11, Task 10E (the
C++ final wave).** C++ (cpp) moved from foundational to parser-backed, completing the top-10
milestone: `_symbol_navigation_descriptor()` now returns **10 parser-backed** (c, cpp, csharp, go,
java, javascript, php, python, rust, typescript) + **0 foundational** defs/imports-only (the
foundational tier is EMPTY). Verified live on origin/main a6242bb (v1.110.14):
`parser-backed-refs-callers:c-cpp-csharp-go-java-javascript-php-python-rust-typescript
+foundational-defs-imports-only:`. Do not "fix" the dated snapshots above — they are accurate-as-
dated history; the CURRENT tier count is 10/0. Cross-checked by
`tests/unit/test_lang_registry.py` (10 registrations) and the re-verification one-liner below.

**2026-08-12 retention pass (append-only).** Four corrections, each verified against the tree at
base `568065a`: §2's wrapper-preference grep instructions, the quick-reference row, and the
re-verify command were re-pointed from `main.py` to `ast_workflows.py` (`main.py`'s
`_select_ast_backend_for_pattern` is now a forwarding shim; the "Prefer the ast-grep wrapper"
comment and the wrapper-first check live only in `ast_workflows.py`). §2a's
`provenance_when_missing` count updated 6 -> 8 field settings (rust now sets `regex-heuristic`
explicitly; count via the field-shape grep). §9's RRF entry notes the shipped `combine="max"`
default (the sum formula is the original formulation kept for byte-identity). §9's `TG_LATE_RERANK`
hold marked RETIRED 2026-08-05 (task F10) via the SUPERSEDED block above.

Re-verification commands:

```bash
# Confirm the version this doc was written against
git -C C:/dev/projects/tensor-grep log -1 --format=%H -- pyproject.toml
grep -n '^version' C:/dev/projects/tensor-grep/pyproject.toml

# Re-check the cited line numbers haven't drifted (grep, don't trust the number blindly)
grep -n "def supports_pcre2" C:/dev/projects/tensor-grep/src/tensor_grep/backends/ripgrep_backend.py
grep -n "_score_text_terms\|_score_symbol\|_symbol_rank_key\|_TEST_SHADOW_PENALTY" C:/dev/projects/tensor-grep/src/tensor_grep/cli/repo_map.py
grep -n "_personalized_reverse_import_pagerank\|_GRAPH_PAGERANK_SEED_FILE_LIMIT" C:/dev/projects/tensor-grep/src/tensor_grep/cli/repo_map.py
grep -n "_central_files_from_map" C:/dev/projects/tensor-grep/src/tensor_grep/cli/orient_capsule.py
grep -n "py.detach\|gil_used" C:/dev/projects/tensor-grep/rust_core/src/lib.rs
grep -n "Content-Length\|_MAX_LSP_MESSAGE_BYTES" C:/dev/projects/tensor-grep/src/tensor_grep/cli/lsp_external_provider.py

# Re-check the §2a symbol-graph registry hasn't silently regressed. Do NOT hand-count the
# languages or their tier split (this has been done wrong three times in this repo's history) --
# run the product's own descriptor and cross-check the registration count:
python -c "import sys;sys.path.insert(0,'src');from tensor_grep.cli import repo_map as r;print(r._symbol_navigation_descriptor())"
grep -c "lang_registry.register_language(" C:/dev/projects/tensor-grep/src/tensor_grep/cli/repo_map.py
# Expect: 10 registrations, split parser-backed-refs-callers:c-cpp-csharp-go-java-javascript-php-python-rust-typescript
# + foundational-defs-imports-only: (empty -- Task 10E, the final wave, promoted cpp too) --
# and confirm the MOST-FORGOTTEN seam is still wired for every one of the 10:
grep -n 'register_language(\|language_id="' C:/dev/projects/tensor-grep/src/tensor_grep/cli/repo_map.py
grep -n "_target_language_for_path\|_SUPPORTED_FILE_DEPENDENCY_LANGUAGES\|_language_coverage_gaps_for_universe" C:/dev/projects/tensor-grep/src/tensor_grep/cli/repo_map.py

# Re-check AstBackend.is_available() has not re-grown a GPU/torch_geometric gate, and that the
# ast-grep wrapper is still preferred over native in the real router function (main.py's copy is
# now a forwarding shim onto ast_workflows.py -- grep there, not in main.py)
grep -n "def is_available" -A 12 C:/dev/projects/tensor-grep/src/tensor_grep/backends/ast_backend.py
grep -n "Prefer the ast-grep wrapper\|def _select_ast_backend_for_pattern" C:/dev/projects/tensor-grep/src/tensor_grep/cli/ast_workflows.py

# Confirm the semantic-search dense+RRF leg is still present under its real module names
# (embed_backend.py / retrieval_hybrid.py never existed -- don't grep for those)
test -f C:/dev/projects/tensor-grep/src/tensor_grep/core/retrieval_dense.py -a -f C:/dev/projects/tensor-grep/src/tensor_grep/core/retrieval_fusion.py && echo "SHIPPED (section 9 current)" || echo "MISSING -- section 9 needs re-audit"

# Re-check the ReDoS gate is still closed (audit #6/#16/#111) — confirm the fixed_strings-only
# gate still exists and BOTH the UTF-8-fallback and native-failure branches call it before any
# Python `re` retry. It must admit ONLY fixed_strings; a static pattern-char allow-list is unsound.
grep -n "_fallback_pattern_is_provably_linear\|needs_word_or_context_rust_routing" C:/dev/projects/tensor-grep/src/tensor_grep/backends/cpu_backend.py

# Re-run the AST comparison benchmark before citing its numbers (measure cold-process, single-pass,
# and confirm which backend actually served the request — see profile-guided-byte-identical-
# optimization for why a warm/cached run of either binary understates its true per-call cost)
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
- The re-measured AST benchmark ratios in §2 (`tg 0.116s` vs `sg 0.151s`, etc.) are carried forward
  from the v1.49.3 baseline **unchanged** this pass — they were not re-run (this refresh was a
  citation/fact sweep, not a benchmark run), and given the routing-default correction documented
  above, what a fresh run of that script would actually be timing is now genuinely unclear without
  re-running it. Treat the numbers as historical, not current, until someone re-runs
  `benchmarks/run_ast_benchmarks.py` and confirms which backend served each side.
