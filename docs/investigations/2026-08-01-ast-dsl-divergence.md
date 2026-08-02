# #141 investigation: native `AstBackend` vs the ast-grep wrapper — DSL divergence

Date: 2026-08-01. Scope: investigation only (per the task brief) — no redesign. Two genuine
defects were found and locked in as tests (one xfail, documented below); everything else is
findings + a corrected map.

## Headline correction to AGENTS.md / the board

**There are three AST engines, not two.** AGENTS.md's "AST Native/Wrapper Two-Engine Divergence
(task #141)" section, and `docs/TASK_BOARD.md`'s P4 line, describe only the two **Python**
backends (`backends/ast_backend.py`'s tree-sitter `AstBackend` and
`backends/ast_wrapper_backend.py`'s `AstGrepWrapperBackend`, which shells out to the `ast-grep`
binary). Neither document mentions that `rust_core/src/backend_ast.rs` contains a **third,
independent `AstBackend`**, written in Rust, that uses the `ast_grep_core`/`ast_grep_language`
crates **directly as a library** — the same pattern-compilation code the `ast-grep` CLI itself is
built on — with no subprocess involved. Its `routing_reason` constant is literally `"ast-native"`
(`rust_core/src/routing.rs::RoutingDecision::ast`, `rust_core/src/backend_ast.rs`'s
`RewritePlan`/`BatchRewritePlan` constructors).

More importantly: **this Rust engine is what a compiled `tg run <pattern>` actually calls by
default.** `cli/bootstrap.py::main_entry` intercepts `run`/`scan`/`test`/`ast-info` before Typer
ever loads, and for `run` specifically:

```python
if argv[0] == "run":
    # ast-grep semantic options (--selector/--strictness/--stdin/--globs) are
    # served by the Python AST workflow. Routing them to the native binary would
    # bounce right back here (native spawns `python -m tensor_grep run ...`) and
    # ping-pong forever, so handle them directly in Python.
    if _run_requires_ast_workflow(argv[1:]):
        _run_ast_workflow_cli(argv)
        return
    native_binary_path = resolve_native_tg_binary()
    ...
    raise SystemExit(_run_native_tg_command(native_binary, argv))
```

`_run_requires_ast_workflow` (`cli/bootstrap.py`) only returns `True` when the user passes
`--selector`/`--strictness`/`--stdin`/`--globs`. **For the common case — `tg run <pattern> <path>
--lang <lang>` with no semantic options — the request never reaches Python's `Pipeline`,
`ast_workflows.py`, or either Python `AstBackend`/`AstGrepWrapperBackend` at all.** It goes
straight to the compiled Rust binary's own `AstBackend::search`, which compiles the pattern with
`ast_grep_core::Pattern::try_new` — full ast-grep pattern-DSL semantics (including metavariables:
`build_rust_function_signature_fallback` explicitly handles `$$$ARGS`), natively, no subprocess.

**Confirmed live** (this box, `tg 1.101.29`, ast-grep 0.42.1 installed):

```
$ tg run --pattern identifier --lang python --json sample.py
{"routing_backend":"AstBackend","routing_reason":"ast-native", ... "total_matches":4, ...}
```

Four matches — the lines where the literal token `identifier` appears (ast-grep code-pattern
semantics), even though ast-grep IS installed. That is because `tg run` never asked either Python
backend; the Rust engine served it directly and gave the *correct* ast-grep-DSL answer.

**Practical consequence for scoping #141's remaining work:** the two-Python-engine divergence
AGENTS.md worries about is real, but it is reachable only through a narrower slice of real usage
than the doc implies:
- `tg run --selector/--strictness/--stdin/--globs ...` (forced into the Python workflow by
  `_run_requires_ast_workflow`)
- `tg scan` when `_scan_requires_full_cli` trips (`cli/bootstrap.py::_scan_requires_full_cli`)
- `tg search --ast` (the plain search command — never delegates to native at all; see below)
- the MCP `tg_ast_search` tool

Reconciling the *Rust* engine's DSL with anything is not needed — it already speaks ast-grep's
DSL correctly, because it *is* ast-grep's own pattern library. The remaining #141 surface is
Python-only, and smaller than advertised.

## What each engine actually accepts

### 1. `AstGrepWrapperBackend` (`backends/ast_wrapper_backend.py`) — full ast-grep DSL, subprocess

Shells out to the real `ast-grep`/`sg` binary (`_get_binary_name`, probe-gated per `is_available`,
`_is_ast_grep_sg_binary`). Builds `sg run --json -p <pattern> [--lang] [--selector] [--strictness]
[--globs]... [--stdin] -- <paths>` (`_build_command`). Accepts ast-grep's full pattern language —
metavariables (`$NAME`, `$$$ARGS`), selectors, strictness — because it *is* ast-grep.

### 2. `AstBackend` (`backends/ast_backend.py`) — narrow native tree-sitter, in-process

> "A native, in-process structural-search backend: parses source code into an Abstract Syntax
> Tree (AST) using tree-sitter and matches tree-sitter queries directly against the parsed tree
> (or a cached node-type index for simple single-node-type patterns)."

Two pattern shapes only, gated by `_is_simple_node_type_pattern` (`re.fullmatch(r"[A-Za-z_]
[A-Za-z0-9_]*", pattern)`):
- a **bare identifier** → looked up as a **grammar node TYPE** in a cached index
  (`_build_node_type_index`: `node_type_index.setdefault(node.type, set()).add(line)`) — i.e. "find
  every AST node of this tree-sitter node kind," not "find this literal token."
- an **s-expression** starting with `(` → compiled as a real tree-sitter `Query` (`_get_query`) and
  matched via `query.captures`/`QueryCursor`.

It has **zero concept of ast-grep metavariables**. Only 5 languages have a real parser branch in
`_get_parser`: `python`, `javascript`, `typescript`, `tsx`, `rust` (`_NATIVE_AST_LANGUAGES`) — even
though `_SUPPORTED_AST_LANGUAGES` advertises 26 (java, csharp, php, c, cpp, go, ... included).

### 3. Rust `AstBackend` (`rust_core/src/backend_ast.rs`) — full ast-grep DSL, in-process

`compile_ast_pattern` calls `ast_grep_core::Pattern::try_new(pattern, language)` directly — the
same pattern compiler the `sg` CLI uses. Supports metavariables (`build_rust_function_signature_fallback`
explicitly special-cases `$$$ARGS`), plus two tg-specific extensions layered on top: a JS/TS
"bare method body" contextual-pattern rewrite (`build_js_ts_method_contextual_pattern`) and a
Rust function-signature structural fallback for patterns like `fn foo($$$ARGS)` that don't parse
as a bare expression pattern. Supports python/javascript/typescript/rust (`build_ast_search_types`);
everything else is `anyhow::bail!("Unsupported language type filter")`.

### Where a pattern silently means something different

A **bare word that also happens to be a real tree-sitter grammar node-type name** — `identifier`,
`string`, `call`, `comment`, `block`, `parameters`, `assignment`, and dozens more, one set per
grammar — is the exact case where the three engines disagree *without either erroring*:

| engine | reading of `identifier` | result on the fixture below |
|---|---|---|
| ast-grep wrapper / Rust `AstBackend` (ast_grep_core) | literal code-pattern match | 4 (lines containing the token) |
| Python `AstBackend` | "every node of type `identifier`" | 7 (also matches `total`, `Foo`, `bar`, `self` — none of which contain the substring `identifier` at all) |

Fixture (`sample.py`):
```python
def compute(identifier):
    total = identifier + 1
    return total


class Foo:
    def bar(self):
        identifier = 5
        return identifier
```

Real output, Python `AstBackend.search(file, "identifier", SearchConfig(ast=True, lang="python"))`
(no mocks, real tree-sitter, verified this session):
```
routing_backend: AstBackend
routing_reason: ast_structural_index
total_matches: 7
 line 1 'def compute(identifier):'
 line 2 'total = identifier + 1'
 line 3 'return total'
 line 6 'class Foo:'
 line 7 'def bar(self):'
 line 8 'identifier = 5'
 line 9 'return identifier'
```
vs. real `tg run --pattern identifier --lang python --json sample.py` (Rust engine, this session):
`total_matches: 4`, lines 1, 2, 8, 9 only.

Both are non-empty, well-formed `SearchResult`s. Nothing distinguishes "correct ast-grep-DSL
answer" from "correct-for-a-different-DSL answer" at the point of use.

## The guard map

### Verified sites (each called directly, not inferred)

1. **`Pipeline._supports_native_ast_pattern`** (`core/pipeline.py`) — the shared classifier: bare
   identifier or `(`-prefixed string → native-shaped; a pattern containing `$` (or anything else)
   → not. Confirmed unchanged.
2. **`Pipeline.__init__`'s AST branch** (`core/pipeline.py`, guarded by `config.ast` before the
   NLP/count/GPU branches) — when the pattern is not native-shaped and the wrapper is unavailable,
   raises `ConfigurationError` via `_raise_explicit_ast_configuration_error`. Confirmed unchanged,
   still exercised by `tests/unit/test_pipeline.py::test_should_reject_ast_grep_metavariable_pattern_when_wrapper_is_unavailable`
   (reran it this session: 3/3 metavariable-marked tests pass in `test_ast_workflows.py`, and the
   `test_pipeline.py` sibling passes too).
