"""C language extractor for tensor-grep's multi-language symbol graph (PATH A Stage 3).

Ninth language expansion (top-10 language-support campaign, Phase 1 of C/C++ -- C++ is a
SEPARATE follow-up, out of scope here). Sibling of ``lang_go.py``/``lang_csharp.py``/
``lang_php.py`` (see those modules' docstrings for the full "PATH A Stage 1" framing). Plugs
into the ``lang_registry`` seam Stage 0 built (see ``lang_registry.py`` + the
``lang_registry.register_language(...)`` calls near the bottom of ``repo_map.py``) -- C gets its
OWN ``LanguageSpec`` entry, registered from ``repo_map.py``.

FOUNDATIONAL SCOPE (same tier Java/PHP/C#/Go landed at, EXTENDED by Task 10D -- see below): this
module lights up ``defs``/``source``/``imports``/``agent`` for ``.c`` files -- function
definitions AND prototypes (kind "function"), struct/union/enum definitions (kind "class", per
the fail-closed struct/union/enum -> "class" mapping this campaign's other languages already
use), and typedefs (kind "type", mirroring Go's ``type_spec`` -> "type" kind). The remaining
cross-file caller-graph fields (``file_imports_symbol_from_definition``/``import_update_target``/
``prime_repo_context``) stay ``None``, deferred to a follow-up -- same shape as Go/Java/C#/PHP's
own gap, so ``tg refs``/``tg callers``/``tg blast-radius`` on a C symbol still fall through to the
honest ``resolution_gaps`` reverse-import disclosure (never a crash, never a fabricated cross-file
match) for the reverse-import leg specifically.

TASK 10D: ``c_references_and_calls`` promotes C from the foundational (defs/imports-only) tier to
the parser-backed refs/callers tier, mirroring Task 10A's Java landing / 10B's C# landing / 10C's
PHP landing -- IN-FILE AST reference/call extraction only, no cross-file import resolution. Owns
its own parser factory (``_c_parser()``, already defined below for ``c_imports_and_symbols`` --
this module predates Task 10D), matching ``lang_csharp.py``'s/``lang_php.py``'s shape rather than
``lang_java.py``'s externally-built-parser shape, for the same reason PHP gives: a second factory
here would create two sources of truth for "is the C grammar installed".

C IS GENUINELY DIFFERENT FROM JAVA/C#/PHP, and this is deliberate, not an oversight: C has no
methods and no receiver types, so the "receiver's declared type is in-file and declares the
member" confirmation those three languages use has NO C analogue, and this module does not fake
one (see "RESOLUTION CONFIDENCE / PROVENANCE" below for what C offers instead, and what stays
honestly demoted). See ``c_references_and_calls``'s own docstring for the full node-shape mapping
(live-verified against a real ``tree_sitter_c`` 0.24.2 parse, not guessed).

``.h`` is DELIBERATELY NOT claimed by this module. ``_provider_language_for_path`` (the LSP
provider dispatch, repo_map.py) already assigns every C/C++ header suffix (``.h``, ``.hh``,
``.hpp``, ``.hxx``) to ``"cpp"`` -- since tree-sitter-cpp is a strict grammar superset of C, a
future ``lang_cpp.py`` (Phase 2, not built here) is the natural owner of ``.h`` so that pure-C
headers still parse under the C++ grammar. Registering ``.h`` here too would make this module's
``language_id="c"`` disagree with that pre-existing "cpp" assignment and fail
``test_target_and_provider_language_agree_with_registry``.

FAIL-CLOSED CONTRACT (Stage 0 honesty floor, extended here exactly as it was for Go/PHP/C#): C
has NO regex-heuristic fallback. When the ``tree_sitter_c`` grammar package is not installed,
every extractor in this module returns empty ([]/([], [])) rather than degrading to a regex/text
heuristic (unlike JS/TS/Rust, which all fall back to regex extraction when their own tree-sitter
grammar is missing). ``LanguageSpec.provenance_when_missing="grammar-missing"`` (NOT
``"regex-heuristic"``) is what makes ``_language_coverage_gaps_for_universe`` in repo_map.py
treat a grammar-absent C file as a genuine ``resolution_gaps`` entry instead of silently
reporting zero matches as if the symbol just did not exist.

``#include`` IS a parse-tree node (tree-sitter-c does not run a preprocessor, so it never strips
or expands a ``#include`` directive) -- ``preproc_include`` with a ``path`` field whose node
SHAPE varies by include form (live-verified against a real tree_sitter_c 0.24.2 parse, not
guessed from the grammar README):
  ``#include <stdio.h>``     -> path field type ``system_lib_string``, text ``"<stdio.h>"``
  ``#include "local.h"``     -> path field type ``string_literal``, nested ``string_content``
  ``#include MACRO_HEADER``  -> path field type ``identifier`` (macro-expanded form)
  ``#include COMBINE(a,b)``  -> path field type ``call_expression`` (macro-combined form)
``_c_include_target_text`` strips the ``<...>``/``"..."`` delimiters where present so the
recorded ``module`` string is the bare target (``"stdio.h"``, not ``"<stdio.h>"``), matching how
every other language's extractor here records a delimiter-free module string. Resolution stays
HONEST-UNRESOLVED: repo_map.py's ``_resolve_raw_import_entry`` reports every row
``resolved=None, external=False`` (never a fabricated path, never a fabricated ``external=True``)
-- true ``#include`` -> file resolution has no standardized C manifest to resolve against (no
``go.mod``/``composer.json``/``.csproj`` equivalent) and stays deferred to BACKLOG, same as the
go/php/csharp resolvers.

DECLARATOR NAME RESOLUTION (the one genuinely new wrinkle vs Go/PHP/C#): a C declarator can nest
a name arbitrarily deep -- ``int *make_ptr(void)`` wraps ``function_declarator`` inside
``pointer_declarator``; ``typedef void (*FuncPtr)(int);`` wraps a ``parenthesized_declarator``
(exposing NO named "declarator" field of its own, unlike every other wrapper) around a
``pointer_declarator`` around the ``type_identifier`` leaf. ``_c_declarator_name_node`` handles
both: it follows the named "declarator" field where one exists, and falls back to the single
NAMED child when a wrapper (like ``parenthesized_declarator``) exposes none. Live-verified against
real parses of plain/pointer/array/function-pointer declarators (including the
``typedef char *(*ComplexFuncPtr)(int, char);`` double-wrap case) before being written here --
see the module's originating PR description for the exact fixtures probed.

A ``declaration`` node is ambiguous by TYPE ALONE -- it is a function PROTOTYPE
(``int add(int a, int b);``), a file-scope function-pointer VARIABLE (``void (*handler)(int);``),
or a plain variable declaration (``int counter = 0;``, ``extern int flag;``) depending on its
declarator shape. ``_c_declarator_name_node`` returns a ``seen_function`` boolean so the
extractor can gate: only a ``declaration`` whose chain named a REAL function is emitted (as kind
"function"); a plain variable declaration AND a function-pointer variable are both silently
excluded from the symbol table, matching this module's foundational scope (top-level variables
are not a tracked symbol kind here, mirroring every other ``lang_*.py`` module -- none of them
track module-level variables either, except Go's explicit ``const_spec``/``var_spec`` kinds,
which this module does not add). "Chain passes through ``function_declarator``" is NOT by itself
the gate -- a function-pointer variable's chain also passes through one (it is
``function_declarator``-outermost too); see ``_c_declarator_name_node``'s own docstring for the
real, live-verified ``parenthesized_declarator`` tell that distinguishes the two.
A ``struct_specifier``/``union_specifier``/``enum_specifier`` is similarly ambiguous by type
alone -- ``struct Foo;`` (forward declaration) and ``struct Foo *p`` (usage as a type) both parse
as the SAME node type with no ``body`` field, indistinguishable from a real definition except by
checking for a ``body`` field's presence -- only a body-bearing specifier is emitted.

TASK 10D CALL/ACCESS NODE SHAPES (live-verified against a real ``tree_sitter_c`` 0.24.2 parse, not
guessed): C has exactly TWO call/access node shapes, far narrower than PHP's five -- there is no
``new``, no method, no scoped/static access:

- ``call_expression``: field ``function`` (EITHER a bare ``identifier`` for ``add(1, 2)`` /
  ``fp(3)`` / ``ADD_MACRO(1,2)`` -- a real function call, a function-pointer-VARIABLE call, and a
  function-like-macro invocation are ALL THE SAME NODE SHAPE, indistinguishable by grammar alone;
  OR a ``field_expression`` for ``w.handler(4)`` / ``p->handler(5)`` -- calling a function pointer
  reached through a struct/union member), field ``arguments``. A symbol match on a bare
  ``identifier`` function field is a **call** (``ref_kind="call"``, both buckets); so is a symbol
  match on a ``field_expression`` function field's ``field`` child.
- ``field_expression`` (``w.x`` / ``p->x``, NOT a call): fields ``argument`` (the receiver,
  typically an ``identifier``), ``operator`` (``.`` or ``->`` -- text-identical either way, so this
  module does not distinguish value vs pointer access), ``field`` (a ``field_identifier``). A
  symbol match on ``field`` (when not already claimed by the ``call_expression`` branch above) is
  ``ref_kind="field"``.
- ``identifier`` / ``type_identifier`` / ``field_identifier``: reused across value/type/
  declaration-name roles, mirroring every other language extractor in this registry. Every
  symbol-matching ``identifier``/``type_identifier`` not already claimed by one of the special
  cases above, and not itself a declaration/definition site (a function name, a variable/parameter/
  field declarator name, a struct/union/enum tag's own defining ``name`` field, an ``enumerator``'s
  own ``name`` field), is ``ref_kind="type"`` when it is a ``type_identifier`` (a struct/union/enum
  tag or a typedef alias used as a type, e.g. ``struct Widget w;``'s or ``Point p;``'s type name),
  else ``ref_kind="value"`` (a plain variable/enum-constant/function-pointer-as-value use).
- ``string_literal`` / ``comment``: never walked as any of the above node types, so a symbol name
  appearing inside a string literal or a comment is structurally excluded.

RESOLUTION CONFIDENCE / PROVENANCE (the two-band honesty shape Java/C#/PHP already ship,
C-specific mechanism and numbers -- and an HONEST, DELIBERATE NARROWING vs those three languages'
receiver-type confirmation, not an oversight):

- ``_C_DEMOTED_CONFIDENCE`` (0.6) / ``_C_DEMOTED_PROVENANCE`` (``"c-name-heuristic"``): the DEFAULT
  band for every entry -- an AST-confirmed node (a real call/field-access/type/value site, never a
  string literal or comment) that this module cannot independently confirm from THIS file's AST.
- ``_C_CONFIRMED_CONFIDENCE`` (0.9) / ``_C_CONFIRMED_PROVENANCE``
  (``"c-infile-function-declared"``): fires in exactly ONE shape -- a ``call_expression`` whose
  ``function`` field is a bare ``identifier`` matching *symbol*, where *symbol* also names a REAL
  function in THIS file: either a ``function_definition``, or a ``declaration`` whose declarator
  chain ``_c_declarator_name_node`` resolves with ``seen_function=True`` (a genuine prototype, not
  a function-pointer variable -- reusing the SAME #736-safe declarator walk ``c_imports_and_symbols``
  already uses for def extraction, so this confirmation signal inherits that fix rather than
  re-deriving declarator-shape logic a second time). This is what naturally, structurally
  discriminates a real function call from a function-pointer-variable call (``fp(3)`` where
  ``fp`` is declared ``void (*fp)(int);`` -- ``_c_declarator_name_node`` resolves its declaration
  with ``seen_function=False``, so ``fp`` never confirms) and from a function-like macro call
  (``ADD_MACRO(1,2)`` where no ``function_definition``/prototype named ``ADD_MACRO`` exists in this
  file) WITHOUT any name-pattern heuristic (no "is it ALL_CAPS" guess) -- both stay honestly
  demoted because neither has a matching in-file function declaration, exactly like an unresolved
  call to a function declared only in another translation unit (a standard-library call like
  ``printf()``, or an ``extern``-declared function with no local prototype).

  Not 1.0: a translation unit can contain a ``static`` function whose name shadows a symbol with
  external linkage elsewhere, or (rarer) a macro that happens to share a real function's name and
  is itself invoked at this call site instead of the function -- 0.9 reflects "real evidence, not
  proof of soundness", matching Java's/C#'s/PHP's identical 0.9 rationale.

  DELIBERATELY NOT CONFIRMED (the honest narrowing this module's docstring promised above):
  - A call through a struct/union member (``w.handler(4)`` / ``p->handler(5)``) ALWAYS stays
    demoted. C's field-pointer confirmation would need to resolve the receiver's declared struct
    type from a local declaration, then check that type's body for a member named *symbol* -- the
    exact PHP/Java/C# shape -- but C structs commonly alias through ``typedef``, get reached via
    ``void *``/generic-container casts, or live behind an opaque forward-declared pointer with no
    body in this file at all (a common C idiom); attempting the PHP-style receiver-type walk here
    would silently overclaim confidence on exactly the cases where C's type system gives the LEAST
    guarantee. This module chooses honest demotion over a plausible-looking but unsound confirmation.
  - Non-call field access (``w.x``, ``p->x``) and every type/value reference stay demoted for the
    same reason every other language in this registry demotes its own non-call type/value
    references (Java/C#/PHP's own generic-identifier walks never confirm these either -- only a
    call/member-call gets the confirmation attempt in any of these modules).
  - A call to a function that IS declared/defined in this file, but under a DIFFERENT name reached
    through a macro/typedef alias for the function pointer type, is not specially detected -- an
    accepted, documented gap (this module never fabricates a resolution it cannot derive from a
    direct AST match).
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Duplicated tiny helpers -- see the module docstring: no import from repo_map.py, to avoid an
# import cycle (repo_map.py imports THIS module). Keep byte-identical to repo_map.py's twins
# (``_tree_sitter_node_text`` / ``_is_clean_symbol_name`` / ``_symbol_record``) -- and to
# ``lang_go.py``/``lang_csharp.py``/``lang_php.py``'s own copies of the same three -- if any of
# them ever change there.
# ---------------------------------------------------------------------------

_CLEAN_SYMBOL_NAME_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")


def _is_clean_symbol_name(name: str) -> bool:
    return bool(_CLEAN_SYMBOL_NAME_RE.match(name))


def _tree_sitter_node_text(source_bytes: bytes, node: Any) -> str:
    return source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _symbol_record(
    *,
    name: str,
    kind: str,
    file: Path,
    start_line: int,
    end_line: int | None = None,
) -> dict[str, Any]:
    normalized_end_line = start_line if end_line is None else end_line
    return {
        "name": name,
        "kind": kind,
        "file": str(file),
        "line": start_line,
        "start_line": start_line,
        "end_line": normalized_end_line,
    }


# ---------------------------------------------------------------------------
# Parser factory (clone of lang_go.py's ``_go_parser`` shape).
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _c_parser() -> Any | None:
    try:
        import tree_sitter
        import tree_sitter_c
    except ImportError:
        return None

    language = tree_sitter.Language(tree_sitter_c.language())
    return tree_sitter.Parser(language)


# ---------------------------------------------------------------------------
# Defs + imports: one tree-sitter pass per file.
# ---------------------------------------------------------------------------

# struct/union/enum specifiers collapse to kind "class" -- the fail-closed cross-language mapping
# this campaign's other struct-bearing languages already use (C#'s own struct/interface/enum ->
# "class" precedent).
_C_CLASS_LIKE_KINDS = frozenset({"struct_specifier", "union_specifier", "enum_specifier"})
# Node types a C def can appear as -- informational/documentation only (mirrors
# lang_go._GO_DEF_NODE_KINDS' role), matching LanguageSpec.def_node_kinds' "Stage 0:
# informational only" contract.
_C_DEF_NODE_KINDS = (
    "function_definition",
    "declaration",
    "struct_specifier",
    "union_specifier",
    "enum_specifier",
    "type_definition",
)
# _c_declarator_name_node's backstop hop limit: no real C declarator nests anywhere close to this
# deep (the deepest live-verified fixture -- `typedef char *(*ComplexFuncPtr)(int, char);`, a
# pointer-returning function-pointer typedef -- took 5 hops: pointer_declarator ->
# function_declarator -> parenthesized_declarator -> pointer_declarator -> type_identifier); this
# only guards against an unforeseen future grammar cycle, never hit in practice.
_MAX_DECLARATOR_HOPS = 64


def _c_parenthesized_declarator_wraps_bare_name(node: Any) -> bool:
    """Return True when a ``parenthesized_declarator`` node's single named child is a bare
    ``identifier``/``type_identifier``/``field_identifier`` -- i.e. the parens are purely
    REDUNDANT around a real name (``int (foo)(void);``, live-verified: ``function_declarator``'s
    own "declarator" field is a ``parenthesized_declarator`` wrapping ``identifier`` "foo"
    directly) -- as opposed to wrapping a ``pointer_declarator``/``array_declarator``/anything
    else (the tell for a function-pointer or array variable, e.g. ``void (*handler)(int);``,
    where the same parenthesized shape instead wraps a ``pointer_declarator``). Only a single
    hop's own wrapped shape is inspected here; a nested ``parenthesized_declarator`` (doubly
    redundant parens) falls through to False like any other non-bare-name wrap -- a residual,
    exceedingly rare edge case, not one any live-verified fixture has hit."""
    named_children = [child for child in node.children if child.is_named]
    if len(named_children) != 1:
        return False
    return named_children[0].type in {"identifier", "type_identifier", "field_identifier"}


def _c_declarator_name_node(declarator: Any) -> tuple[Any | None, bool]:
    """Descend a declarator chain to its innermost identifier/type_identifier NAME node, tracking
    whether the chain passed through a ``function_declarator`` that names a REAL function --
    a prototype/definition, or a function that returns a pointer or a function pointer -- as
    opposed to a file-scope function-pointer VARIABLE's declarator, which is also
    ``function_declarator``-shaped but must NOT set this signal (see below).

    Live-verified against real tree_sitter_c 0.24.2 parses (see the module docstring's
    "DECLARATOR NAME RESOLUTION" section) across every shape this module's callers hit:
    plain (``identifier``/``type_identifier`` directly), pointer (``pointer_declarator``),
    array (``array_declarator``), function (``function_declarator``, whose OWN "declarator"
    field is the plain name), pointer-to-function (``pointer_declarator`` wrapping
    ``function_declarator``), and the function-pointer typedef's ``parenthesized_declarator``
    (which, uniquely among these, exposes NO named "declarator" field -- the loop falls back to
    the wrapper's single NAMED child, which resolves through to the inner
    ``pointer_declarator``/name).

    A file-scope function-pointer VARIABLE (``void (*handler)(int);``) is declarator-shaped
    almost identically to a real function -- its outermost node is ALSO a
    ``function_declarator`` ("outermost-direct" does NOT distinguish the two; both are
    outermost-direct). The real, live-verified tell: a REAL function's ``function_declarator``
    has its own "declarator" FIELD as a bare name -- directly (``void f(int);``), nested one hop
    under a return-type ``pointer_declarator`` (``int *make_ptr(void);``), OR wrapped in
    REDUNDANT parens (``int (foo)(void);`` -- ``function_declarator``'s own "declarator" field is
    a ``parenthesized_declarator`` wrapping ``identifier`` "foo" directly, still a real function,
    the parens are meaningless noise) -- while a function-pointer VARIABLE's
    ``function_declarator`` instead has its own "declarator" field as a
    ``parenthesized_declarator`` wrapping something OTHER than a bare name -- a
    ``pointer_declarator`` down to the name (the paren-grouped ``(*name)``). So a
    ``parenthesized_declarator`` hop is not by itself the tell (a redundant-paren prototype has
    one too); ``_c_parenthesized_declarator_wraps_bare_name`` resolves the ambiguity by checking
    what it wraps. A hop through the variable-shaped form does not, by itself, mark
    ``seen_function`` True -- but the boolean is never force-reset to False either, so a function
    that itself RETURNS a function pointer (``void (*get_handler(int))(int);``, the classic
    ``signal()`` prototype shape) still resolves correctly: its outer ``function_declarator`` hop
    is skipped (its parens wrap a ``pointer_declarator``, not a bare name), but the INNER
    ``function_declarator`` -- the function's own parameter list -- has a bare-identifier
    declarator field directly (no parens at all), so ``seen_function`` still ends up True from
    that hop. Live-verified against real tree_sitter_c 0.24.2 parses of all of the above,
    including the full ``void (*signal(int sig, void (*func)(int)))(int);`` shape (the
    function-pointer PARAMETER inside is never visited -- only the top-level declarator chain
    is walked).

    Returns ``(None, seen_function)`` if no name-bearing leaf was found (e.g. an abstract
    declarator with no name, such as a bare ``void`` parameter) -- callers must check for
    ``None`` before using the result.
    """
    seen_function = False
    current = declarator
    hops = 0
    while current is not None and hops < _MAX_DECLARATOR_HOPS:
        hops += 1
        if current.type in {"identifier", "type_identifier", "field_identifier"}:
            return current, seen_function
        next_node = current.child_by_field_name("declarator")
        if current.type == "function_declarator":
            # A `parenthesized_declarator` hop is not by itself the function-pointer-variable
            # tell -- a redundant-paren prototype (`int (foo)(void);`) has one too, wrapping a
            # bare identifier. Only a paren wrap around something ELSE (`pointer_declarator`,
            # `array_declarator`, ...) is the real tell (see the docstring). Not
            # force-resetting `seen_function` to False here is deliberate: it lets a function
            # that returns a function pointer still resolve True via its own (inner)
            # function_declarator hop.
            is_variable_shaped_parens = (
                next_node is not None
                and next_node.type == "parenthesized_declarator"
                and not _c_parenthesized_declarator_wraps_bare_name(next_node)
            )
            if not is_variable_shaped_parens:
                seen_function = True
        if next_node is not None:
            current = next_node
            continue
        named_children = [child for child in current.children if child.is_named]
        if len(named_children) == 1:
            current = named_children[0]
            continue
        return None, seen_function
    return None, seen_function


def _c_include_target_text(path_field: Any, source_bytes: bytes) -> str | None:
    """Return a ``preproc_include`` node's target text, quote/bracket-stripped where the include
    form carries delimiters. See the module docstring's ``#include`` node-shape table for the
    four forms this handles."""
    if path_field is None:
        return None
    if path_field.type == "system_lib_string":
        raw = _tree_sitter_node_text(source_bytes, path_field)
        if len(raw) >= 2 and raw[0] == "<" and raw[-1] == ">":
            return raw[1:-1]
        return raw
    if path_field.type == "string_literal":
        content_node = next(
            (child for child in path_field.children if child.type == "string_content"),
            None,
        )
        if content_node is not None:
            return _tree_sitter_node_text(source_bytes, content_node)
        # Fallback (mirrors lang_go.py's F11 quote-stripping fallback): an empty ``#include ""``
        # or an unusual grammar build with no ``string_content`` child still yields a clean
        # module string instead of silently dropping the row.
        raw = _tree_sitter_node_text(source_bytes, path_field)
        if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
            return raw[1:-1]
        return raw
    # identifier (macro name, e.g. MACRO_HEADER) / call_expression (macro-combined, e.g.
    # COMBINE(a,b)) / anything else: no delimiters to strip -- the raw text IS the honest
    # include target (this module never fabricates a resolved path for these either way; see
    # repo_map.py's `_resolve_raw_import_entry` "c" branch).
    return _tree_sitter_node_text(source_bytes, path_field)


def c_imports_and_symbols(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    """Extract ``#include`` targets + function/struct/union/enum/typedef definitions from a C
    source file, one AST pass (mirrors ``lang_go.go_imports_and_symbols``'s shape).

    Defs covered: ``function_definition`` (kind "function"), a ``declaration`` whose declarator
    chain passes through a ``function_declarator`` (a prototype, kind "function" -- a plain
    variable declaration is excluded, see the module docstring), ``struct_specifier``/
    ``union_specifier``/``enum_specifier`` WITH a body (kind "class" -- a body-less
    forward-declaration/usage-as-type is excluded), and ``type_definition`` (kind "type", one
    record per declarator so ``typedef int A, B;`` yields both). Imports come from every
    ``preproc_include`` directive's target text (see ``_c_include_target_text``).
    """
    if path.suffix != ".c":
        return [], []

    parser = _c_parser()
    if parser is None:
        return [], []

    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return [], []

    source_bytes = source.encode("utf-8")
    tree = parser.parse(source_bytes)

    def _node_text(node: Any) -> str:
        return _tree_sitter_node_text(source_bytes, node)

    imports: list[str] = []
    symbols: list[dict[str, Any]] = []

    def _walk(root: Any) -> None:
        # Explicit-stack DFS instead of recursion, matching lang_go.py's walkers (F26 precedent:
        # a pathologically deep AST can never raise RecursionError). Children pushed in reverse
        # so the leftmost child is popped (visited) first, preserving pre-order traversal. A
        # plain (non-recursive) stack walk also naturally reaches nodes nested inside
        # `preproc_ifdef`/`preproc_if`/`preproc_elif` guards (live-verified: both branches of an
        # `#ifdef`/`#else` parse simultaneously as ordinary children) -- no special-casing needed.
        stack = [root]
        while stack:
            node = stack.pop()
            node_type = node.type
            if node_type == "preproc_include":
                path_field = node.child_by_field_name("path")
                target = _c_include_target_text(path_field, source_bytes)
                if target:
                    imports.append(target)
            elif node_type == "function_definition":
                for declarator in node.children_by_field_name("declarator"):
                    name_node, _seen_function = _c_declarator_name_node(declarator)
                    if name_node is None:
                        continue
                    name = _node_text(name_node)
                    if _is_clean_symbol_name(name):
                        symbols.append(
                            _symbol_record(
                                name=name,
                                kind="function",
                                file=path,
                                start_line=node.start_point[0] + 1,
                                end_line=node.end_point[0] + 1,
                            )
                        )
            elif node_type == "declaration":
                for declarator in node.children_by_field_name("declarator"):
                    name_node, is_function = _c_declarator_name_node(declarator)
                    if not is_function or name_node is None:
                        continue
                    name = _node_text(name_node)
                    if _is_clean_symbol_name(name):
                        symbols.append(
                            _symbol_record(
                                name=name,
                                kind="function",
                                file=path,
                                start_line=node.start_point[0] + 1,
                                end_line=node.end_point[0] + 1,
                            )
                        )
            elif node_type in _C_CLASS_LIKE_KINDS:
                body_field = node.child_by_field_name("body")
                name_field = node.child_by_field_name("name")
                if body_field is not None and name_field is not None:
                    name = _node_text(name_field)
                    if _is_clean_symbol_name(name):
                        symbols.append(
                            _symbol_record(
                                name=name,
                                kind="class",
                                file=path,
                                start_line=node.start_point[0] + 1,
                                end_line=node.end_point[0] + 1,
                            )
                        )
            elif node_type == "type_definition":
                for declarator in node.children_by_field_name("declarator"):
                    name_node, _seen_function = _c_declarator_name_node(declarator)
                    if name_node is None:
                        continue
                    name = _node_text(name_node)
                    if _is_clean_symbol_name(name):
                        symbols.append(
                            _symbol_record(
                                name=name,
                                kind="type",
                                file=path,
                                start_line=node.start_point[0] + 1,
                                end_line=node.end_point[0] + 1,
                            )
                        )
            stack.extend(reversed(node.children))

    _walk(tree.root_node)
    imports = sorted(dict.fromkeys(imports))
    symbols.sort(key=lambda item: (item["file"], item["line"], item["kind"], item["name"]))
    return imports, symbols


# #74-follow-up: `tg imports` foundational-tier extractor (mirrors repo_map.py's
# `_java_imports_with_lines` shape/role exactly). One row per `preproc_include` STATEMENT with
# its 1-based line number -- same extraction source as `c_imports_and_symbols` above (every
# `preproc_include`'s target text via `_c_include_target_text`), just line-tagged instead of
# deduped into a flat list.
#
# Deliberately NOT resolved to a target file: repo_map.py's `_resolve_raw_import_entry` "c"
# branch keeps every row unresolved (resolved=None, external=False) -- true `#include` -> file
# resolution has no standardized C manifest to resolve against (see the module docstring), so a
# real path is not guessable without fabricating one.
def c_imports_with_lines(path: Path) -> list[dict[str, Any]]:
    if path.suffix != ".c":
        return []

    parser = _c_parser()
    if parser is None:
        return []

    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    source_bytes = source.encode("utf-8")
    tree = parser.parse(source_bytes)

    entries: list[dict[str, Any]] = []

    def _walk(root: Any) -> None:
        # Explicit-stack DFS -- see the identical comment on c_imports_and_symbols's `_walk`.
        stack = [root]
        while stack:
            node = stack.pop()
            if node.type == "preproc_include":
                path_field = node.child_by_field_name("path")
                target = _c_include_target_text(path_field, source_bytes)
                if target:
                    entries.append({
                        "module": target,
                        "line": node.start_point[0] + 1,
                    })
            stack.extend(reversed(node.children))

    _walk(tree.root_node)
    return entries


def c_parser_symbol_sources(path: Path, symbol: str) -> list[dict[str, Any]]:
    """Full source text of every function/struct/union/enum/typedef matching *symbol* (mirrors
    the Go/C#/PHP ``*_parser_symbol_sources`` shape for the ``tg source`` command).

    A function/typedef appearing both as a prototype/forward form and a full definition emits a
    source block for EACH matching AST node (no dedup/preference between them) -- the same
    "every real AST node is a legitimate hit" behavior C#'s own module already ships (an
    interface method and its class implementation sharing a name both resolve as separate
    ``tg source`` blocks)."""
    if path.suffix != ".c":
        return []

    parser = _c_parser()
    if parser is None:
        return []

    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    source_bytes = source.encode("utf-8")
    tree = parser.parse(source_bytes)
    sources: list[dict[str, Any]] = []

    def _node_text(node: Any) -> str:
        return _tree_sitter_node_text(source_bytes, node)

    def _append(node: Any, kind: str) -> None:
        block = _node_text(node)
        if block and not block.endswith("\n"):
            block = f"{block}\n"
        sources.append({
            "name": symbol,
            "kind": kind,
            "file": str(path),
            "start_line": node.start_point[0] + 1,
            "end_line": node.end_point[0] + 1,
            "source": block,
        })

    def _walk(root: Any) -> None:
        # Explicit-stack DFS -- see the identical comment on c_imports_and_symbols's `_walk`.
        stack = [root]
        while stack:
            node = stack.pop()
            node_type = node.type
            if node_type == "function_definition":
                for declarator in node.children_by_field_name("declarator"):
                    name_node, _seen_function = _c_declarator_name_node(declarator)
                    if name_node is not None and _node_text(name_node) == symbol:
                        _append(node, "function")
                        break
            elif node_type == "declaration":
                for declarator in node.children_by_field_name("declarator"):
                    name_node, is_function = _c_declarator_name_node(declarator)
                    if is_function and name_node is not None and _node_text(name_node) == symbol:
                        _append(node, "function")
                        break
            elif node_type in _C_CLASS_LIKE_KINDS:
                body_field = node.child_by_field_name("body")
                name_field = node.child_by_field_name("name")
                if (
                    body_field is not None
                    and name_field is not None
                    and _node_text(name_field) == symbol
                ):
                    _append(node, "class")
            elif node_type == "type_definition":
                for declarator in node.children_by_field_name("declarator"):
                    name_node, _seen_function = _c_declarator_name_node(declarator)
                    if name_node is not None and _node_text(name_node) == symbol:
                        _append(node, "type")
                        break
            stack.extend(reversed(node.children))

    _walk(tree.root_node)
    sources.sort(key=lambda item: (item["file"], item["start_line"], item["kind"], item["name"]))
    return sources


# ---------------------------------------------------------------------------
# Task 10D: references + calls (in-file AST extraction, no cross-file resolution).
# See the module docstring's "TASK 10D CALL/ACCESS NODE SHAPES" / "RESOLUTION CONFIDENCE /
# PROVENANCE" sections for the full derivation of the two honesty bands and C's narrower
# confirmable population vs Java/C#/PHP.
# ---------------------------------------------------------------------------

_C_DEMOTED_CONFIDENCE = 0.6
_C_DEMOTED_PROVENANCE = "c-name-heuristic"
_C_CONFIRMED_CONFIDENCE = 0.9
_C_CONFIRMED_PROVENANCE = "c-infile-function-declared"

# Node types whose "declarator" field (possibly wrapped, resolved via
# _c_declarator_name_node) names a DEFINITION site -- excluded from the reference/call walk (a
# symbol's own declaration site is not a reference to itself, the same rule every other language
# in this registry follows).
_C_DECLARATOR_DEFINING_NODE_TYPES = frozenset({
    "function_definition",
    "declaration",
    "type_definition",
    "parameter_declaration",
    "field_declaration",
})


def _c_symbol_has_infile_function(root: Any, source_bytes: bytes, symbol: str) -> bool:
    """True iff *symbol* names a REAL function in this file -- a ``function_definition``, or a
    ``declaration`` whose declarator chain ``_c_declarator_name_node`` resolves with
    ``seen_function=True`` (a genuine prototype, never a function-pointer variable). This is the
    ONLY confirmation signal Task 10D uses (see the module docstring's RESOLUTION CONFIDENCE
    section) -- reusing the SAME #736-safe declarator walk ``c_imports_and_symbols`` already uses
    for def extraction, so a function-pointer variable sharing this query's name can never
    falsely confirm a call to it (the exact declarator-shape hazard PR #736 fixed).
    """

    def node_text(node: Any) -> str:
        return _tree_sitter_node_text(source_bytes, node)

    stack = [root]
    while stack:
        node = stack.pop()
        node_type = node.type
        if node_type == "function_definition":
            for declarator in node.children_by_field_name("declarator"):
                name_node, _seen_function = _c_declarator_name_node(declarator)
                if name_node is not None and node_text(name_node) == symbol:
                    return True
        elif node_type == "declaration":
            for declarator in node.children_by_field_name("declarator"):
                name_node, is_function = _c_declarator_name_node(declarator)
                if is_function and name_node is not None and node_text(name_node) == symbol:
                    return True
        stack.extend(node.children)
    return False


def _c_definition_site_positions(root: Any) -> set[tuple[int, int]]:
    """Collect the byte-span of every DEFINITION-site name node in this file -- a function name, a
    variable/parameter/struct-field declarator name, a struct/union/enum tag's own defining
    ``name`` field (only when a ``body`` is present -- a body-less usage like ``struct Foo *p`` is
    a REFERENCE, not a definition), and an ``enumerator``'s own ``name`` field. Reuses
    ``_c_declarator_name_node`` for every declarator-shaped case so a pointer/array/function-
    pointer-wrapped declarator name resolves exactly like ``c_imports_and_symbols``'s own
    extraction does (same #736-safe walk, single source of truth)."""
    positions: set[tuple[int, int]] = set()
    stack = [root]
    while stack:
        node = stack.pop()
        node_type = node.type
        if node_type in _C_DECLARATOR_DEFINING_NODE_TYPES:
            # `children_by_field_name` (plural) uniformly for every node type here -- a
            # `field_declaration`/`declaration` can carry MULTIPLE "declarator" fields for a
            # multi-name statement (`int x, y;`); it degrades gracefully to zero-or-one item for
            # `parameter_declaration`/`function_definition`/`type_definition`, which never do.
            for declarator in node.children_by_field_name("declarator"):
                name_node, _seen_function = _c_declarator_name_node(declarator)
                if name_node is not None:
                    positions.add((name_node.start_byte, name_node.end_byte))
        elif node_type in _C_CLASS_LIKE_KINDS:
            body_field = node.child_by_field_name("body")
            name_field = node.child_by_field_name("name")
            if body_field is not None and name_field is not None:
                positions.add((name_field.start_byte, name_field.end_byte))
        elif node_type == "enumerator":
            name_field = node.child_by_field_name("name")
            if name_field is not None:
                positions.add((name_field.start_byte, name_field.end_byte))
        stack.extend(node.children)
    return positions


def c_references_and_calls(
    path: Path, symbol: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """In-file AST reference/call rows for *symbol* in *path* -- see the module docstring's
    "TASK 10D CALL/ACCESS NODE SHAPES" section for the full AST-shape mapping. Scope: single-file
    only, no cross-file resolution (mirrors ``lang_java.java_references_and_calls``/
    ``lang_csharp.csharp_references_and_calls``/``lang_php.php_references_and_calls`` exactly).
    Owns its own parser factory (``_c_parser()``, defined above), matching ``lang_csharp.py``'s/
    ``lang_php.py``'s shape rather than ``lang_java.py``'s externally-built-parser shape -- C
    already had its own grammar-probing factory before Task 10D (needed by
    ``c_imports_and_symbols``), so a second factory here would create two sources of truth for
    "is the C grammar installed"; this function reuses the SAME ``_c_parser()`` every other
    function in this module already calls.
    """
    if path.suffix != ".c":
        return [], []

    parser = _c_parser()
    if parser is None:
        return [], []

    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return [], []

    source_bytes = source.encode("utf-8")
    tree = parser.parse(source_bytes)
    # Split strictly on "\n" (tree-sitter's own row semantics), stripping a trailing "\r" so
    # CRLF-terminated files still read cleanly -- see lang_go.go_references_and_calls's identical
    # comment (F26 fix, audit #63) for why `str.splitlines()` is NOT safe here.
    lines = [line.rstrip("\r") for line in source.split("\n")]
    references: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []

    def _node_text(node: Any) -> str:
        return _tree_sitter_node_text(source_bytes, node)

    def _line_text(node: Any) -> str:
        line_index = node.start_point[0]
        return lines[line_index] if 0 <= line_index < len(lines) else ""

    definition_positions = _c_definition_site_positions(tree.root_node)
    function_confirmed = _c_symbol_has_infile_function(tree.root_node, source_bytes, symbol)

    def _is_definition_site(node: Any) -> bool:
        return (node.start_byte, node.end_byte) in definition_positions

    def _emit(
        bucket: list[dict[str, Any]], node: Any, *, kind: str, ref_kind: str, confirmed: bool
    ) -> None:
        if confirmed:
            confidence = _C_CONFIRMED_CONFIDENCE
            provenance = _C_CONFIRMED_PROVENANCE
        else:
            confidence = _C_DEMOTED_CONFIDENCE
            provenance = _C_DEMOTED_PROVENANCE
        bucket.append({
            "name": symbol,
            "kind": kind,
            "ref_kind": ref_kind,
            "file": str(path),
            "line": node.start_point[0] + 1,
            "text": _line_text(node),
            # PER-MATCH honesty band -- see the module docstring's RESOLUTION CONFIDENCE /
            # PROVENANCE section for the full derivation.
            "resolution_confidence": confidence,
            "resolution_provenance": [provenance],
        })

    # Nodes already claimed by the call_expression special case below are tracked here so the
    # generic identifier/type_identifier/field_identifier walk never double-emits them. Keyed on
    # (start_byte, end_byte), NOT Python `id()` -- see lang_java.py's/lang_csharp.py's/
    # lang_php.py's identical comment for why (tree_sitter mints a fresh wrapper object on every
    # `.children`/`.child_by_field_name` access to the same underlying node).
    claimed_node_ids: set[tuple[int, int]] = set()

    def _walk_calls(root: Any) -> None:
        # Explicit-stack DFS (not recursion) -- matches every other language extractor in this
        # registry; avoids a RecursionError on a pathologically deep real-world AST.
        stack = [root]
        while stack:
            node = stack.pop()
            if node.type == "call_expression":
                function_field = node.child_by_field_name("function")
                if function_field is not None and function_field.type == "identifier":
                    if _node_text(function_field) == symbol:
                        claimed_node_ids.add((function_field.start_byte, function_field.end_byte))
                        _emit(
                            references,
                            function_field,
                            kind="reference",
                            ref_kind="call",
                            confirmed=function_confirmed,
                        )
                        _emit(
                            calls,
                            function_field,
                            kind="call",
                            ref_kind="call",
                            confirmed=function_confirmed,
                        )
                elif function_field is not None and function_field.type == "field_expression":
                    field_child = function_field.child_by_field_name("field")
                    if field_child is not None and _node_text(field_child) == symbol:
                        claimed_node_ids.add((field_child.start_byte, field_child.end_byte))
                        # A call reached through a struct/union member (`w.handler(4)` /
                        # `p->handler(5)`) ALWAYS stays demoted -- see the module docstring's
                        # RESOLUTION CONFIDENCE section for why C deliberately does not attempt a
                        # PHP/Java/C#-style receiver-type confirmation here.
                        _emit(
                            references,
                            field_child,
                            kind="reference",
                            ref_kind="call",
                            confirmed=False,
                        )
                        _emit(calls, field_child, kind="call", ref_kind="call", confirmed=False)
            stack.extend(reversed(node.children))

    def _walk_generic_identifiers(root: Any) -> None:
        stack = [root]
        while stack:
            node = stack.pop()
            node_type = node.type
            claim_key = (node.start_byte, node.end_byte)
            if claim_key not in claimed_node_ids and not _is_definition_site(node):
                if node_type == "field_expression":
                    field_child = node.child_by_field_name("field")
                    if (
                        field_child is not None
                        and _node_text(field_child) == symbol
                        and (field_child.start_byte, field_child.end_byte) not in claimed_node_ids
                    ):
                        claimed_node_ids.add((field_child.start_byte, field_child.end_byte))
                        _emit(
                            references,
                            field_child,
                            kind="reference",
                            ref_kind="field",
                            confirmed=False,
                        )
                elif node_type == "type_identifier" and _node_text(node) == symbol:
                    _emit(references, node, kind="reference", ref_kind="type", confirmed=False)
                elif (
                    node_type == "identifier"
                    and _node_text(node) == symbol
                    and claim_key not in claimed_node_ids
                ):
                    _emit(references, node, kind="reference", ref_kind="value", confirmed=False)
            stack.extend(reversed(node.children))

    _walk_calls(tree.root_node)
    _walk_generic_identifiers(tree.root_node)

    references.sort(key=lambda item: (item["file"], item["line"], item["text"]))
    calls.sort(key=lambda item: (item["file"], item["line"], item["text"]))
    return references, calls


__all__ = [
    "c_imports_and_symbols",
    "c_imports_with_lines",
    "c_parser_symbol_sources",
    "c_references_and_calls",
]
