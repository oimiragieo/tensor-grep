---
name: tensor-grep-add-language
description: Use when adding a new language to tensor-grep's tree-sitter symbol graph (defs/refs/callers/blast-radius, `tg source`, `tg imports`) — registering a `LanguageSpec` in `lang_registry.py`, writing a new `src/tensor_grep/cli/lang_<x>.py` extractor module, debugging a C/C++ declarator-walker mis-kind, or verifying a banked "the fix is obviously X" hypothesis against a live parse tree before writing declarator-shape logic. Triggers and keywords include add a language, add <language> to tensor-grep, new grammar, tree-sitter grammar, lang_registry, register_language, new lang_<x>.py module, symbol-graph language, onboard a language, "tg doesn't find symbols in <language>", `_target_language_for_path`, grammar-missing provenance, C/C++ symbol graph, function-pointer variable mis-kinded as function, declarator shape.
---

# tensor-grep: adding a language to the symbol graph

The registration checklist for extending tensor-grep's tree-sitter symbol graph
(`defs`/`refs`/`callers`/`blast-radius`/`tg source`/`tg imports`) to a new language.
Ground-truthed directly against `src/tensor_grep/cli/lang_registry.py` (full file),
`lang_go.py`, `lang_php.py`, and `lang_csharp.py` (the three shipped module-shaped
languages), and `src/tensor_grep/cli/repo_map.py`'s real dispatch sites — not from
memory or the session ledger alone. Sibling of `tensor-grep-architecture-contract`,
scoped to one subsystem (the symbol-graph tier), not the front door / routing /
backend contract.

## When to use this skill vs. a sibling

| You are about to… | Use |
|---|---|
| Add/extend the symbol graph for a language (this skill) | **you are here** |
| Understand the front door, routing, or the Backend Fail-Closed Contract for search itself | `tensor-grep-architecture-contract` |
| Land the change safely (registration gates, one-merge-per-tick, dogfood) | `tensor-grep-change-control` |
| Adversarially check an AI-drafted add-language plan against real code before dispatch | `verify-plan-against-code` (global skill) |
| Debug a live "no symbols found" / wrong-result report for an already-supported language | `tensor-grep-debugging-playbook` |
| Find a hot-path lever and prove an optimization byte-identical (not language-specific) | `profile-guided-byte-identical-optimization` (global skill) |
| Drain several language PRs that all touch `test_lang_registry.py` / `uv.lock` / the pyproject `ast` extra | `tensor-grep-change-control`'s Campaign Orchestration cross-ref (AGENTS.md A22) |
| Use `tg` as a consumer (search/orient/callers flags) | `code-search-and-retrieval-reference` |

## Current status (verified against tg v1.110.14, `origin/main` @ `a6242bb`)

