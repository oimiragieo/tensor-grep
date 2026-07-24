"""C++ symbol graph (lang_cpp.py) tests -- foundational-tier expansion, top-10 language campaign
(Phase 2 of C/C++; closes the top-10 symbol-graph tier to 10/10; C shipped first as Phase 1,
#731/v1.97.0).

Foundational scope (mirrors PATH A Stage 1's lang_go.py / lang_php.py / lang_csharp.py / lang_c.py
precedent): C++ gets its own ``LanguageSpec`` entry + dedicated module providing
``defs``/``source``/``imports``/``agent`` support (function definitions/prototypes including
qualified out-of-class methods, class/struct/union/enum definitions, namespaces, typedefs/using-
aliases, templates, plus ``#include`` directive extraction). The cross-file caller-graph
(``references_and_calls``/``file_imports_symbol_from_definition``/``import_update_target``) is
explicitly DEFERRED to a follow-up, exactly like Go/PHP/C#/C's own ``import_update_target=None``
gap -- `tg refs`/`tg callers`/`tg blast-radius` on a C++ symbol fall through to the generic
``_regex_references_and_calls`` text-heuristic path (never a crash, never a fabricated AST-
verified match).

Covered here:
- ``defs``: function definitions/prototypes resolve with kind "function" (free functions,
  in-class inline methods, in-class prototypes, and out-of-class QUALIFIED method/constructor
  definitions -- ``Foo::bar()`` -- all resolve under the BARE name, so an in-class prototype and
  its out-of-class qualified definition are separate records under the SAME name, mirroring C's
  own prototype+definition dual-recording pattern); class/struct/union/enum(-class) definitions
  resolve with kind "class"; namespaces resolve with kind "namespace"; typedefs AND ``using``
  alias declarations resolve with kind "type"; templates are transparently unwrapped (the kind is
  the wrapped construct's own kind, no separate "template" kind); a body-less forward declaration
  is NOT emitted; a plain variable/field declaration is NOT emitted; a destructor resolves under
  its class's bare name (the tilde is stripped); an operator overload is honestly EXCLUDED (no
  clean identifier to emit).
- ``source``: full source text for a function definition and other extracted kinds.
- ``imports``: ``#include`` directive targets (angle/quoted/macro forms), extracted by
  ``cpp_imports_and_symbols`` and surfaced through ``build_repo_map``.
- Grammar-absent (monkeypatched ``lang_cpp._cpp_parser`` -> ``None``): fail-closed, zero
  fabricated rows, an honest ``resolution_gaps`` entry, an honest non-zero/non-crash CLI exit code
  -- mirrors Go/PHP/C#/C's Stage 1 fail-closed contract exactly (``provenance_when_missing ==
  "grammar-missing"``, never "regex-heuristic").
- The agent capsule reports ``primary_target_language == "cpp"``.
- All 7 registered suffixes (``.cc``/``.cpp``/``.cxx``/``.h``/``.hh``/``.hpp``/``.hxx``) resolve
  to "cpp", including ``.h`` (claimed by C++, NOT by C -- the header-ambiguity resolution).
- A pathologically deep AST does not raise ``RecursionError`` (F26-class regression guard,
  applied preemptively since ``lang_go.py``/``lang_c.py`` already paid for this lesson once).
"""

from __future__ import annotations

import sys
from pathlib import Path

from tensor_grep.cli import agent_capsule, lang_cpp, lang_registry, repo_map

# ---------------------------------------------------------------------------
# Fixture: includes (angle + quoted) + a namespace wrapping a typedef/using-alias pair, a plain
# enum + a scoped `enum class`, a union, a class with an in-class constructor + typed method
# PROTOTYPE, matching out-of-class QUALIFIED definitions for both, and a template function.
# ---------------------------------------------------------------------------

_WIDGET_CPP_SOURCE = (
    "#include <cstdio>\n"
    "#include <vector>\n"
    '#include "widget_types.h"\n'
    "\n"
    "namespace app {\n"
    "\n"
    "typedef struct Point {\n"
    "    int x;\n"
    "    int y;\n"
    "} PointAlias;\n"
    "\n"
    "using ValueAlias = int;\n"
    "\n"
    "enum WidgetKind {\n"
    "    SMALL,\n"
    "    LARGE,\n"
    "};\n"
    "\n"
    "enum class ScopedKind {\n"
    "    A,\n"
    "    B,\n"
    "};\n"
    "\n"
    "union WidgetValue {\n"
    "    int i;\n"
    "    float f;\n"
    "};\n"
    "\n"
    "class Widget {\n"
    "public:\n"
    "    Widget(int value);\n"
    "    int getValue() const;\n"
    "private:\n"
    "    int value_;\n"
    "};\n"
    "\n"
    "Widget::Widget(int value) : value_(value) {\n"
    "}\n"
    "\n"
    "int Widget::getValue() const {\n"
    "    return value_;\n"
    "}\n"
    "\n"
    "template <typename T>\n"
    "T identity(T value) {\n"
    "    return value;\n"
    "}\n"
    "\n"
    "}  // namespace app\n"
)


def _write_cpp_fixture(root: Path) -> Path:
    widget_cpp = root / "widget.cpp"
    widget_cpp.write_text(_WIDGET_CPP_SOURCE, encoding="utf-8")
    return widget_cpp


# ---------------------------------------------------------------------------
# Registration + provenance + suffix ownership
# ---------------------------------------------------------------------------


