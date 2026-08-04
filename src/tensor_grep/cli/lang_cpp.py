"""C++ language extractor for tensor-grep's multi-language symbol graph (PATH A Stage 3).

Tenth language expansion (top-10 language-support campaign, Phase 2 of C/C++ -- closes the
top-10 symbol-graph tier to 10/10; C shipped first as Phase 1, #731/v1.97.0). Sibling of
``lang_c.py``/``lang_go.py``/``lang_csharp.py``/``lang_php.py`` (see those modules' docstrings
for the full "PATH A Stage 1" framing). Plugs into the ``lang_registry`` seam Stage 0 built (see
``lang_registry.py`` + the ``lang_registry.register_language(...)`` calls near the bottom of
``repo_map.py``) -- C++ gets its OWN ``LanguageSpec`` entry, registered from ``repo_map.py``,
separate from C's (two grammar packages, ``tree-sitter-c`` vs ``tree-sitter-cpp``, mirroring the
shipped JS/TS "two specs, not one mode flag" precedent -- ``_SPEC_BY_SUFFIX`` is a flat
suffix->ONE-spec dict, and ``.h`` must resolve to exactly one language, see below).

FOUNDATIONAL SCOPE (same tier Java/PHP/C#/Go/C landed at): this module lights up
``defs``/``source``/``imports``/``agent`` for C++ files -- function definitions AND prototypes
(kind "function", including qualified out-of-class method definitions like ``Foo::bar()`` and
in-class method prototypes), class/struct/union/enum definitions (kind "class", the same
fail-closed struct/union/enum -> "class" mapping C/C#/Java already use), namespace definitions
(kind "namespace", matching the label ``_lsp_symbol_kind_name`` already uses for LSP symbol kind
3 -- not a novel vocabulary item), and type aliases -- both ``typedef`` (kind "type", mirroring
C's own ``type_definition`` handling) and C++11 ``using X = ...`` alias declarations (kind
"type", same bucket). Template-wrapped declarations (``template_declaration``) are NOT
specially matched in the walker -- the explicit-stack DFS naturally descends into whatever a
template wraps (a class/function/qualified method definition), so no separate branch is needed;
the emitted kind is the WRAPPED construct's own kind, not a distinct "template" kind. C++20
``concept_definition`` is DELIBERATELY NOT extracted (parses cleanly, but is low-value for a
foundational landing -- deferred, matching the design plan's own "optional, can defer" call).
The cross-file caller-graph (``references_and_calls``/``file_imports_symbol_from_definition``/
``import_update_target``/``prime_repo_context``) is DEFERRED to a follow-up -- this module
registers all four of those ``LanguageSpec`` fields as ``None``, exactly like every other
foundational-tier language's own precedent. ``tg refs``/``tg callers``/``tg blast-radius`` on a
C++ symbol fall through to the generic ``_regex_references_and_calls`` text-heuristic path
(never a crash, never a fabricated AST-verified match) -- unaffected by this module.

``.h`` IS claimed by this module (unlike ``lang_c.py``, which deliberately excludes it).
``_provider_language_for_path`` (the LSP provider dispatch, repo_map.py) ALREADY assigns every
C/C++ header suffix (``.h``, ``.hh``, ``.hpp``, ``.hxx``) plus ``.cc``/``.cpp``/``.cxx`` to
``"cpp"`` -- a latent pre-wiring that PREDATES this module and effectively forces the choice:
``language_id="cpp"`` and this exact 7-suffix set, or ``test_target_and_provider_language_agree_
with_registry`` fails. tree-sitter-cpp is a strict grammar superset of tree-sitter-c, so a
pure-C header parses fine under this module's grammar too (the header-ambiguity tradeoff
``lang_c.py``'s own docstring already documents and defers to this module).

FAIL-CLOSED CONTRACT (Stage 0 honesty floor, extended here exactly as it was for Go/PHP/C#/C):
C++ has NO regex-heuristic fallback. When the ``tree_sitter_cpp`` grammar package is not
installed, every extractor in this module returns empty ([]/([], [])) rather than degrading to a
regex/text heuristic (unlike JS/TS/Rust, which all fall back to regex extraction when their own
tree-sitter grammar is missing). ``LanguageSpec.provenance_when_missing="grammar-missing"`` (NOT
``"regex-heuristic"``) is what makes ``_language_coverage_gaps_for_universe`` in repo_map.py
treat a grammar-absent C++ file as a genuine ``resolution_gaps`` entry instead of silently
reporting zero matches as if the symbol just did not exist.

``#include`` IS a parse-tree node (tree-sitter-cpp does not run a preprocessor, so it never
strips or expands a ``#include`` directive) -- ``preproc_include`` with a ``path`` field whose
node SHAPE matches tree-sitter-c's exactly (live-verified against a real tree_sitter_cpp 0.23.4
parse, not guessed from the grammar README or assumed identical to C without checking):
  ``#include <stdio.h>``     -> path field type ``system_lib_string``, text ``"<stdio.h>"``
  ``#include "local.h"``     -> path field type ``string_literal``, nested ``string_content``
  ``#include MACRO_HEADER``  -> path field type ``identifier`` (macro-expanded form)
  ``#include COMBINE(a,b)``  -> path field type ``call_expression`` (macro-combined form)
``_cpp_include_target_text`` strips the ``<...>``/``"..."`` delimiters where present, matching
every other language's extractor here. Resolution stays HONEST-UNRESOLVED: repo_map.py's
``_resolve_raw_import_entry`` reports every row ``resolved=None, external=False`` (never a
fabricated path, never a fabricated ``external=True``) -- true ``#include`` -> file resolution
has no standardized C++ manifest to resolve against (no ``go.mod``/``composer.json``/``.csproj``
equivalent) and stays deferred to BACKLOG, same as the go/php/csharp/c resolvers.

DECLARATOR NAME RESOLUTION (extends C's ``_c_declarator_name_node`` with THREE live-verified
C++-only wrinkles, none guessable from a grammar README):

1. QUALIFIED OUT-OF-CLASS NAMES (``Foo::bar``): a ``qualified_identifier`` node has NO
   "declarator" field of its own (its fields are "scope" and "name") and has TWO named children
   (scope + name), so C's generic "single named child" fallback does not apply here -- an
   explicit branch is required. ``_cpp_declarator_name_node`` follows the "name" field (never
   "scope") to continue the descent. This also transparently handles NESTED qualification
   (``app::Thing::compute`` -- the outer qualified_identifier's own "name" field is itself
   ANOTHER qualified_identifier, ``Thing::compute``, requiring the loop to recurse through it) and
   TEMPLATED scopes (``Box<T>::get`` -- the "scope" field is a ``template_type`` wrapping
   ``Box``+``<T>``, but the loop never descends into "scope" at all, so the template arguments
   never interfere with reaching "get"). The emitted symbol name is the BARE method name
   (``getValue``, never ``Widget::getValue``) -- this is a deliberate design choice, not an
   oversight: it is what makes the declaration/definition dedup in ``§1.4`` of the design plan
   work at all (an in-class prototype's bare name and an out-of-class qualified definition's
   resolved bare name must match for ``tg defs getValue`` to find BOTH records from either file).
2. DESTRUCTORS (``~Widget``): a ``destructor_name`` node's own text includes the leading ``~``,
   but it has exactly ONE named child (a plain ``identifier`` holding just ``Widget``, no tilde)
   -- C's EXISTING generic "single named child" fallback already resolves through it correctly
   with NO new branch needed (verified: this is not a coincidence to rely on blindly, it was
   confirmed against a real parse before this module trusted it). The extracted name is the bare
   class name (``Widget``), shared with the constructor's own name -- ``tg defs Widget`` then
   surfaces the class, its constructor(s), AND its destructor together, which reads as useful
   grouping rather than a collision (an explicit, disclosed design choice, not an accident).
3. OPERATOR OVERLOADS (``operator+=``): an ``operator_name`` node has ZERO named children (its
   ``operator`` keyword and the operator token itself are both anonymous) -- C's generic
   fallback returns ``None`` here (0 != 1 named children), so operator overloads are HONESTLY
   EXCLUDED from extraction, not crashed on and not mis-named. Even if a name node were somehow
   produced, ``_is_clean_symbol_name``'s shared ``^[A-Za-z_$][A-Za-z0-9_$]*$`` regex (kept
   byte-identical to every sibling module's own copy, per that regex's own cross-module contract)
   would reject ``"operator+="`` anyway (it is not an identifier shape) -- this exclusion is
   doubly honest, not a special-cased gap.

A ``declaration`` node is ambiguous by TYPE ALONE, exactly as in C (a function PROTOTYPE vs a
plain variable declaration, gated by whether the declarator chain passes through a
``function_declarator``) -- see ``lang_c.py``'s own docstring for the full explanation, unchanged
here. C++ ADDS a second, sibling ambiguity: inside a class/struct/union body, ``field_declaration``
plays the SAME role ``declaration`` plays at file/namespace scope (a typed method prototype like
``int getValue() const;`` vs a plain data member like ``int value_;``, live-verified to
consistently carry the field name in a "declarator" field either way) -- a CONSTRUCTOR prototype
(``Widget(int value);``, no return type) is the one in-class exception that parses as a plain
``declaration`` node instead of ``field_declaration``, gated identically. Both node types are
walked with the SAME ``function_declarator``-presence gate C already established; a plain field
member is silently excluded from the symbol table, matching this module's and C's shared
foundational scope (no top-level/member variable tracking here).

A ``class_specifier``/``struct_specifier``/``union_specifier``/``enum_specifier`` is similarly
ambiguous by type alone (a forward declaration / usage-as-type vs a real definition,
indistinguishable except by a ``body`` field's presence) -- unchanged from C's own explanation;
only a body-bearing specifier is emitted. A ``namespace_definition`` with NO "name" field
(an anonymous ``namespace { ... }`` block, live-verified to omit the field entirely rather than
supply an empty string) is likewise excluded from the symbol table -- its CONTENTS are still
reached and extracted normally via the ordinary stack walk, only the anonymous wrapper itself is
not emitted as a named symbol.

KNOWN, DISCLOSED PREPROCESSOR-RECALL CEILING (live-verified, not merely theorized -- see the
design plan's own risk #1 and this module's originating PR body for the concrete dogfood finding):
tree-sitter-cpp does not expand macros. A plain visibility-macro-prefixed FREE FUNCTION
(``API_EXPORT void f();``) still resolves correctly here (the macro token gets mis-attributed to
the "type" field and an ``ERROR`` node appears as a sibling, but the "declarator" field --
``function_declarator`` -- is UNAFFECTED, so the function name is still recovered). A
macro-prefixed CLASS (``class MYLIB_PUBLIC Name { ... };``) does NOT recover: tree-sitter
misparses the whole construct as a ``function_definition`` (the macro token becomes a fake
"return type" via a body-less ``class_specifier``, and ``Name`` becomes the function's own
declarator), so the class is extracted as kind "function" under its real name instead of kind
"class" -- a genuinely WRONG (not merely missing) kind label. No guard was added to special-case
this: the exact AST shape produced by the misparse (``function_definition`` whose "type" field is
a body-less class/struct/union/enum specifier) is INDISTINGUISHABLE from the shape produced by a
perfectly legitimate, common, non-macro construct -- a function that returns a struct/class BY
VALUE (``struct Point make_point() { ... }`` parses identically). Suppressing the misparse case
would also suppress the legitimate case, which is strictly more common in real code; the honest
choice is to trust the parser's own (occasionally macro-confused) shape rather than add a
heuristic that trades one wrong answer for a different, more frequent one. This is the disclosed,
inherent ceiling of a preprocessor-unaware tool, not a bug to chase.

CONFIRMED ON REAL, LIVE CODE (not just a synthetic fixture -- this module's originating PR
dogfooded two real public headers, CPython's ``Include/object.h`` and LLVM's
``llvm/ADT/StringRef.h``, both fetched fresh, neither vendored into this repo): LLVM's own
``class LLVM_GSL_POINTER StringRef { ... }`` hits the exact misparse above -- the ~830-line
`StringRef` class extracts as kind "function" (7 records, one per overloaded constructor) rather
than one kind "class" record, while its sibling `StringLiteral : public StringRef` (no
macro-prefix) extracts correctly as kind "class". The nuance worth disclosing: MEMBER recall
mostly SURVIVES the container-level misparse -- of `StringRef`'s ~85 distinct public methods,
essentially all of them (``begin``, ``find``, ``substr``, ``split``, ``getAsInteger``, etc.) still
resolve as individual kind "function" symbols, because tree-sitter's error recovery is local to
the misparsed node, not global -- only the ENCLOSING class's own kind label is wrong, not its
members' discoverability. CPython's ``object.h`` (macro-guarded ``#ifdef``/``#else`` struct
bodies, function-pointer typedef style) surfaced a SEPARATE real finding: an anonymous union
prefixed with a visibility macro (``_Py_ANONYMOUS union { ... };``, no tag name) misparses such
that the bare KEYWORD ``union`` itself becomes the extracted declarator text. Unlike the
class/struct-vs-return-type ambiguity above, this ONE ceiling instance IS safely fixable: no valid
C++ program can ever declare a symbol literally named a reserved keyword (``union``, ``class``,
``struct``, ...), so rejecting the C++ keyword set as symbol names (``_is_clean_cpp_symbol_name``,
layered on top of the shared ``_is_clean_symbol_name`` shape check) has ZERO legitimate-code
false-negative cost -- this module does apply that guard, unlike the class-macro-misparse, which
it deliberately does NOT special-case (the false-negative cost there is real and worse than the
false-positive it would fix). See the module's originating PR body for the full dogfood
methodology and quantified numbers.
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
# ``lang_c.py``/``lang_go.py``/``lang_csharp.py``/``lang_php.py``'s own copies of the same three --
# if any of them ever change there.
# ---------------------------------------------------------------------------

_CLEAN_SYMBOL_NAME_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")


def _is_clean_symbol_name(name: str) -> bool:
    return bool(_CLEAN_SYMBOL_NAME_RE.match(name))


# Live-verified dogfood finding (see the module docstring's macro-recall-ceiling section): a
# macro-prefixed ANONYMOUS union/struct/class (e.g. CPython's own
# ``_Py_ANONYMOUS union { ... };``) can misparse such that the bare KEYWORD itself (``union``)
# becomes the extracted "declarator" text -- `_is_clean_symbol_name`'s shape-only regex accepts
# it (it IS identifier-shaped), but no valid C++ program can ever declare a symbol literally
# named `union`/`class`/`struct`/etc. (reserved words can never be identifiers), so this is
# unconditionally a misparse artifact, never a legitimate symbol -- unlike the "class MACRO Name"
# -> function misparse (see the docstring), rejecting a reserved keyword has NO legitimate-code
# false-negative cost, so it is a safe, zero-downside precision fix (found via the real-header
# dogfood this module's originating PR required, not a hypothetical).
_CPP_RESERVED_KEYWORDS = frozenset({
    "alignas",
    "alignof",
    "and",
    "and_eq",
    "asm",
    "auto",
    "bitand",
    "bitor",
    "bool",
    "break",
    "case",
    "catch",
    "char",
    "char8_t",
    "char16_t",
    "char32_t",
    "class",
    "compl",
    "concept",
    "const",
    "consteval",
    "constexpr",
    "constinit",
    "const_cast",
    "continue",
    "co_await",
    "co_return",
    "co_yield",
    "decltype",
    "default",
    "delete",
    "do",
    "double",
    "dynamic_cast",
    "else",
    "enum",
    "explicit",
    "export",
    "extern",
    "false",
    "float",
    "for",
    "friend",
    "goto",
    "if",
    "inline",
    "int",
    "long",
    "mutable",
    "namespace",
    "new",
    "noexcept",
    "not",
    "not_eq",
    "nullptr",
    "operator",
    "or",
    "or_eq",
    "private",
    "protected",
    "public",
    "register",
    "reinterpret_cast",
    "requires",
    "return",
    "short",
    "signed",
    "sizeof",
    "static",
    "static_assert",
    "static_cast",
    "struct",
    "switch",
    "template",
    "this",
    "thread_local",
    "throw",
    "true",
    "try",
    "typedef",
    "typeid",
    "typename",
    "union",
    "unsigned",
    "using",
    "virtual",
    "void",
    "volatile",
    "wchar_t",
    "while",
    "xor",
    "xor_eq",
})


def _is_clean_cpp_symbol_name(name: str) -> bool:
    return _is_clean_symbol_name(name) and name not in _CPP_RESERVED_KEYWORDS


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
# Parser factory (clone of lang_c.py's ``_c_parser`` shape).
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _cpp_parser() -> Any | None:
    try:
        import tree_sitter
        import tree_sitter_cpp
    except ImportError:
        return None

    language = tree_sitter.Language(tree_sitter_cpp.language())
    return tree_sitter.Parser(language)


# ---------------------------------------------------------------------------
# Defs + imports: one tree-sitter pass per file.
# ---------------------------------------------------------------------------

# `.h` is claimed by C++ (not C) -- see the module docstring's header-ambiguity note.
_CPP_SUFFIXES = frozenset({".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"})

# class/struct/union/enum specifiers collapse to kind "class" -- the fail-closed cross-language
# mapping this campaign's other struct-bearing languages already use (C's own struct/union/enum
# and C#'s struct/interface/enum/record precedents).
_CPP_CLASS_LIKE_KINDS = frozenset({
    "class_specifier",
    "struct_specifier",
    "union_specifier",
    "enum_specifier",
})
# Node types a C++ def can appear as -- informational/documentation only (mirrors
# lang_c._C_DEF_NODE_KINDS' role), matching LanguageSpec.def_node_kinds' "Stage 0:
# informational only" contract. `template_declaration` is listed even though the walker never
# matches it directly -- see the module docstring: it is a transparent wrapper the explicit-stack
# DFS descends into for free.
_CPP_DEF_NODE_KINDS = (
    "function_definition",
    "declaration",
    "field_declaration",
    "class_specifier",
    "struct_specifier",
    "union_specifier",
    "enum_specifier",
    "namespace_definition",
    "template_declaration",
    "type_definition",
    "alias_declaration",
)
# _cpp_declarator_name_node's backstop hop limit -- mirrors lang_c.py's own
# _MAX_DECLARATOR_HOPS choice (64) with headroom above any plausible real nesting, including the
# extra qualified_identifier/destructor_name/reference_declarator hops C++ adds over C; this only
# guards against an unforeseen future grammar cycle, never hit in practice.
_MAX_DECLARATOR_HOPS = 64


def _cpp_parenthesized_declarator_wraps_bare_name(node: Any) -> bool:
    """Return True when a ``parenthesized_declarator`` node's single named child is a bare
    ``identifier``/``type_identifier``/``field_identifier`` -- i.e. the parens are purely
    REDUNDANT around a real name (``int (foo)(void);``) -- as opposed to wrapping a
    ``pointer_declarator``/``array_declarator``/``qualified_identifier``/anything else (the tell
    for a function-pointer or pointer-to-member-function VARIABLE, e.g. ``void (*handler)(int);``
    or a FILE/NAMESPACE-scope ``void (C::*mp)(int);``).

    The pointer-to-member-function shape is DEPENDENT ON SCOPE -- live-verified against a real
    tree_sitter_cpp 0.23.4 parse of BOTH forms, not assumed identical:

    - At FILE or NAMESPACE scope (``void (C::*mp)(int);``, a plain ``declaration``), the
      ``parenthesized_declarator``'s single named child IS a ``qualified_identifier`` node
      (fields "scope"=``C`` a ``namespace_identifier``, "name"=a ``pointer_type_declarator``
      wrapping ``mp``) -- exactly ONE named child, so this function's own ``len(named_children)
      == 1`` check runs, and ``qualified_identifier`` is correctly rejected (it is not in the
      bare-name set), so ``False`` is returned via the TYPE check on line below -- this is the
      code path that actually excludes this shape from being (mis)treated as a real function.
    - IN-CLASS (``class C { public: void (C::*mp)(int); };``, a ``field_declaration``),
      tree-sitter-cpp's parser CANNOT resolve ``C::`` inside a class body the same way (there is
      no enclosing scope for it to bind against in this local parse) and instead emits an
      ``ERROR`` node for the ``C::`` portion, sibling to a plain ``pointer_declarator`` for
      ``*mp`` -- so the ``parenthesized_declarator`` here has **TWO** named children
      (``['ERROR', 'pointer_declarator']``), not one. This shape is excluded by the ``len(
      named_children) != 1`` EARLY RETURN below -- a DIFFERENT code path than the file-scope
      case, never reaching the bare-name TYPE check at all. (This in-class shape was already
      excluded on the pre-fix ``main`` too, for an unrelated reason: the malformed 2-named-child
      ``parenthesized_declarator`` also breaks ``_cpp_declarator_name_node``'s own single-
      named-child fallback when it later tries to descend to a NAME, so ``name_node`` resolves
      to ``None`` regardless of this function's verdict -- callers already discard a ``None``
      name_node. Only the FILE-scope shape was the actual pre-fix bug, emitting ``mp`` as kind
      "function".)

    Ported (with the above scope-dependent nuance ADDED, not blindly copied) from ``lang_c.py``'s
    ``_c_parenthesized_declarator_wraps_bare_name`` (C has no member-pointer syntax at all, so
    this nuance is C++-only) -- duplicated rather than imported per this module's no-cross-import
    convention (see the top-of-file comment). Only a single hop's own wrapped shape is inspected
    here; a nested ``parenthesized_declarator`` falls through to False like any other
    non-bare-name wrap, same residual edge case C's own copy documents."""
    named_children = [child for child in node.children if child.is_named]
    if len(named_children) != 1:
        return False
    return named_children[0].type in {"identifier", "type_identifier", "field_identifier"}


