"""PHP language extractor for tensor-grep's multi-language symbol graph (PATH A Stage 1).

Sibling of ``lang_go.py`` (see that module's docstring for the full "PATH A Stage 1" framing).
Plugs into the ``lang_registry`` seam Stage 0 built (see ``lang_registry.py`` + the
``lang_registry.register_language(...)`` calls near the bottom of ``repo_map.py``) -- PHP gets
its OWN ``LanguageSpec`` entry, registered from ``repo_map.py``.

SCOPE: this module extracts DEFS + IMPORTS (``php_imports_and_symbols``, the ``.py``/``.go`` "one
AST pass" shape) and ``php_parser_symbol_sources`` (the ``tg source`` companion, mirroring
``lang_go.go_parser_symbol_sources``), PLUS (as of Task 10C) in-file AST reference/call extraction
via ``php_references_and_calls`` (see the "TASK 10C" section below for the full design). The
remaining cross-file caller-graph fields (``file_imports_symbol_from_definition`` /
``import_update_target`` / ``prime_repo_context``) stay DEFERRED to a follow-up -- PHP's
``LanguageSpec`` registers all three of those callables as ``None``, same shape as Java's/C#'s own
Task 11A-equivalent gap. ``_language_coverage_gaps_for_universe`` in repo_map.py already treats
``import_update_target is None`` as an honest ``resolution_gaps`` entry (see the "audit #81 #4"
comment on that function), so `tg callers`/`tg blast-radius` stay honest about PHP's current lack
of reverse-import (PSR-4/``composer.json``) resolution instead of silently reading as a proven
zero.

Like ``lang_go.py``, this module imports NOTHING from ``repo_map.py`` (``repo_map`` ->
``lang_php``, never the reverse -- ``repo_map.py`` needs to import this module to register PHP's
``LanguageSpec`` and to call ``php_imports_and_symbols``/``php_parser_symbol_sources`` directly at
the couple of per-language dispatch sites that mirror how it calls the Go equivalents; a reverse
import would cycle). The handful of tiny helpers this module needs from ``repo_map.py`` are
duplicated here instead of imported, matching ``lang_go.py``'s own precedent.

FAIL-CLOSED CONTRACT (Stage 0 honesty floor, extended to PHP like Go): PHP has NO
regex-heuristic fallback. When the ``tree_sitter_php`` grammar package is not installed, every
extractor in this module returns empty ([]/([], [])) rather than degrading to a regex/text
heuristic (unlike JS/TS/Rust, which all fall back to regex extraction when their own tree-sitter
grammar is missing). ``LanguageSpec.provenance_when_missing="grammar-missing"`` (NOT
``"regex-heuristic"``) is what makes ``_language_coverage_gaps_for_universe`` in repo_map.py
treat a grammar-absent PHP file as a genuine ``resolution_gaps`` entry instead of silently
reporting zero matches as if the symbol just did not exist.

GRAMMAR VARIANT: ``tree_sitter_php`` exposes two language functions -- ``language_php_only()``
(pure PHP, no surrounding markup) and ``language_php()`` (the full grammar: PHP embedded in an
HTML document). This module uses ``language_php()`` so a template-style ``.php`` file (HTML +
``<?php ... ?>`` blocks, common in real web-app repos) parses the same as a bare ``<?php`` file
instead of erroring on the HTML it doesn't expect.

KNOWN EXTRACTION GAPS (documented, not silent): ``namespace_use_clause`` import extraction only
recognizes the two forms named in the design (``use Foo\\Bar;`` / ``use Foo\\Bar as Baz;`` -- a
clause with a ``qualified_name`` child). Group-use (``use App\\Shared\\{Foo, Bar as Baz};``,
where each inner clause only carries a bare ``name`` child, not a ``qualified_name``) and
``use function ...`` / ``use const ...`` imports are not extracted -- verified via direct grammar
probing (they do not carry a ``qualified_name`` child in the shape this walk expects), not
guessed. Both degrade safely to "this clause contributes no import" rather than emitting a wrong
or partial path.

TASK 10C: ``php_references_and_calls`` promotes PHP from the foundational (defs/imports-only)
tier to the parser-backed refs/callers tier, mirroring Task 10A's Java landing
(``lang_java.java_references_and_calls``) and Task 10B's C# landing
(``lang_csharp.csharp_references_and_calls``) -- IN-FILE AST reference/call extraction only, no
cross-file import resolution (``file_imports_symbol_from_definition``/``import_update_target``/
``prime_repo_context`` all stay ``None``, same deferred-scope shape as Go/Java/C#'s own gaps).
PHP's own grammar shape (verified against the installed ``tree_sitter_php`` grammar via a live AST
dump, not guessed -- see B5 of ``tensor-grep-add-language``) has FIVE distinct call/access node
types where Java has two and C# has effectively one:

- ``function_call_expression``: field ``function`` (a bare ``name`` for ``helper()``, or a
  ``qualified_name`` for a namespaced call -- the qualified form is an accepted, documented gap,
  matching Java's fully-qualified-constructor gap), field ``arguments``. A symbol match on
  ``function`` (when it is a bare ``name``) is a **call** (``ref_kind="call"``, both buckets).
- ``member_call_expression`` (``$foo->bar()``): fields ``object`` (a ``variable_name``, e.g.
  ``$foo``/``$this``), ``name`` (the method name, a bare ``name`` node), ``arguments``. A symbol
  match on ``name`` is a **call**.
- ``scoped_call_expression`` (``Foo::bar()`` / ``self::bar()`` / ``static::bar()`` /
  ``parent::bar()``): fields ``scope`` (EITHER a bare ``name`` for a literal class name, OR a
  ``relative_scope`` node whose text is ``self``/``static``/``parent``), ``name``, ``arguments``.
  A symbol match on ``name`` is a **call**.
- ``object_creation_expression`` (``new Foo()``): NO named field for the type -- verified via live
  AST dump that its type child is a bare, un-fielded ``name`` (``new Foo()``) or ``qualified_name``
  (``new \\App\\Foo()``) sitting directly between the ``new`` token and ``arguments``. A symbol
  match on that type node (or its ``qualified_name``'s trailing ``name`` segment) is a
  **constructor reference** (``ref_kind="constructor"``, both buckets) -- always demoted, same as
  Java/C# (there is no receiver to confirm a ``new`` expression against).
- ``member_access_expression`` (``$foo->baz``, non-call): fields ``object``, ``name``. Non-call
  member access. A symbol match on ``name`` is ``ref_kind="field"``.
- ``scoped_property_access_expression`` (``Foo::$staticProp``): fields ``scope``, ``name`` -- but
  unlike every other ``name`` field above, this one is a ``variable_name`` node (``$staticProp``,
  DOLLAR-PREFIXED), not a bare ``name``. Matched against the symbol via its inner ``name`` child
  (stripping the leading ``$``, exactly like a variable receiver). ``ref_kind="field"``.
- ``name`` / ``variable_name``: reused across value/qualifier/declaration-name roles, mirroring
  Java's ``identifier`` reuse. Every symbol-matching ``name`` node not already claimed by one of
  the special cases above is ``ref_kind="type"`` when its parent is a type-position node
  (``named_type``, ``base_clause``, ``class_interface_clause``), else ``ref_kind="value"`` --
  UNLESS it is itself the NAME of a declaration (class/interface/trait/enum/function/method/
  property/parameter/enum-case), which is excluded entirely (a symbol's own declaration site is
  not a reference to itself, the same rule every other language in this registry follows). A
  symbol-matching ``variable_name`` not already claimed is ``ref_kind="value"`` (matched via its
  bare, ``$``-stripped inner name).
- ``string_literal`` / ``comment``: never walked as any of the above node types, so a symbol name
  appearing inside a string literal or a comment is structurally excluded.

RESOLUTION CONFIDENCE / PROVENANCE (same two-band honesty shape as Java/C#, PHP-specific
mechanism, numbers, and an HONEST caveat: PHP is dynamically typed, so the confirmable population
is narrower than Java/C#'s -- a bare, untyped ``$foo->bar()`` (no type hint anywhere in this file)
can NEVER confirm, which is correct and expected, not a bug):

- ``_PHP_DEMOTED_CONFIDENCE`` (0.6) / ``_PHP_DEMOTED_PROVENANCE`` (``"php-name-heuristic"``): the
  DEFAULT band for every entry -- an AST-confirmed node (a real call/field-access/type/value site,
  never a string literal or comment) whose receiver's static type/scope is NOT resolvable from
  evidence in this same file.
- ``_PHP_CONFIRMED_CONFIDENCE`` (0.9) / ``_PHP_CONFIRMED_PROVENANCE``
  (``"php-infile-type-confirmation"``): fires in exactly THREE shapes, each independently
  verifiable from THIS file's AST alone, with no cross-file assumption:

  1. A ``member_call_expression``/``member_access_expression`` whose ``object`` is a
     ``variable_name`` (a local var/property/parameter, or ``$this``) with a PHP type-hint
     DIRECTLY readable in this file (a typed property declaration, a typed parameter, or --
     for ``$this`` -- the nearest enclosing class/interface/trait/enum), AND that exact type
     ALSO directly declares a member named *symbol* in its own body.
  2. A ``scoped_call_expression``/``scoped_property_access_expression`` whose ``scope`` is a
     literal class ``name`` (``Foo::bar()``) matching a class/interface/trait/enum declared in
     THIS file that directly declares a member named *symbol* -- PHP-specific and actually
     STRONGER than the instance case, since the class name is given literally with no variable
     type-tracking needed.
  3. A ``scoped_call_expression``/``scoped_property_access_expression`` whose ``scope`` is
     ``self``/``static`` (never ``parent`` -- the parent class is not guaranteed present in this
     file, so ``parent::`` always stays demoted, an honest gap) resolves to the nearest enclosing
     type, exactly like ``$this->``.

  Not 1.0: PHP allows inheritance, magic methods (``__call``/``__get``), and duck typing, so a
  declared-type match one step removed (inherited rather than declared directly, or a magic method
  intercepting the call) is not fully ruled out -- 0.9 reflects "real evidence, not proof of
  soundness", matching Java's/C#'s identical 0.9 rationale.
  A ``function_call_expression`` (``helper()``, no receiver at all) is a SPECIAL case not present
  in Java/C#'s member-call-only model: PHP has genuine top-level functions. Confirmed only when a
  ``function_definition`` with that exact name exists in this file -- there is no "enclosing type"
  fallback here (a bare function call has no implicit receiver the way an unqualified C# method
  call does), so an unresolved global function call (declared in ANOTHER file, or a builtin like
  ``strlen()``) stays honestly demoted.
  Only a simple, DIRECTLY-readable type hint counts for the declared-type lookup (a bare ``name``
  or the trailing segment of a ``qualified_name``, optionally wrapped in ``?`` for nullable) --
  union types (``Foo|Bar $x``, PHP 8+), intersection types, and untyped properties/parameters are
  an accepted, documented gap (no confirmation attempted), mirroring Java's/C#'s identical
  "simple declared type only" requirement.

F7 TASK 11 WAVE 2b: cross-file caller resolution via ``use``/namespace evidence, mirroring Task
11A's Java landing (``lang_java.java_file_imports_symbol_from_definition`` /
``_java_type_resolves_into_definition_dirs``) with PHP's own mechanism. PHP has NO
compiler-enforced file/namespace mapping (unlike Java's javac-checked package/source-root, or C#'s
assembly-checked namespace) -- PSR-4 autoload mapping lives in ``composer.json``, which this
module deliberately does NOT read (parsing an arbitrary, possibly-absent, possibly-custom autoload
map is out of scope; a missing or non-PSR-4-standard ``composer.json`` would silently degrade to
guessing). Two independent mechanisms, both regex-based (no tree-sitter parser required -- mirrors
``lang_java.py``'s own regex-only package/import scan) and both fail-closed:

- ``php_file_imports_symbol_from_definition`` (the ``LanguageSpec`` field, used by
  ``_preferred_definition_files`` / ``_should_scan_for_symbol_callers`` scoring): reads
  *definition_path*'s own source directly, finds the class/interface/trait/enum whose NAME equals
  the definition file's STEM (PSR-4's autoload contract requires this 1:1 filename/classname
  match, or Composer's autoloader could never locate the class -- the same "public top-level type
  == filename" convention ``_java_definition_fqn`` relies on, PHP's own equivalent guarantee),
  computes that type's FQN from the definition file's own ``namespace`` declaration (or the bare
  name when the definition has no namespace -- the PHP global namespace), then checks whether
  *file_path*'s source imports that exact FQN via a simple ``use`` statement (alias-insensitive --
  an alias renames the LOCAL binding, not the imported identity) OR shares the same declared
  namespace (files in the same namespace see each other's classes without a ``use`` statement,
  true PHP semantics) OR both files sit in the PHP global namespace (also directly visible,
  no import needed). Returns ``False`` (demote, never guess) when the definition file's stem does
  not match any declared type name in it (composer.json could remap this; a mismatch is an
  honest "cannot confirm", not a hard error) or the FQN is not visible in the importer.
- The CONFIRMED band inside ``php_references_and_calls`` (fired when a *definition_dirs* set is
  supplied by ``repo_map``, matching Go's/Java's ``definition_dirs`` seam exactly): a receiver
  variable's declared type-hint, OR a ``Foo::``/``self::``/``static::`` scope's literal class name,
  resolves -- via *this file's* ``use``/namespace evidence -- to an FQN whose NAMESPACE portion is
  a directory-path SUFFIX of one of *definition_dirs* (the real parent directory of the selected
  definition file(s), already symbol-scoped by ``repo_map`` upstream). This directory-suffix check
  mirrors ``_java_fqn_package_dir_matches`` exactly, and inherits the SAME honest limitation
  Java's package check has, made WORSE by PHP's composer-root-prefix convention: a project whose
  ``composer.json`` maps ``"App\\": "src/"`` (stripping the ``App`` namespace segment from the
  physical path -- extremely common in Laravel/Symfony-style PSR-4 layouts) will NOT suffix-match
  even for a genuinely-correct import, because the namespace has one MORE segment than the
  directory has. This is a deliberate DEMOTE-not-guess trade-off (see the module docstring's
  opening paragraph): without reading ``composer.json``'s actual autoload map, there is no way to
  distinguish a root-prefix-stripped PSR-4 layout from a namespace that genuinely does not
  correspond to any real directory, so this module never fabricates that mapping -- it only
  confirms when the namespace-to-directory correspondence is directly OBSERVABLE, and leaves every
  other real cross-file call honestly in the demoted band rather than guessing wrong.
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
# ``lang_go.py``'s own copies of the same three -- if any of them ever change there.
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
def _php_parser() -> Any | None:
    try:
        import tree_sitter
        import tree_sitter_php
    except ImportError:
        return None

    # See the module docstring's "GRAMMAR VARIANT" note: language_php() (not
    # language_php_only()) so a template-style .php file (HTML + <?php ... ?>) parses too.
    language = tree_sitter.Language(tree_sitter_php.language_php())
    return tree_sitter.Parser(language)


# ---------------------------------------------------------------------------
# Defs + imports: one tree-sitter pass per file.
# ---------------------------------------------------------------------------

# node types a PHP def can appear as -- informational/documentation only (mirrors
# lang_go._GO_DEF_NODE_KINDS' role), matching LanguageSpec.def_node_kinds' "Stage 0:
# informational only" contract.
_PHP_DEF_NODE_KINDS = (
    "class_declaration",
    "interface_declaration",
    "trait_declaration",
    "enum_declaration",
    "function_definition",
    "method_declaration",
)

_PHP_CLASS_LIKE_KINDS = frozenset({
    "class_declaration",
    "interface_declaration",
    "trait_declaration",
    "enum_declaration",
})
_PHP_FUNCTION_LIKE_KINDS = frozenset({"function_definition", "method_declaration"})


def php_imports_and_symbols(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    """Extract ``use`` import paths + class/interface/trait/enum/function/method definitions
    from a PHP source file, one AST pass (mirrors ``lang_go.go_imports_and_symbols``'s shape).

    Defs covered: ``class_declaration``/``interface_declaration``/``trait_declaration``/
    ``enum_declaration`` (kind "class") and ``function_definition``/``method_declaration`` (kind
    "function"). Imports come from every ``namespace_use_clause``'s ``qualified_name`` child's
    raw text -- PHP's namespace separator is a BACKSLASH (``\\``), not a dot, so the recorded
    string is e.g. ``"App\\Contracts\\Named"``, preserved as-written (never rewritten to
    dot-form) so it feeds the reverse-import alias graph the same way Python's dotted
    ``node.module`` does today. An ``as`` alias is not recorded (matching how
    ``_python_imports_and_symbols`` records the source module path, not a locally bound name).
    See the module docstring's "KNOWN EXTRACTION GAPS" note for the two import forms this
    deliberately does not cover.
    """
    if path.suffix != ".php":
        return [], []

    parser = _php_parser()
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
        # so the leftmost child is popped (visited) first, preserving pre-order traversal.
        stack = [root]
        while stack:
            node = stack.pop()
            node_type = node.type
            if node_type == "namespace_use_clause":
                qualified_name_node = next(
                    (child for child in node.children if child.type == "qualified_name"),
                    None,
                )
                if qualified_name_node is not None:
                    imports.append(_node_text(qualified_name_node))
            elif node_type in _PHP_CLASS_LIKE_KINDS:
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
            elif node_type in _PHP_FUNCTION_LIKE_KINDS:
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
# `_java_imports_with_lines` shape/role exactly). One row per `namespace_use_clause` STATEMENT
# with its 1-based line number -- same extraction source/gaps as `php_imports_and_symbols` above
# (only a clause with a `qualified_name` child is recorded; see the module docstring's "KNOWN
# EXTRACTION GAPS" note for the group-use / `use function` / `use const` forms this does not
# cover), just line-tagged instead of deduped into a flat list.
#
# Deliberately NOT resolved to a target file: repo_map.py's `_resolve_raw_import_entry` "php"
# branch keeps every row unresolved, because PHP namespace-to-file resolution needs a PSR-4/
# composer.json autoload-map reader that does not exist yet (this module's `LanguageSpec`
# registers both `import_update_target` and `prime_repo_context` as `None` -- see repo_map.py),
# so a real path is not guessable without fabricating one.
def php_imports_with_lines(path: Path) -> list[dict[str, Any]]:
    if path.suffix != ".php":
        return []

    parser = _php_parser()
    if parser is None:
        return []

    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    source_bytes = source.encode("utf-8")
    tree = parser.parse(source_bytes)

    def _node_text(node: Any) -> str:
        return _tree_sitter_node_text(source_bytes, node)

    entries: list[dict[str, Any]] = []

    def _walk(root: Any) -> None:
        # Explicit-stack DFS -- see the identical comment on php_imports_and_symbols's `_walk`.
        stack = [root]
        while stack:
            node = stack.pop()
            if node.type == "namespace_use_clause":
                qualified_name_node = next(
                    (child for child in node.children if child.type == "qualified_name"),
                    None,
                )
                if qualified_name_node is not None:
                    entries.append({
                        "module": _node_text(qualified_name_node),
                        "line": node.start_point[0] + 1,
                    })
            stack.extend(reversed(node.children))

    _walk(tree.root_node)
    return entries


def php_parser_symbol_sources(path: Path, symbol: str) -> list[dict[str, Any]]:
    """Full source text of every class/interface/trait/enum/function/method matching *symbol*
    (mirrors the Go/Rust/JS-TS ``*_parser_symbol_sources`` shape for the ``tg source``
    command)."""
    if path.suffix != ".php":
        return []

    parser = _php_parser()
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
        # Explicit-stack DFS -- see the identical comment on php_imports_and_symbols's `_walk`.
        stack = [root]
        while stack:
            node = stack.pop()
            node_type = node.type
            name_node: Any | None = None
            kind: str | None = None
            if node_type in _PHP_CLASS_LIKE_KINDS:
                name_node = node.child_by_field_name("name")
                kind = "class"
            elif node_type in _PHP_FUNCTION_LIKE_KINDS:
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


# ---------------------------------------------------------------------------
# Task 10C: references + calls (in-file AST extraction, no cross-file resolution).
# See the module docstring's "TASK 10C" / "RESOLUTION CONFIDENCE / PROVENANCE" sections for the
# full derivation of the two honesty bands and PHP's five distinct call/access node shapes.
# ---------------------------------------------------------------------------

_PHP_DEMOTED_CONFIDENCE = 0.6
_PHP_DEMOTED_PROVENANCE = "php-name-heuristic"
_PHP_CONFIRMED_CONFIDENCE = 0.9
_PHP_CONFIRMED_PROVENANCE = "php-infile-type-confirmation"
# F7 Task 11 wave 2b: same confidence, different provenance -- fires when the receiver/scope's
# resolved FQN's namespace directory-suffix-matches a supplied `definition_dirs` entry (see the
# module docstring's "F7 TASK 11 WAVE 2b" section).
_PHP_CROSS_FILE_CONFIRMED_PROVENANCE = "php-use-namespace-confirmation"

# Type-declaration node kinds whose own body (declaration_list) can directly declare a member --
# the CONFIRMED band's "owner type" universe. Same set as _PHP_CLASS_LIKE_KINDS above (kept as a
# separate name here to mirror lang_java.py's/lang_csharp.py's own _*_TYPE_BODY_DECLARATION_TYPES
# naming, even though the membership is identical to _PHP_CLASS_LIKE_KINDS in this module).
_PHP_TYPE_BODY_DECLARATION_TYPES = _PHP_CLASS_LIKE_KINDS

# Node types whose "name" field (or, for property_element/simple_parameter, whose "name" field
# holding a variable_name) defines *this* declaration rather than referencing an existing one
# elsewhere -- excluded from the reference/call walk (mirrors _JAVA_NAME_DEFINING_PARENT_TYPES /
# _CSHARP_NAME_DEFINING_PARENT_TYPES, adapted to PHP's own grammar).
_PHP_NAME_DEFINING_PARENT_TYPES = {
    "class_declaration",
    "interface_declaration",
    "trait_declaration",
    "enum_declaration",
    "function_definition",
    "method_declaration",
    "property_element",
    "simple_parameter",
    "variadic_parameter",
    "enum_case",
}

# Parent node types whose "name"-typed child is a TYPE POSITION rather than a value use --
# mirrors lang_java's distinct type_identifier node type / lang_csharp's is_type_position check,
# adapted to PHP's grammar which reuses the plain "name" node for both roles.
_PHP_TYPE_POSITION_PARENT_TYPES = {"named_type", "base_clause", "class_interface_clause"}

# scoped_call_expression / scoped_property_access_expression's "scope" field text values that
# resolve to the nearest ENCLOSING type declaration, exactly like a bare `$this->` receiver.
# "parent" is deliberately excluded: the parent class is not guaranteed to be declared in this
# same file, so `parent::` always stays in the demoted band -- an honest, documented gap (see the
# module docstring's "TASK 10C" section, RESOLUTION CONFIDENCE bullet 3).
_PHP_SELF_LIKE_RELATIVE_SCOPES = frozenset({"self", "static"})


def _php_variable_bare_text(node: Any, source_bytes: bytes) -> str:
    """Return a ``variable_name`` node's bare, ``$``-stripped inner name text (``$foo`` -> "foo",
    ``$this`` -> "this") -- prefers the inner ``name`` child (the grammar's own decomposition);
    falls back to stripping a leading ``$`` from the raw text for any shape that lacks one (e.g. a
    dynamic ``$$foo`` variable-variable, an accepted, documented gap this fallback never crashes
    on -- it just returns the raw, un-stripped text, which will simply never match a clean
    symbol name).
    """
    for child in node.children:
        if child.type == "name":
            return _tree_sitter_node_text(source_bytes, child)
    text = _tree_sitter_node_text(source_bytes, node)
    return text[1:] if text.startswith("$") else text


def _php_qualified_or_name_text(node: Any, source_bytes: bytes) -> str | None:
    """Return the matchable text for a bare ``name`` node, or the TRAILING segment of a
    ``qualified_name`` (``\\App\\Foo`` -> "Foo", never the leading namespace segments) -- the same
    "take the final segment" rule ``_csharp_type_base_identifier`` uses for a dotted C# type.
    Anything else returns ``None`` (an accepted, documented gap, never a wrong guess).
    """
    if node.type == "name":
        return _tree_sitter_node_text(source_bytes, node)
    if node.type == "qualified_name":
        last_name: Any | None = None
        for child in node.children:
            if child.type == "name":
                last_name = child
        if last_name is None:
            return None
        return _tree_sitter_node_text(source_bytes, last_name)
    return None


def _php_type_base_name(type_node: Any | None, source_bytes: bytes) -> str | None:
    """Return the base type name for a PHP type-hint node, or ``None`` for a shape this module
    deliberately does not resolve (a union/intersection type, an untyped hint). Handles:
    ``named_type`` (unwraps to its single ``name``/``qualified_name`` child), ``optional_type``
    (``?Foo`` -- unwraps past the ``?`` token to the inner ``named_type``), a bare ``name``/
    ``qualified_name`` directly. Mirrors ``_csharp_type_base_identifier``'s recursive-unwrap
    shape, adapted to PHP's own node kinds (verified via live AST dump, not guessed -- see the
    module docstring's "TASK 10C" section).
    """
    if type_node is None:
        return None
    if type_node.type == "optional_type":
        for child in type_node.children:
            if child.type != "?":
                return _php_type_base_name(child, source_bytes)
        return None
    if type_node.type == "named_type":
        for child in type_node.children:
            return _php_type_base_name(child, source_bytes)
        return None
    return _php_qualified_or_name_text(type_node, source_bytes)


def _php_object_creation_type_node(node: Any) -> Any | None:
    """The base type node (``name`` or ``qualified_name``) for an ``object_creation_expression``
    -- verified via live AST dump that PHP's grammar gives this NO named field (unlike Java's
    ``type`` field or C#'s ``type`` field): the type sits as a plain, un-fielded child between the
    ``new`` token and ``arguments``. Returns ``None`` for any other shape (e.g. a dynamic
    ``new $className()``), an accepted, documented gap.
    """
    for child in node.children:
        if child.type in ("name", "qualified_name"):
            return child
    return None


def _php_resolution_context(
    root: Any, source_bytes: bytes, symbol: str
) -> tuple[dict[str, set[str]], set[str], set[str], bool]:
    """Single upfront walk building the facts the in-file CONFIRMED band (see the module
    docstring's "TASK 10C" / "RESOLUTION CONFIDENCE" sections) needs -- all derived from THIS
    file's AST alone, mirroring ``_java_resolution_context``/``_csharp_resolution_context``'s
    shape with PHP's own node kinds:

    - ``declared_types``: every bare (``$``-stripped) variable NAME this file declares with a
      readable type hint (a typed property, a typed parameter), mapped to the SET of base-type
      name texts it was ever declared with in this file.
    - ``method_owner_types``: the name of every class/interface/trait/enum declared in this file
      whose OWN body (direct child ``method_declaration``, not inherited, not nested deeper)
      declares a method named *symbol*.
    - ``field_owner_types``: same, for a ``property_declaration``'s ``property_element`` named
      *symbol*.
    - ``function_declared``: ``True`` iff a top-level ``function_definition`` named *symbol*
      exists anywhere in this file (feeds the PHP-specific ``function_call_expression``
      confirmation path -- see the module docstring, no Java/C# equivalent since neither language
      has a bare top-level function).
    """
    declared_types: dict[str, set[str]] = {}
    method_owner_types: set[str] = set()
    field_owner_types: set[str] = set()
    function_declared = False

    def node_text(node: Any) -> str:
        return _tree_sitter_node_text(source_bytes, node)

    def owner_type_name(member_parent: Any | None) -> str | None:
        # member_parent is the declaration_list a member sits directly inside; its own parent is
        # the type declaration (class/interface/trait/enum) that owns it.
        if member_parent is None or member_parent.type != "declaration_list":
            return None
        type_decl = member_parent.parent
        if type_decl is None or type_decl.type not in _PHP_TYPE_BODY_DECLARATION_TYPES:
            return None
        type_name_field = type_decl.child_by_field_name("name")
        return node_text(type_name_field) if type_name_field is not None else None

    stack = [root]
    while stack:
        node = stack.pop()
        node_type = node.type

        if node_type == "property_declaration":
            type_field = node.child_by_field_name("type")
            type_name = _php_type_base_name(type_field, source_bytes)
            owner = owner_type_name(node.parent)
            for child in node.children:
                if child.type != "property_element":
                    continue
                name_field = child.child_by_field_name("name")
                if name_field is None:
                    continue
                bare_name = _php_variable_bare_text(name_field, source_bytes)
                if type_name is not None:
                    declared_types.setdefault(bare_name, set()).add(type_name)
                if owner is not None and bare_name == symbol:
                    field_owner_types.add(owner)
        elif node_type in {"simple_parameter", "variadic_parameter"}:
            type_field = node.child_by_field_name("type")
            name_field = node.child_by_field_name("name")
            type_name = _php_type_base_name(type_field, source_bytes)
            if type_name is not None and name_field is not None:
                bare_name = _php_variable_bare_text(name_field, source_bytes)
                declared_types.setdefault(bare_name, set()).add(type_name)
        elif node_type == "method_declaration":
            name_field = node.child_by_field_name("name")
            if name_field is not None and node_text(name_field) == symbol:
                owner = owner_type_name(node.parent)
                if owner is not None:
                    method_owner_types.add(owner)
        elif node_type == "function_definition":
            name_field = node.child_by_field_name("name")
            if name_field is not None and node_text(name_field) == symbol:
                function_declared = True

        stack.extend(node.children)

    return declared_types, method_owner_types, field_owner_types, function_declared


def _php_enclosing_type_name(node: Any, source_bytes: bytes) -> str | None:
    """Walk PARENTS (not the file-wide stack above) to find the nearest enclosing
    class/interface/trait/enum declaration's name -- used for a bare ``$this`` receiver and for
    ``self``/``static`` scope resolution, mirroring ``_java_enclosing_type_name``/
    ``_csharp_enclosing_type_name``.
    """
    current = node.parent
    while current is not None:
        if current.type in _PHP_TYPE_BODY_DECLARATION_TYPES:
            name_field = current.child_by_field_name("name")
            if name_field is not None:
                return _tree_sitter_node_text(source_bytes, name_field)
            return None
        current = current.parent
    return None


# ---------------------------------------------------------------------------
# F7 Task 11 wave 2b: cross-file caller resolution via `use`/namespace evidence -- regex-based (no
# tree-sitter parser required), mirroring lang_java.py's identical regex-only package/import scan.
# See the module docstring's "F7 TASK 11 WAVE 2b" section for the full derivation.
# ---------------------------------------------------------------------------

# `^\s*namespace X;` -- the simple (non-block) namespace-declaration form. `namespace Foo { ... }`
# block form is not matched -- an accepted, documented gap (rare in modern PSR-4 code, which
# always uses the simple form); a file using the block form is honestly treated as having no
# readable namespace declaration (demote, never guess).
_PHP_NAMESPACE_RE = re.compile(r"^\s*namespace\s+([A-Za-z_][\w\\]*)\s*;", re.MULTILINE)

# `^\s*use X[ as Y];` -- a simple class-import `use` statement, capturing the imported FQN and an
# optional alias. Deliberately excludes `use function ...`/`use const ...` (function/const
# imports, not class imports -- see the negative lookahead) via the SAME exclusion
# `_java_import_specs`'s `is_static` branch performs for Java's static imports. Group-use
# (`use App\Group\{One, Two};`) and a closure's `use ($capture)` capture clause (unrelated PHP
# meaning) both naturally fail this pattern (no bare FQN immediately followed by `;`/`as`), so
# neither is ever misread as a class import -- verified, not assumed: a closure's `use (...)`
# starts with `(`, outside this pattern's `[A-Za-z_][\w\\]*` character class, and a group-use's
# FQN prefix is followed by `{`, not `;`/`as`/end-of-match.
_PHP_USE_RE = re.compile(
    r"^\s*use\s+(?!function\s|const\s)([A-Za-z_][\w\\]*)(?:\s+as\s+([A-Za-z_]\w*))?\s*;",
    re.MULTILINE,
)


def _php_namespace_declaration(source: str) -> str | None:
    match = _PHP_NAMESPACE_RE.search(source)
    return match.group(1) if match else None


def _php_use_specs(source: str) -> list[tuple[str, str | None]]:
    """Return ``(imported_fqn, alias_or_none)`` pairs from every simple `use` statement in
    *source* -- see ``_PHP_USE_RE``'s docstring comment for exactly which forms this covers."""
    return [(match.group(1), match.group(2)) for match in _PHP_USE_RE.finditer(source)]


def _php_definition_fqn(definition_path: Path, definition_source: str) -> str | None:
    """FQN of the class/interface/trait/enum in *definition_path* whose NAME matches the file's
    own stem, or ``None`` when no such declaration is found (fail closed, never guess -- see the
    module docstring's "F7 TASK 11 WAVE 2b" section for the PSR-4 filename/classname rationale).
    """
    stem = definition_path.stem
    if not _is_clean_symbol_name(stem):
        return None
    if not re.search(
        r"\b(?:class|interface|trait|enum)\s+" + re.escape(stem) + r"\b", definition_source
    ):
        return None
    namespace = _php_namespace_declaration(definition_source)
    return f"{namespace}\\{stem}" if namespace else stem


def php_file_imports_symbol_from_definition(
    file_path: Path,
    source: str,
    symbol: str,
    definition_path: str,
    repo_root: Path | str | None = None,
) -> bool:
    """True iff *file_path* can see *symbol*'s definition via PHP namespace/`use` evidence.

    Requires the definition file's declared type name to match its own filename stem (PSR-4's
    autoload contract). *symbol* is unused -- like Java, PHP visibility here is type/namespace
    scoped, not member-name scoped (member-name-specific confirmation lives in
    ``php_references_and_calls``'s in-file CONFIRMED band). *repo_root* is accepted for
    ``LanguageSpec`` signature parity with Go/Java; resolution is source-text local and does not
    scan the repo or read `composer.json`.
    """
    del repo_root  # signature parity; unused by design
    del symbol  # PHP visibility is type/namespace scoped; member name is not part of import proof
    try:
        definition = Path(definition_path).expanduser().resolve()
    except OSError:
        return False
    try:
        definition_source = definition.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False

    definition_fqn = _php_definition_fqn(definition, definition_source)
    if definition_fqn is None:
        return False
    definition_namespace = definition_fqn.rsplit("\\", 1)[0] if "\\" in definition_fqn else None

    importer_namespace = _php_namespace_declaration(source)
    if importer_namespace is not None and importer_namespace == definition_namespace:
        return True
    if importer_namespace is None and definition_namespace is None:
        # Both files sit in the PHP global namespace -- directly visible to each other, no `use`
        # statement required (true PHP semantics, not a guess).
        return True

    for imported_fqn, _alias in _php_use_specs(source):
        if imported_fqn == definition_fqn:
            return True
    return False


def _php_fqns_for_qualifier(source: str, qualifier: str) -> set[str]:
    """FQNs *qualifier* (a bare class name or alias as it appears in code, e.g. ``Foo`` in
    ``Foo::bar()`` or ``QAlias`` in ``QAlias::bar()``) could denote in *source*, via: (1) an
    aliased ``use X as qualifier;`` -- exact alias match; (2) an unaliased ``use X;`` whose FQN's
    trailing segment equals *qualifier*; (3) *source*'s own declared namespace (a bare same-
    namespace reference needs no ``use``), or the PHP global namespace when *source* declares
    none. Mirrors ``_java_type_fqns_visible_in_file``'s shape with PHP's own `use`/alias mechanism.
    """
    if not qualifier or not _is_clean_symbol_name(qualifier):
        return set()
    fqns: set[str] = set()
    for imported_fqn, alias in _php_use_specs(source):
        if alias is not None:
            if alias == qualifier:
                fqns.add(imported_fqn)
            continue
        if imported_fqn.rsplit("\\", 1)[-1] == qualifier:
            fqns.add(imported_fqn)
    namespace = _php_namespace_declaration(source)
    fqns.add(f"{namespace}\\{qualifier}" if namespace else qualifier)
    return fqns


def _php_fqn_namespace_dir_matches(fqn: str, definition_dir: Path) -> bool:
    if "\\" not in fqn:
        return False
    namespace_parts = tuple(fqn.rsplit("\\", 1)[0].split("\\"))
    dir_parts = definition_dir.parts
    return (
        len(dir_parts) >= len(namespace_parts)
        and dir_parts[-len(namespace_parts) :] == namespace_parts
    )


def _php_type_resolves_into_definition_dirs(
    qualifier: str,
    source: str,
    definition_dirs: frozenset[str],
) -> bool:
    if not definition_dirs:
        return False
    fqns = _php_fqns_for_qualifier(source, qualifier)
    if not fqns:
        return False
    for fqn in fqns:
        if fqn.rsplit("\\", 1)[-1] != qualifier:
            continue
        for directory in definition_dirs:
            try:
                resolved_dir = Path(directory).expanduser().resolve()
            except OSError:
                continue
            if _php_fqn_namespace_dir_matches(fqn, resolved_dir):
                return True
    return False


def php_references_and_calls(
    path: Path,
    symbol: str,
    repo_root: Path | str | None = None,
    *,
    definition_dirs: frozenset[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """In-file AST reference/call rows for *symbol* in *path* -- see the module docstring's
    "TASK 10C" section for the full AST-shape mapping. Owns its own parser factory
    (``_php_parser()``, defined above), matching ``lang_csharp.py``'s shape rather than
    ``lang_java.py``'s externally-built-parser shape -- PHP already had its own grammar-probing
    factory before Task 10C (needed by ``php_imports_and_symbols``), so a second factory here
    would create two sources of truth for "is the PHP grammar installed"; this function reuses
    the SAME ``_php_parser()`` every other function in this module already calls.

    As of F7 Task 11 wave 2b: when *definition_dirs* is supplied (``repo_map`` always supplies it
    from the selected definition's directory), a receiver/scope whose resolved FQN's namespace
    directory-suffix-matches one of those directories earns the cross-file CONFIRMED band -- see
    the module docstring's "F7 TASK 11 WAVE 2b" section. *repo_root* is accepted for registry-
    adapter signature parity (Go F25 shape); unused by the PHP resolver, which is source-text and
    ``definition_dirs``-local.
    """
    del repo_root  # signature parity with the uniform registry adapter; unused by this resolver
    if path.suffix != ".php":
        return [], []

    parser = _php_parser()
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
        if parent is None or parent.type not in _PHP_NAME_DEFINING_PARENT_TYPES:
            return False
        name_field = parent.child_by_field_name("name")
        return name_field is not None and name_field == node

    declared_types, method_owner_types, field_owner_types, function_declared = (
        _php_resolution_context(tree.root_node, source_bytes, symbol)
    )

    def _receiver_confirmation(object_node: Any | None, owner_types: set[str]) -> bool:
        """True only when *object_node* (a ``variable_name``) has a type-hint readable from THIS
        file (a local var/property/parameter declaration, or -- for a bare ``$this`` -- the
        enclosing type) that is ALSO one of *owner_types* (the types this file itself saw declare
        a member named *symbol*). See the module docstring's RESOLUTION CONFIDENCE section."""
        if object_node is None or not owner_types or object_node.type != "variable_name":
            return False
        bare_name = _php_variable_bare_text(object_node, source_bytes)
        if bare_name == "this":
            enclosing = _php_enclosing_type_name(object_node, source_bytes)
            return enclosing is not None and enclosing in owner_types
        receiver_types = declared_types.get(bare_name, set())
        return not receiver_types.isdisjoint(owner_types)

    def _scope_confirmation(scope_node: Any | None, owner_types: set[str]) -> bool:
        """True only when *scope_node* (a ``scoped_call_expression``/
        ``scoped_property_access_expression``'s ``scope`` field) resolves to a type this file saw
        declare a member named *symbol*. A literal class ``name`` (``Foo::``) is checked directly
        -- no variable type-tracking needed, the strongest PHP-specific confirmation shape. A
        ``relative_scope`` of ``self``/``static`` resolves via the enclosing type, exactly like
        ``$this->``; ``parent`` never confirms (see ``_PHP_SELF_LIKE_RELATIVE_SCOPES``'s
        docstring comment above)."""
        if scope_node is None or not owner_types:
            return False
        if scope_node.type in ("name", "qualified_name"):
            class_name = _php_qualified_or_name_text(scope_node, source_bytes)
            return class_name is not None and class_name in owner_types
        if scope_node.type == "relative_scope":
            scope_text = _node_text(scope_node)
            if scope_text in _PHP_SELF_LIKE_RELATIVE_SCOPES:
                enclosing = _php_enclosing_type_name(scope_node, source_bytes)
                return enclosing is not None and enclosing in owner_types
        return False

    def _cross_file_receiver_confirmation(object_node: Any | None) -> bool:
        """F7 Task 11 wave 2b: True when *object_node* (a ``variable_name``) has a type-hint
        readable from THIS file whose resolved FQN's namespace directory-suffix-matches a
        ``definition_dirs`` entry -- see ``_php_type_resolves_into_definition_dirs``. A bare
        ``$this`` never resolves this way (its type IS the enclosing file's own type, already
        covered by the in-file band above, never a cross-file concern)."""
        if object_node is None or not definition_dirs or object_node.type != "variable_name":
            return False
        bare_name = _php_variable_bare_text(object_node, source_bytes)
        if bare_name == "this":
            return False
        for type_name in declared_types.get(bare_name, set()):
            if _php_type_resolves_into_definition_dirs(type_name, source, definition_dirs):
                return True
        return False

    def _cross_file_scope_confirmation(scope_node: Any | None) -> bool:
        """F7 Task 11 wave 2b: True when *scope_node* is a literal class ``name``/``qualified_name``
        (``Foo::``) whose resolved FQN's namespace directory-suffix-matches a ``definition_dirs``
        entry. ``self``/``static``/``parent`` never resolve this way (they name the enclosing
        file's own type, never a cross-file concern)."""
        if (
            scope_node is None
            or not definition_dirs
            or scope_node.type
            not in (
                "name",
                "qualified_name",
            )
        ):
            return False
        class_name = _php_qualified_or_name_text(scope_node, source_bytes)
        if class_name is None:
            return False
        return _php_type_resolves_into_definition_dirs(class_name, source, definition_dirs)

    def _member_confirmation(object_node: Any | None, owner_types: set[str]) -> tuple[bool, bool]:
        if _receiver_confirmation(object_node, owner_types):
            return True, False
        if _cross_file_receiver_confirmation(object_node):
            return True, True
        return False, False

    def _scoped_confirmation(scope_node: Any | None, owner_types: set[str]) -> tuple[bool, bool]:
        if _scope_confirmation(scope_node, owner_types):
            return True, False
        if _cross_file_scope_confirmation(scope_node):
            return True, True
        return False, False

    def _emit(
        bucket: list[dict[str, Any]],
        node: Any,
        *,
        kind: str,
        ref_kind: str,
        confirmed: bool,
        cross_file: bool = False,
    ) -> None:
        if confirmed:
            confidence = _PHP_CONFIRMED_CONFIDENCE
            provenance = (
                _PHP_CROSS_FILE_CONFIRMED_PROVENANCE if cross_file else _PHP_CONFIRMED_PROVENANCE
            )
        else:
            confidence = _PHP_DEMOTED_CONFIDENCE
            provenance = _PHP_DEMOTED_PROVENANCE
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

    # Nodes already claimed by a special-case branch below are tracked here so the generic
    # name/variable_name walk never double-emits them. Keyed on (start_byte, end_byte), NOT
    # Python `id()` -- see lang_java.py's/lang_csharp.py's identical comment for why (tree_sitter
    # mints a fresh wrapper object on every `.children`/`.child_by_field_name` access to the same
    # underlying node).
    claimed_node_ids: set[tuple[int, int]] = set()

    def _walk(root: Any) -> None:
        # Explicit-stack DFS (not recursion) -- matches every other language extractor in this
        # registry; avoids a RecursionError on a pathologically deep real-world AST.
        stack = [root]
        while stack:
            node = stack.pop()
            node_type = node.type

            if node_type == "function_call_expression":
                function_field = node.child_by_field_name("function")
                if (
                    function_field is not None
                    and function_field.type == "name"
                    and _node_text(function_field) == symbol
                ):
                    claimed_node_ids.add((function_field.start_byte, function_field.end_byte))
                    _emit(
                        references,
                        function_field,
                        kind="reference",
                        ref_kind="call",
                        confirmed=function_declared,
                    )
                    _emit(
                        calls,
                        function_field,
                        kind="call",
                        ref_kind="call",
                        confirmed=function_declared,
                    )
            elif node_type == "member_call_expression":
                name_field = node.child_by_field_name("name")
                if name_field is not None and _node_text(name_field) == symbol:
                    claimed_node_ids.add((name_field.start_byte, name_field.end_byte))
                    confirmed, cross_file = _member_confirmation(
                        node.child_by_field_name("object"), method_owner_types
                    )
                    _emit(
                        references,
                        name_field,
                        kind="reference",
                        ref_kind="call",
                        confirmed=confirmed,
                        cross_file=cross_file,
                    )
                    _emit(
                        calls,
                        name_field,
                        kind="call",
                        ref_kind="call",
                        confirmed=confirmed,
                        cross_file=cross_file,
                    )
            elif node_type == "scoped_call_expression":
                name_field = node.child_by_field_name("name")
                if name_field is not None and _node_text(name_field) == symbol:
                    claimed_node_ids.add((name_field.start_byte, name_field.end_byte))
                    confirmed, cross_file = _scoped_confirmation(
                        node.child_by_field_name("scope"), method_owner_types
                    )
                    _emit(
                        references,
                        name_field,
                        kind="reference",
                        ref_kind="call",
                        confirmed=confirmed,
                        cross_file=cross_file,
                    )
                    _emit(
                        calls,
                        name_field,
                        kind="call",
                        ref_kind="call",
                        confirmed=confirmed,
                        cross_file=cross_file,
                    )
            elif node_type == "object_creation_expression":
                type_node = _php_object_creation_type_node(node)
                if type_node is not None:
                    matched_node = type_node
                    if type_node.type == "qualified_name":
                        matched_node = next(
                            (c for c in type_node.children if c.type == "name"),
                            type_node,
                        )
                    if _node_text(matched_node) == symbol:
                        claimed_node_ids.add((matched_node.start_byte, matched_node.end_byte))
                        _emit(
                            references,
                            matched_node,
                            kind="reference",
                            ref_kind="constructor",
                            confirmed=False,
                        )
                        _emit(
                            calls,
                            matched_node,
                            kind="call",
                            ref_kind="constructor",
                            confirmed=False,
                        )
            elif node_type == "member_access_expression":
                name_field = node.child_by_field_name("name")
                if name_field is not None and _node_text(name_field) == symbol:
                    claimed_node_ids.add((name_field.start_byte, name_field.end_byte))
                    confirmed, cross_file = _member_confirmation(
                        node.child_by_field_name("object"), field_owner_types
                    )
                    _emit(
                        references,
                        name_field,
                        kind="reference",
                        ref_kind="field",
                        confirmed=confirmed,
                        cross_file=cross_file,
                    )
            elif node_type == "scoped_property_access_expression":
                name_field = node.child_by_field_name("name")
                if name_field is not None and name_field.type == "variable_name":
                    bare_name = _php_variable_bare_text(name_field, source_bytes)
                    if bare_name == symbol:
                        claimed_node_ids.add((name_field.start_byte, name_field.end_byte))
                        confirmed, cross_file = _scoped_confirmation(
                            node.child_by_field_name("scope"), field_owner_types
                        )
                        _emit(
                            references,
                            name_field,
                            kind="reference",
                            ref_kind="field",
                            confirmed=confirmed,
                            cross_file=cross_file,
                        )

            stack.extend(reversed(node.children))

    def _walk_generic_identifiers(root: Any) -> None:
        stack = [root]
        while stack:
            node = stack.pop()
            node_type = node.type
            claim_key = (node.start_byte, node.end_byte)
            parent_node = node.parent
            # A "name" node whose immediate parent is "variable_name" is a purely structural
            # inner component of that variable ($foo -> "$" + name "foo") -- it is matched
            # (bare-text, $-stripped) through the "variable_name" branch below, never through
            # this branch, or a query for a variable name (e.g. "count") would double-emit once
            # per role (once here as a bare "name", once below as the variable). Verified via a
            # live AST dump: this shape is real, not hypothetical -- `private $count;`'s
            # declaration-exclusion and `Foo::$staticProp`'s field-access both hit this exact
            # double-count without the guard (see the module's TASK 10C test coverage).
            if claim_key not in claimed_node_ids and not (
                node_type == "name"
                and parent_node is not None
                and parent_node.type == "variable_name"
            ):
                if (
                    node_type == "name"
                    and _node_text(node) == symbol
                    and not _is_definition_identifier(node)
                ):
                    parent = node.parent
                    is_type_position = (
                        parent is not None and parent.type in _PHP_TYPE_POSITION_PARENT_TYPES
                    )
                    ref_kind = "type" if is_type_position else "value"
                    _emit(references, node, kind="reference", ref_kind=ref_kind, confirmed=False)
                elif (
                    node_type == "variable_name"
                    and not _is_definition_identifier(node)
                    and _php_variable_bare_text(node, source_bytes) == symbol
                ):
                    _emit(references, node, kind="reference", ref_kind="value", confirmed=False)
            stack.extend(reversed(node.children))

    _walk(tree.root_node)
    _walk_generic_identifiers(tree.root_node)

    references.sort(key=lambda item: (item["file"], item["line"], item["text"]))
    calls.sort(key=lambda item: (item["file"], item["line"], item["text"]))
    return references, calls


__all__ = [
    "php_file_imports_symbol_from_definition",
    "php_imports_and_symbols",
    "php_parser_symbol_sources",
    "php_references_and_calls",
]