def test_cpp_is_registered_with_tree_sitter_provenance() -> None:
    spec = lang_registry.LANGUAGE_REGISTRY["cpp"]
    assert spec.suffixes == frozenset({".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"})
    assert spec.provenance_when_parsed == "tree-sitter"
    # Fail-closed (Stage 1 trap, mirrors Go/PHP/C#/C): never "regex-heuristic" -- C++ has no
    # fallback.
    assert spec.provenance_when_missing == "grammar-missing"
    assert spec.parser_for_path is not None


def test_target_language_for_path_reports_cpp_for_every_registered_suffix() -> None:
    for suffix in (".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"):
        name = f"widget{suffix}"
        assert repo_map._target_language_for_path(name) == "cpp", suffix
        assert repo_map._language_for_path(name) == "cpp", suffix
        assert repo_map._provider_language_for_path(name) == "cpp", suffix


def test_header_suffix_is_claimed_by_cpp_not_c() -> None:
    """`.h` (and every other C/C++ header suffix) is registered under C++, NOT C -- tree-sitter-
    cpp is a strict grammar superset of C, and `_provider_language_for_path` already assigned
    every header suffix to "cpp" before this module existed (a latent pre-wiring lang_c.py's own
    docstring defers to this module)."""
    spec = lang_registry.spec_for_path("widget.h")
    assert spec is not None
    assert spec.language_id == "cpp"
    assert lang_registry.spec_for_path("widget.c") is not None
    assert lang_registry.spec_for_path("widget.c").language_id == "c"


def test_c_suffix_is_not_claimed_by_cpp() -> None:
    """`.c` stays owned by lang_c.py -- the two specs must never overlap."""
    assert ".c" not in lang_registry.LANGUAGE_REGISTRY["cpp"].suffixes


# ---------------------------------------------------------------------------
# defs: class/struct/union/enum(-class)/namespace
# ---------------------------------------------------------------------------


def test_defs_finds_enum_scoped_enum_and_union_as_class_kind(tmp_path: Path) -> None:
    _write_cpp_fixture(tmp_path)

    for name in ("WidgetKind", "ScopedKind", "WidgetValue"):
        payload = repo_map.build_symbol_defs(name, tmp_path)
        assert not payload.get("no_match"), f"expected a definition for {name}"
        assert payload["definitions"][0]["kind"] == "class", f"{name} should be kind=class"
        assert payload["definitions"][0]["provenance"] == "tree-sitter"


def test_defs_finds_struct_tag_and_typedef_alias_sharing_different_names(tmp_path: Path) -> None:
    """`typedef struct Point {...} PointAlias;` -- the struct TAG (Point) and the typedef ALIAS
    (PointAlias) are two separate, legitimately different-named defs."""
    _write_cpp_fixture(tmp_path)

    point_payload = repo_map.build_symbol_defs("Point", tmp_path)
    alias_payload = repo_map.build_symbol_defs("PointAlias", tmp_path)

    assert not point_payload.get("no_match")
    assert point_payload["definitions"][0]["kind"] == "class"
    assert not alias_payload.get("no_match")
    assert alias_payload["definitions"][0]["kind"] == "type"


def test_defs_finds_using_alias_as_type_kind(tmp_path: Path) -> None:
    _write_cpp_fixture(tmp_path)

    payload = repo_map.build_symbol_defs("ValueAlias", tmp_path)

    assert not payload.get("no_match")
    assert payload["definitions"][0]["kind"] == "type"


def test_defs_finds_namespace_as_namespace_kind(tmp_path: Path) -> None:
    _write_cpp_fixture(tmp_path)

    payload = repo_map.build_symbol_defs("app", tmp_path)

    assert not payload.get("no_match")
    assert payload["definitions"][0]["kind"] == "namespace"


def test_defs_excludes_body_less_forward_declaration(tmp_path: Path) -> None:
    root = tmp_path
    (root / "fwd.cpp").write_text(
        "class ForwardOnly;\n\nvoid use_it(ForwardOnly *p) {\n}\n",
        encoding="utf-8",
    )

    payload = repo_map.build_symbol_defs("ForwardOnly", root)

    assert payload.get("no_match") is True


def test_defs_excludes_plain_field_and_variable_declarations(tmp_path: Path) -> None:
    root = tmp_path
    (root / "globals.cpp").write_text(
        "int global_counter = 0;\nclass Holder {\npublic:\n    int held_value;\n};\n",
        encoding="utf-8",
    )

    counter_payload = repo_map.build_symbol_defs("global_counter", root)
    field_payload = repo_map.build_symbol_defs("held_value", root)

    assert counter_payload.get("no_match") is True
    assert field_payload.get("no_match") is True