def _cpp_declarator_name_node(declarator: Any) -> tuple[Any | None, bool]:
    """Descend a declarator chain to its innermost identifier/type_identifier/field_identifier
    NAME node, tracking whether the chain passed through a ``function_declarator`` that names a
    REAL function -- as opposed to a file-scope (or pointer-to-member) function-pointer
    VARIABLE's declarator, which is also ``function_declarator``-shaped but must NOT set this
    signal (see ``lang_c.py``'s ``_c_declarator_name_node`` docstring for the full explanation of
    the underlying tell, unchanged here: a real function's ``function_declarator`` has its own
    "declarator" field as a bare name -- directly, nested under a return-type
    ``pointer_declarator``, or wrapped in REDUNDANT parens around a bare name -- while a
    function-pointer/pointer-to-member-function VARIABLE's ``function_declarator`` instead has
    its own "declarator" field as a ``parenthesized_declarator`` wrapping something OTHER than a
    bare name. The boolean is never force-reset True->False, so a function that itself RETURNS a
    function pointer still resolves True via its own INNER ``function_declarator`` hop).

    Extends ``lang_c.py``'s ``_c_declarator_name_node`` with ONE new branch (``qualified_
    identifier`` -> follow "name", never "scope") -- see the module docstring's "DECLARATOR NAME
    RESOLUTION" section for why that is the only new branch needed: destructors and reference-
    wrapped operators already resolve (or honestly fail to resolve, for operators) through the
    same generic "declarator field, else the single named child" fallback C already has.

    Returns ``(None, seen_function)`` if no name-bearing leaf was found (e.g. an operator overload,
    whose ``operator_name`` node has zero named children) -- callers must check for ``None``
    before using the result.
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
            # `qualified_identifier` for a pointer-to-member, ...) is the real tell (see the
            # docstring). Not force-resetting `seen_function` to False here is deliberate: it lets
            # a function that returns a function pointer still resolve True via its own (inner)
            # function_declarator hop.
            is_variable_shaped_parens = (
                next_node is not None
                and next_node.type == "parenthesized_declarator"
                and not _cpp_parenthesized_declarator_wraps_bare_name(next_node)
            )
            if not is_variable_shaped_parens:
                seen_function = True
        if current.type == "qualified_identifier":
            # Foo::bar / app::Thing::compute / Box<T>::get -- take the RHS "name" field (which
            # may itself be another qualified_identifier for a nested namespace::class chain, or
            # a destructor_name/operator_name leaf); NEVER the "scope" field (which may be a
            # template_type for a templated out-of-class method -- its <T> arguments must never
            # leak into the extracted name).
            name_node = current.child_by_field_name("name")
            if name_node is not None:
                current = name_node
                continue
            return None, seen_function
        if next_node is not None:
            current = next_node
            continue
        named_children = [child for child in current.children if child.is_named]
        if len(named_children) == 1:
            current = named_children[0]
            continue
        return None, seen_function
    return None, seen_function


def _cpp_include_target_text(path_field: Any, source_bytes: bytes) -> str | None:
    """Return a ``preproc_include`` node's target text, quote/bracket-stripped where the include
    form carries delimiters. Byte-identical logic to ``lang_c.py``'s ``_c_include_target_text``
    (live-verified: tree-sitter-cpp's ``preproc_include`` node shape matches tree-sitter-c's
    exactly across all four include forms) -- duplicated rather than imported, matching every
    other cross-cutting helper's precedent in this module (see the top-of-file comment)."""
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
        # Fallback (mirrors lang_c.py's own fallback): an empty ``#include ""`` or an unusual
        # grammar build with no ``string_content`` child still yields a clean module string
        # instead of silently dropping the row.
        raw = _tree_sitter_node_text(source_bytes, path_field)
        if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
            return raw[1:-1]
        return raw
    # identifier (macro name, e.g. MACRO_HEADER) / call_expression (macro-combined, e.g.
    # COMBINE(a,b)) / anything else: no delimiters to strip -- the raw text IS the honest
    # include target (this module never fabricates a resolved path for these either way; see
    # repo_map.py's `_resolve_raw_import_entry` "cpp" branch).
    return _tree_sitter_node_text(source_bytes, path_field)