3. **`_select_ast_backend_for_pattern`** in `cli/ast_workflows.py` (the classifier `tg run`'s
   Python fallback and `tg run --rewrite`/`--apply` use) — computes `pattern_kind` from
   `ast_prefer_native`, pattern shape, `is_native_ast_language(lang)`, **and**
   `requires_ast_grep_wrapper` (`ast_selector`/`ast_strictness`/`ast_stdin`/`glob`); raises
   `ConfigurationError` when `pattern_kind == "wrapper"` and the wrapper is unavailable.
4. **`tg_ast_search`** (`cli/mcp_server.py`) — wraps `Pipeline(...)` construction in
   `try/except ConfigurationError` and converts it to a structured `{"error": {"code":
   "unavailable", ...}}` JSON shape. **Re-verified this session**: `tg_ast_search` builds `config =
   SearchConfig(ast=True, lang=lang, no_messages=True)` with **no `query_pattern` field set at
   all**. Since `_supports_native_ast_pattern` reads `config.query_pattern` and treats an
   empty/missing pattern as non-native, this construction step is *unconditionally*
   non-native-shaped — every `tg_ast_search` call requires the wrapper, regardless of the caller's
   actual `pattern` argument. Native `AstBackend` is structurally unreachable through this tool.
   AGENTS.md's claim here is accurate and still true.

