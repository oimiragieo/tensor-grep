---
name: tensor-grep-add-language
description: Use when adding a new language to tensor-grep's tree-sitter symbol graph (defs/refs/callers/blast-radius, `tg source`, `tg imports`) — registering a `LanguageSpec` in `lang_registry.py`, writing a new `src/tensor_grep/cli/lang_<x>.py` extractor module, or scoping the deferred C/C++ language-expansion follow-up. Triggers and keywords include add a language, add <language> to tensor-grep, new grammar, tree-sitter grammar, lang_registry, register_language, new lang_<x>.py module, symbol-graph language, onboard a language, "tg doesn't find symbols in <language>", `_target_language_for_path`, grammar-missing provenance, C/C++ symbol graph.
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

## Current status (verified against tg v1.96.1-pending, `origin/main` @ `29cf59f`)

`repo_map.py` currently carries **8** `lang_registry.register_language(...)` call sites
(`grep -n "register_language(" src/tensor_grep/cli/repo_map.py`): `python`, `javascript`,
`typescript`, `rust` (the original four, inline in `repo_map.py`), plus `go`, `java`,
`php`, and `csharp` — confirmed via `language_id=` greps and
`tests/unit/test_lang_registry.py::test_language_registry_has_exactly_the_stage2_languages`'s
literal set-pin (which now includes `"csharp"`). C# landed via PR #726 as a
module-shaped language (`lang_csharp.py`, mirrors `lang_go.py`, not Java's inline
shape) — **all top-10 languages except C/C++ are now registered.** **Re-run the grep
above before trusting any "N of top-10" count** — it is a snapshot, not a promise; this
count changed twice in the span of authoring this skill.

The tiered language model (unchanged shape, re-verify the coverage numbers):