def cpp_imports_and_symbols(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    """Extract ``#include`` targets + function/class/namespace/type-alias definitions from a
    C++ source file, one AST pass (mirrors ``lang_c.c_imports_and_symbols``'s shape).

    Defs covered: ``function_definition`` (kind "function" -- free functions, in-class inline
    methods, and out-of-class qualified method/constructor/destructor definitions all share this
    node type), a ``declaration``/``field_declaration`` whose declarator chain passes through a
    ``function_declarator`` (a prototype, kind "function" -- a plain data member or variable
    declaration is excluded, see the module docstring), ``class_specifier``/``struct_specifier``/
    ``union_specifier``/``enum_specifier`` WITH a body (kind "class" -- a body-less forward
    declaration/usage-as-type is excluded), ``namespace_definition`` WITH a name (kind
    "namespace" -- an anonymous namespace's own wrapper is excluded, its contents are not),
    ``type_definition`` (kind "type", one record per declarator so ``typedef int A, B;`` yields
    both), and ``alias_declaration`` (kind "type" -- a C++11 ``using X = ...`` alias). A
    ``template_declaration`` is never matched directly; the walker naturally descends into
    whatever it wraps. Imports come from every ``preproc_include`` directive's target text (see
    ``_cpp_include_target_text``).
    """
    if path.suffix not in _CPP_SUFFIXES:
        return [], []

    parser = _cpp_parser()
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
        # Explicit-stack DFS instead of recursion, matching lang_c.py/lang_go.py's walkers (F26
        # precedent: a pathologically deep AST can never raise RecursionError). Children pushed
        # in reverse so the leftmost child is popped (visited) first, preserving pre-order
        # traversal. A plain (non-recursive) stack walk also naturally reaches nodes nested
        # inside `preproc_ifdef`/`preproc_if`/`preproc_elif` guards AND inside a
        # `template_declaration` wrapper AND inside nested class/namespace bodies -- live-verified:
        # both branches of an `#ifdef`/`#else` parse simultaneously as ordinary children, and a
        # template-wrapped or nested construct is just another descendant -- no special-casing
        # needed for any of the three.
        stack = [root]
        while stack:
            node = stack.pop()
            node_type = node.type
            if node_type == "preproc_include":
                path_field = node.child_by_field_name("path")
                target = _cpp_include_target_text(path_field, source_bytes)
                if target:
                    imports.append(target)
            elif node_type == "function_definition":
                for declarator in node.children_by_field_name("declarator"):
                    name_node, _seen_function = _cpp_declarator_name_node(declarator)
                    if name_node is None:
                        continue
                    name = _node_text(name_node)
                    if _is_clean_cpp_symbol_name(name):
                        symbols.append(
                            _symbol_record(
                                name=name,
                                kind="function",
                                file=path,
                                start_line=node.start_point[0] + 1,
                                end_line=node.end_point[0] + 1,
                            )
                        )
            elif node_type in ("declaration", "field_declaration"):
                for declarator in node.children_by_field_name("declarator"):
                    name_node, is_function = _cpp_declarator_name_node(declarator)
                    if not is_function or name_node is None:
                        continue
                    name = _node_text(name_node)
                    if _is_clean_cpp_symbol_name(name):
                        symbols.append(
                            _symbol_record(
                                name=name,
                                kind="function",
                                file=path,
                                start_line=node.start_point[0] + 1,
                                end_line=node.end_point[0] + 1,
                            )
                        )
            elif node_type in _CPP_CLASS_LIKE_KINDS:
                body_field = node.child_by_field_name("body")
                name_field = node.child_by_field_name("name")
                if body_field is not None and name_field is not None:
                    name = _node_text(name_field)
                    if _is_clean_cpp_symbol_name(name):
                        symbols.append(
                            _symbol_record(
                                name=name,
                                kind="class",
                                file=path,
                                start_line=node.start_point[0] + 1,
                                end_line=node.end_point[0] + 1,
                            )
                        )
            elif node_type == "namespace_definition":
                name_field = node.child_by_field_name("name")
                if name_field is not None:
                    name = _node_text(name_field)
                    if _is_clean_cpp_symbol_name(name):
                        symbols.append(
                            _symbol_record(
                                name=name,
                                kind="namespace",
                                file=path,
                                start_line=node.start_point[0] + 1,
                                end_line=node.end_point[0] + 1,
                            )
                        )
            elif node_type == "type_definition":
                for declarator in node.children_by_field_name("declarator"):
                    name_node, _seen_function = _cpp_declarator_name_node(declarator)
                    if name_node is None:
                        continue
                    name = _node_text(name_node)
                    if _is_clean_cpp_symbol_name(name):
                        symbols.append(
                            _symbol_record(
                                name=name,
                                kind="type",
                                file=path,
                                start_line=node.start_point[0] + 1,
                                end_line=node.end_point[0] + 1,
                            )
                        )
            elif node_type == "alias_declaration":
                name_field = node.child_by_field_name("name")
                if name_field is not None:
                    name = _node_text(name_field)
                    if _is_clean_cpp_symbol_name(name):
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


# #74-follow-up: `tg imports` foundational-tier extractor (mirrors lang_c.c_imports_with_lines'
# shape/role exactly). One row per `preproc_include` STATEMENT with its 1-based line number --
# same extraction source as `cpp_imports_and_symbols` above (every `preproc_include`'s target
# text via `_cpp_include_target_text`), just line-tagged instead of deduped into a flat list.
#
# Deliberately NOT resolved to a target file: repo_map.py's `_resolve_raw_import_entry` "cpp"
# branch keeps every row unresolved (resolved=None, external=False) -- true `#include` -> file
# resolution has no standardized C++ manifest to resolve against (see the module docstring), so a
# real path is not guessable without fabricating one.
def cpp_imports_with_lines(path: Path) -> list[dict[str, Any]]:
    if path.suffix not in _CPP_SUFFIXES:
        return []

    parser = _cpp_parser()
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
        # Explicit-stack DFS -- see the identical comment on cpp_imports_and_symbols's `_walk`.
        stack = [root]
        while stack:
            node = stack.pop()
            if node.type == "preproc_include":
                path_field = node.child_by_field_name("path")
                target = _cpp_include_target_text(path_field, source_bytes)
                if target:
                    entries.append({
                        "module": target,
                        "line": node.start_point[0] + 1,
                    })
            stack.extend(reversed(node.children))

    _walk(tree.root_node)
    return entries


def cpp_parser_symbol_sources(path: Path, symbol: str) -> list[dict[str, Any]]:
    """Full source text of every function/class/namespace/type-alias matching *symbol* (mirrors
    ``lang_c.c_parser_symbol_sources``'s shape for the ``tg source`` command).

    A function/typedef appearing both as a prototype/forward form and a full definition emits a
    source block for EACH matching AST node (no dedup/preference between them) -- the same
    "every real AST node is a legitimate hit" behavior C's own module already ships (a prototype
    and its later definition sharing a name both resolve as separate ``tg source`` blocks)."""
    if path.suffix not in _CPP_SUFFIXES:
        return []

    parser = _cpp_parser()
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
        # Explicit-stack DFS -- see the identical comment on cpp_imports_and_symbols's `_walk`.
        stack = [root]
        while stack:
            node = stack.pop()
            node_type = node.type
            if node_type == "function_definition":
                for declarator in node.children_by_field_name("declarator"):
                    name_node, _seen_function = _cpp_declarator_name_node(declarator)
                    if name_node is not None and _node_text(name_node) == symbol:
                        _append(node, "function")
                        break
            elif node_type in ("declaration", "field_declaration"):
                for declarator in node.children_by_field_name("declarator"):
                    name_node, is_function = _cpp_declarator_name_node(declarator)
                    if is_function and name_node is not None and _node_text(name_node) == symbol:
                        _append(node, "function")
                        break
            elif node_type in _CPP_CLASS_LIKE_KINDS:
                body_field = node.child_by_field_name("body")
                name_field = node.child_by_field_name("name")
                if (
                    body_field is not None
                    and name_field is not None
                    and _node_text(name_field) == symbol
                ):
                    _append(node, "class")
            elif node_type == "namespace_definition":
                name_field = node.child_by_field_name("name")
                if name_field is not None and _node_text(name_field) == symbol:
                    _append(node, "namespace")
            elif node_type == "type_definition":
                for declarator in node.children_by_field_name("declarator"):
                    name_node, _seen_function = _cpp_declarator_name_node(declarator)
                    if name_node is not None and _node_text(name_node) == symbol:
                        _append(node, "type")
                        break
            elif node_type == "alias_declaration":
                name_field = node.child_by_field_name("name")
                if name_field is not None and _node_text(name_field) == symbol:
                    _append(node, "type")
            stack.extend(reversed(node.children))

    _walk(tree.root_node)
    sources.sort(key=lambda item: (item["file"], item["start_line"], item["kind"], item["name"]))
    return sources


# ---------------------------------------------------------------------------
# Task 10E: references + calls (in-file AST extraction, no cross-file resolution). Extends
# lang_c.py's TASK 10D shape with C++-only call forms -- see this module's own additions below
# for the full derivation of what C++ adds over C (qualified calls, `this->`, `new`) and what it
# deliberately still demotes (receiver-typed member calls, overloads, templates, `auto`).
#
# TASK 10E CALL/ACCESS NODE SHAPES (live-verified against a real ``tree_sitter_cpp`` 0.23.4
# parse of a fixture covering every shape below -- free/method/static/qualified/nested-qualified/
# templated-scope calls, `this->`, inherited-unqualified calls, `new`, and a dependent-type
# template-body call):
#
# - ``call_expression`` whose ``function`` field is a bare ``identifier``: a free-function call
#   (``add(1, 2)``), an UNQUALIFIED call to a method of the enclosing class reached via the
#   implicit ``this`` (``method(5)`` inside a member function body -- live-verified: C++ gives
#   this the SAME node shape as a free-function call, no syntactic tell distinguishes them), or a
#   function-like-macro invocation -- structurally identical to C's own bare-identifier call, and
#   handled the same way (see below).
# - ``call_expression`` whose ``function`` field is a ``qualified_identifier``
#   (``Foo::bar()``, ``app::Widget::staticMethod()``, ``Box<T>::get()``): resolved to its
#   terminal bare name via ``_cpp_declarator_name_node`` (the SAME descent this module's def
#   extraction already uses -- reused rather than re-derived, see ``_cpp_call_target_name_node``).
#   A ``qualified_identifier`` whose terminal is a ``template_function`` (``std::make_unique<Widget>()``)
#   resolves to ``None`` here (``template_function`` has 2 named children, so the shared
#   ``_cpp_declarator_name_node`` fallback cannot pick one) -- an accepted, documented gap; the
#   bare ``identifier`` inside it still surfaces via the generic identifier walk below, just
#   under ``ref_kind="value"`` rather than ``"call"``.
# - ``call_expression`` whose ``function`` field is a ``field_expression``: a receiver-typed
#   member call. Two sub-shapes, given DIFFERENT treatment (see RESOLUTION CONFIDENCE below):
#   ``argument`` field is the literal keyword node ``this`` (``this->method(6)``) vs. anything
#   else (``w.method(1)``, ``p->method(2)``, ``q->method(3)``).
# - ``new_expression``: field ``type`` is either a bare ``type_identifier`` (``new Widget()``) or
#   a ``template_type`` whose first ``type_identifier`` child is the base type
#   (``new Box<T>()`` -> base type is ``Box``) -- mirrors
#   ``lang_java._java_object_creation_type_identifier``'s identical shape/precedent. A symbol
#   match on that base type identifier is ``ref_kind="constructor"`` (both buckets, like
#   Java/C#/PHP's own constructor handling) -- there is no in-file receiver to confirm a
#   constructor call against, same reasoning those three languages already give.
# - ``field_expression`` (not already claimed by the call branch above -- a non-call member
#   access like ``w.x``): field ``field`` is ``ref_kind="field"``, always demoted.
# - ``identifier`` / ``type_identifier`` / ``field_identifier``: reused across value/type/
#   declaration-name roles, same as C. A symbol-matching ``identifier``/``type_identifier`` not
#   already claimed above and not itself a definition site is ``ref_kind="type"`` (when
#   ``type_identifier``) else ``ref_kind="value"``.
#
# RESOLUTION CONFIDENCE / PROVENANCE (extends C's two-band shape; C++-specific mechanism):
#
# - ``_CPP_DEMOTED_CONFIDENCE`` (0.6) / ``_CPP_DEMOTED_PROVENANCE`` (``"cpp-name-heuristic"``):
#   the default band -- an AST-confirmed real reference/call site this module cannot
#   independently confirm from THIS file's AST.
# - ``_CPP_CONFIRMED_CONFIDENCE`` (0.9) / ``_CPP_CONFIRMED_PROVENANCE``
#   (``"cpp-infile-function-declared"``): fires for THREE call shapes, all gated on the SAME
#   single boolean (``_cpp_symbol_has_infile_function`` -- does *symbol* name a real
#   ``function_definition``/``declaration``/``field_declaration`` function-shaped declarator
#   ANYWHERE in this file, reusing the #736/#737-safe ``_cpp_declarator_name_node`` walk):
#     1. A bare-identifier call (free function OR the implicit-``this`` unqualified-member-call
#        shape -- C++ gives these the same node shape, so this module cannot and does not try to
#        tell them apart; both get the SAME evidence a real function/method of that name exists
#        somewhere in this file).
#     2. A qualified call (``Foo::bar()``) whose terminal bare name resolves and matches -- the
#        qualifier itself (``Foo``) is NOT verified against the terminal name's actual owner (this
#        module has no symbol table to check "does Foo really declare bar"); this is the SAME
#        "a real declaration with this name exists in-file" evidence as the bare-identifier case,
#        not a stronger claim earned by the qualifier being present.
#     3. An explicit ``this->method(...)`` call -- chosen for confirmation (unlike arbitrary
#        receiver calls, see below) because ``this``'s type is NOT a local-declaration lookup: it
#        is syntactically fixed to be the enclosing class, with zero aliasing/casting/``auto``
#        ambiguity. That still only proves "some function/method named *symbol* exists in this
#        file" (the same weak signal as 1/2), not that it is declared on the SPECIFIC enclosing
#        class or its bases -- deliberately not made stronger than the evidence actually supports.
#
#   Not 1.0, matching C/Java/C#/PHP's identical rationale: real evidence, not proof of soundness.
#   OVERLOADS are the sharpest caveat this band accepts and does not resolve: C++ routinely
#   declares several functions/methods sharing one name with different signatures, and this
#   module's confirmation is arity- and type-blind -- a symbol match against ANY in-file
#   declaration of that name confirms, even if the call site actually resolves (by overload
#   resolution this module does not implement) to a DIFFERENT declaration of the same name. This
#   is the same caveat C already accepts (a `static` name-shadow, or a same-named macro) --
#   overload resolution is strictly harder than either, so the caveat is at least as real here.
#
#   DELIBERATELY NOT CONFIRMED (the honest narrowing this module chooses, matching the "narrower
#   band beats a plausible-but-unsound wider one" precedent C's own struct-member decision set):
#   - A call through a NON-``this`` receiver (``w.method(1)``, ``p->method(2)``, ``q->method(3)``)
#     ALWAYS stays demoted, even though C++ has a real static type system unlike C. A sound
#     confirmation here would need to: (a) resolve the receiver's DECLARED type from a local
#     declaration -- impossible for `auto` (``auto q = std::make_unique<Widget>();`` has no
#     syntactic type at the use site at all), unreliable across pointer/reference/smart-pointer
#     wrapping; (b) look up that type's ``class_specifier``/``struct_specifier`` body in THIS
#     file for a directly-declared member -- but C++ has real single/multiple INHERITANCE, so a
#     method reached through a base-class pointer (``Widget *p = &derived; p->method();``) may be
#     declared only in a base class whose own definition might not even be visible in this file
#     (only ``#include``d); and a TEMPLATE-typed receiver (``t.method(7)`` where ``t: T`` inside a
#     ``template_declaration``) has no concrete type to resolve AT ALL -- ``T`` is a dependent
#     name. Implementing the direct-member-only subset (no inheritance walk) would be sound but
#     silently confirm only a narrow slice of real code while doing nothing for the common
#     inherited/`auto`/templated cases -- an implicit promise this module chooses not to make.
#     Every one of these stays honestly demoted instead.
#   - `new Widget()` / `new Box<T>()`: always demoted (``ref_kind="constructor"``), matching
#     Java/C#/PHP's identical "no receiver to confirm a constructor against" reasoning.
#   - Non-call field access (``w.x``) and every type/value reference stay demoted for the same
#     reason C's own generic identifier walk never confirms these either.
#   - An OPERATOR OVERLOAD call site (``a += b``) is never claimed by any branch here: it has no
#     ``call_expression``/``operator_name`` textual identifier shape this walk matches (consistent
#     with this module's def-extraction side, which already excludes operator overloads from the
#     symbol table entirely -- see the module docstring's OPERATOR OVERLOADS section).
# ---------------------------------------------------------------------------

_CPP_DEMOTED_CONFIDENCE = 0.6
_CPP_DEMOTED_PROVENANCE = "cpp-name-heuristic"
_CPP_CONFIRMED_CONFIDENCE = 0.9
_CPP_CONFIRMED_PROVENANCE = "cpp-infile-function-declared"

# Node types whose "declarator" field (possibly wrapped, resolved via
# _cpp_declarator_name_node) names a DEFINITION site -- excluded from the reference/call walk, the
# same rule C's own module already follows, extended with `field_declaration` (C++'s in-class
# prototype sibling of `declaration`, see the module docstring).
_CPP_DECLARATOR_DEFINING_NODE_TYPES = frozenset({
    "function_definition",
    "declaration",
    "field_declaration",
    "type_definition",
    "parameter_declaration",
})


def _cpp_symbol_has_infile_function(root: Any, source_bytes: bytes, symbol: str) -> bool:
    """True iff *symbol* names a REAL function or method in this file -- a ``function_definition``,
    or a ``declaration``/``field_declaration`` whose declarator chain
    ``_cpp_declarator_name_node`` resolves with ``seen_function=True``. The ONLY confirmation
    signal Task 10E uses (see the module's TASK 10E docstring block above) -- reusing the SAME
    #736/#737-safe declarator walk ``cpp_imports_and_symbols`` already uses for def extraction."""

    def node_text(node: Any) -> str:
        return _tree_sitter_node_text(source_bytes, node)

    stack = [root]
    while stack:
        node = stack.pop()
        node_type = node.type
        if node_type == "function_definition":
            for declarator in node.children_by_field_name("declarator"):
                name_node, _seen_function = _cpp_declarator_name_node(declarator)
                if name_node is not None and node_text(name_node) == symbol:
                    return True
        elif node_type in ("declaration", "field_declaration"):
            for declarator in node.children_by_field_name("declarator"):
                name_node, is_function = _cpp_declarator_name_node(declarator)
                if is_function and name_node is not None and node_text(name_node) == symbol:
                    return True
        stack.extend(node.children)
    return False


def _cpp_definition_site_positions(root: Any) -> set[tuple[int, int]]:
    """Collect the byte-span of every DEFINITION-site name node in this file -- extends
    ``lang_c._c_definition_site_positions``'s shape with C++'s class/namespace/type-alias
    definition sites (see ``cpp_imports_and_symbols`` for the matching def-extraction logic this
    mirrors)."""
    positions: set[tuple[int, int]] = set()
    stack = [root]
    while stack:
        node = stack.pop()
        node_type = node.type
        if node_type in _CPP_DECLARATOR_DEFINING_NODE_TYPES:
            for declarator in node.children_by_field_name("declarator"):
                name_node, _seen_function = _cpp_declarator_name_node(declarator)
                if name_node is not None:
                    positions.add((name_node.start_byte, name_node.end_byte))
        elif node_type in _CPP_CLASS_LIKE_KINDS:
            body_field = node.child_by_field_name("body")
            name_field = node.child_by_field_name("name")
            if body_field is not None and name_field is not None:
                positions.add((name_field.start_byte, name_field.end_byte))
        elif node_type == "namespace_definition":
            name_field = node.child_by_field_name("name")
            if name_field is not None:
                positions.add((name_field.start_byte, name_field.end_byte))
        elif node_type == "alias_declaration":
            name_field = node.child_by_field_name("name")
            if name_field is not None:
                positions.add((name_field.start_byte, name_field.end_byte))
        elif node_type == "enumerator":
            name_field = node.child_by_field_name("name")
            if name_field is not None:
                positions.add((name_field.start_byte, name_field.end_byte))
        stack.extend(node.children)
    return positions


def _cpp_call_target_name_node(function_field: Any) -> Any | None:
    """The terminal bare-name node for a ``call_expression``'s ``function`` field, when that
    field is a bare ``identifier`` or a ``qualified_identifier`` (``Foo::bar``) -- reuses
    ``_cpp_declarator_name_node``'s existing "follow 'name', never 'scope'" descent (the SAME
    walk this module's def extraction already trusts) rather than re-deriving qualified-name
    resolution a second time. Returns ``None`` for any other function-field shape (``field_
    expression``, handled separately by the caller; ``template_function``, an accepted gap -- see
    the module's TASK 10E docstring block)."""
    if function_field is None:
        return None
    if function_field.type == "identifier":
        return function_field
    if function_field.type == "qualified_identifier":
        name_node, _seen_function = _cpp_declarator_name_node(function_field)
        return name_node
    return None


def _cpp_new_expression_type_identifier(node: Any) -> Any | None:
    """The base ``type_identifier`` node for a ``new_expression``'s ``type`` field -- direct if
    the field IS a ``type_identifier`` (``new Widget()``), or the first ``type_identifier`` child
    of a ``template_type`` field (``new Box<T>()`` -> base type is ``Box``, not ``T``). Mirrors
    ``lang_java._java_object_creation_type_identifier``'s identical shape/precedent."""
    type_field = node.child_by_field_name("type")
    if type_field is None:
        return None
    if type_field.type == "type_identifier":
        return type_field
    if type_field.type == "template_type":
        for child in type_field.children:
            if child.type == "type_identifier":
                return child
    return None


def cpp_references_and_calls(
    path: Path, symbol: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """In-file AST reference/call rows for *symbol* in *path* -- see the module's TASK 10E
    docstring block above for the full AST-shape mapping and the confirmed/demoted band
    rationale. Scope: single-file only, no cross-file resolution (mirrors
    ``lang_c.c_references_and_calls`` exactly). Owns its own parser factory (``_cpp_parser()``,
    defined above), matching ``lang_c.py``'s/``lang_csharp.py``'s/``lang_php.py``'s shape rather
    than ``lang_java.py``'s externally-built-parser shape -- C++ already had its own
    grammar-probing factory before Task 10E (needed by ``cpp_imports_and_symbols``), so a second
    factory here would create two sources of truth for "is the C++ grammar installed"."""
    if path.suffix not in _CPP_SUFFIXES:
        return [], []

    parser = _cpp_parser()
    if parser is None:
        return [], []

    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return [], []

    source_bytes = source.encode("utf-8")
    tree = parser.parse(source_bytes)
    # Split strictly on "\n" (tree-sitter's own row semantics), stripping a trailing "\r" so
    # CRLF-terminated files still read cleanly -- see lang_c.c_references_and_calls's identical
    # comment (F26 fix, audit #63) for why `str.splitlines()` is NOT safe here.
    lines = [line.rstrip("\r") for line in source.split("\n")]
    references: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []

    def _node_text(node: Any) -> str:
        return _tree_sitter_node_text(source_bytes, node)

    def _line_text(node: Any) -> str:
        line_index = node.start_point[0]
        return lines[line_index] if 0 <= line_index < len(lines) else ""

    definition_positions = _cpp_definition_site_positions(tree.root_node)
    function_confirmed = _cpp_symbol_has_infile_function(tree.root_node, source_bytes, symbol)

    def _is_definition_site(node: Any) -> bool:
        return (node.start_byte, node.end_byte) in definition_positions

    def _emit(
        bucket: list[dict[str, Any]], node: Any, *, kind: str, ref_kind: str, confirmed: bool
    ) -> None:
        if confirmed:
            confidence = _CPP_CONFIRMED_CONFIDENCE
            provenance = _CPP_CONFIRMED_PROVENANCE
        else:
            confidence = _CPP_DEMOTED_CONFIDENCE
            provenance = _CPP_DEMOTED_PROVENANCE
        bucket.append({
            "name": symbol,
            "kind": kind,
            "ref_kind": ref_kind,
            "file": str(path),
            "line": node.start_point[0] + 1,
            "text": _line_text(node),
            # PER-MATCH honesty band -- see the module's TASK 10E docstring block above.
            "resolution_confidence": confidence,
            "resolution_provenance": [provenance],
        })

    # Nodes already claimed by a special-case branch below are tracked here so the generic
    # identifier/type_identifier/field_identifier walk never double-emits them. Keyed on
    # (start_byte, end_byte), NOT Python `id()` -- see lang_c.py's/lang_java.py's identical
    # comment for why (tree_sitter mints a fresh wrapper object on every `.children`/
    # `.child_by_field_name` access to the same underlying node).
    claimed_node_ids: set[tuple[int, int]] = set()

    def _walk_calls(root: Any) -> None:
        # Explicit-stack DFS (not recursion) -- matches every other language extractor in this
        # registry; avoids a RecursionError on a pathologically deep real-world AST.
        stack = [root]
        while stack:
            node = stack.pop()
            node_type = node.type
            if node_type == "call_expression":
                function_field = node.child_by_field_name("function")
                if function_field is not None and function_field.type == "field_expression":
                    field_child = function_field.child_by_field_name("field")
                    argument_field = function_field.child_by_field_name("argument")
                    if field_child is not None and _node_text(field_child) == symbol:
                        claimed_node_ids.add((field_child.start_byte, field_child.end_byte))
                        # `this->method(...)` is the ONE receiver-typed call shape this module
                        # confirms -- `this`'s type is syntactically fixed (no local-declaration
                        # lookup, no `auto`, no aliasing). Every other receiver stays demoted --
                        # see the module's TASK 10E docstring block for the full inheritance/
                        # auto/template reasoning.
                        confirmed = (
                            function_confirmed
                            and argument_field is not None
                            and argument_field.type == "this"
                        )
                        _emit(
                            references,
                            field_child,
                            kind="reference",
                            ref_kind="call",
                            confirmed=confirmed,
                        )
                        _emit(calls, field_child, kind="call", ref_kind="call", confirmed=confirmed)
                else:
                    name_node = _cpp_call_target_name_node(function_field)
                    if name_node is not None and _node_text(name_node) == symbol:
                        claimed_node_ids.add((name_node.start_byte, name_node.end_byte))
                        _emit(
                            references,
                            name_node,
                            kind="reference",
                            ref_kind="call",
                            confirmed=function_confirmed,
                        )
                        _emit(
                            calls,
                            name_node,
                            kind="call",
                            ref_kind="call",
                            confirmed=function_confirmed,
                        )
            elif node_type == "new_expression":
                type_identifier = _cpp_new_expression_type_identifier(node)
                if type_identifier is not None and _node_text(type_identifier) == symbol:
                    claimed_node_ids.add((type_identifier.start_byte, type_identifier.end_byte))
                    # No in-file receiver to confirm a constructor call against -- matches
                    # Java/C#/PHP's identical `object_creation_expression` handling.
                    _emit(
                        references,
                        type_identifier,
                        kind="reference",
                        ref_kind="constructor",
                        confirmed=False,
                    )
                    _emit(
                        calls,
                        type_identifier,
                        kind="call",
                        ref_kind="constructor",
                        confirmed=False,
                    )
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
    "cpp_imports_and_symbols",
    "cpp_imports_with_lines",
    "cpp_parser_symbol_sources",
    "cpp_references_and_calls",
]