# ---------------------------------------------------------------------------
# Declarator-shape matrix: the C sibling bug (#736/v1.98.2) -- a file-scope function-pointer
# VARIABLE (`void (*handler)(int);`) was mis-kinded "function" because `_cpp_declarator_name_node`
# unconditionally set `seen_function = True` for ANY `function_declarator` hop, but a
# function-pointer variable's declarator chain is ALSO `function_declarator`-outermost. Ported
# from `lang_c.py`'s own declarator-shape matrix (see `test_lang_c.py`'s identical section for the
# full C-side narrative) -- live-verified against a REAL tree_sitter_cpp 0.23.4 parse (not ported
# blind from C's shape, see `_cpp_parenthesized_declarator_wraps_bare_name`'s docstring) across
# every shape below, PLUS two C++-only additions this matrix carries that C's never needed:
# shape 9 (pointer-to-MEMBER-function variable, `void (C::*mp)(int);` -- its
# `parenthesized_declarator` wraps a `qualified_identifier`, not a `pointer_declarator`, but the
# exclusion rule ("wraps something other than a bare name") already covers it with no new branch)
# and shape 12 (qualified out-of-class method definitions must not regress: still kind "function",
# still bare-name "getValue", covered by the existing tests just below this matrix).
#
# Shape 9 (pointer-to-MEMBER-function VARIABLE, `void (C::*mp)(int);`) is SCOPE-DEPENDENT --
# live-verified against real tree_sitter_cpp 0.23.4 parses of BOTH forms (see
# `_cpp_parenthesized_declarator_wraps_bare_name`'s docstring for the full node dump):
# - FILE/NAMESPACE scope: `parenthesized_declarator`'s single named child IS a
#   `qualified_identifier` -- excluded by the bare-name TYPE check. THIS is the shape the fix
#   actually repairs: on pre-fix `main` it resolved and was emitted as kind "function" (`mp`).
# - IN-CLASS (`class C { public: void (C::*mp)(int); }; `): tree-sitter-cpp cannot resolve `C::`
#   inside a class body and emits an `ERROR` node alongside a `pointer_declarator`, giving the
#   `parenthesized_declarator` TWO named children -- excluded by the `len(named_children) != 1`
#   EARLY RETURN, a completely different code path. This shape was ALREADY excluded on pre-fix
#   `main` (name resolution fails regardless of the fix), so it is a no-regression PIN, not a
#   guard for the bug. Both are exercised below, separately labeled.
# ---------------------------------------------------------------------------

_DECLARATOR_SHAPES_CPP_SOURCE = (
    "void shape1_prototype(int x);\n"
    "\n"
    "int shape2_definition(void) {\n"
    "    return 0;\n"
    "}\n"
    "\n"
    "int *shape3_returns_pointer(void);\n"
    "\n"
    "void (*shape4_fn_ptr_variable)(int);\n"
    "\n"
    "typedef void (*Shape5FnPtrTypedef)(int);\n"
    "\n"
    "struct Shape6Struct {\n"
    "    int x;\n"
    "};\n"
    "\n"
    "int (shape7_redundant_paren_prototype)(void);\n"
    "\n"
    "class Shape9bClass {\n"
    "public:\n"
    "    void (Shape9bClass::*shape9b_inclass_member_fn_ptr_variable)(int);\n"
    "};\n"
    "\n"
    "void (Shape9aFileScope::*shape9a_filescope_member_fn_ptr_variable)(int);\n"
)


def _write_declarator_shapes_fixture(root: Path) -> Path:
    shapes_cpp = root / "declarator_shapes.cpp"
    shapes_cpp.write_text(_DECLARATOR_SHAPES_CPP_SOURCE, encoding="utf-8")
    return shapes_cpp


def test_declarator_shape_1_prototype_is_kind_function(tmp_path: Path) -> None:
    _write_declarator_shapes_fixture(tmp_path)

    payload = repo_map.build_symbol_defs("shape1_prototype", tmp_path)

    assert not payload.get("no_match")
    assert payload["definitions"][0]["kind"] == "function"


def test_declarator_shape_2_definition_is_kind_function(tmp_path: Path) -> None:
    _write_declarator_shapes_fixture(tmp_path)

    payload = repo_map.build_symbol_defs("shape2_definition", tmp_path)

    assert not payload.get("no_match")
    assert payload["definitions"][0]["kind"] == "function"


def test_declarator_shape_3_function_returning_pointer_is_kind_function(tmp_path: Path) -> None:
    """The trap: `int *make_ptr(void);` -- the return-type `pointer_declarator` is outermost,
    wrapping `function_declarator` whose own `declarator` field is a bare identifier. A real
    function; must NOT be excluded by the shape-4 fix below."""
    _write_declarator_shapes_fixture(tmp_path)

    payload = repo_map.build_symbol_defs("shape3_returns_pointer", tmp_path)

    assert not payload.get("no_match")
    assert payload["definitions"][0]["kind"] == "function"


def test_declarator_shape_4_function_pointer_variable_is_excluded(tmp_path: Path) -> None:
    """THE BUG (C sibling of #736): `void (*handler)(int);` is a file-scope function-pointer
    VARIABLE, not a function -- `function_declarator` is outermost, but ITS OWN `declarator`
    field is a `parenthesized_declarator` (wrapping `pointer_declarator` -> the name), not a bare
    identifier. Must be excluded from the symbol table entirely (this module does not track
    top-level variables), same as a plain `int counter;`."""
    shapes_cpp = _write_declarator_shapes_fixture(tmp_path)

    _imports, symbols = lang_cpp.cpp_imports_and_symbols(shapes_cpp)
    names = {s["name"] for s in symbols}
    assert "shape4_fn_ptr_variable" not in names

    payload = repo_map.build_symbol_defs("shape4_fn_ptr_variable", tmp_path)
    assert payload.get("no_match") is True


def test_declarator_shape_5_function_pointer_typedef_is_kind_type(tmp_path: Path) -> None:
    """Unchanged by the shape-4 fix: a function-pointer TYPEDEF goes through the separate
    `type_definition` branch, which always emits kind "type" regardless of
    `_cpp_declarator_name_node`'s boolean return (the branch discards it)."""
    _write_declarator_shapes_fixture(tmp_path)

    payload = repo_map.build_symbol_defs("Shape5FnPtrTypedef", tmp_path)

    assert not payload.get("no_match")
    assert payload["definitions"][0]["kind"] == "type"