### A previously-undocumented FIFTH site (not counted in AGENTS.md's "3"/4-item list)

5. **`AstBackend.search`'s own `_get_query` exception handler** (`backends/ast_backend.py`) — when
   a bare-identifier pattern misses the node-type index *and* fails to compile as a tree-sitter
   query, it raises `BackendExecutionError` naming the ast-grep dependency instead of a generic
   "invalid pattern" message (the `#144 hotfix / #141 DSL divergence` comment). This is real,
   tested (`test_bare_identifier_ast_grep_pattern_fails_closed_with_ast_dependency_message`,
   re-ran green this session), and is the *only* guard that fires **inside** the native backend
   itself rather than at a routing/classification layer above it. AGENTS.md's "three verified
   sites" sentence undercounts by at least this one (and arguably by the Rust `main_entry` split
   below), which is itself a small instance of the "cite the SYMBOL and re-verify the count, don't
   reason A-covers-B" trap this repo has been bitten by before.

### A SIXTH, drifted, near-duplicate copy (main.py) — not mentioned anywhere

`cli/main.py` defines its **own** `_select_ast_backend_for_pattern` (used by `tg scan`'s per-rule
engine selection, via `_run_ast_scan_payload`) — textually similar to #3 above but **not the same
function** (different module, no shared import). Comparing the two:

