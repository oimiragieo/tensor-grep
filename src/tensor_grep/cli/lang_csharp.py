"""C# language extractor for tensor-grep's multi-language symbol graph (PATH A Stage 1).

Second language expansion beyond the original four (python/javascript/typescript/rust), added
alongside Go. Plugs into the ``lang_registry`` seam Stage 0 built (see ``lang_registry.py`` +
the ``lang_registry.register_language(...)`` calls near the bottom of ``repo_map.py``) -- C# gets
its OWN ``LanguageSpec`` entry, registered from ``repo_map.py``, with zero special-casing beyond
the couple of dispatch sites documented in the module docstring there.

Like ``lang_go.py``, this module imports NOTHING from ``repo_map.py`` (``repo_map`` -> this
module, never the reverse -- ``repo_map.py`` needs to import this module to register C#'s
``LanguageSpec`` and to call its ``csharp_parser_symbol_sources`` directly at the ``tg source``
dispatch site; a reverse import would cycle). The handful of tiny helpers this module needs from
``repo_map.py`` are duplicated here instead of imported, matching ``lang_go.py``'s own precedent.

FOUNDATIONAL SCOPE: this module lights up ``defs``/``source``/``imports``/``agent`` for ``.cs``
files (symbols: class/interface/struct/enum/record declarations as kind "class", method/
constructor declarations as kind "function"; imports: dotted ``using``-directive namespace
names). The cross-file caller-graph (``references_and_calls`` / ``file_imports_symbol_from_
definition`` / ``import_update_target`` / per-repo-root context priming for a `.csproj`/
namespace-to-file resolver) is DEFERRED to a follow-up -- this module registers those
``LanguageSpec`` fields as ``None``, exactly like Go's own ``import_update_target=None`` gap.
``tg refs``/``tg callers``/`tg blast-radius`` on a C# symbol fall through to the generic
``_regex_references_and_calls`` text-heuristic path in repo_map.py (never a crash, never a
fabricated AST-verified match) -- unaffected by this module.

FAIL-CLOSED CONTRACT (Stage 0 honesty floor, extended here exactly as it was for Go): C# has NO
regex-heuristic fallback. When the ``tree_sitter_c_sharp`` grammar package is not installed,
every extractor in this module returns empty ([]/([], [])) rather than degrading to a regex/text
heuristic (unlike JS/TS/Rust, which all fall back to regex extraction when their own tree-sitter
grammar is missing). ``LanguageSpec.provenance_when_missing="grammar-missing"`` (NOT
``"regex-heuristic"``) is what makes ``_language_coverage_gaps_for_universe`` in repo_map.py
treat a grammar-absent C# file as a genuine ``resolution_gaps`` entry instead of silently
reporting zero matches as if the symbol just did not exist.

TASK 10B: ``csharp_references_and_calls`` promotes C# from the foundational (defs/imports-only)
tier to the parser-backed refs/callers tier, mirroring Task 10A's Java landing
(``lang_java.java_references_and_calls``) -- IN-FILE AST reference/call extraction only, no
cross-file import resolution (that stays deferred, same as Java's Task 11A gap). C#'s own grammar
shape (verified against the installed ``tree_sitter_c_sharp`` 0.23.x grammar, not guessed --
see B5 of ``tensor-grep-add-language``) differs from Java's in one structural way that matters:
Java's ``method_invocation`` node carries an optional ``object`` field directly; C#'s
``invocation_expression`` instead has a single ``function`` field that is EITHER a bare
``identifier`` (unqualified call, e.g. ``Local()``) OR a ``member_access_expression`` (qualified
call, e.g. ``_helper.Compute()``) -- there is no C# node type equivalent to Java's
``field_access`` that is distinct from a call's receiver expression; C# reuses
``member_access_expression`` for both a call's qualifier AND a plain (non-call) member/property
read (``_helper.Field``, ``w.Foo``), discriminated only by whether a ``member_access_expression``
is itself the ``function`` field of an enclosing ``invocation_expression``.

RESOLUTION CONFIDENCE / PROVENANCE (same two-band honesty shape as Java, C#-specific mechanism
and numbers):

- ``_CSHARP_DEMOTED_CONFIDENCE`` (0.6) / ``_CSHARP_DEMOTED_PROVENANCE``
  (``"csharp-name-heuristic"``): the DEFAULT band for every entry -- an AST-confirmed node (a
  real call/field-access/type/value site, never a string literal or comment) whose receiver's
  static type is NOT resolvable from evidence in this same file.
- ``_CSHARP_CONFIRMED_CONFIDENCE`` (0.9) / ``_CSHARP_CONFIRMED_PROVENANCE``
  (``"csharp-infile-type-confirmation"``): an ``invocation_expression``/``member_access_expression``
  node whose receiver (an ``identifier``, a bare ``this``, or -- for an UNQUALIFIED call -- the
  enclosing type itself, since C# lets a member call omit an explicit ``this.``) has a static type
  DIRECTLY readable from a declaration node in this SAME file (a ``variable_declaration``'s
  ``variable_declarator`` under a ``field_declaration``/``local_declaration_statement``, a
  ``parameter``, or a ``catch_declaration``), AND that exact type ALSO directly declares a member
  named *symbol* in its own body (a ``method_declaration`` for a call, a ``field_declaration``'s
  ``variable_declarator`` OR a ``property_declaration`` for a field/property access -- C# idiomatic
  member access is usually through a property, not a raw field, so both count) -- two
  independently-checked AST facts joined with no cross-file assumption. Not 1.0, matching Java's
  0.9 rationale exactly: C# also allows inheritance, overloading, and shadowing, so a
  declared-type match one step removed is not fully ruled out.
- A constructor reference (``object_creation_expression``) always stays in the demoted band, same
  as Java's constructor handling -- there is no in-file receiver to confirm against for a `new`
  expression.
- Only simple, unqualified type positions are recorded for the CONFIRMED band's declared-type
  lookup (a ``variable_declaration``/``parameter``/``catch_declaration`` whose ``type`` field is a
  bare ``identifier``) -- ``var``-inferred locals (``implicit_type``), predefined types (``int``,
  ``string``), and qualified/generic declared types are an accepted, documented gap, mirroring
  Java's requirement of a directly-readable ``type_identifier``.

Cross-file caller confirmation (namespace/`.csproj` import resolution, matching Go's
``go-import-resolution`` band) is still NOT implemented -- a cross-file caller today always lands
in the demoted band, honestly labeled.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Duplicated tiny helpers -- see the module docstring: no import from repo_map.py, to avoid an
# import cycle (repo_map.py imports THIS module). Keep byte-identical to repo_map.py's twins
# (``_tree_sitter_node_text`` / ``_is_clean_symbol_name`` / ``_symbol_record``) if any of them
# ever change there.
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
# Parser factory (clone of repo_map.py's ``_rust_parser`` / lang_go.py's ``_go_parser`` shape).
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _csharp_parser() -> Any | None:
    try:
        import tree_sitter
        import tree_sitter_c_sharp
    except ImportError:
        return None

    language = tree_sitter.Language(tree_sitter_c_sharp.language())
    return tree_sitter.Parser(language)


# ---------------------------------------------------------------------------
# Defs + imports: one tree-sitter pass per file.
# ---------------------------------------------------------------------------

# Type-declaration node kinds -> symbol kind "class" (matches how tensor-grep already collapses
# TS interfaces/JS classes into a single "class" bucket for defs/orient rather than minting a
# kind per C# declaration form).
_CSHARP_CLASS_NODE_TYPES = frozenset({
    "class_declaration",
    "interface_declaration",
    "struct_declaration",
    "enum_declaration",
    "record_declaration",
})
# Member-declaration node kinds -> symbol kind "function".
_CSHARP_FUNCTION_NODE_TYPES = frozenset({"method_declaration", "constructor_declaration"})
# ``using_directive``'s target-namespace child is always the RIGHTMOST ``identifier``/
# ``qualified_name`` child regardless of modifier -- verified against the installed
# tree_sitter_c_sharp 0.23.x grammar's node shapes for all four directive forms:
#   using System;                              -> identifier
#   using System.Collections.Generic;          -> qualified_name
#   using MyAlias = System.Text.StringBuilder;  -> [identifier "MyAlias", qualified_name TARGET]
#   using static System.Math;                   -> qualified_name (after the "static" token)
#   global using System.Linq;                   -> qualified_name (after the "global"/"using" tokens)
# In the aliased form the ALIAS name is emitted FIRST (leftmost), so taking the last matching
# child is what discriminates "the namespace actually being imported" from "the local alias" --
# never the reverse (an alias is never emitted after its target).
_CSHARP_USING_TARGET_NODE_TYPES = frozenset({"identifier", "qualified_name"})
# Informational/documentation only (Stage 0/1 convention) -- no dispatch seam reads this field
# yet; see lang_registry.LanguageSpec.def_node_kinds docstring.
_CSHARP_DEF_NODE_KINDS = (
    "class_declaration",
    "interface_declaration",
    "struct_declaration",
    "enum_declaration",
    "record_declaration",
    "method_declaration",
    "constructor_declaration",
)

# ---------------------------------------------------------------------------
# Task 10B: references + calls (in-file AST extraction, no cross-file resolution).
# See the module docstring's "TASK 10B" / "RESOLUTION CONFIDENCE / PROVENANCE" sections for the
# full derivation of the two honesty bands and the C#-specific grammar shape this mirrors from
# Java's lang_java.py without copying it blindly.
# ---------------------------------------------------------------------------

_CSHARP_DEMOTED_CONFIDENCE = 0.6
_CSHARP_DEMOTED_PROVENANCE = "csharp-name-heuristic"
_CSHARP_CONFIRMED_CONFIDENCE = 0.9
_CSHARP_CONFIRMED_PROVENANCE = "csharp-infile-type-confirmation"

# Type-declaration node kinds whose own body (declaration_list) can directly declare a member --
# the CONFIRMED band's "owner type" universe. Struct is a C#-specific addition beyond Java's
# class/interface/enum/record set (a C# struct can declare methods/fields/properties too).
_CSHARP_TYPE_BODY_DECLARATION_TYPES = {
    "class_declaration",
    "interface_declaration",
    "struct_declaration",
    "record_declaration",
}

# Node types whose "name" field defines *this* declaration rather than referencing an existing
# one elsewhere -- excluded from the reference/call walk (mirrors lang_java's
# _JAVA_NAME_DEFINING_PARENT_TYPES, adapted to C#'s own grammar: property_declaration and
# catch_declaration have no Java equivalent in that set; enum_member_declaration is C#'s
# enum-constant-name node).
_CSHARP_NAME_DEFINING_PARENT_TYPES = {
    "class_declaration",
    "interface_declaration",
    "struct_declaration",
    "enum_declaration",
    "record_declaration",
    "method_declaration",
    "constructor_declaration",
    "property_declaration",
    "variable_declarator",
    "parameter",
    "catch_declaration",
    "enum_member_declaration",
}


def _csharp_type_base_identifier(type_node: Any) -> Any | None:
    """Return the base ``identifier`` node for a type-position node, or ``None``.

    Handles the three shapes a type reference can take in this grammar: a bare ``identifier``
    (``Helper``), a ``generic_name`` (``List<int>`` -> base identifier ``List``, never a type
    argument), or a ``qualified_name`` whose rightmost (``name`` field) segment is itself either
    of the first two shapes (``System.Collections.Generic.List<int>`` -> ``List``,
    ``System.Exception`` -> ``Exception``). Anything else (an array type, a nullable/tuple type)
    is an accepted, documented gap -- returns ``None`` rather than a wrong guess, mirroring
    ``lang_java._java_object_creation_type_identifier``'s identical fallback.
    """
    if type_node.type == "identifier":
        return type_node
    if type_node.type == "generic_name":
        for child in type_node.children:
            if child.type == "identifier":
                return child
        return None
    if type_node.type == "qualified_name":
        name_field = type_node.child_by_field_name("name")
        if name_field is None:
            return None
        return _csharp_type_base_identifier(name_field)
    return None


def _csharp_object_creation_type_identifier(node: Any) -> Any | None:
    """The base type identifier for an ``object_creation_expression``'s ``type`` field -- see
    ``_csharp_type_base_identifier`` for the shape-handling rules."""
    type_field = node.child_by_field_name("type")
    if type_field is None:
        return None
    return _csharp_type_base_identifier(type_field)


def _csharp_resolution_context(
    root: Any, source_bytes: bytes, symbol: str
) -> tuple[dict[str, set[str]], set[str], set[str]]:
    """Single upfront walk building the three facts the in-file receiver-type CONFIRMED band
    needs -- all derived from THIS file's AST alone, mirroring
    ``lang_java._java_resolution_context``'s shape with C#'s own node kinds:

    - ``declared_types``: every ``identifier`` NAME this file declares with a readable static
      type (a local variable, a field, a parameter, a caught exception), mapped to the SET of
      base-type-identifier texts it was ever declared with in this file.
    - ``method_owner_types``: the name of every class/interface/struct/record declared in this
      file whose OWN body (direct child ``method_declaration``, not inherited, not nested deeper)
      declares a method named *symbol*.
    - ``field_owner_types``: same, for a ``field_declaration``'s ``variable_declarator`` OR a
      ``property_declaration`` named *symbol* (C# idiomatic member access is usually through a
      property, not a raw field -- both count, since the grammar does not distinguish a property
      read from a field read at the reference site; see the module docstring).
    """
    declared_types: dict[str, set[str]] = {}
    method_owner_types: set[str] = set()
    field_owner_types: set[str] = set()

    def node_text(node: Any) -> str:
        return _tree_sitter_node_text(source_bytes, node)

    def record_declared_type(name_node: Any | None, type_node: Any | None) -> None:
        if name_node is None or type_node is None or type_node.type != "identifier":
            return
        declared_types.setdefault(node_text(name_node), set()).add(node_text(type_node))

    def owner_type_name(member_parent: Any | None) -> str | None:
        # member_parent is the declaration_list a member sits directly inside; its own parent is
        # the type declaration (class_declaration/interface_declaration/struct_declaration/
        # record_declaration) that owns it.
        if member_parent is None or member_parent.type != "declaration_list":
            return None
        type_decl = member_parent.parent
        if type_decl is None or type_decl.type not in _CSHARP_TYPE_BODY_DECLARATION_TYPES:
            return None
        type_name_field = type_decl.child_by_field_name("name")
        return node_text(type_name_field) if type_name_field is not None else None

    stack = [root]
    while stack:
        node = stack.pop()
        node_type = node.type

        if node_type == "variable_declaration":
            type_field = node.child_by_field_name("type")
            for child in node.children:
                if child.type == "variable_declarator":
                    record_declared_type(child.child_by_field_name("name"), type_field)
        elif node_type in {"parameter", "catch_declaration"}:
            record_declared_type(node.child_by_field_name("name"), node.child_by_field_name("type"))
        elif node_type == "method_declaration":
            name_field = node.child_by_field_name("name")
            if name_field is not None and node_text(name_field) == symbol:
                owner = owner_type_name(node.parent)
                if owner is not None:
                    method_owner_types.add(owner)
        elif node_type == "field_declaration":
            owner = owner_type_name(node.parent)
            if owner is not None:
                for declarator_parent in node.children:
                    if declarator_parent.type != "variable_declaration":
                        continue
                    for declarator in declarator_parent.children:
                        if declarator.type != "variable_declarator":
                            continue
                        name_field = declarator.child_by_field_name("name")
                        if name_field is not None and node_text(name_field) == symbol:
                            field_owner_types.add(owner)
        elif node_type == "property_declaration":
            name_field = node.child_by_field_name("name")
            if name_field is not None and node_text(name_field) == symbol:
                owner = owner_type_name(node.parent)
                if owner is not None:
                    field_owner_types.add(owner)

        stack.extend(node.children)

    return declared_types, method_owner_types, field_owner_types


def _csharp_enclosing_type_name(node: Any, source_bytes: bytes) -> str | None:
    """Walk PARENTS (not the file-wide stack above) to find the nearest enclosing type
    declaration's name -- used for a bare ``this`` receiver AND for an unqualified call (C# lets
    a member call omit an explicit ``this.``, so the implicit receiver's type is whatever type
    encloses the call site), mirroring ``lang_java._java_enclosing_type_name``.
    """
    current = node.parent
    while current is not None:
        if current.type in _CSHARP_TYPE_BODY_DECLARATION_TYPES:
            name_field = current.child_by_field_name("name")
            if name_field is not None:
                return _tree_sitter_node_text(source_bytes, name_field)
            return None
        current = current.parent
    return None


def csharp_references_and_calls(
    path: Path, symbol: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """In-file AST reference/call rows for *symbol* in *path* -- see the module docstring's
    "TASK 10B" section for the full AST-shape mapping. Scope: single-file only, no cross-file
    resolution (mirrors ``lang_java.java_references_and_calls`` exactly)."""
    if path.suffix != ".cs":
        return [], []

    parser = _csharp_parser()
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

    def _is_definition_identifier(node: Any) -> bool:
        parent = node.parent
        if parent is None or parent.type not in _CSHARP_NAME_DEFINING_PARENT_TYPES:
            return False
        name_field = parent.child_by_field_name("name")
        return name_field is not None and name_field == node

    declared_types, method_owner_types, field_owner_types = _csharp_resolution_context(
        tree.root_node, source_bytes, symbol
    )

    def _receiver_confirmation(object_node: Any | None, owner_types: set[str]) -> bool:
        """True only when *object_node* (an ``invocation_expression``'s
        ``member_access_expression.expression`` field, or a ``member_access_expression``'s own
        ``expression`` field) has a static type readable from THIS file that is ALSO one of
        *owner_types*. See the module docstring's RESOLUTION CONFIDENCE / PROVENANCE section."""
        if object_node is None or not owner_types:
            return False
        if object_node.type == "identifier":
            receiver_types = declared_types.get(_node_text(object_node), set())
            return not receiver_types.isdisjoint(owner_types)
        if object_node.type == "this":
            enclosing = _csharp_enclosing_type_name(object_node, source_bytes)
            return enclosing is not None and enclosing in owner_types
        return False

    def _unqualified_call_confirmed(invocation_node: Any, owner_types: set[str]) -> bool:
        """An unqualified call (``Local()``) has an implicit ``this`` receiver -- confirmed only
        when the enclosing type itself directly declares a member named *symbol*."""
        if not owner_types:
            return False
        enclosing = _csharp_enclosing_type_name(invocation_node, source_bytes)
        return enclosing is not None and enclosing in owner_types

    def _emit(
        bucket: list[dict[str, Any]], node: Any, *, kind: str, ref_kind: str, confirmed: bool
    ) -> None:
        if confirmed:
            confidence = _CSHARP_CONFIRMED_CONFIDENCE
            provenance = _CSHARP_CONFIRMED_PROVENANCE
        else:
            confidence = _CSHARP_DEMOTED_CONFIDENCE
            provenance = _CSHARP_DEMOTED_PROVENANCE
        bucket.append({
            "name": symbol,
            "kind": kind,
            "ref_kind": ref_kind,
            "file": str(path),
            "line": node.start_point[0] + 1,
            "text": _line_text(node),
            # PER-MATCH honesty band -- see the module docstring's RESOLUTION CONFIDENCE /
            # PROVENANCE section.
            "resolution_confidence": confidence,
            "resolution_provenance": [provenance],
        })

    # Nodes already claimed by a special-case branch below (an invocation's function identifier
    # or member_access_expression.name, an object_creation_expression's base type identifier) are
    # tracked here so neither the generic identifier walk NOR the plain member_access_expression
    # branch (when that same node is ALSO an invocation's function) ever double-emits them. Keyed
    # on (start_byte, end_byte), NOT Python `id()` -- see lang_java.py's identical comment for why
    # (tree_sitter mints a fresh wrapper object on every `.children`/`.child_by_field_name` access
    # to the same underlying node).
    claimed_node_ids: set[tuple[int, int]] = set()

    def _walk(root: Any) -> None:
        # Explicit-stack DFS (not recursion) -- matches every other language extractor in this
        # registry; avoids a RecursionError on a pathologically deep real-world AST.
        stack = [root]
        while stack:
            node = stack.pop()
            node_type = node.type

            if node_type == "invocation_expression":
                function = node.child_by_field_name("function")
                if function is not None and function.type == "identifier":
                    if _node_text(function) == symbol:
                        claimed_node_ids.add((function.start_byte, function.end_byte))
                        confirmed = _unqualified_call_confirmed(node, method_owner_types)
                        _emit(
                            references,
                            function,
                            kind="reference",
                            ref_kind="call",
                            confirmed=confirmed,
                        )
                        _emit(calls, function, kind="call", ref_kind="call", confirmed=confirmed)
                elif function is not None and function.type == "member_access_expression":
                    name_field = function.child_by_field_name("name")
                    if name_field is not None and _node_text(name_field) == symbol:
                        claimed_node_ids.add((name_field.start_byte, name_field.end_byte))
                        confirmed = _receiver_confirmation(
                            function.child_by_field_name("expression"), method_owner_types
                        )
                        _emit(
                            references,
                            name_field,
                            kind="reference",
                            ref_kind="call",
                            confirmed=confirmed,
                        )
                        _emit(calls, name_field, kind="call", ref_kind="call", confirmed=confirmed)
            elif node_type == "object_creation_expression":
                type_identifier = _csharp_object_creation_type_identifier(node)
                if type_identifier is not None and _node_text(type_identifier) == symbol:
                    claimed_node_ids.add((type_identifier.start_byte, type_identifier.end_byte))
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
            elif node_type == "member_access_expression":
                # Only a PLAIN (non-call) member/property read -- a member_access_expression that
                # is itself an invocation_expression's `function` field was already claimed above.
                name_field = node.child_by_field_name("name")
                if (
                    name_field is not None
                    and (name_field.start_byte, name_field.end_byte) not in claimed_node_ids
                    and _node_text(name_field) == symbol
                ):
                    claimed_node_ids.add((name_field.start_byte, name_field.end_byte))
                    confirmed = _receiver_confirmation(
                        node.child_by_field_name("expression"), field_owner_types
                    )
                    _emit(
                        references,
                        name_field,
                        kind="reference",
                        ref_kind="field",
                        confirmed=confirmed,
                    )

            stack.extend(reversed(node.children))

    def _walk_generic_identifiers(root: Any) -> None:
        stack = [root]
        while stack:
            node = stack.pop()
            if (
                node.type == "identifier"
                and (node.start_byte, node.end_byte) not in claimed_node_ids
                and _node_text(node) == symbol
                and not _is_definition_identifier(node)
            ):
                parent = node.parent
                is_type_position = parent is not None and (
                    parent.type == "base_list" or parent.child_by_field_name("type") == node
                )
                ref_kind = "type" if is_type_position else "value"
                _emit(references, node, kind="reference", ref_kind=ref_kind, confirmed=False)
            stack.extend(reversed(node.children))

    _walk(tree.root_node)
    _walk_generic_identifiers(tree.root_node)

    references.sort(key=lambda item: (item["file"], item["line"], item["text"]))
    calls.sort(key=lambda item: (item["file"], item["line"], item["text"]))
    return references, calls