def test_declarator_shape_6_struct_is_kind_class(tmp_path: Path) -> None:
    _write_declarator_shapes_fixture(tmp_path)

    payload = repo_map.build_symbol_defs("Shape6Struct", tmp_path)

    assert not payload.get("no_match")
    assert payload["definitions"][0]["kind"] == "class"


def test_declarator_shape_7_redundant_paren_prototype_is_kind_function(tmp_path: Path) -> None:
    """Same Opus-gate-caught regression trap C's own fix disclosed: `int (foo)(void);` is a REAL
    function prototype with meaningless redundant parens around the name -- its
    `function_declarator` has its own `declarator` field as a `parenthesized_declarator`, the
    exact same NODE TYPE as shape 4's function-pointer variable. The two are distinguished by
    WHAT the parens wrap: shape 7 wraps a bare `identifier` directly (still a real function);
    shape 4 wraps a `pointer_declarator` (a variable). A fix that treats "hop is
    `parenthesized_declarator`" alone as the exclusion signal (rather than checking what it
    wraps) wrongly excludes this real function too."""
    _write_declarator_shapes_fixture(tmp_path)

    payload = repo_map.build_symbol_defs("shape7_redundant_paren_prototype", tmp_path)

    assert not payload.get("no_match")
    assert payload["definitions"][0]["kind"] == "function"


def test_declarator_function_returning_function_pointer_is_kind_function(tmp_path: Path) -> None:
    """THE TRAP (shape 8 in the task matrix): a function that RETURNS a function pointer --
    `void (*get_handler(int x))(int);`, the same shape as the standard library's `signal()`
    prototype -- nests a SECOND `function_declarator` (the function's own parameter list) inside
    the outer one's `parenthesized_declarator` wrap. The outer `function_declarator` hop is
    skipped (its own declarator field is a `parenthesized_declarator` wrapping a
    `pointer_declarator`, the same tell as shape 4 -- NOT a bare name, so shape 7's
    redundant-parens carve-out does not apply here), but the INNER `function_declarator`'s own
    declarator field is a bare identifier, so `seen_function` still ends up True. Guards against
    an overly-broad fix that force-resets the signal to False the moment a
    `parenthesized_declarator` appears anywhere in the chain, which would wrongly exclude this
    real function too."""
    root = tmp_path
    (root / "returns_fn_ptr.cpp").write_text(
        "void (*get_handler(int x))(int);\n",
        encoding="utf-8",
    )

    payload = repo_map.build_symbol_defs("get_handler", root)

    assert not payload.get("no_match")
    assert payload["definitions"][0]["kind"] == "function"


def test_declarator_shape_9a_filescope_member_function_pointer_variable_is_excluded(
    tmp_path: Path,
) -> None:
    """THE ACTUAL BUG THIS FIX REPAIRS (C++-only, not in C's matrix at all): a FILE-scope
    pointer-to-MEMBER-function variable, `void (C::*mp)(int);`. On PRE-FIX `main` this resolved
    all the way to a name and was mis-emitted as kind "function" (`mp`) -- verified by hand before
    writing this fix (see the module docstring's dumped AST). Live-verified real AST shape: the
    outer `function_declarator`'s own "declarator" field is a `parenthesized_declarator` whose
    SINGLE named child is a `qualified_identifier` ("scope"=a `namespace_identifier`, "name"=a
    `pointer_type_declarator` wrapping the member name) -- a DIFFERENT node type than shape 4's
    plain `pointer_declarator` wrap, but `_cpp_parenthesized_declarator_wraps_bare_name` excludes
    it via the exact same "not a bare identifier/type_identifier/field_identifier" TYPE check, no
    dedicated branch needed. Guarded SEPARATELY from the in-class shape below (9b) -- that one is
    excluded via a completely different code path (a length check, not the type check) and was
    never actually broken."""
    shapes_cpp = _write_declarator_shapes_fixture(tmp_path)

    _imports, symbols = lang_cpp.cpp_imports_and_symbols(shapes_cpp)
    names = {s["name"] for s in symbols}
    assert "shape9a_filescope_member_fn_ptr_variable" not in names

    payload = repo_map.build_symbol_defs("shape9a_filescope_member_fn_ptr_variable", tmp_path)
    assert payload.get("no_match") is True


def test_declarator_shape_9b_inclass_member_function_pointer_variable_is_excluded(
    tmp_path: Path,
) -> None:
    """NO-REGRESSION PIN, not a guard for this fix (coverage-gap correction): an IN-CLASS
    pointer-to-member-function variable, `void (C::*mp)(int);` written inside a class body. This
    shape was ALREADY excluded on pre-fix `main` for an unrelated reason -- live-verified,
    tree-sitter-cpp cannot resolve `C::` inside a class body and instead emits an `ERROR` node
    sibling to a `pointer_declarator`, giving the `parenthesized_declarator` TWO named children
    (`['ERROR', 'pointer_declarator']`). `_cpp_parenthesized_declarator_wraps_bare_name`'s own
    `len(named_children) != 1` EARLY RETURN excludes it before the bare-name TYPE check even
    runs -- a different code path than shape 9a above, exercised here so both parses stay
    covered."""
    shapes_cpp = _write_declarator_shapes_fixture(tmp_path)

    _imports, symbols = lang_cpp.cpp_imports_and_symbols(shapes_cpp)
    names = {s["name"] for s in symbols}
    assert "shape9b_inclass_member_fn_ptr_variable" not in names
    # The enclosing class itself must still resolve normally -- the exclusion is scoped to the
    # member declaration, not the whole class body.
    assert "Shape9bClass" in names

    payload = repo_map.build_symbol_defs("shape9b_inclass_member_fn_ptr_variable", tmp_path)
    assert payload.get("no_match") is True