`repo_map.py` currently carries **10** `lang_registry.register_language(...)` call sites
(`grep -n "register_language(" src/tensor_grep/cli/repo_map.py`): `python`, `javascript`,
`typescript`, `rust` (the original four, inline in `repo_map.py`), plus `go`, `java`,
`php`, `csharp`, `c`, and `cpp` — confirmed via `language_id=` greps and
`tests/unit/test_lang_registry.py::test_language_registry_has_exactly_the_stage2_languages`'s
literal set-pin (which now includes `"c"` and `"cpp"`). **All top-10 languages are now
registered — the "C/C++ deferred" framing below this line in earlier passes of this skill is
STALE; C landed via PR #731 (v1.97.0) and C++ via PR #732 (v1.98.0), each a self-contained
module (`lang_c.py`/`lang_cpp.py`, mirroring `lang_go.py`'s shape), both at the same
foundational tier as Java/PHP/C# (defs/imports only, no `references_and_calls`).** A
follow-up C fix (#736, v1.98.2) corrected a file-scope function-pointer-variable mis-kind -
see B5's declarator-shape addendum below before writing similar C/C++ declarator-walking
logic. **SUPERSEDED 2026-08-04 by Task 10E (C++, the final wave of the top-10 language-support
campaign):** the "foundational tier" claim in the paragraph above is now STALE for every language
named in it. Java (10A), C# (10B), PHP (10C), C (10D), and now C++ (10E) all carry a real
`references_and_calls` extractor; `repo_map._symbol_navigation_descriptor()` reports
**10 parser-backed / 0 foundational** — the foundational tier is EMPTY. `lang_cpp.py`'s new
`cpp_references_and_calls` extends C's bare-identifier-call confirmation with three C++-only
shapes (qualified calls `Foo::bar()`, explicit `this->method()`, and `new Widget()` as a
`ref_kind="constructor"` reference) but DELIBERATELY does not attempt Java/C#/PHP-style
general receiver-type confirmation (`w.method()`, `p->method()`) — C++'s real inheritance and
`auto` make that walk unsound for the common case; see `lang_cpp.py`'s own "TASK 10E CALL/
ACCESS NODE SHAPES" / "RESOLUTION CONFIDENCE" docstring block for the full reasoning. Re-run
the one-liner rather than trust this paragraph either.

**SUPERSEDED (append-only, do not edit the paragraph above) - 2026-08-11, Task 10E
final wave:** C and C++ are now PARSER-BACKED too; `_symbol_navigation_descriptor()` returns
**10 parser-backed** (c, cpp, csharp, go, java, javascript, php, python, rust, typescript)
and the **foundational tier is EMPTY**. The "same foundational tier as Java/PHP/C#" claim
above is accurate-as-dated (v1.98.x) history; the tier split has since moved to 10/0 and is
pinned by `tests/unit/test_lang_registry.py`. **Re-run the grep above before trusting any
"N of top-10" count** - it is a snapshot, not a promise; this count has changed on every pass
of this skill so far.

*(2026-08-12 maintenance note: the two SUPERSEDED blocks above were reordered on this date to
restore append-only chronology — the 2026-08-11 entry had been placed ABOVE the 2026-08-04
entry despite the append-only (newest-last) instruction. Both blocks describe the same 10/0
terminal state; all dated content was preserved verbatim, only the order changed: oldest
first, newest last.)*

The tiered language model (unchanged shape, re-verify the coverage numbers):

| Tier | Scope | Mechanism | Coverage |
|---|---|---|---|
| Text search | any file | rg passthrough (bootstrap front door) | universal |
| Structural scan/rewrite | many languages | ast-grep, which `tg` wraps (`tg ast-info`, `tg run`) | ~26 langs (ast-grep's own list) |
| **Symbol graph (this skill)** | tree-sitter grammars in `lang_registry` | `defs`/`refs`/`callers`/`blast-radius`/`tg source`/`tg imports` | **10 of 10 top-10 languages live on `main`** (Python/JS/TS/Java/C#/C++/C/Go/Rust/PHP) |

Positioning: tg = rg (text) + ast-grep (structural) + this symbol/retrieval/capsule layer —
"not faster grep" (mirrors `tensor-grep-architecture-contract`'s moat framing). Top-10
ranking (TIOBE Jul-2026 + Stack Overflow 2025 + GitHub Octoverse 2025 consensus): Python,
JavaScript, TypeScript, Java, C#, C++, C, Go, Rust, PHP.

## B1 — the pattern: `register_language` + a `lang_<x>.py` module

**The current, correct pattern for a NEW language is a self-contained
`src/tensor_grep/cli/lang_<x>.py` module** (clone `lang_go.py`) that ends in a
`lang_registry.register_language(LanguageSpec(...))` call from `repo_map.py`. This is
**not** the inline `_rust_*` / `_parser_for_source_suffix` machinery still visible in
`repo_map.py` for Rust and Python — that style predates the registry (Stage 0's pure-parity
refactor wrapped it, it did not replace it). Java is the one exception that used
inline-in-`repo_map.py` (`_java_imports_and_symbols` etc. — `grep -n "^def _java_imports_and_symbols" src/tensor_grep/cli/repo_map.py`, was `:4782`, now `:4783`) and still
registers through `lang_registry` — both shapes are contract-consistent, but **the module
shape is what Go, PHP, and C# (the three most recent additions) all converged on**, and is
what `lang_go.py`'s own docstring recommends: it keeps `repo_map.py` from growing further.

One-directional import rule (stated in both `lang_registry.py`'s module docstring — `grep -n
"never the reverse" src/tensor_grep/cli/lang_registry.py`, was `:10-12`, now `:10-11` — and
`lang_go.py:9-15`):
`repo_map.py` → `lang_<x>.py`, never the reverse. A helper the new module needs that
`repo_map.py` already has must be **duplicated locally** (see `lang_go.py:44-87`'s (was cited
`:37-87`, re-grep `grep -n "Duplicated tiny helpers"` to relocate the block if this drifts again)
byte-identical-to-`repo_map.py` tiny helpers), not imported — importing back creates a
cycle.

`LanguageSpec` (`grep -n "class LanguageSpec" src/tensor_grep/cli/lang_registry.py` — was
`:67-111`, now `:72-119`; frozen dataclass) is the single contract. Fields
worth knowing before writing one:

| Field | Status | Note |
|---|---|---|
| `language_id`, `suffixes` | wired, required | e.g. `"go"`, `frozenset({".go"})` |
| `parser_for_path` | wired | returns the parser or `None` if the grammar package isn't installed — the fail-closed gate |
| `provenance_when_missing` | wired, **default `"regex-heuristic"`** | a language with no regex fallback (every language after the original four) **must override this to `"grammar-missing"`** — see B3 |
| `extract_imports_and_symbols`, `references_and_calls`, `provider_alias_calls`, `file_imports_symbol_from_definition`, `import_update_target` | wired | any of these left `None` = an honestly-deferred capability, not a bug — see PHP's precedent in B3 |
| `prime_repo_context` | wired | `None` if the language has no per-repo workspace state to prime (tsconfig/`go.mod`-style) |
| `def_node_kinds`, `classify_ref_kind` | **doc-only in Stage 0** | no dispatch seam reads these yet — populate for self-documentation, do not assume they are wired |

`register_language()` is idempotent (`grep -n "def register_language"
src/tensor_grep/cli/lang_registry.py` — was `:118-128`, now `:126-136`) — re-registering the same
`language_id` replaces the entry and re-derives every suffix pointer, so a stale mapping
never survives a reload. `LANGUAGE_REGISTRY` starts **empty** (`grep -n "^LANGUAGE_REGISTRY"
src/tensor_grep/cli/lang_registry.py` — was `:114`, now `:122`) until whatever module
calls `register_language(...)` is imported — a bare `import lang_registry` with no
`import repo_map` gets an empty dict (see "Fast self-check" below).

## B2 — the critical seams (miss one = a silent half-integration)

Enumerate every seam `lang_go.py` touches and hit **all** of them. These are re-verified
`repo_map.py` locations on v1.96.1-pending (re-grepped fresh after PR #728 inserted a 16-line
go/php/csharp dispatch block inside `_imports_with_lines_for_path`, shifting every seam below
it by +16 — except `build_file_imports`, which shifted +41, because a 12-line frozenset
addition and a 13-line `_resolve_raw_import_entry` branch both land between it and seam 6; see
B2's worked example below) — re-grep the symbol before trusting the line number on a later
version (`main.py`/`repo_map.py` churn every release):

| # | Seam | Location | Feeds | Miss-it symptom |
|---|---|---|---|---|
| 1 | `lang_registry.register_language(LanguageSpec(...))` | `repo_map.py` (**10** call sites as of this pass — was reported "8" in an earlier pass of this table, already stale then; re-derive, don't trust either number: `grep -c "register_language(" src/tensor_grep/cli/repo_map.py`) | wiring the suffix at all | new suffix never resolves; silently excluded everywhere |
| 2 | `_imports_and_symbols_for_path` | `repo_map.py` — `grep -n "^def _imports_and_symbols_for_path" src/tensor_grep/cli/repo_map.py` (was `:6626`, now `:6627`) | symbol/def extraction dispatch | new language absent from defs/symbols |
| 3 | `_imports_with_lines_for_path` | `repo_map.py` — `grep -n "^def _imports_with_lines_for_path" src/tensor_grep/cli/repo_map.py` (was `:6831`, now `:6832`) | `tg imports` (line-numbered import entries) | `tg imports` silently empty even though defs exist |
| 4 | `build_symbol_source_from_map` | `repo_map.py` — `grep -n "^def build_symbol_source_from_map" src/tensor_grep/cli/repo_map.py` (was `:16309`, now `:16326`) | `tg source` | `tg source` returns nothing for a real symbol |
| 5a | **`_target_language_for_path` — MOST-FORGOTTEN** | `repo_map.py` — `grep -n "^def _target_language_for_path" src/tensor_grep/cli/repo_map.py` (was `:7850`, now `:7867`) | `tg agent` capsule's `primary_target_language` / confidence gate | a target file in the new language does not filter a mismatched-language validation suggestion |
| 5b | **`_provider_language_for_path` — a SIBLING seam, easy to miss because 5a's own comments never mention it** | `repo_map.py` — `grep -n "^def _provider_language_for_path" src/tensor_grep/cli/repo_map.py` (was `:15192`, now `:15209`) | the LSP-provider language dispatch (sits just above `_path_from_lsp_file_uri`/`_lsp_symbol_kind_name` — a DIFFERENT purpose than 5a's symbol-graph capsule gate, but it must resolve the SAME `language_id` for any suffix a `LanguageSpec` registers) | `test_target_and_provider_language_agree_with_registry` (below) fails loudly for the new suffix; less obviously, an LSP-provider code path silently disagrees with the symbol graph about what language a file is |
| 6 | `_SUPPORTED_FILE_DEPENDENCY_LANGUAGES` | `repo_map.py` — `grep -n '_SUPPORTED_FILE_DEPENDENCY_LANGUAGES\s*=' src/tensor_grep/cli/repo_map.py` (was `:17131`, now `:17148`) | gates whether `tg imports`/`tg importers` even attempts dependency resolution | file-dependency graph silently (but honestly, see B3) excludes the language |

**Seam 5b is easy to miss precisely because seam 5a's own code comments never mention it** —
unlike every other seam in this table, nothing in `_target_language_for_path` points you at
`_provider_language_for_path`. The two functions serve different callers (5a feeds the agent
capsule's confidence gate; 5b feeds the LSP-provider dispatch, e.g. clangd-via-LSP for a
language whose symbol-graph tier is only foundational or absent) but **both must return the
SAME `language_id` for every suffix a `LanguageSpec` registers**, or the dynamic parity test
below fails. `_provider_language_for_path` sometimes ALREADY recognizes a suffix before its
`LanguageSpec` is registered (a latent pre-wiring, not a bug) — e.g. it independently maps
`.c`/`.cc`/`.cpp`/`.cxx`/`.h`/`.hh`/`.hpp`/`.hxx` to `"c"`/`"cpp"` for the LSP provider even
before a C or C++ `LanguageSpec` exists — which means the CHOICE of `language_id` for a new
language is not always free: check `_provider_language_for_path` for an existing mapping
BEFORE naming your new `LanguageSpec.language_id`, or the two functions will disagree the
moment you register it.

Seam 5a is not a hypothesis — the live code says so in its own comments. Reading
`_target_language_for_path` on `main` today:

```text
if suffix == ".go":
    # MOST-FORGOTTEN seam (PATH A Stage 1 design note): without this, the capsule's
    # query-language-vs-target-language 0.55 confidence cap (agent_capsule.py) never even
    # sees "go" as a candidate target language...
    return "go"
...
if suffix in _JAVA_SUFFIXES:
    # Same MOST-FORGOTTEN seam, Stage 2: without this, `tg agent`'s capsule never reports
    # primary_target_language == "java" for a Java target.
    return "java"
if suffix == ".php":
    # MOST-FORGOTTEN seam (see the ".go" branch above) -- same fix, same reason...
    return "php"
```

Seam 5b has NO equivalent per-branch comment on `main` today — it is a plain suffix
dispatch (`repo_map.py:14711-14739`) with no "MOST-FORGOTTEN"-style warning attached to any
of its branches, which is exactly why it is the one this skill itself omitted until this
pass: nothing in the code nudges you toward it the way seam 5a's comments do.

**Worked example, from PR #728, SUPERSEDED by the top-10 C/C++ campaign — seam 6 was closed
for go/php/csharp at the FOUNDATIONAL tier first, then C/C++ joined the same frozenset later;
re-read this before assuming "in the frozenset" means "fully working," and re-derive the
member count rather than trusting either number below.** `_SUPPORTED_FILE_DEPENDENCY_LANGUAGES`
(`grep -n '_SUPPORTED_FILE_DEPENDENCY_LANGUAGES\s*=' src/tensor_grep/cli/repo_map.py` — was
`:17131`, now `:17148`) on `main` today is `frozenset({"python", "javascript", "typescript",
"rust", "java", "go", "php", "csharp", "c", "cpp"})` — **all 10 registered languages are now
members** (this table used to say "all 8" right after #728 landed C/C++ hadn't joined yet; a
later "Top-10 language campaign" commit added `"c"`/`"cpp"` to the same frozenset with its own
inline comment — re-count with the grep above, don't carry either "8" or "10" forward without
checking). PR #728 shipped three new per-language extractors — `lang_go.go_imports_with_lines`,
`lang_php.php_imports_with_lines`, `lang_csharp.csharp_imports_with_lines` — dispatched from
`_imports_with_lines_for_path` (`grep -n "^def _imports_with_lines_for_path" src/tensor_grep/cli/repo_map.py`
— was `:6831`, now `:6832`); each walks the same node kind its `*_imports_and_symbols` sibling
already walks (`import_spec` / `namespace_use_clause` / `using_directive` respectively) and
emits one `{"module": ..., "line": ...}` row per statement. `tg imports` on a `.go`/`.php`/`.cs`
file no longer reports `result_incomplete` with an empty list the way it did before this PR —
it returns real, line-numbered rows.

**But resolution — WHICH file/module each row's `module` string actually points to — is
still deferred for all five (go/php/csharp/c/cpp), and it is honestly deferred, never silently
faked.** `_resolve_raw_import_entry` (`grep -n "^def _resolve_raw_import_entry" src/tensor_grep/cli/repo_map.py`
— was `:17160`, now `:17177`) carries an `elif language_id in ("go", "php", "csharp", "c",
"cpp")` branch (re-grep `elif language_id in (` in `repo_map.py`; currently `:17246`, mirroring
the `elif language_id == "java"` branch immediately above it, currently `:17237-17245` — both
numbers already superseded twice across this skill's re-verify passes, re-grep rather than
trusting either) that always returns `resolved, external, provenance, confidence = None, False,
[], 0.0` — every row comes back `resolved=None, external=False` rather than a fabricated file
path or a fabricated `external=True`. Each language is missing *different* resolver machinery:
Go's own `_go_import_path_to_dir` (`lang_go.py`) already resolves an import path to a **package
directory**, not a single file — a Go import names a package that can span many `.go` files
with no 1:1 import-to-file mapping, so picking "the" file needs new design, not just wiring
existing code; PHP has no PSR-4/`composer.json` autoload-map reader; C# has no `.csproj`/
assembly-reference map; C/C++ have no standardized manifest at all (no
go.mod/composer.json/.csproj equivalent). None of that resolver machinery is built yet — see
`docs/BACKLOG.md` for the exact per-language scope still open. The fail-closed contract (B3)
still fires exactly as before for any language genuinely outside this frozenset:
`build_file_imports` (`grep -n "^def build_file_imports" src/tensor_grep/cli/repo_map.py` — was
`:17271`, now `:17288`) sets `result_incomplete=True` with
`incomplete_reason=f"'{language_id}' has no import-resolution support in \`tg imports\` yet"`
for any registered-but-unsupported language, and `_imports_with_lines_for_path`'s own
docstring (inside the function body, `grep -n "unsupported language (e.g. Kotlin)" src/tensor_grep/cli/repo_map.py`
— was cited `:6440`, now `:6835`) names Kotlin as its worked example of one — go/php/csharp/c/cpp
just are not examples of it anymore.

**A second, separate gate stays narrower still, and closing seam 6 does not close it too.**
`_confirm_import_edges` (`grep -n "^def _confirm_import_edges" src/tensor_grep/cli/repo_map.py`
— was `:17350`, now `:17367`; the `tg importers` reverse-confirm step that turns a prefiltered
"maybe imports it" into a confirmed edge) has its own independent language allow-list —
`if language_id not in ("javascript", "typescript", "rust", "python"): return []` (re-verified
unchanged this pass) — which still excludes java, go, php, csharp, c, AND cpp alike. Membership
in `_SUPPORTED_FILE_DEPENDENCY_LANGUAGES` does not imply membership in this second, stricter
gate; a future PR that builds true forward resolution for go/php/csharp/c/cpp still would not
make `tg importers`'s reverse-confirm step cover them without touching this allow-list too. This
is the same "forgot a seam but the honesty floor caught it" lesson as before, one tier deeper:
even a foundational landing must decide, per emitted row, whether to fabricate confidence it
doesn't have — every landing so far has chosen not to, matching Java's (#725) precedent exactly.
True forward resolution for go/php/csharp/c/cpp (and then extending
`_confirm_import_edges`'s allow-list) remains a good next PR for whoever reads this skill next.

Two more seams exist beyond this table, found by reading `lang_go.py` itself rather than
the ledger (not independently re-grepped against `repo_map.py`'s call sites this pass —
verify before citing a line number): (7) the per-language dispatch arms that call
`references_and_calls` / `file_imports_symbol_from_definition` directly, which feed
`tg callers`/`tg blast-radius`; (8) `clear_<lang>_repo_context_cache` (`lang_go.py:449`)
wired into the daemon-refresh sweep, so `tg session refresh` doesn't serve stale
import-resolution context after a repo change.

## B3 — fail-closed contract, extended per-language

- **Override `provenance_when_missing`.** The registry default is `"regex-heuristic"`
  (`grep -n "provenance_when_missing: str" src/tensor_grep/cli/lang_registry.py` — was `:89`,
  now `:94`) — true for the original JS/TS/Rust languages, which have a real
  regex fallback. Every language shipped since (Go, PHP) has **no** regex fallback and
  explicitly sets `provenance_when_missing="grammar-missing"` in its `LanguageSpec(...)`
  call. Skipping this override makes a grammar-absent file for the new language read as
  "zero symbols found" instead of a genuine `resolution_gaps` entry — a silent lie by
  omission (`lang_go.py:17-24`).
- **A `None` callable field is an honest deferral, not a bug — PHP is the shipped
  precedent.** `lang_php.py`'s own docstring states its Stage 1 landing is "deliberately
  narrower than Go's": it implements `extract_imports_and_symbols` +
  `parser_symbol_sources` only, and registers `references_and_calls`,
  `file_imports_symbol_from_definition`, `import_update_target`, and `prime_repo_context`
  all as `None`. `repo_map.py`'s `_language_coverage_gaps_for_universe` already treats
  `import_update_target is None` as a `resolution_gaps` entry — so `tg callers`/
  `tg blast-radius` stay honest about PHP's current lack of reverse-import resolution
  instead of reading as a proven zero. **You do not have to land every seam in one PR** —
  land a real, honestly-labeled subset, exactly like PHP did.
- Every extractor function returns the empty shape (`[]` / `([], [])`), **never raises**,
  when the grammar is missing (every public function in `lang_go.py` starts with
  `parser = _go_parser(); if parser is None: return <empty>`).
- **Symbol-kind vocabulary — emit the language's own, do not pre-collapse.** Each module
  emits its native kind strings (Go: `"function"`/`"method"`/`"struct"`/`"interface"`/
  `"const"`/`"var"`/`"type"`). The FULL def-node vocabulary for Go is `_GO_DEF_NODE_KINDS`
  (`grep -n "_GO_DEF_NODE_KINDS" src/tensor_grep/cli/lang_go.py` — this spot was cited as
  `lang_go.py:110-113`, which is only `_GO_TYPE_SPEC_KIND_BY_TYPE_FIELD`, the struct/interface
  sub-mapping applied INSIDE a `type_spec`; the full set now lives at `:117-123`, with the
  kind emission sites at `:290` (`const`/`var`), `:397` (`function`), `:400` (`method`), and
  the `type_spec` branch defaulting to `"type"`). A later normalization layer (not
  independently re-verified this pass — presumably in `repo_map.py`) is what the ledger
  records as the cross-language collapse: class/interface/struct/enum/record/trait →
  `"class"`; method/constructor/function → `"function"`. Emit the real vocabulary in the new
  module; re-verify where the collapse actually happens before assuming its exact shape.
- **`resolution_confidence` banding is the same fail-closed principle per-match.**
  `go_references_and_calls` (`lang_go.py:711`) bands 0.95 for a confirmed resolution
  (`resolution_provenance=["go-import-resolution"]`) vs. 0.7
  `"receiver-heuristic"` for a textually-plausible-but-statically-unconfirmed one
  (`lang_go.py:820`) — an unconfirmed match is **demoted, never dropped**. This is the
  per-match instance of the Backend Fail-Closed Contract: never fabricate certainty.

## B4 — verify the plan against current code before dispatch

A real onboarding brief this session said "mirror inline `_rust_*`" — **stale**, because the
repo had already grown `lang_registry.py` and the module pattern since that mental model
formed. All three build agents that received the brief independently caught it via the
`verify-plan-against-code` discipline before writing code, and corrected to the module
shape. **Rule: before dispatching an add-a-language plan — to a subagent, codex, cursor, or
your own future self — re-read `lang_registry.py` plus the most recently added `lang_<x>.py`
sibling fresh.** Do not trust a memory, an old skill snapshot (including this one — see the
"Fast self-check" below), or a prior session's summary about which shape is current.

## B5 — live-verify grammar node shapes before writing extraction logic

**Do not guess a node shape from documentation, another language's grammar, or intuition —
dump the real parse tree.** `lang_go.py` shipped with at least three node-shape surprises
found exactly this way, each pinned by an inline `F<n> fix` comment — worked, re-verified
proof this step cannot be skipped:

- **Generic receiver type nesting** (`lang_go.py:126-159`, F8 fix): `func (r *MyType[T]) M()`
  parses the receiver's type as a `generic_type` node whose raw text is `"MyType[T]"` — never
  matching the plain `"MyType"` a `type_spec` declares, unless you descend into
  `generic_type`'s own `type` field.
- **Grammar-version-dependent content node** (`lang_go.py:162-189`, F11 fix): a recent
  `tree_sitter_go` exposes `interpreted_string_literal_content` as a child of an import path;
  an older/differently-built grammar can omit it — silently zeroing out every import in the
  file with no error and no `resolution_gaps` entry (the parser loaded fine, so nothing marks
  a gap). Fix: fall back to quote-stripping the raw node text.
- **Row-counting divergence** (`lang_go.py:766`, the row-counting fix -- **cite this by LINE, not
  by F-tag**: `F26` is reused across at least 5 sites in `lang_go.py`, so an F-tag grep lands on a
  different fix): tree-sitter's row index
  advances only on `"\n"`; naive Python line-splitting also splits on other separators — one
  stray separator shifts every later line lookup out of alignment with tree-sitter's own rows
  unless you count rows the same way tree-sitter does.

None of these were guessable from a grammar README. Parse real (or minimal handwritten)
source covering every construct you plan to extract through the target `tree_sitter_<lang>`
package directly, and print `node.type`/`node.children` recursively, before writing
extraction logic.

**A fourth, independently-verified example (PR #726 merged mid-authoring-pass — re-checked
against the real file rather than left as a secondhand ledger note): C#'s aliased `using`
directive.** `using MyAlias = System.Text.StringBuilder;` parses with the alias identifier
emitted **first** (leftmost child) and the actual target namespace **last** (rightmost
child) — the reverse of what you might guess. `_csharp_using_directive_target`
(`lang_csharp.py:138-150`) handles all four `using` forms (plain, dotted, aliased,
`static`/`global`-qualified) with one rule: take the **last** matching
`identifier`/`qualified_name` child, never the first — verified against the installed
`tree_sitter_c_sharp` 0.23.x grammar for all four forms (`lang_csharp.py:113-124`'s own
comment table). Getting this backwards would record every aliased import as its local
alias name instead of the namespace actually being imported.

**A fifth example, and the most important one for C-family declarator walkers (PR #736, v1.98.2):
a "seems obviously right" tell was FALSIFIED by the live AST, not confirmed by it.** `lang_c.py`'s
`_c_declarator_name_node` walks a declarator chain to decide `seen_function: bool` — "did the chain
pass through a `function_declarator`?" A banked note from an earlier session proposed the fix as
"require `function_declarator` to be OUTERMOST" to exclude a file-scope function-pointer VARIABLE
(`void (*handler)(int);`, which this module deliberately must NOT report as a function). Dumping the
real `tree_sitter_c` 0.24.2 AST before writing any code showed that hypothesis was simply wrong: a
function-pointer variable's declarator chain ALSO has `function_declarator` outermost — identical in
that one respect to a real prototype `void f(int);`. "Outermost" is not the discriminator at all. The
real tell lives one level deeper, in what that `function_declarator` node's OWN `declarator` field
WRAPS:

| Shape | `function_declarator`'s own `declarator` field |
|---|---|
| `void f(int);` (real prototype) | bare `identifier` |
| `int *make_ptr(void);` (returns pointer) | bare `identifier`, one hop under a `pointer_declarator` |
| `void (*handler)(int);` (fn-ptr VARIABLE — exclude) | `parenthesized_declarator` wrapping a `pointer_declarator` |
| `int (foo)(void);` (redundant-paren real prototype — keep, gate-caught refinement) | `parenthesized_declarator` wrapping a bare `identifier` directly |

A `parenthesized_declarator` hop alone is not the signal — a real function can be wrapped in
meaningless redundant parens too (row 4); what matters is whether the parens wrap a bare name (real
function) or a pointer declarator (variable). `lang_cpp.py` had its OWN, independently-written
declarator walker (`_cpp_declarator_name_node`) with the same latent bug shape — **this WAS fixed
by PR #737 (confirmed landed: `git log --oneline -- src/tensor_grep/cli/lang_cpp.py` shows
`3c68a34 fix(lang-cpp): exclude function-pointer variables (were mis-kinded "function") (#737)`
on top of the #732 landing) — do not assume this skill's own older passes describing it as "not
yet landed" are still current, re-run the git log**. The fix ports `lang_c.py`'s
`_c_parenthesized_declarator_wraps_bare_name` tell via a new
`_cpp_parenthesized_declarator_wraps_bare_name` (`lang_cpp.py:381`) and confirms the
member-function-pointer wrinkle (`void (C::*mp)(int);`, which wraps a `qualified_identifier`
instead of a `pointer_declarator`) is SCOPE-DEPENDENT: file/namespace-scope excludes via the
bare-name type check, while in-class scope excludes via a different path entirely (tree-sitter-cpp
can't resolve `C::` inside a class body and emits an `ERROR` node, giving the
`parenthesized_declarator` two named children instead of one). **Rule for this skill's audience
specifically:** a banked "the fix is obviously X" note — even one written in a prior session about
this exact bug class — is a hypothesis about a declarator SHAPE, and declarator shapes are exactly
the thing B5 already tells you not to guess. Dump the real parse tree for every shape you plan to
include/exclude before trusting a one-line fix description, including your own.

## B6 — tiered model recap (see "Current status" above for the live table)

text search (any language, rg passthrough) → structural scan/rewrite (~26 langs via the
ast-grep wrapper `tg` wraps) → deep symbol graph (this skill's tier, the tree-sitter
grammars in `lang_registry`). Adding a language to the symbol graph does not change the
other two tiers — a language with no `LanguageSpec` still gets full-text search and (if
ast-grep supports it) structural scan/rewrite; it just has no `defs`/`refs`/`callers`/
`tg source` support until it clears this checklist.

## E1 — priority and what's next

Top-10 by TIOBE Jul-2026 + Stack Overflow 2025 + GitHub Octoverse 2025 consensus: Python,
JavaScript, TypeScript, Java, C#, C++, C, Go, Rust, PHP. **All 10 are now registered on
`main`** — C landed via PR #731 (v1.97.0), C++ via PR #732 (v1.98.0), both scoped exactly per
items 1-5 below (a Stage-1, per-file, foundational-tier landing, not the full
`#include`-graph resolution items 1-2 warn against blocking on). The five scoping notes
below are kept as a worked EXAMPLE of "scope before starting, not while coding" for the
next language beyond the top-10, not as an open task:

1. **No module system.** Go has `go.mod`/`go.work`; C/C++ has no compiler-enforced
   namespace-to-directory mapping. The honest floor for a first landing is per-file symbol
   extraction (filename-as-scope), not a full `compile_commands.json`/CMake include-graph.
   **SUPERSEDED 2026-08-12 — the "`#include` resolution ... tracked but not started" claim
   this item used to end with is STALE:** PR #957 (commit `9f854d4`, verified via
   `git log --oneline -1 9f854d4`) shipped a fail-closed `#include` resolution engine —
   `src/tensor_grep/cli/lang_c_cpp_include.py` (quoted includes search the importer's
   directory first then repo-rooted include roots; angle includes only the repo-rooted roots;
   macro/call-form includes stay unresolved; never fabricates an on-disk path) — wired into
   `lang_c.c_file_imports_symbol_from_definition` and
   `lang_cpp.cpp_file_imports_symbol_from_definition` (grep those symbols in
   `src/tensor_grep/cli/lang_c.py` / `lang_cpp.py`), and tested by
   `tests/unit/test_c_cpp_cross_file_callers.py`. What REMAINS honestly deferred: forward
   `tg imports` resolution for c/cpp — `_resolve_raw_import_entry`'s `elif language_id in
   ("go", "php", "csharp", "c", "cpp")` branch still returns the deferred tuple
   (`resolved=None, external=False`) — and a clangd-grade
   `compile_commands.json`/CMake include-graph resolver, which is not started.
2. **`#include` is textual, not semantic.** tree-sitter has no preprocessor; a
   `#define`-wrapped declaration (export/visibility macros are common in real C/C++ headers)
   can hide or reshape the node the extractor expects — B5's live-verify discipline is
   mandatory here, not optional, on a much larger surface than Go's. **Confirmed as shipped:**
   `class MACRO Name` (a macro-decorated class declaration) misparses on the installed C++
   grammar — an INHERENT tree-sitter ceiling, not a bug in this codebase's extractor, and
   disclosed as accepted in PR #732.
3. **Declaration/definition split.** A C/C++ function typically appears twice (a header
   prototype, a body-bearing definition) — which one is canonical for `tg source`/`tg defs`
   is a design decision to make explicitly, not an assumption carried over from Go's
   one-declaration model.
4. **C and C++ are two separate grammar packages** (`tree-sitter-c` vs. `tree-sitter-cpp`) —
   decide upfront whether they are one `LanguageSpec` or two (recommend two, mirroring how
   JS/TS already get two specs rather than one with a mode flag). **Confirmed as shipped:**
   they landed as two separate modules (`lang_c.py`/`lang_cpp.py`), each its own
   `LanguageSpec`, exactly as recommended here.
5. A first Stage 1 landing can reasonably scope to per-file extraction +
   declaration/definition dedup by name, in the same 0.7 `"receiver-heuristic"`-equivalent
   confidence band Go uses for anything short of confirmed resolution — a real,
   honestly-labeled feature now, rather than blocking on `#include`-graph resolution.

**Known post-ship correction (PR #736, v1.98.2 — see B5's declarator-shape addendum above for
the full worked example):** C's file-scope function-pointer VARIABLE
(`void (*handler)(int);`) was initially mis-kinded `"function"` — fixed by distinguishing
what a `function_declarator`'s own `declarator` field wraps, not whether it's outermost. **The
sibling fix for `lang_cpp.py`'s independently-written declarator walker (same bug shape, plus
a C++-only member-function-pointer wrinkle) HAS SINCE LANDED as PR #737** (`3c68a34
fix(lang-cpp): exclude function-pointer variables (were mis-kinded "function") (#737)`,
confirmed via `git log --oneline -- src/tensor_grep/cli/lang_cpp.py` — two earlier passes of
this skill said "not yet landed"; that was accurate when written and is now stale, which is
exactly why this section says "re-verify with git log" instead of asserting a fixed date).

## Parallel-drain hygiene (cross-ref: AGENTS.md Campaign Orchestration A22)

A new grammar touches three files that several in-flight language PRs are likely to touch
at once: `tests/unit/test_lang_registry.py` (the `LANGUAGE_REGISTRY.keys()` set-pin test,
`test_language_registry_has_exactly_the_stage2_languages`), the pyproject `ast` extra
(`grep -n '^ast = ' pyproject.toml` — was `:600`, now `:614`, now `:621`, plus the mirrored `dev`/`bench` extras), and `uv.lock` (a new
`tree-sitter-<lang>` `[[package]]` block). When more than one language PR is in flight:

- Drain ONE at a time and rebase each onto the prior, **UNIONing** the assertions — e.g. the
  set-pin test must assert the full accumulated language set, never take-one-side.
- A CLEAN rebase (no conflict marker) is **not** proof of correctness — a silent auto-merge
  can drop a `lang_*` import. Always re-run `pytest tests/unit/test_lang_registry.py` after
  every rebase, not just after the final one.
- `uv lock` regenerated from scratch churns ~280 unrelated lines (local-vs-CI uv-version
  marker-expr reformatting) — hand-splice only the new dependency's `[[package]]` block
  (alphabetical) plus its `requires-dist`/optional-dependency refs, and verify with
  `uv export --format requirements.txt --all-extras --no-emit-project --locked` (must exit
  0 — the exact `audit.yml` "Dependency & License Audit" gate).
- If you edit `uv.lock`/`ci.yml` (CRLF-committed files) with a Python text-mode write
  (`open(path, newline="\n")`), it flips every line ending in the file, turning an 11-line
  change into a 1000+ line diff. Read/write in binary mode (`rb`/`wb`) and byte-replace,
  preserving `\r\n`.

See `AGENTS.md`'s Campaign Orchestration Disciplines (A22) for the general form of this
rule, not specific to language PRs.

## Validation

- **Extend `tests/unit/test_lang_registry.py`**, not just a new bespoke test file — it
  already carries the pattern a new language must fit: `test_spec_for_path_resolves_every_
  registered_suffix`, `test_language_registry_has_exactly_the_stage2_languages` (the
  union-pin set — add your `language_id` here), `test_target_and_provider_language_agree_
  with_registry` (this ONE dynamic test pins BOTH seam 5a `_target_language_for_path` AND
  seam 5b `_provider_language_for_path` at once — it iterates every registered `LanguageSpec`
  and asserts both functions return that spec's own `language_id` for each of its suffixes,
  so it fails loudly if you wire only one of the pair), and the
  `test_*_provenance_is_tree_sitter_when_grammar_present` /
  `test_grammar_absent_monkeypatch_*_provenance_flips_to_grammar_missing` pair (21 tests as of 2026-07-27 -- re-run the grep rather
  than trusting this number; it grows with every language
  total as of this writing — `grep -c "def test_" tests/unit/test_lang_registry.py`).
- **Fixture/parity dogfood**: write a minimal real-world-shaped fixture file in the new
  language exercising every construct you extract (functions, types/generics if the
  language has them, qualified access, imports) and run `tg defs`/`tg refs`/`tg callers`/
  `tg source`/`tg imports` against it through the **real installed binary**, not `CliRunner`
  (`AGENTS.md`'s "Dogfood the Real Binary, Not CliRunner").
- **Confirm `_target_language_for_path` (seam 5) with a live `tg agent` capsule run** on a
  fixture in the new language and check `primary_target_language` in the JSON — this is the
  seam a unit test on `lang_registry` alone will not catch, because it lives in
  `repo_map.py`, not the registry module.
- Never trust a subagent's "I added the language and it works" as a self-report — confirm
  against external state: the registry dict, a real symbol-command run, and (if
  `_target_language_for_path` was touched) a capsule run.

## Fast self-check before trusting a claim about this design

```powershell
# Import side effect: LANGUAGE_REGISTRY is empty until the registering module (repo_map) is imported
uv run python -c "from tensor_grep.cli import lang_registry; print(sorted(lang_registry.LANGUAGE_REGISTRY.keys()))"
uv run python -c "from tensor_grep.cli import lang_registry, repo_map; print(sorted(lang_registry.LANGUAGE_REGISTRY.keys()))"

# Does a suffix resolve, and what does provenance_when_missing say?
uv run python -c "from tensor_grep.cli import lang_registry, repo_map; s = lang_registry.spec_for_path('x.go'); print(s.language_id, s.provenance_when_missing)"

# Re-derive the current registered-language set + re-locate the 6 seams (5a+5b count as two)
# before citing a line number
grep -n "register_language(" src/tensor_grep/cli/repo_map.py
grep -n "^def _imports_and_symbols_for_path\|^def _imports_with_lines_for_path\|^def build_symbol_source_from_map\|^def _target_language_for_path\|^def _provider_language_for_path\|_SUPPORTED_FILE_DEPENDENCY_LANGUAGES" src/tensor_grep/cli/repo_map.py

# Version identity
tg --version
```

## Provenance and maintenance

- **2026-08-12 retention pass — the 2026-08-01 "zero drift" claim for `lang_registry.py` is
  FALSIFIED; every bare `lang_registry.py` citation above converted to grep-the-symbol form.**
  The fourth pass (below) asserted the registry-contract lines "matched the live file exactly,
  byte-for-byte range, with zero drift" — re-measured this pass against `origin/main` @
  `568065a`, they had all moved: `class LanguageSpec` was `:67-111`, now `:72-119`;
  `provenance_when_missing: str` default was `:89`, now `:94`; `LANGUAGE_REGISTRY` was `:114`,
  now `:122`; `register_language` was `:118-128`, now `:126-136`; the one-directional-import
  docstring was `:10-12`, now `:10-11` (`grep -n "never the reverse"`). Also fixed this pass:
  E1 item 1's "`#include` resolution tracked but not started" (superseded by PR #957/`9f854d4`,
  see the item itself), the Go kind-vocabulary citation (`lang_go.py:110-113` is only the
  `_GO_TYPE_SPEC_KIND_BY_TYPE_FIELD` subset; the full set is `_GO_DEF_NODE_KINDS`), and the
  inverted SUPERSEDED chronology in "Current status" (2026-08-04 entry now precedes the
  2026-08-11 entry, append-only order restored).
- **Fourth re-verify pass, 2026-08-01** (skill-library drift audit). Every `repo_map.py` seam in
  B2/the worked example had drifted 1-85 lines since the third pass (register_language count
  unchanged at 10; `_imports_and_symbols_for_path` `:6626`->`:6627`;
  `_imports_with_lines_for_path` `:6831`->`:6832`; `_target_language_for_path` `:7850`->`:7867`;
  `_provider_language_for_path` `:15192`->`:15209`; `build_symbol_source_from_map`
  `:16309`->`:16326`; `_SUPPORTED_FILE_DEPENDENCY_LANGUAGES` `:17131`->`:17148`;
  `_resolve_raw_import_entry` `:17160`->`:17177`; `build_file_imports` `:17271`->`:17288`;
  `_confirm_import_edges` `:17350`->`:17367`; the `elif language_id == "java"` branch
  `:16714-16722`->`:17237-17245`; the Kotlin-example docstring inside
  `_imports_with_lines_for_path` `:6440`->`:6835`; the Java inline extractor
  `_java_imports_and_symbols` `:4782`->`:4783`; `pyproject.toml`'s `ast` extra `:600`->`:614`, now `:621`).
  Replaced every one of these with a `grep -n "^def <symbol>"` instruction plus the `was -> now`
  receipt per `AGENTS.md`'s never-re-stamp rule, rather than swapping in a fresh bare number that
  would just rot again. **One substantive content error found and fixed, not just a line-number
  drift:** B5's fifth example and E1's "Known post-ship correction" both said the `lang_cpp.py`
  sibling fix for the C++ function-pointer-variable mis-kind was "tracked as a follow-up, not yet
  landed" — false as of this pass. It landed as PR #737 (`3c68a34 fix(lang-cpp): exclude
  function-pointer variables (were mis-kinded "function") (#737)`, confirmed via
  `git log --oneline -- src/tensor_grep/cli/lang_cpp.py`), including the scope-dependent
  member-function-pointer resolution both prior passes flagged as open. Also found and fixed: B2
  table row 1 said "8 call sites" for `register_language(...)` while the "Current status" section
  two paragraphs above it correctly said 10 — an internal inconsistency, not just staleness. The
  worked-example's `_SUPPORTED_FILE_DEPENDENCY_LANGUAGES` frozenset was also stale at "8 members"
  — a later "Top-10 language campaign" commit added `"c"`/`"cpp"` to the same frozenset (now 10
  members), which this pass's own "Current status" section already knew but the worked example
  had not caught up to. **Everything in B1, B3, B4, B5's first four examples, and Sections
  lang_registry.py/lang_go.py/lang_csharp.py cite: re-verified UNCHANGED this pass** — every
  `lang_registry.py`, `lang_go.py`, and `lang_csharp.py` line citation in this skill (including
  the 126-159/162-189/449/711/766/820 F-tag fixes, the 9-12/17-24 docstring rules, and the
  67-111/89/114/118-128 registry-contract lines) matched the live file exactly, byte-for-byte
  range, with zero drift — those three files are far more stable than `repo_map.py` and did not
  need touching. `test_lang_registry.py`'s own test count (`grep -c "def test_"
  tests/unit/test_lang_registry.py`) is still 21, unchanged.
- **Third re-verify pass, 2026-07-24, against tg v1.98.2** (`main` HEAD `ba63aa0`). Corrected the
  "Current status" section and E1 from stale "C/C++ deferred, C# is next" framing (accurate as of
  the prior pass, staled by C landing in PR #731/v1.97.0 and C++ in PR #732/v1.98.0 — both merged
  after the prior pass) to the current true state: all 10 top-10 languages are registered
  (`grep -c "register_language(" src/tensor_grep/cli/repo_map.py` -> 10;
  `test_language_registry_has_exactly_the_stage2_languages` now pins `c`/`cpp` alongside the prior
  8). Added the B5 declarator-shape addendum documenting PR #736 (v1.98.2) — a banked "the fix is
  obviously X" hypothesis for a C symbol-graph mis-kind that a live AST dump FALSIFIED before any
  fix code was written; this is the single most directly relevant fact this skill carries for the
  next C/C++-family declarator-walker change, since `lang_cpp.py`'s own independently-written
  walker has the same latent bug shape and is not yet fixed (`lang_cpp.py`'s own log has no commit
  past #732 as of this pass — re-verify with `git log --oneline -- src/tensor_grep/cli/lang_cpp.py`
  before assuming a fix has landed). Ground truth this pass: `git log --oneline` for `lang_c.py`/
  `lang_cpp.py`, PR #731/#732/#736's real commit messages and diffs, and
  `tests/unit/test_lang_registry.py`'s current set-pin.
- **Verified against tg v1.96.1-pending** (`main` HEAD `29cf59f`, `pyproject.toml` still
  stamps `1.96.0` since semantic-release derives the version at publish time — #728 is a
  `fix:` commit, so the next publish is v1.96.1). This is the skill's **second** re-verify
  pass: #726 (C#) first, then **PR #728** (go/php/csharp foundational-tier file-dependency
  wiring, merged after the prior pass) staled the B2 worked example — which had described
  go/php/csharp as excluded from `_SUPPORTED_FILE_DEPENDENCY_LANGUAGES` — plus every
  `repo_map.py` seam line number at or below the `_imports_with_lines_for_path` insertion
  point (`_target_language_for_path`, `build_symbol_source_from_map`,
  `_SUPPORTED_FILE_DEPENDENCY_LANGUAGES`, and `build_file_imports` all shifted;
  `_imports_and_symbols_for_path`/`_imports_with_lines_for_path` themselves did not, since
  the insertion lands inside/after their own bodies) plus three `lang_go.py` citations below
  its own new-function insertion point (`clear_go_repo_context_cache`,
  `go_references_and_calls`, its `"receiver-heuristic"` band). This pass re-derived every
  number directly against `origin/main` @ `29cf59f` (`git cat-file blob`, never the
  possibly-stale local checkout) rather than carrying the prior pass's numbers forward, and
  confirmed the diff hunk COUNT in every touched file (`repo_map.py`: 4 hunks; each of
  `lang_go.py`/`lang_php.py`/`lang_csharp.py`: 1 hunk) before trusting any citation below an
  insertion point as unaffected. Ground truth read directly this pass: PR #728's real diff
  (`git show 29cf59f`), every cited `repo_map.py` seam (re-grepped fresh, not carried over)
  plus the new `_resolve_raw_import_entry` go/php/csharp branch and `_confirm_import_edges`'s
  own separate allow-list, the three new `*_imports_with_lines` extractor bodies in
  `lang_go.py`/`lang_php.py`/`lang_csharp.py`, and `docs/BACKLOG.md`'s `#728` entry.
- **Not independently verified this pass**: the exact `repo_map.py` line numbers for seam 7
  (per-language `references_and_calls`/`file_imports_symbol_from_definition` dispatch arms)
  and the daemon-refresh cache-clear sweep CALL site (distinct from
  `clear_go_repo_context_cache`'s own definition, which this pass did re-verify); the Java
  inline extractor's own line-level shape beyond its function names; C#'s/PHP's
  `*_imports_and_symbols` def/caller-graph line-level shape beyond the `using`-directive
  target-selection function cited above and their new `*_imports_with_lines` siblings (only
  the NEW functions and the diff that introduced them were read this pass — check whether
  C#/PHP shipped the narrower PHP-style defs+imports-only slice or a fuller Go-style caller
  graph before citing either). Re-verify all of these — and every line number above — before
  citing them in a later session; `repo_map.py` moves fast (~100+ lines/release, per
  `tensor-grep-run-and-operate`).
- **Prior-pass provenance (kept for history)**: the original B1-B6/E1 framing and the C#
  node-shape lead came from `session_learnings_2026-07-24.md` (a scratch file, not a
  permanent repo artifact), later independently confirmed against `lang_csharp.py` once #726
  landed. This pass's #728 corrections did not consult that file — they were re-derived
  directly from the live repo and PR #728's real diff.
- If a re-verify disagrees with this skill, fix the skill — a wrong runbook is worse than
  none — and route any actual code change through `tensor-grep-change-control`.