- `ast_workflows.py`'s copy: `pattern_kind` requires `ast_prefer_native AND not
  requires_ast_grep_wrapper AND supports_native_pattern AND is_native_ast_language(lang)`, where
  `requires_ast_grep_wrapper = bool(ast_selector or ast_strictness or ast_stdin or glob)`.
- `main.py`'s copy: `pattern_kind` requires `ast_prefer_native AND supports_native_pattern AND
  is_native_ast_language(lang)` — **no `requires_ast_grep_wrapper` check at all.**

`_run_ast_scan_payload` builds its base `cfg` with `ast_prefer_native=True` and `glob=list
(scan_globs or []) or None`, and `rule_cfg = replace(cfg, lang=rule["language"])` carries that
`glob` through unchanged into every per-rule `_select_ast_backend_for_pattern` call. So a `tg scan`
run with a `--globs`-equivalent scan filter and a native-shaped rule pattern, on a box where
ast-grep is unavailable, is classified `"wrapper"` (correctly refuses, `ConfigurationError`) by the
`ast_workflows.py` copy but would be classified `"native"` (routes to `AstBackend`, no refusal) by
`main.py`'s copy — **if `main.py`'s copy were the one on that code path**, which it is, for `tg
scan`. In practice this doesn't currently manifest as a wrong *per-file* result (file-level glob
filtering already happened upstream via `DirectoryScanner` before any per-rule backend is
selected), so I am not claiming a live wrong-answer here — but it is a real, untested, drifted
duplication: nothing asserts the two copies agree, no test in `tests/unit/test_cli_modes.py`,
`test_sarif_scan_integration.py`, `test_apply_policy.py`, or `test_suppression_improvements.py`
exercises `main.py`'s copy with a metavariable/selector/strictness/stdin pattern and a
wrapper-unavailable environment the way `test_ast_workflows.py`'s sibling test does for the
`ast_workflows.py` copy. A future field added to `requires_ast_grep_wrapper` in one copy (e.g. a
new ast-grep-only CLI option) will silently NOT protect the other, and nothing will fail to say so.

### The real, unguarded, silently-wrong path

**`tg search --ast`** (the plain `search_command` in `cli/main.py`) builds `SearchConfig(...,
ast=ast, lang=lang, ...)` with **no `ast_prefer_native` field set** (confirmed: `grep ast=|lang=`
around the config construction shows no `ast_prefer_native=`) and passes it straight to
`Pipeline(force_cpu=..., config=config)` — it does **not** go through `_select_ast_backend_for_pattern`
at all. Reading `Pipeline.__init__`'s AST branch again:

```python
elif config and config.ast:
    ...
    supports_native_ast_pattern = self._supports_native_ast_pattern(config)
    if (config.ast_prefer_native and supports_native_ast_pattern and ast_backend.is_available()):
        self.backend = ast_backend                      # (1) opt-in native
    elif ast_wrapper.is_available():
        self.backend = ast_wrapper                       # (2) prefer wrapper
    elif supports_native_ast_pattern and ast_backend.is_available():
        self.backend = ast_backend                       # (3) UNCONDITIONAL fallback
        selected_backend_reason = "ast_backend_available_fallback"
    elif not supports_native_ast_pattern:
        self._raise_explicit_ast_configuration_error(...)
    else:
        self._raise_explicit_ast_configuration_error("no AST backend is available")