| Tier | Scope | Mechanism | Coverage |
|---|---|---|---|
| Text search | any file | rg passthrough (bootstrap front door) | universal |
| Structural scan/rewrite | many languages | ast-grep, which `tg` wraps (`tg ast-info`, `tg run`) | ~26 langs (ast-grep's own list) |
| **Symbol graph (this skill)** | tree-sitter grammars in `lang_registry` | `defs`/`refs`/`callers`/`blast-radius`/`tg source`/`tg imports` | 8 of top-10 live on `main` (Python/JS/TS/Java/C#/Go/Rust/PHP); **C/C++ deferred** |

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
inline-in-`repo_map.py` (`_java_imports_and_symbols` etc., `repo_map.py:4544`+) and still
registers through `lang_registry` — both shapes are contract-consistent, but **the module
shape is what Go, PHP, and C# (the three most recent additions) all converged on**, and is
what `lang_go.py`'s own docstring recommends: it keeps `repo_map.py` from growing further.

One-directional import rule (stated in both `lang_registry.py:10-12` and `lang_go.py:9-15`):
`repo_map.py` → `lang_<x>.py`, never the reverse. A helper the new module needs that
`repo_map.py` already has must be **duplicated locally** (see `lang_go.py:37-87`'s
byte-identical-to-`repo_map.py` tiny helpers), not imported — importing back creates a
cycle.

`LanguageSpec` (`lang_registry.py:67-111`, frozen dataclass) is the single contract. Fields
worth knowing before writing one:

| Field | Status | Note |
|---|---|---|
| `language_id`, `suffixes` | wired, required | e.g. `"go"`, `frozenset({".go"})` |
| `parser_for_path` | wired | returns the parser or `None` if the grammar package isn't installed — the fail-closed gate |
| `provenance_when_missing` | wired, **default `"regex-heuristic"`** | a language with no regex fallback (every language after the original four) **must override this to `"grammar-missing"`** — see B3 |
| `extract_imports_and_symbols`, `references_and_calls`, `provider_alias_calls`, `file_imports_symbol_from_definition`, `import_update_target` | wired | any of these left `None` = an honestly-deferred capability, not a bug — see PHP's precedent in B3 |
| `prime_repo_context` | wired | `None` if the language has no per-repo workspace state to prime (tsconfig/`go.mod`-style) |
| `def_node_kinds`, `classify_ref_kind` | **doc-only in Stage 0** | no dispatch seam reads these yet — populate for self-documentation, do not assume they are wired |

`register_language()` is idempotent (`lang_registry.py:118-128`) — re-registering the same
`language_id` replaces the entry and re-derives every suffix pointer, so a stale mapping
never survives a reload. `LANGUAGE_REGISTRY` starts **empty** (`:114`) until whatever module
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
| 1 | `lang_registry.register_language(LanguageSpec(...))` | `repo_map.py` (8 call sites, "near the bottom") | wiring the suffix at all | new suffix never resolves; silently excluded everywhere |
| 2 | `_imports_and_symbols_for_path` | `repo_map.py:6244` | symbol/def extraction dispatch | new language absent from defs/symbols |
| 3 | `_imports_with_lines_for_path` | `repo_map.py:6440` | `tg imports` (line-numbered import entries) | `tg imports` silently empty even though defs exist |
| 4 | `build_symbol_source_from_map` | `repo_map.py:15815` | `tg source` | `tg source` returns nothing for a real symbol |
| 5a | **`_target_language_for_path` — MOST-FORGOTTEN** | `repo_map.py:7383` | `tg agent` capsule's `primary_target_language` / confidence gate | a target file in the new language does not filter a mismatched-language validation suggestion |
| 5b | **`_provider_language_for_path` — a SIBLING seam, easy to miss because 5a's own comments never mention it** | `repo_map.py:14711` | the LSP-provider language dispatch (sits just above `_path_from_lsp_file_uri`/`_lsp_symbol_kind_name` — a DIFFERENT purpose than 5a's symbol-graph capsule gate, but it must resolve the SAME `language_id` for any suffix a `LanguageSpec` registers) | `test_target_and_provider_language_agree_with_registry` (below) fails loudly for the new suffix; less obviously, an LSP-provider code path silently disagrees with the symbol graph about what language a file is |
| 6 | `_SUPPORTED_FILE_DEPENDENCY_LANGUAGES` | `repo_map.py:16633` | gates whether `tg imports`/`tg importers` even attempts dependency resolution | file-dependency graph silently (but honestly, see B3) excludes the language |

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

**Worked example, UPDATED after PR #728 — seam 6 was closed for go/php/csharp, but only at
the FOUNDATIONAL tier, not full resolution; re-read this before assuming "in the frozenset"
means "fully working."** `_SUPPORTED_FILE_DEPENDENCY_LANGUAGES` (`repo_map.py:16633`) on
`main` today is `frozenset({"python", "javascript", "typescript", "rust", "java", "go",
"php", "csharp"})` — **all 8 registered languages are now members**, closing the exact gap
this worked example used to describe (go/php/csharp were absent; as of #728, none are). PR
#728 shipped three new per-language extractors — `lang_go.go_imports_with_lines`,
`lang_php.php_imports_with_lines`, `lang_csharp.csharp_imports_with_lines` — dispatched from
`_imports_with_lines_for_path` (`repo_map.py:6440`); each walks the same node kind its
`*_imports_and_symbols` sibling already walks (`import_spec` / `namespace_use_clause` /
`using_directive` respectively) and emits one `{"module": ..., "line": ...}` row per
statement. `tg imports` on a `.go`/`.php`/`.cs` file no longer reports `result_incomplete`
with an empty list the way it did before this PR — it returns real, line-numbered rows.

**But resolution — WHICH file/module each row's `module` string actually points to — is
still deferred for all three, and it is honestly deferred, never silently faked.**
`_resolve_raw_import_entry` (`repo_map.py:16654`) gained an
`elif language_id in ("go", "php", "csharp")` branch (`repo_map.py:16723-16735`, mirroring
the `elif language_id == "java"` branch immediately above it at `repo_map.py:16714-16722`)
that always returns `resolved, external, provenance, confidence = None, False, [], 0.0` —
every row comes back `resolved=None, external=False` rather than a fabricated file path or a
fabricated `external=True`. Each language is missing *different* resolver machinery: Go's own
`_go_import_path_to_dir` (`lang_go.py`) already resolves an import path to a **package
directory**, not a single file — a Go import names a package that can span many `.go` files
with no 1:1 import-to-file mapping, so picking "the" file needs new design, not just wiring
existing code; PHP has no PSR-4/`composer.json` autoload-map reader; C# has no `.csproj`/
assembly-reference map. None of that resolver machinery is built by #728 — see
`docs/BACKLOG.md`'s `#728` entry for the exact per-language scope still open. The fail-closed
contract (B3) still fires exactly as before for any language genuinely outside the 8-member
set: `build_file_imports` (`repo_map.py:16760`) sets `result_incomplete=True` with
`incomplete_reason=f"'{language_id}' has no import-resolution support in \`tg imports\` yet"`
for any registered-but-unsupported language, and `_imports_with_lines_for_path`'s own
docstring (`repo_map.py:6440`) names Kotlin as its worked example of one — go/php/csharp just
are not examples of it anymore.

**A second, separate gate stays narrower still, and closing seam 6 does not close it too.**
`_confirm_import_edges` (`repo_map.py:16839`, the `tg importers` reverse-confirm step that
turns a prefiltered "maybe imports it" into a confirmed edge) has its own independent
language allow-list — `if language_id not in ("javascript", "typescript", "rust", "python"):
return []` — which still excludes java, go, php, AND csharp alike. Membership in
`_SUPPORTED_FILE_DEPENDENCY_LANGUAGES` does not imply membership in this second, stricter
gate; a future PR that builds true forward resolution for go/php/csharp still would not make
`tg importers`'s reverse-confirm step cover them without touching this allow-list too. This is
the same "forgot a seam but the honesty floor caught it" lesson as before, one tier deeper:
even a foundational landing must decide, per emitted row, whether to fabricate confidence it
doesn't have — #728 chose not to, matching Java's (#725) precedent exactly. True forward
resolution for go/php/csharp (and then extending `_confirm_import_edges`'s allow-list) remains
a good next PR for whoever reads this skill next.

Two more seams exist beyond this table, found by reading `lang_go.py` itself rather than
the ledger (not independently re-grepped against `repo_map.py`'s call sites this pass —
verify before citing a line number): (7) the per-language dispatch arms that call
`references_and_calls` / `file_imports_symbol_from_definition` directly, which feed
`tg callers`/`tg blast-radius`; (8) `clear_<lang>_repo_context_cache` (`lang_go.py:449`)
wired into the daemon-refresh sweep, so `tg session refresh` doesn't serve stale
import-resolution context after a repo change.

## B3 — fail-closed contract, extended per-language

- **Override `provenance_when_missing`.** The registry default is `"regex-heuristic"`
  (`lang_registry.py:89`) — true for the original JS/TS/Rust languages, which have a real
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
  `"const"`/`"var"`/`"type"`, `lang_go.py:110-113`). A later normalization layer (not
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
- **Row-counting divergence** (`lang_go.py:226-229`, F26 fix): tree-sitter's row index
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

## B6 — tiered model recap (see "Current status" above for the live table)

text search (any language, rg passthrough) → structural scan/rewrite (~26 langs via the
ast-grep wrapper `tg` wraps) → deep symbol graph (this skill's tier, the tree-sitter
grammars in `lang_registry`). Adding a language to the symbol graph does not change the
other two tiers — a language with no `LanguageSpec` still gets full-text search and (if
ast-grep supports it) structural scan/rewrite; it just has no `defs`/`refs`/`callers`/
`tg source` support until it clears this checklist.

## E1 — priority and what's next

Top-10 by TIOBE Jul-2026 + Stack Overflow 2025 + GitHub Octoverse 2025 consensus: Python,
JavaScript, TypeScript, Java, C#, C++, C, Go, Rust, PHP. All 8 non-C/C++ entries are now
registered on `main` (C# landed via PR #726). **C/C++ is the next concrete target**, and it
is harder than any language shipped so far — scope before starting, not while coding:

1. **No module system.** Go has `go.mod`/`go.work`; C/C++ has no compiler-enforced
   namespace-to-directory mapping. The honest floor for a first landing is per-file symbol
   extraction (filename-as-scope), not a full `compile_commands.json`/CMake include-graph.
2. **`#include` is textual, not semantic.** tree-sitter has no preprocessor; a
   `#define`-wrapped declaration (export/visibility macros are common in real C/C++ headers)
   can hide or reshape the node the extractor expects — B5's live-verify discipline is
   mandatory here, not optional, on a much larger surface than Go's.
3. **Declaration/definition split.** A C/C++ function typically appears twice (a header
   prototype, a body-bearing definition) — which one is canonical for `tg source`/`tg defs`
   is a design decision to make explicitly, not an assumption carried over from Go's
   one-declaration model.
4. **C and C++ are two separate grammar packages** (`tree-sitter-c` vs. `tree-sitter-cpp`) —
   decide upfront whether they are one `LanguageSpec` or two (recommend two, mirroring how
   JS/TS already get two specs rather than one with a mode flag).
5. A first Stage 1 landing can reasonably scope to per-file extraction +
   declaration/definition dedup by name, in the same 0.7 `"receiver-heuristic"`-equivalent
   confidence band Go uses for anything short of confirmed resolution — a real,
   honestly-labeled feature now, rather than blocking on `#include`-graph resolution.

## Parallel-drain hygiene (cross-ref: AGENTS.md Campaign Orchestration A22)

A new grammar touches three files that several in-flight language PRs are likely to touch
at once: `tests/unit/test_lang_registry.py` (the `LANGUAGE_REGISTRY.keys()` set-pin test,
`test_language_registry_has_exactly_the_stage2_languages`), the pyproject `ast` extra
(`pyproject.toml:600`, plus the mirrored `dev`/`bench` extras), and `uv.lock` (a new
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
  `test_grammar_absent_monkeypatch_*_provenance_flips_to_grammar_missing` pair (17 tests
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