def _csharp_using_directive_target(node: Any, source_bytes: bytes) -> str | None:
    """Return a ``using_directive`` node's target namespace text, or ``None``.

    See ``_CSHARP_USING_TARGET_NODE_TYPES`` docstring comment above for why "last matching
    child" is correct across all four directive forms (plain/dotted/aliased/static/global).
    """
    target: Any | None = None
    for child in node.children:
        if child.type in _CSHARP_USING_TARGET_NODE_TYPES:
            target = child
    if target is None:
        return None
    return _tree_sitter_node_text(source_bytes, target)


def csharp_imports_and_symbols(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    """Extract ``using``-directive namespace names + type/member declarations from a C# source
    file, one AST pass.

    Defs covered: ``class_declaration``/``interface_declaration``/``struct_declaration``/
    ``enum_declaration``/``record_declaration`` (kind "class"), and ``method_declaration``/
    ``constructor_declaration`` (kind "function", including an interface's body-less method
    signature -- the C# grammar reuses ``method_declaration`` for both an abstract interface
    member and a concrete class method, discriminated only by the presence of a ``body`` field,
    which this extractor does not need to distinguish since both are legitimate definitions).
    Imports come from every ``using_directive``'s target namespace (alias/static/global
    qualifiers do not change what gets recorded -- see ``_csharp_using_directive_target``).
    """
    if path.suffix != ".cs":
        return [], []

    parser = _csharp_parser()
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
        # Explicit-stack DFS (not recursion): a pathologically deep AST must never raise
        # RecursionError -- mirrors lang_go.py's F26 fix (audit #63) precedent, applied here
        # preemptively rather than retrofitted after an incident. Children are pushed in
        # reverse so the leftmost child is popped (and thus visited) first, preserving the
        # original pre-order traversal.
        stack = [root]
        while stack:
            node = stack.pop()
            node_type = node.type
            if node_type == "using_directive":
                target_text = _csharp_using_directive_target(node, source_bytes)
                if target_text is not None:
                    imports.append(target_text)
            elif node_type in _CSHARP_CLASS_NODE_TYPES:
                name_node = node.child_by_field_name("name")
                if name_node is not None:
                    name = _node_text(name_node)
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
            elif node_type in _CSHARP_FUNCTION_NODE_TYPES:
                name_node = node.child_by_field_name("name")
                if name_node is not None:
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
            stack.extend(reversed(node.children))

    _walk(tree.root_node)
    imports = sorted(dict.fromkeys(imports))
    symbols.sort(key=lambda item: (item["file"], item["line"], item["kind"], item["name"]))
    return imports, symbols


# #74-follow-up: `tg imports` foundational-tier extractor (mirrors repo_map.py's
# `_java_imports_with_lines` shape/role exactly). One row per `using_directive` STATEMENT with
# its 1-based line number -- reuses `_csharp_using_directive_target` exactly like
# `csharp_imports_and_symbols` above, so alias/static/global directives all record the namespace
# actually being imported, never the local alias, just line-tagged instead of deduped into a
# flat list.
#
# Deliberately NOT resolved to a target file: repo_map.py's `_resolve_raw_import_entry` "csharp"
# branch keeps every row unresolved, because C# namespace-to-file resolution needs a `.csproj`/
# assembly-reference map that does not exist yet (this module's `LanguageSpec` registers both
# `import_update_target` and `prime_repo_context` as `None` -- see repo_map.py), so a real path
# is not guessable without fabricating one.
def csharp_imports_with_lines(path: Path) -> list[dict[str, Any]]:
    if path.suffix != ".cs":
        return []

    parser = _csharp_parser()
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
        # Explicit-stack DFS -- see the identical comment on csharp_imports_and_symbols's `_walk`.
        stack = [root]
        while stack:
            node = stack.pop()
            if node.type == "using_directive":
                target_text = _csharp_using_directive_target(node, source_bytes)
                if target_text is not None:
                    entries.append({
                        "module": target_text,
                        "line": node.start_point[0] + 1,
                    })
            stack.extend(reversed(node.children))

    _walk(tree.root_node)
    return entries


def csharp_parser_symbol_sources(path: Path, symbol: str) -> list[dict[str, Any]]:
    """Full source text of every declaration matching *symbol* (mirrors the Rust/JS-TS/Go
    ``*_parser_symbol_sources`` shape for the ``tg source`` command)."""
    if path.suffix != ".cs":
        return []

    parser = _csharp_parser()
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

    def _walk(root: Any) -> None:
        # Explicit-stack DFS -- see the identical comment on csharp_imports_and_symbols's
        # `_walk` above for the rationale/precedent.
        stack = [root]
        while stack:
            node = stack.pop()
            node_type = node.type
            name_node: Any | None = None
            kind: str | None = None
            if node_type in _CSHARP_CLASS_NODE_TYPES:
                name_node = node.child_by_field_name("name")
                kind = "class"
            elif node_type in _CSHARP_FUNCTION_NODE_TYPES:
                name_node = node.child_by_field_name("name")
                kind = "function"
            if name_node is not None and kind is not None and _node_text(name_node) == symbol:
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
            stack.extend(reversed(node.children))

    _walk(tree.root_node)
    sources.sort(key=lambda item: (item["file"], item["start_line"], item["kind"], item["name"]))
    return sources


__all__ = [
    "csharp_imports_and_symbols",
    "csharp_parser_symbol_sources",
    "csharp_references_and_calls",
]