def test_declarator_shape_11_namespace_scoped_fnptr_variable_and_prototype(
    tmp_path: Path,
) -> None:
    """Shapes 4 and 1, replayed inside a `namespace app { ... }` block -- same verdicts as at
    file scope: the function-pointer variable is excluded, the prototype is kind "function", and
    the namespace itself still resolves as kind "namespace"."""
    root = tmp_path
    (root / "ns_shapes.cpp").write_text(
        "namespace app {\n"
        "void (*ns_handler)(int);\n"
        "\n"
        "void ns_prototype(int x);\n"
        "}  // namespace app\n",
        encoding="utf-8",
    )

    _imports, symbols = lang_cpp.cpp_imports_and_symbols(root / "ns_shapes.cpp")
    names = {s["name"]: s["kind"] for s in symbols}

    assert "ns_handler" not in names
    assert names.get("ns_prototype") == "function"
    assert names.get("app") == "namespace"


def test_using_alias_declaration_of_function_pointer_is_still_kind_type(tmp_path: Path) -> None:
    """C++-ONLY requirement matrix item 10: `using FP2 = void (*)(int);` -- CURRENT behavior on
    origin/main (unaffected by this fix): resolves via the `alias_declaration` branch, which reads
    the alias's OWN "name" field (`FP2`) directly and always emits kind "type" -- it never touches
    `_cpp_declarator_name_node`/the `function_declarator` walk at all (the aliased
    `type_descriptor` -> `abstract_function_declarator` shape on the RHS is never traversed by the
    declarator walker), so this fix cannot regress it. Documented here as a no-regression pin."""
    root = tmp_path
    (root / "alias_fnptr.cpp").write_text(
        "using FP2 = void (*)(int);\n",
        encoding="utf-8",
    )

    payload = repo_map.build_symbol_defs("FP2", root)

    assert not payload.get("no_match")
    assert payload["definitions"][0]["kind"] == "type"


# ---------------------------------------------------------------------------
# defs: functions, incl. qualified out-of-class methods (the central C++ wrinkle)
# ---------------------------------------------------------------------------


def test_defs_finds_inclass_prototype_and_outofclass_qualified_definition(
    tmp_path: Path,
) -> None:
    """`int getValue() const;` (in-class prototype) and `int Widget::getValue() const {...}`
    (out-of-class QUALIFIED definition) both resolve under the BARE name "getValue" -- this is
    the central design decision documented in the module docstring: the qualified_identifier
    descent must land on the bare name, not "Widget::getValue", or this pairing would break."""
    _write_cpp_fixture(tmp_path)

    payload = repo_map.build_symbol_defs("getValue", tmp_path)

    assert not payload.get("no_match")
    assert len(payload["definitions"]) == 2
    assert all(d["kind"] == "function" for d in payload["definitions"])
    lines = sorted(d["start_line"] for d in payload["definitions"])
    assert lines[0] != lines[1]


def test_defs_finds_constructor_prototype_and_qualified_definition_plus_class(
    tmp_path: Path,
) -> None:
    """The constructor is a THIRD "Widget" record alongside the class itself -- an in-class
    constructor prototype (`Widget(int value);`, a plain `declaration` with no return type) and
    its out-of-class qualified definition (`Widget::Widget(int value) : ... {}`) both resolve to
    the bare name "Widget", same as the class. All three kinds (class + 2x function) are
    legitimate, separate defs sharing one name -- an explicit, disclosed design choice."""
    _write_cpp_fixture(tmp_path)

    payload = repo_map.build_symbol_defs("Widget", tmp_path)

    assert not payload.get("no_match")
    kinds = sorted(d["kind"] for d in payload["definitions"])
    assert kinds == ["class", "function", "function"]


def test_defs_finds_template_function(tmp_path: Path) -> None:
    """`template <typename T> T identity(T value) {...}` -- the walker transparently descends
    into the `template_declaration` wrapper; the emitted kind is "function" (the wrapped
    construct's own kind), not a distinct "template" kind."""
    _write_cpp_fixture(tmp_path)

    payload = repo_map.build_symbol_defs("identity", tmp_path)

    assert not payload.get("no_match")
    assert payload["definitions"][0]["kind"] == "function"


def test_defs_finds_template_class_and_templated_qualified_method(tmp_path: Path) -> None:
    root = tmp_path
    (root / "box.cpp").write_text(
        "template <typename T>\n"
        "class Box {\n"
        "public:\n"
        "    T get() const;\n"
        "};\n"
        "\n"
        "template <typename T>\n"
        "T Box<T>::get() const {\n"
        "    return value_;\n"
        "}\n",
        encoding="utf-8",
    )

    box_payload = repo_map.build_symbol_defs("Box", root)
    get_payload = repo_map.build_symbol_defs("get", root)

    assert not box_payload.get("no_match")
    assert box_payload["definitions"][0]["kind"] == "class"
    assert not get_payload.get("no_match")
    # In-class prototype + the templated out-of-class qualified definition (`Box<T>::get`) --
    # the template arguments in the "scope" field must never leak into the extracted name.
    assert len(get_payload["definitions"]) == 2
    assert all(d["kind"] == "function" for d in get_payload["definitions"])