```

Branch (3) has **no `ast_prefer_native` requirement and no `is_native_ast_language(lang)` check**
— it fires whenever the wrapper is unavailable and the pattern is bare-identifier/`(`-shaped,
*regardless of `ast_prefer_native` and regardless of language*. So `tg search --ast --lang python
identifier` on a box without ast-grep silently returns the **7-match, node-type-collision** answer
shown above — the wrong DSL's answer, framed as a normal successful result. Unlike the two
`_select_ast_backend_for_pattern` copies (which both special-case exactly this by checking
`is_native_ast_language`), `search_command`'s direct `Pipeline()` construction has no equivalent
gate at all, because it never asks the question.

**Nothing makes this swap visible.** Every *other* silent backend swap in `Pipeline.__init__`
(`gpu_heuristic_torch_import_error_fallback`, `nlp_backend_unavailable_fallback`, the two GPU
heuristic branches) sets `self.fallback_reason` and calls `logger.warning(...)` +
`warnings.warn(...)`. The AST branch's `"ast_backend_available_fallback"` reason is recorded only
in `selected_backend_reason` (→ surfaced as `routing_reason` in `--json` output, but **not** in
the default `--format rg` text output, and not through the `fallback_reason`/warning mechanism the
rest of this file uses for exactly this class of problem). A user diffing `tg search --ast`
output between two machines (one with ast-grep, one without) gets silently different answers with
no error and no visible warning either way.

**A second, narrower, but real crash-class defect** in the same neighborhood: branch (3) doesn't
check `is_native_ast_language(config.lang)` either, so for a `_SUPPORTED_AST_LANGUAGES` entry
`_get_parser` cannot actually construct (`java`, `csharp`, `php`, `c`, `cpp`, ...), `AstBackend`
gets selected anyway, and `_get_parser` raises a **bare `RuntimeError`** — not
`BackendExecutionError` — which `search()` never catches (only the later `_get_query()` call is
wrapped). Confirmed live this session, real tree-sitter, no mocks:

```
$ (AstBackend().search("Sample.java", "someIdentifier", SearchConfig(ast=True, ast_prefer_native=True, lang="java")))
RuntimeError: Failed to load tree-sitter grammar for java: Language 'java' is supported by the
ast-grep wrapper but not by the native AstBackend.
```

This is a Backend Fail-Closed Contract violation (a real failure must raise
`BackendExecutionError`, per `backends/base.py` and AGENTS.md's own "Backend Fail-Closed Contract"
section) — a loud crash with a confusing traceback, not a silently-wrong answer, so it is a
secondary finding rather than the headline, but it is real and reachable via `tg search --ast
--lang java <bare-identifier-pattern>` whenever the wrapper is unavailable.

## User-visible failure table

All commands run for real this session (`tg 1.101.29`, this worktree, Windows). Where "ast-grep
unavailable" is required to trigger a path and I did not literally uninstall the system binary, I
drove the exact vulnerable code (`AstBackend.search` / `Pipeline.__init__`) directly with the
wrapper's availability mocked or with `AstGrepWrapperBackend` genuinely not resolvable in a bare
venv — noted per row.

| pattern / command | what a user actually gets today | category |
|---|---|---|
| `tg run --pattern identifier --lang python sample.py` (ast-grep installed) | `routing_backend: AstBackend, routing_reason: ast-native` (the **Rust** engine), 4 matches — correct ast-grep-DSL answer, via `ast_grep_core::Pattern`, no subprocess | correct, real output above |
| `tg run --pattern '$NAME' ...` (no semantic options) | delegates to the Rust binary; `Pattern::try_new` compiles the metavariable natively — no Python divergence question even arises | correct by construction |
| `tg run --pattern identifier --selector ... ` (forces Python fallback) | `_select_ast_backend_for_pattern` (ast_workflows.py) prefers the wrapper when available; refuses closed (`ConfigurationError`, "ast-grep wrapper backend is required...") when wrapper absent and `is_native_ast_language` is true but... (see next row for the bare-identifier case) | fail-closed, tested |
| `tg search --ast --lang python identifier <file>` (wrapper unavailable — proven directly via `AstBackend.search`, no `Pipeline`/wrapper mock needed for the semantic point) | **silent wrong-but-plausible result**: 7 matches (structural node-type reading) vs the 4 an ast-grep-DSL reading would give; no error, no warning, no `fallback_reason` | **silently wrong — the headline finding** |
| `tg search --ast --lang java someIdentifier <file>` (wrapper unavailable, proven directly via `AstBackend.search`) | bare `RuntimeError: Failed to load tree-sitter grammar for java: ...` — uncaught, not `BackendExecutionError` | loud crash, contract violation, secondary finding |
| `tg run --pattern calculateTotal --lang js ...` (wrapper unavailable, existing test) | clean `BackendExecutionError`: "Explicit AST search requires AST dependencies: the ast-grep wrapper backend is required for pattern 'calculateTotal' ..." | fail-closed, tested (pre-existing) |
| MCP `tg_ast_search(pattern="identifier", lang="python")` | always requires the wrapper (native structurally unreachable, confirmed by reading the current `SearchConfig(...)` construction — no `query_pattern` threaded); refuses closed with `{"error": {"code": "unavailable", ...}}` when wrapper absent | fail-closed, confirmed accurate |

## The guard-count correction, stated plainly

AGENTS.md says "already fail-closed, at three verified sites" and then enumerates **four** (the
classifier is not itself a guard; the two `ConfigurationError` raises are guards; the MCP wrapper
is a catch site for the same underlying raise). Counting by "a site that can independently decide
to refuse or not":
- `Pipeline.__init__`'s AST branch: **guards, but has an unconditional native fallback branch (3)
  above with no `is_native_ast_language` check — this is the gap.**
- `ast_workflows.py::_select_ast_backend_for_pattern`: **guards correctly** (checks language +
  wrapper-required-options).
- `main.py::_select_ast_backend_for_pattern` (undocumented sixth site): **guards, but is a
  drifted, less-strict duplicate with no parity test against its sibling.**
- `AstBackend.search`'s own exception handler: **guards correctly**, but only for patterns that
  fail the node-type-index lookup — a pattern that *succeeds* against the index (any grammar
  node-type name) never reaches it.
- `tg_ast_search` (MCP): **guards correctly, unconditionally** (never threads `query_pattern`, so
  native is unreachable regardless).

Net: the *documented* claim ("already fail-closed at N sites") is true for metavariable-shaped
(`$`-containing) patterns specifically — that half is solid and re-verified. It is **not** true in
general for bare-word patterns, where a coincidence with a tree-sitter grammar node-type name
produces a silently wrong (not refused) result through `tg search --ast` and (independently)
through `main.py`'s scan-path classifier lacking the `is_native_ast_language` gate its sibling has.

## Verdict on demand-gating

**Keep demand-gating the full DSL-reconciliation project (native metavariable support / making
native the CPU-perf default) — that verdict is still correct and, if anything, weaker now that the
Rust engine already gives most real `tg run` traffic the correct ast-grep-DSL answer without
needing Python parity at all.** Nothing in this investigation found a concrete consumer who needs
native-tree-sitter *performance* for a pattern the wrapper already serves correctly — the original
gating condition in AGENTS.md.

**But two narrower, cheap fixes are NOT demand-gated design work — they are closed-scope bugs**,
now regression-tested (see below) and left for a follow-up PR:
1. `Pipeline.__init__`'s branch (3) (`"ast_backend_available_fallback"`) should either check
   `is_native_ast_language(config.lang)` (closing the RuntimeError crash for non-native
   "supported" languages) and/or set `fallback_reason` + `warnings.warn` the same way every other
   fallback branch in the same function already does (closing the silent-DSL-swap visibility gap).
   This does not require reconciling the DSLs — it requires the existing fallback to announce
   itself, the same bar every sibling branch already clears.
2. `main.py`'s duplicate `_select_ast_backend_for_pattern` should either import
   `ast_workflows.py`'s version or gain the same `requires_ast_grep_wrapper` check plus a parity
   test asserting the two copies classify identically for a shared table of inputs.

What would change the "keep demand-gating" verdict: a real user report of `tg run` being slow on a
CPU-only box for a pattern the wrapper already serves correctly (the original gating condition),
or evidence that `tg search --ast`'s silent divergence has actually produced a wrong agent
decision in the wild (as opposed to being provable-but-not-yet-observed, which is this
investigation's status).

## Tests added this session

- `tests/unit/test_ast_backend.py::TestAstBackend::test_grammar_node_type_shaped_bare_word_silently_diverges_from_ast_grep_semantics`
  — passes today; locks in the concrete 7-vs-4 divergence as a permanent regression fixture (real
  tree-sitter, no mocks) so nobody "fixes" the node-type-index behavior without noticing it changes
  this observable case.
- `tests/unit/test_ast_backend.py::TestAstBackend::test_unsupported_but_documented_language_raises_backend_execution_error_not_bare_runtime_error`
  — **xfail(strict=True)**, RED today (confirmed: raises bare `RuntimeError`, not caught by
  `pytest.raises(BackendExecutionError)`, exactly as documented). Proves the crash-class defect;
  intentionally not fixed in this pass.
- `tests/unit/test_pipeline.py::TestPipeline::test_native_ast_fallback_for_a_grammar_node_type_pattern_should_surface_a_dsl_divergence_warning`
  — **xfail(strict=True)**, RED today (`pipeline.fallback_reason is None` after the fallback
  fires). Proves the visibility-gap defect; intentionally not fixed in this pass.

All three ran green/xfail as expected this session via
`uv run --no-sync --with pytest --with pytest-mock --with tree-sitter --with tree-sitter-python
--with tree-sitter-javascript --with tree-sitter-typescript --with tree-sitter-rust --with
tree-sitter-java pytest tests/unit/test_ast_backend.py tests/unit/test_pipeline.py -q` (this
worktree's own venv did not exist yet and a bare `uv run` would have re-synced/possibly rebuilt
the native extension, which is disallowed on this shared box; `--with` installs only the
pure-Python/prebuilt-wheel test deps needed, no compilation). Full run: `test_ast_backend.py`
36 passed + 1 xfailed (was 35 passed before); `test_pipeline.py` 40 passed + 1 xfailed (was 39
passed before). Both files pass CI's pinned `ruff check` and `ruff format --check --preview`
(`ruff==0.15.20`).

## Things AGENTS.md gets right (re-verified, not just re-cited)

- The metavariable (`$NAME`/`$$$ARGS`) fail-closed guard genuinely works, at both classifier
  copies, confirmed by re-running the cited tests this session (all pass).
- `tg_ast_search`'s claim ("native `AstBackend` is structurally unreachable through the MCP tool")
  is accurate against current code — re-verified by reading the current `SearchConfig(...)`
  construction, which still never threads `query_pattern`.
- "The native-shaped-pattern fallback is deliberate, not a bug" — still true in spirit; the
  fallback existing is correct (a CPU-only box without `ast-grep` should get *some* AST
  capability). What's missing is not the fallback's existence but its **visibility** and its
  **language gate**, per the two closed-scope fixes above.