def test_macro_prefixed_anonymous_union_does_not_emit_reserved_keyword_as_a_name(
    tmp_path: Path,
) -> None:
    """Real-header dogfood finding (CPython's Include/object.h): a visibility-macro-prefixed
    ANONYMOUS union (`_Py_ANONYMOUS union { ... };`, no tag name) misparses such that the bare
    keyword `union` itself becomes the extracted declarator text. No valid C++ program can ever
    declare a symbol literally named a reserved keyword, so this must be rejected -- a
    zero-legitimate-cost precision fix, unlike the class-macro-misparse (which is NOT
    special-cased, see the module docstring)."""
    root = tmp_path
    (root / "anon_union.cpp").write_text(
        "struct Holder {\n"
        "    _Py_ANONYMOUS union {\n"
        "        int64_t full;\n"
        "        uint32_t half;\n"
        "    };\n"
        "};\n",
        encoding="utf-8",
    )

    _imports, symbols = lang_cpp.cpp_imports_and_symbols(root / "anon_union.cpp")

    names = {s["name"] for s in symbols}
    assert "union" not in names
    assert "Holder" in names


def test_reserved_keyword_helper_rejects_every_cpp_keyword() -> None:
    for keyword in lang_cpp._CPP_RESERVED_KEYWORDS:
        assert not lang_cpp._is_clean_cpp_symbol_name(keyword), keyword
    assert lang_cpp._is_clean_cpp_symbol_name("Widget")
    assert lang_cpp._is_clean_cpp_symbol_name("getValue")


def test_defs_finds_destructor_under_bare_class_name(tmp_path: Path) -> None:
    """A destructor's `destructor_name` node's single named child is the bare identifier (no
    tilde) -- C's existing generic declarator-descent fallback resolves it for free."""
    root = tmp_path
    (root / "resource.cpp").write_text(
        "class Resource {\npublic:\n    ~Resource();\n};\n\nResource::~Resource() {\n}\n",
        encoding="utf-8",
    )

    payload = repo_map.build_symbol_defs("Resource", root)

    assert not payload.get("no_match")
    kinds = sorted(d["kind"] for d in payload["definitions"])
    # class + in-class destructor prototype + out-of-class destructor definition.
    assert kinds == ["class", "function", "function"]


def test_operator_overload_is_honestly_excluded(tmp_path: Path) -> None:
    """`operator_name` has zero named children (no clean identifier to descend to) -- an
    operator overload is honestly excluded, not crashed on and not mis-named."""
    root = tmp_path
    (root / "ops.cpp").write_text(
        "class Widget {\n"
        "public:\n"
        "    Widget& operator+=(int delta);\n"
        "};\n"
        "\n"
        "Widget& Widget::operator+=(int delta) {\n"
        "    return *this;\n"
        "}\n",
        encoding="utf-8",
    )

    _imports, symbols = lang_cpp.cpp_imports_and_symbols(root / "ops.cpp")

    names = {s["name"] for s in symbols}
    assert "Widget" in names  # the class itself
    assert not any("operator" in name for name in names)


def test_anonymous_namespace_is_not_emitted_but_contents_are_reached(tmp_path: Path) -> None:
    root = tmp_path
    (root / "anon.cpp").write_text(
        "namespace {\n    int hidden_helper() { return 1; }\n}\n",
        encoding="utf-8",
    )

    _imports, symbols = lang_cpp.cpp_imports_and_symbols(root / "anon.cpp")

    kinds_by_name = {s["name"]: s["kind"] for s in symbols}
    assert kinds_by_name.get("hidden_helper") == "function"
    assert not any(s["kind"] == "namespace" for s in symbols)


# ---------------------------------------------------------------------------
# source
# ---------------------------------------------------------------------------


def test_source_returns_full_function_body(tmp_path: Path) -> None:
    _write_cpp_fixture(tmp_path)

    payload = repo_map.build_symbol_source("getValue", tmp_path)

    assert not payload.get("no_match")
    assert payload["sources"], "expected at least one source block for getValue"
    combined = "\n".join(s["source"] for s in payload["sources"])
    assert "Widget::getValue() const" in combined
    assert "return value_;" in combined


def test_source_for_inclass_prototype_plus_outofclass_definition_returns_both_blocks(
    tmp_path: Path,
) -> None:
    cpp_file = _write_cpp_fixture(tmp_path)

    sources = lang_cpp.cpp_parser_symbol_sources(cpp_file, "getValue")

    assert len(sources) == 2
    assert any(s["source"].strip() == "int getValue() const;" for s in sources)
    assert any("return value_;" in s["source"] for s in sources)


def test_source_returns_class_and_namespace_blocks(tmp_path: Path) -> None:
    cpp_file = _write_cpp_fixture(tmp_path)

    class_sources = lang_cpp.cpp_parser_symbol_sources(cpp_file, "Widget")
    namespace_sources = lang_cpp.cpp_parser_symbol_sources(cpp_file, "app")

    assert any(s["kind"] == "class" for s in class_sources)
    assert any(s["kind"] == "namespace" for s in namespace_sources)


# ---------------------------------------------------------------------------
# imports: angle / quoted / macro #include forms
# ---------------------------------------------------------------------------


def test_cpp_imports_and_symbols_extracts_include_targets(tmp_path: Path) -> None:
    source = '#include <cstdio>\n#include "local.h"\n\nint main() {\n    return 0;\n}\n'
    cpp_file = tmp_path / "main.cpp"
    cpp_file.write_text(source, encoding="utf-8")

    imports, symbols = lang_cpp.cpp_imports_and_symbols(cpp_file)

    # Quote/bracket delimiters are stripped -- the recorded module string is the bare target.
    assert imports == sorted({"cstdio", "local.h"})
    assert any(s["name"] == "main" and s["kind"] == "function" for s in symbols)


def test_build_repo_map_surfaces_cpp_imports_and_symbols(tmp_path: Path) -> None:
    _write_cpp_fixture(tmp_path)

    repo_map_payload = repo_map.build_repo_map(tmp_path)

    file_imports = [
        entry for entry in repo_map_payload["imports"] if entry["file"].endswith("widget.cpp")
    ]
    assert file_imports, "expected an imports entry for widget.cpp"
    assert "cstdio" in file_imports[0]["imports"]
    assert "vector" in file_imports[0]["imports"]
    assert "widget_types.h" in file_imports[0]["imports"]

    symbol_names = {
        s["name"] for s in repo_map_payload["symbols"] if s["file"].endswith("widget.cpp")
    }
    assert {
        "app",
        "Point",
        "PointAlias",
        "ValueAlias",
        "WidgetKind",
        "ScopedKind",
        "WidgetValue",
        "Widget",
        "getValue",
        "identity",
    }.issubset(symbol_names)


# ---------------------------------------------------------------------------
# #74-follow-up: tg imports (cpp_imports_with_lines / build_file_imports) -- foundational tier,
# mirrors test_lang_c.py's own test_c_imports_with_lines_extracts_includes_with_lines.
# ---------------------------------------------------------------------------


def test_cpp_imports_with_lines_extracts_includes_with_lines(tmp_path: Path) -> None:
    cpp_file = _write_cpp_fixture(tmp_path)

    entries = lang_cpp.cpp_imports_with_lines(cpp_file)

    modules = {entry["module"]: entry["line"] for entry in entries}
    assert modules == {
        "cstdio": 1,
        "vector": 2,
        "widget_types.h": 3,
    }


def test_cpp_imports_with_lines_non_cpp_suffix_returns_empty(tmp_path: Path) -> None:
    not_cpp = tmp_path / "widget.txt"
    not_cpp.write_text("#include <cstdio>\n", encoding="utf-8")

    assert lang_cpp.cpp_imports_with_lines(not_cpp) == []


def test_cpp_imports_with_lines_grammar_absent_returns_empty(tmp_path: Path, monkeypatch) -> None:
    cpp_file = _write_cpp_fixture(tmp_path)
    monkeypatch.setattr(lang_cpp, "_cpp_parser", lambda: None)

    assert lang_cpp.cpp_imports_with_lines(cpp_file) == []


def test_file_imports_returns_cpp_include_directives_with_lines(tmp_path: Path) -> None:
    cpp_file = _write_cpp_fixture(tmp_path)

    payload = repo_map.build_file_imports(cpp_file)

    assert payload["result_incomplete"] is False
    modules = {entry["module"]: entry["line"] for entry in payload["imports"]}
    assert modules == {
        "cstdio": 1,
        "vector": 2,
        "widget_types.h": 3,
    }
    # Foundational tier: raw #include directives are real, but resolving them to a specific file
    # is deferred (C++ has no standardized manifest to resolve against) -- every row must be
    # unresolved and never presumed external, matching the fail-closed contract.
    assert all(entry["resolved"] is None for entry in payload["imports"])
    assert all(entry["external"] is False for entry in payload["imports"])


def test_file_imports_works_for_header_suffix(tmp_path: Path) -> None:
    """`.h` files go through the SAME extractor as `.cpp` files (both are "cpp" per the registry) --
    a header-only fixture must resolve its own #include directives too."""
    header = tmp_path / "widget.h"
    header.write_text('#include <vector>\n#include "helper.h"\n', encoding="utf-8")

    payload = repo_map.build_file_imports(header)

    assert payload["result_incomplete"] is False
    modules = {entry["module"] for entry in payload["imports"]}
    assert modules == {"vector", "helper.h"}


def test_cpp_include_target_text_handles_macro_and_call_forms(tmp_path: Path) -> None:
    """`#include MACRO_HEADER` (macro-expanded) and `#include COMBINE(a, b)` (macro-combined)
    both parse as real preproc_include nodes (tree-sitter-cpp never runs a preprocessor) -- the
    extractor records the raw macro/call text honestly rather than dropping the row."""
    source = "#include MACRO_HEADER\n#include COMBINE(a, b)\n\nvoid noop() {\n}\n"
    cpp_file = tmp_path / "macro_includes.cpp"
    cpp_file.write_text(source, encoding="utf-8")

    entries = lang_cpp.cpp_imports_with_lines(cpp_file)

    modules = {entry["module"] for entry in entries}
    assert "MACRO_HEADER" in modules
    assert any("COMBINE" in module for module in modules)


# ---------------------------------------------------------------------------
# Deferred caller-graph, grammar PRESENT: honest resolution_gaps, not a silent proven-zero.
# ---------------------------------------------------------------------------


def test_refs_grammar_present_still_reports_import_resolution_gap(tmp_path: Path) -> None:
    _write_cpp_fixture(tmp_path)

    payload = repo_map.build_symbol_refs("getValue", tmp_path)

    assert not payload.get("no_match")
    gaps = payload["resolution_gaps"]
    cpp_gaps = [gap for gap in gaps if gap["language"] == "cpp"]
    assert len(cpp_gaps) == 1
    # NOT "fail-closed" (that's the grammar-ABSENT case, covered separately below) -- this is
    # the narrower "grammar works fine, but no reverse-import resolver exists yet" gap.
    assert "fail-closed" not in cpp_gaps[0]["reason"]
    assert "reverse-import" in cpp_gaps[0]["reason"]
    assert cpp_gaps[0]["files_affected"] >= 1
    # Honesty floor: the remediation must tell an agent to treat a zero count as UNKNOWN, not
    # proven-zero -- the exact failure mode this test guards against.
    assert (
        "not proven-zero" in cpp_gaps[0]["remediation"] or "UNKNOWN" in (cpp_gaps[0]["remediation"])
    )


# ---------------------------------------------------------------------------
# Grammar-absent: fail-closed, resolution_gaps, honest exit code.
# ---------------------------------------------------------------------------


def test_grammar_absent_yields_no_fabricated_defs_and_resolution_gap(
    tmp_path: Path, monkeypatch
) -> None:
    _write_cpp_fixture(tmp_path)
    # A python symbol elsewhere in the same repo so refs has something REAL to find -- the
    # resolution_gaps floor is about a C++ file being an honestly-labeled BYSTANDER in the scan
    # universe, not about the query's own target living in the grammar-missing language.
    (tmp_path / "target.py").write_text("def Target():\n    return 1\n", encoding="utf-8")
    monkeypatch.setattr(lang_cpp, "_cpp_parser", lambda: None)

    defs_payload = repo_map.build_symbol_defs("getValue", tmp_path)
    assert defs_payload.get("no_match") is True
    assert defs_payload["definitions"] == []
    defs_gaps = defs_payload["resolution_gaps"]
    assert any(gap["language"] == "cpp" for gap in defs_gaps)
    cpp_gap = next(gap for gap in defs_gaps if gap["language"] == "cpp")
    assert "fail-closed" in cpp_gap["reason"]

    refs_payload = repo_map.build_symbol_refs("Target", tmp_path)
    assert not refs_payload.get("no_match")
    gaps = refs_payload["resolution_gaps"]
    assert any(gap["language"] == "cpp" for gap in gaps)
    cpp_refs_gap = next(gap for gap in gaps if gap["language"] == "cpp")
    assert "fail-closed" in cpp_refs_gap["reason"]
    assert cpp_refs_gap["files_affected"] >= 1
    assert "fall back to plain literal-text/regex matching" not in cpp_refs_gap["remediation"]


def test_grammar_absent_cli_exit_code_is_honest_not_found(tmp_path: Path, monkeypatch) -> None:
    """A C++-only target with the grammar missing must exit 1 (honest not-found) -- never a
    silent 0 and never a crash."""
    from typer.testing import CliRunner

    from tensor_grep.cli.main import app

    _write_cpp_fixture(tmp_path)
    monkeypatch.setattr(lang_cpp, "_cpp_parser", lambda: None)

    result = CliRunner().invoke(app, ["defs", str(tmp_path), "getValue"])

    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# Agent capsule
# ---------------------------------------------------------------------------


def test_agent_capsule_reports_cpp_target_language(tmp_path: Path) -> None:
    _write_cpp_fixture(tmp_path)

    payload = agent_capsule.build_agent_capsule("getValue", tmp_path)

    assert payload["context_consistency"]["primary_target_language"] == "cpp"


# ---------------------------------------------------------------------------
# Deep-AST guard: explicit-stack DFS must not raise RecursionError (lang_go.py/lang_c.py F26
# precedent).
# ---------------------------------------------------------------------------


def _deep_nested_cpp_source(depth: int) -> str:
    return "int target()\n{\n    return " + ("(" * depth) + "1" + (")" * depth) + ";\n}\n"


def test_cpp_walkers_survive_pathologically_deep_ast_without_recursion_error(
    tmp_path: Path,
) -> None:
    depth = sys.getrecursionlimit() + 500
    deep_cpp = tmp_path / "deep.cpp"
    deep_cpp.write_text(_deep_nested_cpp_source(depth), encoding="utf-8")

    imports, symbols = lang_cpp.cpp_imports_and_symbols(deep_cpp)
    assert imports == []
    assert any(s["name"] == "target" and s["kind"] == "function" for s in symbols)

    sources = lang_cpp.cpp_parser_symbol_sources(deep_cpp, "target")
    assert len(sources) == 1
    assert sources[0]["kind"] == "function"


# ---------------------------------------------------------------------------
# Grammar-missing import failure (package not installed) -- distinct from monkeypatched None.
# ---------------------------------------------------------------------------


def test_cpp_parser_returns_none_when_grammar_module_missing(monkeypatch) -> None:
    import builtins

    real_import = builtins.__import__

    def _fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "tree_sitter_cpp":
            raise ImportError("simulated missing grammar")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    lang_cpp._cpp_parser.cache_clear()
    try:
        assert lang_cpp._cpp_parser() is None
    finally:
        lang_cpp._cpp_parser.cache_clear()


# ---------------------------------------------------------------------------
# Read/parse-error guards return ([], []) rather than raising.
# ---------------------------------------------------------------------------


def test_cpp_imports_and_symbols_missing_file_returns_empty(tmp_path: Path) -> None:
    missing = tmp_path / "DoesNotExist.cpp"
    imports, symbols = lang_cpp.cpp_imports_and_symbols(missing)
    assert imports == []
    assert symbols == []


def test_cpp_imports_and_symbols_non_cpp_suffix_returns_empty(tmp_path: Path) -> None:
    other = tmp_path / "widget.txt"
    other.write_text("not cpp", encoding="utf-8")
    imports, symbols = lang_cpp.cpp_imports_and_symbols(other)
    assert imports == []
    assert symbols == []
